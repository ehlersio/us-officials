"""Source URLs, the client identity and the district-map table."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

USER_AGENT = "us-officials/0.1 (+https://github.com/ehlersio/us-officials)"

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
# Sponsored-bill lists between runs. Too large and too churny for git: the update workflow keeps it on the
# `api` release instead.
SPONSORED_STATE = ROOT / "data" / "sponsored_bills.json.gz"
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

# District maps. The lookup must use the maps the people now in office were elected under, which is not the
# newest map: maps drawn for an election only apply once its winners are seated. Names are the layer names
# the Census geocoder and TIGERweb both use; TIGERweb layer ids are looked up by name at build time, since
# Census renumbers layers when it adds new ones.
#
# Congressional maps, per Congress. A row is needed for every Congress; which Congress is seated comes from
# the members' own term dates (see maps.seated_congress), so the switch happens when congress-legislators
# lists the new members, not on a calendar date.
CONGRESSIONAL_MAPS: dict[int, dict] = {
    119: {"name": "119th Congressional Districts", "geocoder_vintage": "ACS2025_Current"},
    120: {"name": "120th Congressional Districts", "geocoder_vintage": "Current_Current"},
}

# State legislative maps, oldest first. A chamber moves to a newer map once a group of its members elected
# under that map has been seated: at least STATE_MAP_COHORT of its seats sharing one start date later than
# `cohort_after` (see maps.choose_state_maps). Special elections are spread over many dates and do not
# count. Each state legislature seats new members on its own date, so states switch one by one.
STATE_LEGISLATIVE_MAPS: list[dict] = [
    {"name": "2024 State Legislative Districts", "geocoder_vintage": "ACS2025_Current", "cohort_after": None},
    {"name": "2026 State Legislative Districts", "geocoder_vintage": "Current_Current", "cohort_after": "2025-06-01"},
]
STATE_MAP_COHORT = {"share": 0.08, "min_seats": 3}

# Corrections where the cohort rule picks the wrong map: (state, "sldu" | "sldl") -> map name.
STATE_MAP_OVERRIDES: dict[tuple[str, str], str] = {}

# A public building's address, geocoded at build time to confirm every configured geocoder vintage still
# answers with the expected map layers before anything is published.
GEOCODER_CHECK_ADDRESS = "16 W Jones St, Raleigh, NC 27601"
GEOCODER = "https://geocoding.geo.census.gov/geocoder/geographies"


def congress_for(day: dt.date) -> int:
    """The Congress in session on `day`. Each starts on January 3 of an odd year."""
    year = day.year
    if day.month == 1 and day.day < 3:
        year -= 1
    return (year - 1787) // 2


def congress_years(congress: int) -> tuple[int, int]:
    first = 2 * congress + 1787
    return first, first + 1


def congressional_map(congress: int) -> dict:
    try:
        return CONGRESSIONAL_MAPS[congress]
    except KeyError:
        raise SystemExit(
            f"No CONGRESSIONAL_MAPS entry for the {congress}th Congress in us_officials/config.py. Add the "
            "congressional district layer name and a Census geocoder vintage that has it."
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
