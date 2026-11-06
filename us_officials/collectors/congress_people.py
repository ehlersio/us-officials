"""Current members of Congress from unitedstates/congress-legislators."""

from __future__ import annotations

from ..config import LEGISLATORS_CURRENT_URL
from ..http import Client
from ..states import NON_STATES, ocd_state

PUBLISHER = "unitedstates/congress-legislators"

# congress-legislators id keys published under `ids`, renamed where the source name is unclear.
_ID_KEYS = {
    "bioguide": "bioguide",
    "fec": "fec",
    "opensecrets": "opensecrets",
    "votesmart": "votesmart",
    "ballotpedia": "ballotpedia",
    "wikidata": "wikidata",
    "wikipedia": "wikipedia",
    "govtrack": "govtrack",
    "icpsr": "icpsr",
    "lis": "senate_lis",
}


# congress-legislators says "Democrat"; Open States and the party itself say "Democratic".
_PARTY = {"Democrat": "Democratic"}


def person_id(bioguide: str) -> str:
    return f"bioguide-{bioguide}"


def _house_title(state: str) -> str:
    if state == "PR":
        return "Resident Commissioner"
    return "Delegate" if state in NON_STATES else "Representative"


def _division(term: dict) -> str:
    state = term["state"]
    if term["type"] == "sen" or state in NON_STATES:
        return ocd_state(state)
    district = term.get("district") or 0
    suffix = "at-large" if district == 0 else str(district)
    return f"{ocd_state(state)}/cd:{suffix}"


def person_from_legislator(leg: dict, retrieved_at: str) -> dict:
    term = leg["terms"][-1]
    name = leg["name"]
    senate = term["type"] == "sen"
    role = {
        "level": "federal",
        "chamber": "senate" if senate else "house",
        "title": "Senator" if senate else _house_title(term["state"]),
        "state": term["state"],
        "district": str(term["district"]) if not senate and term.get("district") else None,
        "at_large": (not senate) and not term.get("district"),
        "division_id": _division(term),
        "start_date": term["start"],
        "end_date": term["end"],
    }
    if senate:
        role["senate_class"] = term.get("class")
        role["state_rank"] = term.get("state_rank")
    contact = {k: term[k] for k in ("url", "phone", "address", "contact_form") if term.get(k)}
    ids = {out: leg["id"][src] for src, out in _ID_KEYS.items() if leg["id"].get(src) not in (None, "", [])}
    return {
        "id": person_id(leg["id"]["bioguide"]),
        "name": name.get("official_full") or f"{name['first']} {name['last']}",
        "given_name": name.get("first"),
        "family_name": name.get("last"),
        "party": _PARTY.get(term.get("party"), term.get("party")),
        "role": role,
        "contact": contact,
        "ids": ids,
        "sources": [{"publisher": PUBLISHER, "url": LEGISLATORS_CURRENT_URL, "retrieved_at": retrieved_at}],
    }


def collect(client: Client, retrieved_at: str) -> list[dict]:
    return [person_from_legislator(leg, retrieved_at) for leg in client.get_json(LEGISLATORS_CURRENT_URL)]
