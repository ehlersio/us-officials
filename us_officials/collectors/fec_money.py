"""Campaign money for each current member of Congress, from the FEC API (api.open.fec.gov).

Needs an api.data.gov key (FEC_API_KEY, or the CONGRESS_API_KEY already used for bills: both APIs take the
same keys). Without one this collector is skipped and no money fields are published.

Per member and two-year cycle it publishes the totals the campaign reported to the FEC, and FEC's
breakdown of itemized individual contributions by contributor state. The FEC allows 1,000 requests an hour
per key, so a run first reads the summary totals of every member in a few batched requests, then fetches
full detail only for candidates whose numbers changed (or whose detail is older than REFRESH_AFTER), up to
DETAIL_BUDGET of them; the rest wait for the next run. Results are kept in `state_path` between runs.

Only aggregates are published, never individual contributors' names: FEC rules limit how those may be used."""

from __future__ import annotations

import datetime as dt
import gzip
import json
import logging
from pathlib import Path

from ..config import FEC_API, FEC_API_KEY
from ..http import Client

log = logging.getLogger(__name__)

PUBLISHER = "Federal Election Commission, OpenFEC API"
_BATCH = 100
DETAIL_BUDGET = 350  # candidate-cycles per run, two requests each
REFRESH_AFTER = dt.timedelta(days=30)  # re-read unchanged detail this often, for amended reports

# Reported totals published per cycle, renamed where FEC's name says less than it means.
_TOTALS = {
    "receipts": "receipts",
    "disbursements": "disbursements",
    "last_cash_on_hand_end_period": "cash_on_hand",
    "last_debts_owed_by_committee": "debts_owed",
    "individual_contributions": "individual_contributions",
    "individual_itemized_contributions": "individual_itemized_contributions",
    "individual_unitemized_contributions": "individual_unitemized_contributions",
    "other_political_committee_contributions": "pac_and_other_committee_contributions",
    "political_party_committee_contributions": "party_committee_contributions",
    "candidate_contribution": "candidate_contribution",
    "loans_made_by_candidate": "loans_made_by_candidate",
    "transfers_from_other_authorized_committee": "transfers_from_other_authorized_committees",
}

NOTES = [
    "Totals are what the candidate's authorized committees reported to the FEC for the two-year cycle, through "
    "coverage_end_date.",
    "Unitemized individual contributions come from donors who gave $200 or less in the cycle; campaigns do not "
    "report those donors' details.",
    "itemized_by_state comes from a different FEC dataset, its processed itemized transactions through "
    "processed_through. Its dollar figures do not add up to the reported itemized total, so read it for the "
    "split between states, not for amounts.",
]


def current_cycles(today: dt.date, count: int = 2) -> list[int]:
    """The two-year cycle `today` is in (it ends in an even year), and the ones before it."""
    current = today.year + (today.year % 2)
    return [current - 2 * i for i in range(count)]


def candidate_ids(person: dict) -> list[str]:
    """FEC candidate ids for the office the member holds (H for House, delegates included; S for Senate)."""
    letter = "S" if person["role"]["chamber"] == "senate" else "H"
    return [i for i in person["ids"].get("fec", []) if i.startswith(letter)]


def summaries(client: Client, api_key: str, ids: list[str], cycle: int) -> dict[str, dict]:
    """{candidate id: summary totals} for the ids that have totals in `cycle`, in batches of 100."""
    out = {}
    for start in range(0, len(ids), _BATCH):
        batch = ids[start:start + _BATCH]
        data = client.get_json(f"{FEC_API}/candidates/totals/", params={
            "api_key": api_key, "cycle": cycle, "election_full": "false", "per_page": _BATCH, "candidate_id": batch,
        })
        for row in data.get("results") or []:
            out[row["candidate_id"]] = row
    return out


def detail(client: Client, api_key: str, candidate_id: str, cycle: int) -> dict:
    totals = client.get_json(f"{FEC_API}/candidate/{candidate_id}/totals/",
                             params={"api_key": api_key, "cycle": cycle, "election_full": "false"})
    rows = totals.get("results") or []
    by_state = client.get_json(f"{FEC_API}/schedules/schedule_a/by_state/by_candidate/", params={
        "api_key": api_key, "candidate_id": candidate_id, "cycle": cycle, "election_full": "false", "per_page": 100,
    })
    return {"totals": rows[0] if rows else None, "by_state": by_state.get("results") or []}


