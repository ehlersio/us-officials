import datetime as dt
import json
from pathlib import Path

import httpx

from us_officials import build
from us_officials.collectors import fec_money
from us_officials.http import Client

TODAY = dt.date(2026, 11, 6)
NOW = dt.datetime(2026, 11, 6, 15, tzinfo=dt.UTC)


def member(pid, chamber, state, fec):
    return {"id": pid, "name": pid, "party": "Independent", "ids": {"bioguide": pid, "fec": fec},
            "role": {"level": "federal", "chamber": chamber, "title": "Rep", "state": state, "district": "1"},
            "sources": []}


FEDERAL = [member("rep", "house", "AK", ["H2AK01083", "S0AK00000"]), member("sen", "senate", "NC", ["S6NC00266"])]


def totals(cid, receipts=100.0, end="2026-10-14T00:00:00"):
    return {"candidate_id": cid, "receipts": receipts, "coverage_end_date": end, "coverage_start_date": "2025-01-01",
            "individual_contributions": 60.0, "individual_itemized_contributions": 40.0,
            "individual_unitemized_contributions": 20.0, "other_political_committee_contributions": 30.0,
            "last_cash_on_hand_end_period": 5.0, "last_report_type_full": "PRE-GENERAL", "last_report_year": 2026,
            "transaction_coverage_date": "2026-07-29T00:00:00"}


class FakeFEC:
    """Answers the three FEC endpoints the collector reads and records each request."""

    def __init__(self, summary, fail_after=None):
        self.summary = summary  # {cycle: {candidate id: receipts}}
        self.fail_after = fail_after
        self.seen = []

    def __call__(self, req):
        path = req.url.path
        if self.fail_after is not None and len(self.seen) >= self.fail_after:
            return httpx.Response(429)
        self.seen.append(path)
        cycle = int(req.url.params["cycle"])
        if path.endswith("/candidates/totals/"):
            ids = req.url.params.get_list("candidate_id")
            rows = [totals(c, r) for c, r in self.summary.get(cycle, {}).items() if c in ids]
            return httpx.Response(200, json={"results": rows})
        if "/by_state/" in path:
            rows = [{"state": "AK", "total": 30.0}, {"state": "CA", "total": 10.0}]
            return httpx.Response(200, json={"results": rows})
        cid = path.split("/")[-3]
        return httpx.Response(200, json={"results": [totals(cid, self.summary[cycle][cid])]})


def _client(handler) -> Client:
    c = Client(retries=1, backoff=0, pause=0)
    c._client = httpx.Client(transport=httpx.MockTransport(handler))
    return c


def run(fake, state, **kw):
    return fec_money.collect(_client(fake), FEDERAL, today=TODAY, now=kw.pop("now", NOW), state_path=state,
                             api_key="k", **kw)


def test_cycles_and_ids():
    assert fec_money.current_cycles(dt.date(2026, 11, 6)) == [2026, 2024]
    assert fec_money.current_cycles(dt.date(2027, 1, 4)) == [2028, 2026]
    assert fec_money.candidate_ids(FEDERAL[0]) == ["H2AK01083"]
    assert fec_money.candidate_ids(FEDERAL[1]) == ["S6NC00266"]


def test_no_key_publishes_nothing(tmp_path):
    assert fec_money.collect(Client(), FEDERAL, today=TODAY, now=NOW, state_path=tmp_path / "s", api_key="") is None


