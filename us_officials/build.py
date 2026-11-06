"""Turn collected rows into the static API under dist/v1/.

Layout (every path is a plain JSON file, so any static host or mirror can serve it):
  index.json                          what is here, counts, sources, district maps in use
  people/index.json                   one summary row per current officeholder
  people/<id>.json                    full record: role, contact, ids in other databases, sources
  people/<id>/votes.json              recorded votes (members of Congress)
  people/<id>/bills.json              sponsored bills (only when collected with a Congress.gov key)
  votes/index.json                    every roll call without positions
  votes/<chamber>/<congress>-<session>-<roll>.json   one roll call with every member's position
  lookup/meta.json                    the congressional map and geocoder vintage the lookup Worker starts from
  lookup/<state>.json                 Census GEOID -> person ids, per chamber, for one state
  coverage.json                       districts that could not be tied to legislators, per state
"""

from __future__ import annotations

import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path

from .collectors.state_people import CHAMBER_LAYER, SEAT_SUFFIX, is_at_large, norm_district
from .config import KNOWN_GAPS
from .states import BY_FIPS, NON_STATES


def summary(p: dict) -> dict:
    r = p["role"]
    return {
        "id": p["id"],
        "name": p["name"],
        "party": p.get("party"),
        "level": r["level"],
        "chamber": r["chamber"],
        "office": r.get("title") or r.get("chamber_name"),
        "state": r["state"],
        "district": r.get("district"),
        "url": f"/v1/people/{p['id']}.json",
    }


def vote_id(v: dict) -> str:
    return f"{v['congress']}-{v['session']}-{v['roll']}"


def vote_url(v: dict) -> str:
    return f"/v1/votes/{v['chamber']}/{vote_id(v)}.json"


def _vote_meta(v: dict) -> dict:
    return {k: v.get(k) for k in ("chamber", "congress", "session", "roll", "date", "question", "description", "bill",
                                  "result")}


def _cd_district(geoid: str) -> int:
    code = int(geoid[2:])
    return 0 if code in (0, 98) else code  # 00 at-large, 98 non-voting delegate


def build_lookup(
    federal: list[dict],
    state_people: list[dict],
    tiger: dict[str, list[dict]],
    choices: dict[tuple[str, str], dict] | None = None,
    accept: dict[tuple[str, str], list[str]] | None = None,
) -> tuple[dict, dict]:
    """Per-state lookup tables and the coverage report.

    `tiger` has the congressional ("cd") and chosen state legislative ("sldu", "sldl") district rows;
    `choices` and `accept` (from maps.choose_state_maps and maps.state_rows) say which state legislative
    map each chamber is on and which geocoder layer names the Worker may read its districts from."""
    lookup: dict[str, dict] = {}
    coverage: dict[str, dict] = {}

    def table(postal: str) -> dict:
        empty = {"state": postal, "senate": [], "cd": {}, "sldu": {}, "sldl": {}, "at_large": [], "people": {}}
        return lookup.setdefault(postal, empty)

    house = defaultdict(list)
    for p in federal:
        r = p["role"]
        t = table(r["state"])
        if r["chamber"] == "senate":
            t["senate"].append(p["id"])
            t["people"][p["id"]] = summary(p)
        else:
            house[(r["state"], int(r["district"] or 0))].append(p)

    for row in tiger.get("cd", []):
        st = BY_FIPS.get(row["STATE"])
        if not st:
            continue
        postal = st["postal"]
        members = house.get((postal, _cd_district(row["GEOID"])), [])
        t = table(postal)
        t["cd"][row["GEOID"]] = [m["id"] for m in members]
        for m in members:
            t["people"][m["id"]] = summary(m)
        if not members:
            coverage.setdefault(postal, {}).setdefault("house_districts_without_member", []).append(row["BASENAME"])

    # State legislators: Open States district name -> TIGER GEOID, per state and Census layer.
    by_name: dict[tuple[str, str, str], str] = {}
    names_of: dict[str, str] = {}
    with_districts: set[str] = set()
    for layer in ("sldu", "sldl"):
        for row in tiger.get(layer, []):
            st = BY_FIPS.get(row["STATE"])
            if not st:
                continue
            key = (st["postal"], layer, norm_district(row["BASENAME"]))
            if key in by_name:
                raise ValueError(f"two Census districts normalize to {key}: {by_name[key]}, {row['GEOID']}")
            by_name[key] = row["GEOID"]
            names_of[row["GEOID"]] = row["BASENAME"]
            with_districts.add(st["postal"])

    seated: dict[str, set[str]] = defaultdict(set)
    counts: dict[str, Counter] = defaultdict(Counter)
    for p in state_people:
        r = p["role"]
        postal, layer = r["state"], CHAMBER_LAYER[r["chamber"]]
        counts[postal]["state_legislators"] += 1
        t = table(postal)
        if is_at_large(r["district"]):
            counts[postal]["matched"] += 1
            t["at_large"].append(p["id"])
            t["people"][p["id"]] = summary(p)
            continue
        geoid = by_name.get((postal, layer, norm_district(r["district"])))
        seat = SEAT_SUFFIX.match(r["district"])
        if geoid is None and seat:
            geoid = by_name.get((postal, layer, norm_district(seat.group(1))))
        if geoid is None:
            coverage.setdefault(postal, {}).setdefault("unmatched_districts", []).append(
                {"chamber": r["chamber"], "district": r["district"], "person": p["id"]}
            )
            continue
        counts[postal]["matched"] += 1
        t[layer].setdefault(geoid, []).append(p["id"])
        t["people"][p["id"]] = summary(p)
        seated[postal].add(geoid)

    for (postal, layer, _), geoid in by_name.items():
        if geoid not in seated[postal]:
            coverage.setdefault(postal, {}).setdefault("districts_without_legislator", []).append(
                {"layer": layer, "geoid": geoid, "name": names_of[geoid]}
            )
            table(postal)[layer].setdefault(geoid, [])

    for (postal, layer), names in (accept or {}).items():
        if postal not in lookup:
            continue
        choice = (choices or {}).get((postal, layer), {})
        lookup[postal].setdefault("maps", {})[layer] = {
            "accept": [m + (" - Upper" if layer == "sldu" else " - Lower") for m in names],
            "geocoder_vintage": choice.get("geocoder_vintage"),
        }
        if choice:
            coverage.setdefault(postal, {}).setdefault("state_legislative_maps", {})[layer] = {
                k: choice[k] for k in ("map", "seats", "cohort_date", "cohort_seats", "override")
            }

    for postal, c in counts.items():
        cov = coverage.setdefault(postal, {})
        cov.update(c)
        cov["has_census_districts"] = postal in with_districts
        if postal in KNOWN_GAPS:
            cov["known_gap"] = KNOWN_GAPS[postal]["reason"]
        cov["match_rate"] = round(c["matched"] / c["state_legislators"], 4) if c["state_legislators"] else None
    for cov in coverage.values():
        for key in ("unmatched_districts", "districts_without_legislator"):
            if key in cov:
                cov[key].sort(key=lambda d: json.dumps(d, sort_keys=True))
    return lookup, dict(sorted(coverage.items()))


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