def _date(value: str | None) -> str | None:
    return value[:10] if value else None


def cycle_record(candidate_id: str, cycle: int, entry: dict, state: str) -> dict | None:
    """The published record for one candidate-cycle, or None when FEC has no totals for it."""
    t = entry.get("totals")
    if not t:
        return None
    states = {r["state"]: r["total"] for r in entry.get("by_state") or [] if r.get("state") and r.get("total")}
    record = {
        "cycle": cycle,
        "candidate_id": candidate_id,
        "fec_url": f"https://www.fec.gov/data/candidate/{candidate_id}/?cycle={cycle}",
        "coverage_start_date": _date(t.get("coverage_start_date")),
        "coverage_end_date": _date(t.get("coverage_end_date")),
        "last_report": " ".join(str(x) for x in (t.get("last_report_type_full"), t.get("last_report_year")) if x)
        or None,
        "totals": {out: t[src] for src, out in _TOTALS.items() if t.get(src) is not None},
        "retrieved_at": entry["fetched_at"],
    }
    if states:
        all_states = round(sum(states.values()), 2)
        in_state = round(states.get(state, 0.0), 2)
        record["itemized_by_state"] = {
            "processed_through": _date(t.get("transaction_coverage_date")),
            "in_state": in_state,
            "all_states": all_states,
            "in_state_share": round(in_state / all_states, 4) if all_states else None,
            "states": dict(sorted(states.items())),
        }
    return record


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"entries": {}}
    with gzip.open(path, "rt") as f:
        return json.load(f)


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as f:
        json.dump(state, f, separators=(",", ":"), sort_keys=True)


def collect(
    client: Client,
    federal: list[dict],
    *,
    today: dt.date,
    now: dt.datetime,
    state_path: Path,
    api_key: str = FEC_API_KEY,
    budget: int = DETAIL_BUDGET,
) -> dict[str, list[dict]] | None:
    """{person id: [cycle record, ...]} for members with any FEC totals, or None without an API key."""
    if not api_key:
        log.info("FEC_API_KEY (or CONGRESS_API_KEY) not set; skipping campaign money")
        return None
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    state = load_state(state_path)
    entries: dict[str, dict] = state["entries"]
    cycles = current_cycles(today)
    ids_of = {p["id"]: candidate_ids(p) for p in federal}
    all_ids = sorted({i for ids in ids_of.values() for i in ids})

    # Which candidate-cycles have totals, and which of those changed since their detail was read.
    active: set[str] = set()
    queue: list[tuple[int, str, str, int, list]] = []
    try:
        for cycle in cycles:
            for cid, row in summaries(client, api_key, all_ids, cycle).items():
                key = f"{cid}:{cycle}"
                active.add(key)
                change = [row.get("coverage_end_date"), row.get("receipts")]
                held = entries.get(key)
                if held is None:
                    queue.append((0, "", cid, cycle, change))
                elif held["change"] != change:
                    queue.append((1, held["fetched_at"], cid, cycle, change))
                elif now - dt.datetime.fromisoformat(held["fetched_at"]) >= REFRESH_AFTER:
                    queue.append((2, held["fetched_at"], cid, cycle, change))
    except RuntimeError as exc:
        log.warning("FEC summaries unavailable (%s); publishing stored money data only", exc)
        active = {k for k in entries if int(k.split(":")[1]) in cycles}
        queue = []

    queue.sort()
    fetched = 0
    for _, _, cid, cycle, change in queue[:budget]:
        try:
            entries[f"{cid}:{cycle}"] = {**detail(client, api_key, cid, cycle), "change": change, "fetched_at": stamp}
        except RuntimeError as exc:
            log.warning("FEC detail stopped after %d candidate-cycles (%s); the rest wait for the next run",
                        fetched, exc)
            break
        fetched += 1
    log.info("campaign money: %d candidate-cycles with totals, %d queued, %d fetched", len(active), len(queue), fetched)
    save_state(state_path, state)

    out: dict[str, list[dict]] = {}
    for p in federal:
        records = []
        for cycle in cycles:
            for cid in ids_of[p["id"]]:
                key = f"{cid}:{cycle}"
                if key in active and key in entries:
                    record = cycle_record(cid, cycle, entries[key], p["role"]["state"])
                    if record:
                        records.append(record)
        if records:
            out[p["id"]] = records
    return out
