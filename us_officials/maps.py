"""Which district maps describe the people now in office.

Congress: the Congress most House members' current terms began in, so the lookup moves to new congressional
maps when congress-legislators lists the new members, whatever the date.

State legislatures: per state and chamber, the newest map a group of current members was seated under (see
config.STATE_LEGISLATIVE_MAPS). The choice, and the evidence for it, is published in coverage.json."""

from __future__ import annotations

import datetime as dt
import math
from collections import Counter, defaultdict

from .collectors.state_people import CHAMBER_LAYER
from .config import (
    GEOCODER,
    GEOCODER_CHECK_ADDRESS,
    STATE_LEGISLATIVE_MAPS,
    STATE_MAP_COHORT,
    STATE_MAP_OVERRIDES,
    congress_for,
)
from .http import Client
from .states import BY_FIPS

LAYER_SUFFIX = {"sldu": " - Upper", "sldl": " - Lower"}


def seated_congress(federal: list[dict]) -> int:
    starts = Counter(p["role"]["start_date"] for p in federal if p["role"]["chamber"] == "house")
    if not starts:
        raise ValueError("no House members to tell the seated Congress from")
    return congress_for(dt.date.fromisoformat(starts.most_common(1)[0][0]))


def layer_name(map_name: str, layer: str) -> str:
    return map_name + LAYER_SUFFIX[layer]


def choose_state_maps(
    state_people: list[dict],
    maps: list[dict] = STATE_LEGISLATIVE_MAPS,
    overrides: dict[tuple[str, str], str] = STATE_MAP_OVERRIDES,
    cohort: dict = STATE_MAP_COHORT,
) -> dict[tuple[str, str], dict]:
    """{(state, "sldu"|"sldl"): {"map", "geocoder_vintage", "seats", "cohort_date", "cohort_seats", "override"}}."""
    seats: Counter = Counter()
    starts: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for p in state_people:
        r = p["role"]
        key = (r["state"], CHAMBER_LAYER[r["chamber"]])
        seats[key] += 1
        if r.get("start_date"):
            starts[key][r["start_date"]] += 1

    by_name = {m["name"]: m for m in maps}
    out = {}
    for key, n in seats.items():
        chosen, evidence = maps[0], (None, 0)
        for m in maps[1:]:
            after = m["cohort_after"]
            days = [(c, d) for d, c in starts[key].items() if d > after]
            if not days:
                continue
            size, day = max(days)
            if size >= max(cohort["min_seats"], math.ceil(cohort["share"] * n)):
                chosen, evidence = m, (day, size)
        override = overrides.get(key)
        if override:
            chosen = by_name[override]
        out[key] = {
            "map": chosen["name"],
            "geocoder_vintage": chosen["geocoder_vintage"],
            "seats": n,
            "cohort_date": evidence[0],
            "cohort_seats": evidence[1],
            "override": bool(override),
        }
    return out


def _signature(rows: list[dict]) -> frozenset:
    return frozenset((r["GEOID"], r["BASENAME"], r.get("AREALAND")) for r in rows)


def state_rows(tiger: dict[str, list[dict]], choices: dict[tuple[str, str], dict]) -> tuple[dict, dict]:
    """The chosen map's rows per chamber, and per (state, layer) every map name whose districts there are
    identical to the chosen one's (so the Worker can accept any of them without a second geocoder call).

    `tiger` holds rows by TIGER layer name for every configured state legislative map."""
    per_state: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for name, rows in tiger.items():
        for r in rows:
            st = BY_FIPS.get(r["STATE"])
            if st:
                per_state[(name, st["postal"], "sldu" if name.endswith(" - Upper") else "sldl")].append(r)

    out: dict[str, list[dict]] = {"sldu": [], "sldl": []}
    accept: dict[tuple[str, str], list[str]] = {}
    postals = {p for (_, p, _) in per_state}
    for postal in sorted(postals):
        for layer in ("sldu", "sldl"):
            choice = choices.get((postal, layer))
            chosen = choice["map"] if choice else STATE_LEGISLATIVE_MAPS[0]["name"]
            rows = per_state.get((layer_name(chosen, layer), postal, layer), [])
            out[layer] += rows
            same = [
                m["name"] for m in STATE_LEGISLATIVE_MAPS if m["name"] != chosen
                and _signature(per_state.get((layer_name(m["name"], layer), postal, layer), [])) == _signature(rows)
            ]
            accept[(postal, layer)] = [chosen, *same]
    return out, accept


def check_geocoder(client: Client, vintages: dict[str, list[str]]) -> None:
    """Fail the build unless each geocoder vintage answers with the layers the lookup will read from it."""
    for vintage, layers in vintages.items():
        data = client.get_json(
            f"{GEOCODER}/onelineaddress",
            params={"address": GEOCODER_CHECK_ADDRESS, "benchmark": "Public_AR_Current", "vintage": vintage,
                    "layers": "all", "format": "json"},
        )
        matches = data.get("result", {}).get("addressMatches") or []
        found = set(matches[0]["geographies"]) if matches else set()
        missing = [name for name in layers if name not in found]
        if missing:
            raise SystemExit(f"Census geocoder vintage {vintage} no longer has {missing}; it has {sorted(found)}")
