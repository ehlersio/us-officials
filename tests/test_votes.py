import datetime as dt
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
    now = dt.datetime(2026, 11, 6, tzinfo=dt.UTC)
    assert sponsored_bills.collect(Client(), ["A000370"], congress=119, now=now, state_path=Path("unused"),
                                   api_key="") is None


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

    bills = sponsored_bills.collect_member(_client(handler), "A000370", "k")
    assert [b["number"] for b in bills] == ["1", "9"]
    assert bills[0]["url"] == "https://www.congress.gov/bill/119th-congress/house-bill/1"
    assert bills[1]["url"] == "https://www.congress.gov/bill/118th-congress/senate-resolution/9"


NOW = dt.datetime(2026, 11, 6, 15, tzinfo=dt.UTC)


def bill(congress, kind, number, title, introduced="2026-01-01", action="Introduced"):
    return {"congress": congress, "type": kind, "number": number, "title": title, "introducedDate": introduced,
            "latestAction": {"actionDate": introduced, "text": action}}


def fake_api(lists, feed, details):
    """A Congress.gov stand-in that records which endpoints were read."""
    seen = []

    def handler(req):
        path = req.url.path
        seen.append(path)
        if path.endswith("/sponsored-legislation"):
            return httpx.Response(200, json={"sponsoredLegislation": lists[path.split("/")[-2]], "pagination": {}})
        if path.endswith("/v3/bill"):
            return httpx.Response(200, json={"bills": feed, "pagination": {}})
        return httpx.Response(200, json={"bill": details[path]})

    return handler, seen


def test_first_run_reads_every_member_and_stores_state(tmp_path):
    state = tmp_path / "s.json.gz"
    handler, seen = fake_api({"A1": [bill(119, "HR", "1", "One")], "B2": []}, [], {})
    out = sponsored_bills.collect(_client(handler), ["A1", "B2"], congress=119, now=NOW, state_path=state, api_key="k")
    assert [b["title"] for b in out["A1"]] == ["One"] and out["B2"] == []
    assert sorted(seen) == ["/v3/member/A1/sponsored-legislation", "/v3/member/B2/sponsored-legislation"]
    assert sponsored_bills.load_state(state)["full_at"] == "2026-11-06T15:00:00Z"


def test_later_run_reads_only_the_update_feed(tmp_path):
    state = tmp_path / "s.json.gz"
    sponsored_bills.save_state(state, {
        "full_at": "2026-11-05T15:00:00Z", "checked_at": "2026-11-05T15:00:00Z",
        "members": {"A1": [sponsored_bills._bill(bill(119, "HR", "1", "One"))], "Gone": []},
    })
    feed = [
        bill(119, "HR", "1", "One, renamed", action="Passed House"),  # held: refreshed in place
        bill(119, "S", "9", "New", introduced="2026-11-05"),  # new: sponsor read from the bill itself
        bill(119, "HR", "7", "Someone else's"),  # new, but its sponsor is not a current member
        bill(117, "HR", "3", "Old"),  # not held and not this Congress: skipped without a request
    ]
    details = {
        "/v3/bill/119/s/9": {**bill(119, "S", "9", "New", introduced="2026-11-05"), "sponsors": [{"bioguideId": "A1"}],
                             "policyArea": {"name": "Health"}},
        "/v3/bill/119/hr/7": {**bill(119, "HR", "7", "Someone else's"), "sponsors": [{"bioguideId": "Z9"}]},
    }
    handler, seen = fake_api({"C3": [bill(119, "HR", "5", "Five")]}, feed, details)
    out = sponsored_bills.collect(_client(handler), ["A1", "C3"], congress=119, now=NOW, state_path=state,
                                  api_key="k")
    assert [(b["number"], b["title"]) for b in out["A1"]] == [("9", "New"), ("1", "One, renamed")]
    assert out["A1"][1]["latest_action"]["text"] == "Passed House"
    assert out["A1"][0]["policy_area"] == "Health"
    assert [b["number"] for b in out["C3"]] == ["5"]  # a member not seen before gets a full read
    assert "Gone" not in out
    assert sorted(seen) == ["/v3/bill", "/v3/bill/119/hr/7", "/v3/bill/119/s/9", "/v3/member/C3/sponsored-legislation"]
    stored = sponsored_bills.load_state(state)
    assert stored["full_at"] == "2026-11-05T15:00:00Z" and stored["checked_at"] == "2026-11-06T15:00:00Z"


def test_feed_window_overlaps_the_last_run(tmp_path):
    state = tmp_path / "s.json.gz"
    sponsored_bills.save_state(state, {"full_at": "2026-11-05T15:00:00Z", "checked_at": "2026-11-06T09:00:00Z",
                                       "members": {"A1": []}})
    params = []

    def handler(req):
        params.append(dict(req.url.params))
        return httpx.Response(200, json={"bills": [], "pagination": {}})

    sponsored_bills.collect(_client(handler), ["A1"], congress=119, now=NOW, state_path=state, api_key="k")
    assert params[0]["fromDateTime"] == "2026-11-06T03:00:00Z" and params[0]["toDateTime"] == "2026-11-06T15:00:00Z"


def test_full_refresh_after_a_week(tmp_path):
    state = tmp_path / "s.json.gz"
    sponsored_bills.save_state(state, {"full_at": "2026-10-30T15:00:00Z", "checked_at": "2026-11-06T09:00:00Z",
                                       "members": {"A1": []}})
    handler, seen = fake_api({"A1": [bill(119, "HR", "1", "One")]}, [], {})
    out = sponsored_bills.collect(_client(handler), ["A1"], congress=119, now=NOW, state_path=state, api_key="k")
    assert seen == ["/v3/member/A1/sponsored-legislation"] and len(out["A1"]) == 1