def test_first_run_reads_detail_for_every_active_candidate_cycle(tmp_path):
    fake = FakeFEC({2026: {"H2AK01083": 100.0, "S6NC00266": 50.0}, 2024: {"S6NC00266": 9.0}})
    out = run(fake, tmp_path / "s.json.gz")
    assert [r["cycle"] for r in out["rep"]] == [2026]
    assert [r["cycle"] for r in out["sen"]] == [2026, 2024]
    rec = out["rep"][0]
    assert rec["totals"]["individual_unitemized_contributions"] == 20.0
    assert rec["totals"]["pac_and_other_committee_contributions"] == 30.0
    assert rec["totals"]["cash_on_hand"] == 5.0
    assert rec["coverage_end_date"] == "2026-10-14" and rec["last_report"] == "PRE-GENERAL 2026"
    assert rec["itemized_by_state"] == {"processed_through": "2026-07-29", "in_state": 30.0, "all_states": 40.0,
                                        "in_state_share": 0.75, "states": {"AK": 30.0, "CA": 10.0}}
    assert rec["fec_url"] == "https://www.fec.gov/data/candidate/H2AK01083/?cycle=2026"
    assert sum(p.endswith("/candidates/totals/") for p in fake.seen) == 2  # one batch per cycle


def test_unchanged_candidates_are_not_read_again(tmp_path):
    state = tmp_path / "s.json.gz"
    run(FakeFEC({2026: {"H2AK01083": 100.0, "S6NC00266": 50.0}}), state)
    fake = FakeFEC({2026: {"H2AK01083": 100.0, "S6NC00266": 75.0}})  # only the Senator filed again
    out = run(fake, state, now=NOW + dt.timedelta(days=1))
    detail_reads = [p for p in fake.seen if not p.endswith("/candidates/totals/")]
    assert detail_reads == ["/v1/candidate/S6NC00266/totals/", "/v1/schedules/schedule_a/by_state/by_candidate/"]
    assert out["rep"][0]["retrieved_at"] == "2026-11-06T15:00:00Z"


def test_old_detail_is_refreshed_and_budget_caps_a_run(tmp_path):
    state = tmp_path / "s.json.gz"
    fake = FakeFEC({2026: {"H2AK01083": 100.0, "S6NC00266": 50.0}})
    out = run(fake, state, budget=1)
    assert len(out) == 1  # the other candidate waits for the next run, and is not published half-read
    out = run(FakeFEC({2026: {"H2AK01083": 100.0, "S6NC00266": 50.0}}), state)
    assert set(out) == {"rep", "sen"}
    later = FakeFEC({2026: {"H2AK01083": 100.0, "S6NC00266": 50.0}})
    run(later, state, now=NOW + fec_money.REFRESH_AFTER)
    assert sum("/candidate/" in p for p in later.seen) == 2


def test_rate_limit_stops_the_run_but_keeps_what_was_read(tmp_path):
    state = tmp_path / "s.json.gz"
    fake = FakeFEC({2026: {"H2AK01083": 100.0, "S6NC00266": 50.0}}, fail_after=4)  # 2 batches + 1 detail
    out = run(fake, state)
    assert len(out) == 1
    assert len(fec_money.load_state(state)["entries"]) == 1


def test_summaries_down_publishes_stored_data(tmp_path):
    state = tmp_path / "s.json.gz"
    run(FakeFEC({2026: {"H2AK01083": 100.0}}), state)
    out = run(FakeFEC({}, fail_after=0), state)
    assert list(out) == ["rep"]


def test_build_writes_money(tmp_path):
    record = fec_money.cycle_record("H2AK01083", 2026, {"totals": totals("H2AK01083"), "by_state": [],
                                                        "fetched_at": "t"}, "AK")
    assert "itemized_by_state" not in record
    build.build(tmp_path, federal=FEDERAL, state_people=[], votes=[], tiger={"cd": [], "sldu": [], "sldl": []},
                sponsored=None, money={"rep": [record]}, congress=119, generated_at="t", sources=[],
                lookup_meta={"congressional": "119th Congressional Districts", "geocoder_vintage": "x"})
    root = Path(tmp_path) / "v1" / "people"
    assert json.loads((root / "rep.json").read_text())["money_url"] == "/v1/people/rep/money.json"
    assert "money_url" not in json.loads((root / "sen.json").read_text())
    money = json.loads((root / "rep" / "money.json").read_text())
    assert money["cycles"][0]["candidate_id"] == "H2AK01083" and money["notes"] == fec_money.NOTES
    assert json.loads((Path(tmp_path) / "v1" / "index.json").read_text())["counts"]["members_with_campaign_money"] == 1
