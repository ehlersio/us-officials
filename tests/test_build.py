import json

import pytest

from us_officials import build

MAPS = {"geocoder_vintage": "ACS2025_Current", "congressional": "119th Congressional Districts",
        "state_legislative": "2024 State Legislative Districts", "tiger_layers": {"cd": 4, "sldu": 5, "sldl": 6}}


def fed(bioguide, chamber, state, district=None):
    return {"id": f"bioguide-{bioguide}", "name": bioguide, "party": "Independent", "ids": {"bioguide": bioguide},
            "role": {"level": "federal", "chamber": chamber, "title": "Senator" if chamber == "senate" else "Rep",
                     "state": state, "district": district}, "sources": []}


def st(uid, chamber, state, district):
    return {"id": f"openstates-{uid}", "name": uid, "party": "Independent", "ids": {"openstates": uid},
            "role": {"level": "state", "chamber": chamber, "chamber_name": "State House", "state": state,
                     "district": district}, "sources": []}


FEDERAL = [fed("S1", "senate", "NC"), fed("S2", "senate", "NC"), fed("R2", "house", "NC", "2"),
           fed("AL", "house", "AK"), fed("DL", "house", "DC")]
STATE = [st("a", "upper", "NC", "14"), st("b", "lower", "NH", "Carroll 1"), st("c", "lower", "ID", "10A"),
         st("d", "lower", "ID", "10B"), st("e", "legislature", "DC", "At-Large"), st("f", "lower", "NC", "Nowhere")]
TIGER = {
    "cd": [{"GEOID": "3702", "STATE": "37", "BASENAME": "2"}, {"GEOID": "3703", "STATE": "37", "BASENAME": "3"},
           {"GEOID": "0200", "STATE": "02", "BASENAME": "Congressional District (at Large)"},
           {"GEOID": "1198", "STATE": "11", "BASENAME": "Delegate District (at Large)"}],
    "sldu": [{"GEOID": "37014", "STATE": "37", "BASENAME": "14"}],
    "sldl": [{"GEOID": "33301", "STATE": "33", "BASENAME": "Carroll 01"}, {"GEOID": "16010", "STATE": "16",
                                                                           "BASENAME": "10"}],
}


def test_lookup_tables():
    lookup, coverage = build.build_lookup(FEDERAL, STATE, TIGER)
    nc = lookup["NC"]
    assert nc["senate"] == ["bioguide-S1", "bioguide-S2"]
    assert nc["cd"] == {"3702": ["bioguide-R2"], "3703": []}
    assert nc["sldu"] == {"37014": ["openstates-a"]}
    assert lookup["AK"]["cd"] == {"0200": ["bioguide-AL"]}
    assert lookup["DC"]["cd"] == {"1198": ["bioguide-DL"]}
    assert lookup["DC"]["at_large"] == ["openstates-e"]
    assert lookup["NH"]["sldl"] == {"33301": ["openstates-b"]}
    assert lookup["ID"]["sldl"] == {"16010": ["openstates-c", "openstates-d"]}
    assert coverage["NC"]["house_districts_without_member"] == ["3"]
    assert coverage["NC"]["unmatched_districts"] == [{"chamber": "lower", "district": "Nowhere",
                                                      "person": "openstates-f"}]
    assert coverage["NC"]["match_rate"] == 0.5
    assert coverage["DC"]["has_census_districts"] is False


def test_duplicate_census_names_fail_loudly():
    tiger = {**TIGER, "sldu": TIGER["sldu"] + [{"GEOID": "37914", "STATE": "37", "BASENAME": "014"}]}
    with pytest.raises(ValueError, match="normalize"):
        build.build_lookup(FEDERAL, STATE, tiger)


def test_build_writes_static_api(tmp_path):
    vote = {"chamber": "house", "congress": 119, "session": 1, "roll": 7, "date": "2025-01-09", "question": "Q",
            "description": "D", "bill": "H R 1", "result": "Passed", "source": {}, "positions": {"R2": "Yea"}}
    index = build.build(tmp_path, federal=FEDERAL, state_people=STATE, votes=[vote], tiger=TIGER, sponsored=None,
                        congress=119, maps=MAPS, generated_at="2026-11-06T00:00:00Z", sources=[])
    root = tmp_path / "v1"
    assert index["counts"]["house_votes"] == 1 and "sponsored_bills" not in index["counts"]
    person = json.loads((root / "people" / "bioguide-R2.json").read_text())
    assert person["votes_url"] == "/v1/people/bioguide-R2/votes.json" and "bills_url" not in person
    mine = json.loads((root / "people" / "bioguide-R2" / "votes.json").read_text())["votes"]
    assert mine[0]["position"] == "Yea" and mine[0]["url"] == "/v1/votes/house/119-1-7.json"
    assert json.loads((root / "votes" / "house" / "119-1-7.json").read_text())["totals"] == {"Yea": 1}
    assert json.loads((root / "lookup" / "meta.json").read_text())["geocoder_vintage"] == "ACS2025_Current"
    assert json.loads((root / "lookup" / "dc.json").read_text())["has_senators"] is False
    assert "Access-Control-Allow-Origin" in (tmp_path / "_headers").read_text()
