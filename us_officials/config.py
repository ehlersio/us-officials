"""Source URLs, the client identity and the district-map table."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

USER_AGENT = "us-officials/0.1 (+https://github.com/ehlersio/us-officials)"

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
DIST_DIR = ROOT / "dist"
API_VERSION = "v1"

# Members of Congress, with a crosswalk to FEC, OpenSecrets, Vote Smart, Ballotpedia, Wikidata and other ids.
LEGISLATORS_CURRENT_URL = "https://unitedstates.github.io/congress-legislators/legislators-current.json"

# State legislators (CC0, Open Civic Data person ids). The tarball is the whole repository.
OPENSTATES_PEOPLE_TARBALL = "https://codeload.github.com/openstates/people/tar.gz/refs/heads/main"
OPENSTATES_PEOPLE_REPO = "https://github.com/openstates/people"

# House roll calls, straight from the Clerk. A roll number past the last one still answers 200, with an
# <xml>Error sanitizing file</xml> body instead of <rollcall-vote>.
HOUSE_CLERK_ROLL_URL = "https://clerk.house.gov/evs/{year}/roll{roll:03d}.xml"

# Senate roll calls. senate.gov answers automated clients with a 403 from its firewall, so Senate votes come
# from Voteview (UCLA), which republishes them with the Senate's own roll numbers and member bioguide ids.
VOTEVIEW_BASE = "https://voteview.com/static/data/out"
SENATE_VOTE_URL = (
    "https://www.senate.gov/legislative/LIS/roll_call_votes/vote{congress}{session}/"
    "vote_{congress}_{session}_{roll:05d}.htm"
)

# Sponsored bills need a free api.data.gov key; without one the collector is skipped and no bill
# fields are published.
CONGRESS_API = "https://api.congress.gov/v3"
CONGRESS_API_KEY = os.environ.get("CONGRESS_API_KEY", "")

# District names per state and chamber, used to tie Open States district names to the Census GEOIDs the
# geocoder returns.
TIGERWEB_LEGISLATIVE = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Legislative/MapServer"

# Which district maps the people currently in office were elected under, per Congress. A row is needed for
# every Congress: maps redrawn for an election only apply once the winners are seated (January 3 for
# Congress). State legislatures seat new members on their own dates, mostly in January; between an
# election and the seating the lookup can be off for the few states that redrew state legislative maps.
#   geocoder_vintage: the Census geocoder vintage whose layers are these maps
#   tiger_layers: the TIGERweb Legislative MapServer layer ids for the same maps
DISTRICT_MAPS: dict[int, dict] = {
    119: {
        "geocoder_vintage": "ACS2025_Current",
        "congressional": "119th Congressional Districts",
        "state_legislative": "2024 State Legislative Districts",
        "tiger_layers": {"cd": 4, "sldu": 5, "sldl": 6},
    },
    120: {
        "geocoder_vintage": "Current_Current",
        "congressional": "120th Congressional Districts",
        "state_legislative": "2026 State Legislative Districts",
        "tiger_layers": {"cd": 0, "sldu": 1, "sldl": 2},
    },
}


def congress_for(day: dt.date) -> int:
    """The Congress in session on `day`. Each starts on January 3 of an odd year."""
    year = day.year
    if day.month == 1 and day.day < 3:
        year -= 1
    return (year - 1787) // 2


def congress_years(congress: int) -> tuple[int, int]:
    first = 2 * congress + 1787
    return first, first + 1


def district_maps(congress: int) -> dict:
    try:
        return DISTRICT_MAPS[congress]
    except KeyError:
        raise SystemExit(
            f"No DISTRICT_MAPS entry for the {congress}th Congress in us_officials/config.py. Add the Census "
            "geocoder vintage and TIGERweb layer ids for the maps its members were elected under."
        ) from None


# States where some legislators cannot be tied to a Census district for a known structural reason, with the
# lowest acceptable share of tied legislators. Everything else must reach the --min-match-rate (90%).
KNOWN_GAPS: dict[str, dict] = {
    "NH": {
        "min_match_rate": 0.8,
        "reason": "Floterial House districts overlap the base districts; Census maps only the base districts, so "
        "floterial representatives are not returned by the lookup.",
    },
}
