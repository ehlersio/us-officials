"""Current state legislators (plus DC and Puerto Rico) from openstates/people."""

from __future__ import annotations

import datetime as dt
import io
import logging
import re
import tarfile

import yaml

from ..config import OPENSTATES_PEOPLE_REPO, OPENSTATES_PEOPLE_TARBALL
from ..http import Client
from ..states import BY_POSTAL, ocd_state

log = logging.getLogger(__name__)

PUBLISHER = "openstates/people"
_PATH = re.compile(r"^[^/]+/data/([a-z]{2})/legislature/[^/]+\.yml$")
_JURISDICTION = re.compile(r"ocd-jurisdiction/country:us/(?:state|district|territory):([a-z]{2})/")

# Chamber names that differ from "State Senate" / "State House". Open States records the chamber only as
# upper / lower / legislature.
_LOWER_NAMES = {
    "CA": "State Assembly", "NV": "State Assembly", "NY": "State Assembly", "WI": "State Assembly",
    "NJ": "General Assembly", "MD": "House of Delegates", "VA": "House of Delegates", "WV": "House of Delegates",
}  # fmt: skip
_UNICAMERAL_NAMES = {"NE": "Legislature", "DC": "Council"}

# Census puts Nebraska's single chamber in the upper (SLDU) layer.
CHAMBER_LAYER = {"upper": "sldu", "lower": "sldl", "legislature": "sldu"}


def person_id(ocd_person: str) -> str:
    return "openstates-" + ocd_person.removeprefix("ocd-person/")


def chamber_name(state: str, chamber: str) -> str:
    if state == "PR":
        return "Senate of Puerto Rico" if chamber == "upper" else "House of Representatives of Puerto Rico"
    if chamber == "upper":
        return "State Senate"
    if chamber == "lower":
        return _LOWER_NAMES.get(state, "State House")
    return _UNICAMERAL_NAMES.get(state, "Legislature")


def norm_district(name: str) -> str:
    """Comparable form of a district name. Open States and Census TIGER differ in case, zero padding
    ("Carroll 1" / "Carroll 01"), list punctuation ("Barnstable, Dukes and Nantucket" /
    "Barnstable-Dukes-Nantucket") and word splits ("Chittenden Southeast" / "Chittenden South East").
    Words are run together and numbers kept apart, so "Windsor-3-2" stays distinct from "Windsor-32"."""
    tokens = [t for t in re.findall(r"[a-z0-9]+", str(name).lower()) if t != "and"]
    if len(tokens) == 2 and tokens[0] == "ward" and tokens[1].isdigit():
        tokens = tokens[1:]  # DC: Open States "Ward 1", Census "1"
    parts: list[str] = []
    for t in tokens:
        if t.isdigit():
            parts.append(t.lstrip("0") or "0")
        elif parts and parts[-1].isalpha() and t.isalpha():
            parts[-1] += t
        else:
            parts.append(t)
    return "-".join(parts)


# A seat letter on a district that is one area: Idaho elects seats A and B in each House district.
SEAT_SUFFIX = re.compile(r"^(\d+)[A-Za-z]$")

# Seats elected by the whole state, DC or territory rather than by a district.
AT_LARGE = {"at-large", "atlarge", "chairman", "chair"}


def is_at_large(district: str) -> bool:
    return norm_district(district).replace("-", "") in AT_LARGE


def division_id(state: str, chamber: str, district: str) -> str:
    base = ocd_state(state)
    if state == "DC":
        m = re.fullmatch(r"ward (\d+)", district.strip().lower())
        return f"{base}/ward:{m.group(1)}" if m else base
    return f"{base}/{CHAMBER_LAYER[chamber]}:{district.strip().lower().replace(' ', '_')}"


def _as_date(value) -> dt.date | None:
    if value in (None, ""):
        return None
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))


def _current(entries: list[dict], today: dt.date) -> list[dict]:
    out = []
    for e in entries or []:
        start, end = _as_date(e.get("start_date")), _as_date(e.get("end_date"))
        if (start is None or start <= today) and (end is None or end >= today):
            out.append(e)
    return out


def person_from_record(rec: dict, today: dt.date, retrieved_at: str) -> dict | None:
    """The published person for one Open States YAML record, or None when it holds no current seat."""
    roles = [r for r in _current(rec.get("roles"), today) if r.get("type") in CHAMBER_LAYER]
    if not roles:
        return None
    role = roles[-1]
    m = _JURISDICTION.match(role.get("jurisdiction", ""))
    if not m or m.group(1).upper() not in BY_POSTAL:
        return None
    state = m.group(1).upper()
    chamber = role["type"]
    district = str(role.get("district", "")).strip()
    parties = _current(rec.get("party"), today)
    contact = {}
    if rec.get("email"):
        contact["email"] = rec["email"]
    offices = rec.get("offices") or []
    if offices:
        office = next((o for o in offices if o.get("classification") == "capitol"), offices[0])
        contact.update({k: office[k] for k in ("address", "voice") if office.get(k)})
        if "voice" in contact:
            contact["phone"] = contact.pop("voice")
    links = [link["url"] for link in rec.get("links") or [] if link.get("url")]
    if links:
        contact["url"] = links[0]
    sources = [{"publisher": PUBLISHER, "url": OPENSTATES_PEOPLE_REPO, "retrieved_at": retrieved_at}]
    sources += [{"url": s["url"]} for s in rec.get("sources") or [] if s.get("url")]
    return {
        "id": person_id(rec["id"]),
        "name": rec["name"],
        "given_name": rec.get("given_name"),
        "family_name": rec.get("family_name"),
        "party": parties[-1]["name"] if parties else None,
        "role": {
            "level": "state",
            "chamber": chamber,
            "chamber_name": chamber_name(state, chamber),
            "state": state,
            "district": district,
            "division_id": division_id(state, chamber, district),
            "start_date": str(role["start_date"]) if role.get("start_date") else None,
            "end_date": str(role["end_date"]) if role.get("end_date") else None,
        },
        "contact": contact,
        "ids": {"openstates": rec["id"]},
        "image": rec.get("image"),
        "sources": sources,
    }


def parse_tarball(blob: bytes, today: dt.date, retrieved_at: str) -> list[dict]:
    people, skipped = [], 0
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar:
            m = _PATH.match(member.name) if member.isfile() else None
            # data/us/ holds members of Congress, which come from congress-legislators instead.
            if not m or m.group(1) == "us":
                continue
            rec = yaml.safe_load(tar.extractfile(member).read())
            person = person_from_record(rec, today, retrieved_at)
            if person is None:
                skipped += 1
            else:
                people.append(person)
    if skipped:
        log.info("openstates: %d legislature records hold no current seat on %s; skipped", skipped, today)
    return people


def collect(client: Client, today: dt.date, retrieved_at: str) -> list[dict]:
    return parse_tarball(client.get_bytes(OPENSTATES_PEOPLE_TARBALL), today, retrieved_at)
