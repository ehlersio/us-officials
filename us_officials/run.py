"""Collect every source and write the static API: `python -m us_officials.run`."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys

from . import build, maps
from .collectors import congress_people, districts, house_votes, senate_votes, sponsored_bills, state_people
from .config import (
    DIST_DIR,
    KNOWN_GAPS,
    LEGISLATORS_CURRENT_URL,
    OPENSTATES_PEOPLE_REPO,
    RAW_DIR,
    SPONSORED_STATE,
    STATE_LEGISLATIVE_MAPS,
    TIGERWEB_LEGISLATIVE,
    VOTEVIEW_BASE,
    congress_years,
    congressional_map,
)
from .http import Client

log = logging.getLogger("us_officials")


def _sources(retrieved_at: str, with_bills: bool) -> list[dict]:
    rows = [
        {"publisher": congress_people.PUBLISHER, "url": LEGISLATORS_CURRENT_URL, "provides": "members of Congress"},
        {"publisher": state_people.PUBLISHER, "url": OPENSTATES_PEOPLE_REPO, "provides": "state legislators"},
        {"publisher": house_votes.PUBLISHER, "url": "https://clerk.house.gov/Votes", "provides": "House roll calls"},
        {"publisher": senate_votes.PUBLISHER, "url": VOTEVIEW_BASE, "provides": "Senate roll calls"},
        {"publisher": districts.PUBLISHER, "url": TIGERWEB_LEGISLATIVE, "provides": "district names and GEOIDs"},
    ]
    if with_bills:
        rows.append({"publisher": sponsored_bills.PUBLISHER, "url": "https://api.congress.gov/",
                     "provides": "sponsored bills"})
    return [{**r, "retrieved_at": retrieved_at} for r in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=dt.date.fromisoformat, default=None, help="as-of date (default: today, UTC)")
    parser.add_argument("--house-max-new", type=int, default=None, help="cap new House roll calls per year (testing)")
    parser.add_argument("--min-match-rate", type=float, default=0.9,
                        help="fail when a state's legislators tie to Census districts below this rate")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    now = dt.datetime.now(dt.UTC)
    today = args.date or now.date()
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    client = Client()

    federal = congress_people.collect(client, stamp)
    congress = maps.seated_congress(federal)
    cd_map = congressional_map(congress)
    log.info("congress-legislators: %d current members of the %dth Congress", len(federal), congress)
    states = state_people.collect(client, today, stamp)
    log.info("openstates: %d current state legislators", len(states))

    choices = maps.choose_state_maps(states)
    moved = sorted(f"{s}-{layer}" for (s, layer), c in choices.items() if c["map"] != STATE_LEGISLATIVE_MAPS[0]["name"])
    log.info("state legislative maps: %d chambers past %s: %s", len(moved), STATE_LEGISLATIVE_MAPS[0]["name"], moved)
    sld_names = [maps.layer_name(m["name"], layer) for m in STATE_LEGISLATIVE_MAPS for layer in ("sldu", "sldl")]
    layers = districts.collect(client, [cd_map["name"], *sld_names])
    sld, accept = maps.state_rows({n: layers[n] for n in sld_names}, choices)
    tiger = {"cd": layers[cd_map["name"]], **sld}
    log.info("tigerweb: %s", {k: len(v) for k, v in tiger.items()})

    # Every geocoder vintage the Worker may call must still have the layers it will read from it.
    wanted: dict[str, set[str]] = {cd_map["geocoder_vintage"]: {cd_map["name"]}}
    for (_, layer), c in choices.items():
        wanted.setdefault(c["geocoder_vintage"], set()).add(maps.layer_name(c["map"], layer))
    maps.check_geocoder(client, {v: sorted(names) for v, names in wanted.items()})

    votes = []
    for year in congress_years(congress):
        if year <= today.year:
            votes += [v for v in house_votes.collect_year(client, RAW_DIR, year, max_new=args.house_max_new)
                      if v["congress"] == congress]
    votes += senate_votes.collect(client, congress)
    log.info("votes: %d", len(votes))

    sponsored = sponsored_bills.collect(
        client, [p["ids"]["bioguide"] for p in federal], congress=congress, now=now, state_path=SPONSORED_STATE
    )

    index = build.build(
        DIST_DIR,
        federal=federal,
        state_people=states,
        votes=votes,
        tiger=tiger,
        sponsored=sponsored,
        congress=congress,
        lookup_meta={"congressional": cd_map["name"], "geocoder_vintage": cd_map["geocoder_vintage"]},
        choices=choices,
        accept=accept,
        generated_at=stamp,
        sources=_sources(stamp, sponsored is not None),
    )
    print(json.dumps(index["counts"], indent=2))

    coverage = json.loads((DIST_DIR / "v1" / "coverage.json").read_text())
    low = {s: c["match_rate"] for s, c in coverage.items() if c.get("has_census_districts")
           and c["match_rate"] < KNOWN_GAPS.get(s, {}).get("min_match_rate", args.min_match_rate)}
    if low:
        log.error("state legislators tied to Census districts below %.0f%%: %s", args.min_match_rate * 100, low)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