def build(
    out_dir: Path,
    *,
    federal: list[dict],
    state_people: list[dict],
    votes: list[dict],
    tiger: dict[str, list[dict]],
    sponsored: dict[str, list[dict]] | None,
    congress: int,
    lookup_meta: dict,
    choices: dict[tuple[str, str], dict] | None = None,
    accept: dict[tuple[str, str], list[str]] | None = None,
    generated_at: str,
    sources: list[dict],
) -> dict:
    root = out_dir / "v1"
    if root.exists():
        shutil.rmtree(root)
    people = federal + state_people
    by_bioguide = {p["ids"]["bioguide"]: p for p in federal}

    personal_votes: dict[str, list[dict]] = defaultdict(list)
    vote_index = []
    for v in sorted(votes, key=lambda v: (v["chamber"], v["congress"], v["session"], v["roll"])):
        meta = _vote_meta(v)
        totals = dict(Counter(v["positions"].values()))
        vote_index.append({**meta, "totals": totals, "url": vote_url(v)})
        _write(root / "votes" / v["chamber"] / f"{vote_id(v)}.json", {**v, "totals": totals})
        for bioguide, position in v["positions"].items():
            if bioguide in by_bioguide:
                personal_votes[bioguide].append({**meta, "position": position, "url": vote_url(v)})

    for p in people:
        record = dict(p)
        bioguide = p["ids"].get("bioguide")
        if bioguide:
            mine = sorted(personal_votes.get(bioguide, []), key=lambda v: (v["date"], v["chamber"], v["roll"]))
            record["votes_url"] = f"/v1/people/{p['id']}/votes.json"
            _write(root / "people" / p["id"] / "votes.json", {"person": p["id"], "votes": mine})
            if sponsored is not None and bioguide in sponsored:
                record["bills_url"] = f"/v1/people/{p['id']}/bills.json"
                _write(root / "people" / p["id"] / "bills.json", {"person": p["id"], "sponsored": sponsored[bioguide]})
        _write(root / "people" / f"{p['id']}.json", record)
    _write(root / "people" / "index.json", sorted((summary(p) for p in people), key=lambda s: (s["state"], s["id"])))
    _write(root / "votes" / "index.json", vote_index)

    lookup, coverage = build_lookup(federal, state_people, tiger, choices, accept)
    lookup_meta = {"congress": congress, **lookup_meta}
    _write(root / "lookup" / "meta.json", lookup_meta)
    for postal, t in lookup.items():
        t["has_senators"] = postal not in NON_STATES
        if postal in KNOWN_GAPS:
            t["known_gap"] = KNOWN_GAPS[postal]["reason"]
        _write(root / "lookup" / f"{postal.lower()}.json", t)
    _write(root / "coverage.json", coverage)

    counts = {
        "members_of_congress": len(federal),
        "state_legislators": len(state_people),
        "house_votes": sum(v["chamber"] == "house" for v in votes),
        "senate_votes": sum(v["chamber"] == "senate" for v in votes),
        "states_in_lookup": len(lookup),
    }
    if sponsored is not None:
        counts["sponsored_bills"] = sum(len(b) for b in sponsored.values())
    index = {
        "api_version": "v1",
        "generated_at": generated_at,
        "congress": congress,
        "district_maps": {
            **lookup_meta,
            "state_legislative": dict(Counter(c["map"] for c in (choices or {}).values())),
        },
        "counts": counts,
        "sources": sources,
        "license": {"data": "CC0-1.0", "code": "MIT"},
    }
    _write(root / "index.json", index)
    # Workers static assets read _headers from the asset root: open CORS, short cache.
    (out_dir / "_headers").write_text(
        "/v1/*\n  Access-Control-Allow-Origin: *\n  Cache-Control: public, max-age=3600\n"
    )
    return index
