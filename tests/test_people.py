import datetime as dt
import io
import tarfile

import pytest
import yaml

from us_officials.collectors import congress_people, state_people
from us_officials.collectors.state_people import is_at_large, norm_district
from us_officials.config import congress_for, congress_years

TODAY = dt.date(2026, 11, 6)


def legislator(type_, state, district=None, **ids):
    term = {"type": type_, "start": "2025-01-03", "end": "2027-01-03", "state": state, "party": "Independent",
            "url": "https://example.house.gov", "phone": "202-555-0100"}
    if district is not None:
        term["district"] = district
    if type_ == "sen":
        term.update({"class": 1, "state_rank": "junior", "end": "2031-01-03"})
    return {"id": {"bioguide": "X000001", **ids}, "name": {"first": "Pat", "last": "Doe"}, "terms": [term]}


@pytest.mark.parametrize(
    ("type_", "state", "district", "title", "division"),
    [
        ("sen", "NC", None, "Senator", "ocd-division/country:us/state:nc"),
        ("rep", "NC", 2, "Representative", "ocd-division/country:us/state:nc/cd:2"),
        ("rep", "AK", 0, "Representative", "ocd-division/country:us/state:ak/cd:at-large"),
        ("rep", "DC", 0, "Delegate", "ocd-division/country:us/district:dc"),
        ("rep", "PR", 0, "Resident Commissioner", "ocd-division/country:us/territory:pr"),
    ],
)
def test_congress_roles(type_, state, district, title, division):
    p = congress_people.person_from_legislator(legislator(type_, state, district), "2026-11-06T00:00:00Z")
    assert p["id"] == "bioguide-X000001"
    assert p["role"]["title"] == title
    assert p["role"]["division_id"] == division
    assert p["sources"][0]["publisher"] == "unitedstates/congress-legislators"
    assert p["role"]["district"] == (str(district) if district else None)


def test_congress_ids_crosswalk_skips_empty():
    leg = legislator("rep", "NC", 2, fec=["H0NC00000"], opensecrets="N00000001", votesmart=None, wikidata="")
    ids = congress_people.person_from_legislator(leg, "t")["ids"]
    assert ids == {"bioguide": "X000001", "fec": ["H0NC00000"], "opensecrets": "N00000001"}


def test_congress_party_name_matches_open_states():
    leg = legislator("rep", "NC", 2)
    leg["terms"][-1]["party"] = "Democrat"
    assert congress_people.person_from_legislator(leg, "t")["party"] == "Democratic"


def osp(roles, **extra):
    return {"id": "ocd-person/00000000-0000-0000-0000-000000000001", "name": "Sam Roe", "roles": roles,
            "party": [{"name": "Democratic"}], **extra}


def role(type_="lower", district="38", state="nc", start=None, end=None):
    r = {"type": type_, "district": district, "jurisdiction": f"ocd-jurisdiction/country:us/state:{state}/government"}
    if start:
        r["start_date"] = start
    if end:
        r["end_date"] = end
    return r


def test_state_person_current_role():
    p = state_people.person_from_record(osp([role()], email="sam@example.gov"), TODAY, "t")
    assert p["id"] == "openstates-00000000-0000-0000-0000-000000000001"
    assert p["role"]["chamber_name"] == "State House"
    assert p["role"]["division_id"] == "ocd-division/country:us/state:nc/sldl:38"
    assert p["contact"]["email"] == "sam@example.gov"
    assert p["party"] == "Democratic"


def test_state_person_without_current_seat_is_skipped():
    assert state_people.person_from_record(osp([role(end=dt.date(2026, 1, 1))]), TODAY, "t") is None
    assert state_people.person_from_record(osp([role(start=dt.date(2027, 1, 1))]), TODAY, "t") is None


def test_chamber_names():
    assert state_people.chamber_name("CA", "lower") == "State Assembly"
    assert state_people.chamber_name("VA", "lower") == "House of Delegates"
    assert state_people.chamber_name("NE", "legislature") == "Legislature"
    assert state_people.chamber_name("TX", "upper") == "State Senate"
    assert state_people.chamber_name("PR", "lower") == "House of Representatives of Puerto Rico"


def test_parse_tarball_skips_congress_and_other_files():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, rec in [
            ("people-main/data/nc/legislature/Sam-Roe.yml", osp([role()])),
            ("people-main/data/us/legislature/Federal.yml", osp([role()])),
            ("people-main/data/nc/retired/Old.yml", osp([role()])),
        ]:
            blob = yaml.safe_dump(rec).encode()
            info = tarfile.TarInfo(name)
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))
    people = state_people.parse_tarball(buf.getvalue(), TODAY, "t")
    assert [p["role"]["state"] for p in people] == ["NC"]


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("014", "14"),
        ("Carroll 01", "Carroll 1"),
        ("Barnstable-Dukes-Nantucket", "Barnstable, Dukes and Nantucket"),
        ("Chittenden South East", "Chittenden Southeast"),
        ("Ward 3", "3"),
        ("14th Suffolk", "14th  suffolk"),
    ],
)
def test_norm_district_matches_spellings(a, b):
    assert norm_district(a) == norm_district(b)


def test_norm_district_keeps_numbers_apart():
    assert norm_district("Windsor-3-2") != norm_district("Windsor-32")
    assert norm_district("21A") != norm_district("21B")


def test_at_large():
    assert is_at_large("At-Large") and is_at_large("Chairman") and not is_at_large("12")


def test_congress_numbers():
    assert congress_for(dt.date(2026, 11, 6)) == 119
    assert congress_for(dt.date(2027, 1, 2)) == 119
    assert congress_for(dt.date(2027, 1, 3)) == 120
    assert congress_years(119) == (2025, 2026)
