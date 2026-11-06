from pathlib import Path

import httpx

from us_officials.collectors import house_votes, senate_votes, sponsored_bills
from us_officials.http import Client

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_house_roll():
    v = house_votes.parse_roll((FIXTURES / "house-roll-2025-240.xml").read_bytes(), "u")
    assert (v["congress"], v["session"], v["roll"], v["date"]) == (119, 1, 240, "2025-09-08")
    assert v["bill"] == "H R 3424"
    assert v["result"] == "Passed"
    assert v["positions"]["A000370"] in {"Yea", "Nay", "Present", "Not Voting"}
    assert len(v["positions"]) == 430


def test_missing_house_roll_is_none():
    assert house_votes.parse_roll((FIXTURES / "house-roll-missing.xml").read_bytes(), "u") is None


def _client(handler) -> Client:
    c = Client(retries=1, pause=0)
    c._client = httpx.Client(transport=httpx.MockTransport(handler))
    return c


def test_collect_year_is_incremental(tmp_path):
    roll = (FIXTURES / "house-roll-2025-240.xml").read_bytes()
    missing = (FIXTURES / "house-roll-missing.xml").read_bytes()
    stored = house_votes.parse_roll(roll, "u")
    house_votes.dump(tmp_path, 2025, [{**stored, "roll": 239}])
    seen = []

    def handler(req):
        seen.append(req.url.path)
        return httpx.Response(200, content=roll if req.url.path.endswith("roll240.xml") else missing)

    votes = house_votes.collect_year(_client(handler), tmp_path, 2025)
    assert seen == ["/evs/2025/roll240.xml", "/evs/2025/roll241.xml"]
    assert [v["roll"] for v in votes] == [239, 240]
    assert [v["roll"] for v in house_votes.load(tmp_path, 2025)] == [239, 240]


def test_senate_votes_from_voteview():
    rollcalls = [{"chamber": "Senate", "rollnumber": "900", "session": "2", "clerk_rollnumber": "255",
                  "date": "2026-09-30", "vote_question": "On the Cloture Motion", "vote_desc": "A nomination",
                  "bill_number": "PN1129", "vote_result": "Cloture Motion Agreed to"}]
    votes = [{"rollnumber": "900", "icpsr": "1", "cast_code": "1"},
             {"rollnumber": "900", "icpsr": "2", "cast_code": "6"},
             {"rollnumber": "900", "icpsr": "3", "cast_code": "9"},
             {"rollnumber": "900", "icpsr": "4", "cast_code": "0"}]
    members = [{"chamber": "Senate", "icpsr": str(i), "bioguide_id": f"S00000{i}"} for i in range(1, 5)]
    [v] = senate_votes.build(rollcalls, votes, members, 119)
    assert (v["session"], v["roll"]) == (2, 255)
    assert v["positions"] == {"S000001": "Yea", "S000002": "Nay", "S000003": "Not Voting"}
    assert v["source"]["official_url"].endswith("/vote1192/vote_119_2_00255.htm")


def test_sponsored_bills_need_a_key():
    assert sponsored_bills.collect(Client(), ["A000370"], api_key="") is None


def test_sponsored_bills_pages_and_drops_amendments():
    pages = {
        0: {"sponsoredLegislation": [{"congress": 119, "type": "HR", "number": "1", "title": "A"},
                                     {"congress": 119, "type": None, "number": "5", "title": "amendment"}],
            "pagination": {"next": "more"}},
        2: {"sponsoredLegislation": [{"congress": 118, "type": "SRES", "number": "9", "title": "B"}],
            "pagination": {}},
    }

    def handler(req):
        return httpx.Response(200, json=pages[int(req.url.params["offset"])])

    bills = sponsored_bills.collect(_client(handler), ["A000370"], api_key="k")["A000370"]
    assert [b["number"] for b in bills] == ["1", "9"]
    assert bills[0]["url"] == "https://www.congress.gov/bill/119th-congress/house-bill/1"
    assert bills[1]["url"] == "https://www.congress.gov/bill/118th-congress/senate-resolution/9"
