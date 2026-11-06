import httpx
import pytest

from us_officials import build, maps
from us_officials.config import STATE_LEGISLATIVE_MAPS, congressional_map
from us_officials.http import Client

OLD, NEW = (m["name"] for m in STATE_LEGISLATIVE_MAPS)


def house(start, district="1", state="NC"):
    return {"role": {"chamber": "house", "start_date": start, "state": state, "district": district}}


def legislator(state, chamber, start, n=1):
    return [{"role": {"state": state, "chamber": chamber, "start_date": start}} for _ in range(n)]


def test_seated_congress_follows_the_members_not_the_date():
    before = [house("2025-01-03")] * 400 + [house("2026-06-10")] * 3  # a few special elections
    assert maps.seated_congress(before) == 119
    after = [house("2027-01-03")] * 435
    assert maps.seated_congress(after) == 120
    assert congressional_map(120)["name"] == "120th Congressional Districts"


def test_unknown_congress_fails_loudly():
    with pytest.raises(SystemExit, match="CONGRESSIONAL_MAPS"):
        congressional_map(121)


def test_a_seated_cohort_moves_a_chamber_to_the_new_map():
    people = (legislator("VA", "lower", "2024-01-10", 80) + legislator("VA", "lower", "2026-01-14", 20)
              + legislator("FL", "upper", "2022-11-08", 35)
              + legislator("FL", "upper", "2025-06-10") + legislator("FL", "upper", "2025-09-02")
              + legislator("FL", "upper", "2026-03-24", 2))  # special elections on different days
    choices = maps.choose_state_maps(people)
    assert choices[("VA", "sldl")]["map"] == NEW
    assert choices[("VA", "sldl")]["cohort_date"] == "2026-01-14" and choices[("VA", "sldl")]["cohort_seats"] == 20
    assert choices[("FL", "sldu")]["map"] == OLD
    assert choices[("FL", "sldu")]["geocoder_vintage"] == STATE_LEGISLATIVE_MAPS[0]["geocoder_vintage"]


def test_january_seating_switches_states_one_by_one():
    # NC seats its 2026 winners in January; TX has not yet.
    people = (legislator("NC", "lower", "2021-01-13", 100) + legislator("NC", "lower", "2027-01-13", 20)
              + legislator("TX", "lower", "2023-01-10", 150))
    choices = maps.choose_state_maps(people)
    assert choices[("NC", "sldl")]["map"] == NEW and choices[("TX", "sldl")]["map"] == OLD


def test_override_wins():
    choices = maps.choose_state_maps(legislator("MS", "lower", "2024-01-02", 120), overrides={("MS", "sldl"): NEW})
    assert choices[("MS", "sldl")]["map"] == NEW and choices[("MS", "sldl")]["override"]


def row(geoid, name, area):
    return {"GEOID": geoid, "STATE": geoid[:2], "BASENAME": name, "AREALAND": area}


def test_state_rows_picks_the_chosen_map_and_accepts_identical_ones():
    tiger = {
        f"{OLD} - Upper": [row("37001", "1", 10), row("51001", "1", 10)],
        f"{NEW} - Upper": [row("37001", "1", 10), row("51001", "1", 99)],  # VA redrew, NC did not
        f"{OLD} - Lower": [], f"{NEW} - Lower": [],
    }
    choices = {("NC", "sldu"): {"map": OLD}, ("VA", "sldu"): {"map": NEW}}
    rows, accept = maps.state_rows(tiger, choices)
    assert {r["GEOID"]: r["AREALAND"] for r in rows["sldu"]} == {"37001": 10, "51001": 99}
    assert accept[("NC", "sldu")] == [OLD, NEW]
    assert accept[("VA", "sldu")] == [NEW]


def test_lookup_tables_carry_the_maps():
    fed = [{"id": "bioguide-S1", "name": "S1", "ids": {"bioguide": "S1"},
            "role": {"level": "federal", "chamber": "senate", "title": "Senator", "state": "VA", "district": None}}]
    choices = {("VA", "sldu"): {"map": NEW, "geocoder_vintage": "Current_Current", "seats": 40,
                                "cohort_date": "2026-01-14", "cohort_seats": 5, "override": False}}
    lookup, coverage = build.build_lookup(fed, [], {"cd": [], "sldu": [], "sldl": []}, choices,
                                          {("VA", "sldu"): [NEW]})
    assert lookup["VA"]["maps"]["sldu"] == {"accept": [f"{NEW} - Upper"], "geocoder_vintage": "Current_Current"}
    assert coverage["VA"]["state_legislative_maps"]["sldu"]["cohort_date"] == "2026-01-14"


def _client(handler) -> Client:
    c = Client(retries=1, pause=0)
    c._client = httpx.Client(transport=httpx.MockTransport(handler))
    return c


def test_check_geocoder_fails_when_a_vintage_loses_a_layer():
    def handler(req):
        layers = {"119th Congressional Districts": [{}]} if req.url.params["vintage"] == "A" else {}
        return httpx.Response(200, json={"result": {"addressMatches": [{"geographies": layers}]}})

    maps.check_geocoder(_client(handler), {"A": ["119th Congressional Districts"]})
    with pytest.raises(SystemExit, match="no longer has"):
        maps.check_geocoder(_client(handler), {"B": ["119th Congressional Districts"]})
