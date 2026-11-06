"""Bills each current member of Congress sponsored, from the Congress.gov API.

Needs CONGRESS_API_KEY (a free api.data.gov key). Without one this collector is skipped, and the build
publishes no sponsored-bill fields rather than empty ones.

Reading every member's list takes about 1,100 requests (25 minutes), so that happens once a week. Other runs
start from the stored lists and read only Congress.gov's /bill update feed since the last run."""

from __future__ import annotations

import datetime as dt
import gzip
import json
import logging
from pathlib import Path

from ..config import CONGRESS_API, CONGRESS_API_KEY
from ..http import Client

log = logging.getLogger(__name__)

PUBLISHER = "Library of Congress, Congress.gov API"
_PAGE = 250
# The weekly full refresh also picks up what the update feed does not carry, such as a policy area assigned
# to an existing bill.
FULL_REFRESH_EVERY = dt.timedelta(days=7)
# Each feed read starts this long before the previous run's, so a bill updated while that run was reading
# the feed is not missed.
UPDATE_OVERLAP = dt.timedelta(hours=6)
_URL_KINDS = {
    "HR": "house-bill", "S": "senate-bill",
    "HRES": "house-resolution", "SRES": "senate-resolution",
    "HJRES": "house-joint-resolution", "SJRES": "senate-joint-resolution",
    "HCONRES": "house-concurrent-resolution", "SCONRES": "senate-concurrent-resolution",
}  # fmt: skip


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def bill_url(congress: int, bill_type: str, number: str) -> str | None:
    kind = _URL_KINDS.get(bill_type.upper())
    return f"https://www.congress.gov/bill/{_ordinal(congress)}-congress/{kind}/{number}" if kind else None


def _bill(item: dict) -> dict:
    action = item.get("latestAction") or {}
    return {
        "congress": item.get("congress"),
        "type": item.get("type"),
        "number": item.get("number"),
        "title": item.get("title"),
        "url": bill_url(item["congress"], item["type"], item["number"]) if item.get("congress") else None,
        "introduced_date": item.get("introducedDate"),
        "policy_area": (item.get("policyArea") or {}).get("name"),
        "latest_action": {"date": action.get("actionDate"), "text": action.get("text")} if action else None,
    }


def collect_member(client: Client, bioguide: str, api_key: str) -> list[dict]:
    url = f"{CONGRESS_API}/member/{bioguide}/sponsored-legislation"
    bills, offset = [], 0
    while True:
        data = client.get_json(url, params={"api_key": api_key, "format": "json", "limit": _PAGE, "offset": offset})
        items = data.get("sponsoredLegislation") or []
        # Amendments come back in the same list without a bill type; this field is about bills.
        bills += [_bill(i) for i in items if i.get("type")]
        if not (data.get("pagination") or {}).get("next") or not items:
            return bills
        offset += len(items)


def _key(item: dict) -> tuple[int, str, str]:
    return int(item["congress"]), str(item["type"]).upper(), str(item["number"])


def _sort(bills: list[dict]) -> list[dict]:
    """Newest first, as the sponsored-legislation endpoint lists them."""
    return sorted(bills, key=lambda b: (b.get("introduced_date") or "", _key(b)), reverse=True)


def updated_bills(client: Client, api_key: str, since: dt.datetime, until: dt.datetime) -> list[dict]:
    """Every bill Congress.gov changed between `since` and `until`, from its /bill update feed."""
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    params = {"api_key": api_key, "format": "json", "limit": _PAGE, "sort": "updateDate desc",
              "fromDateTime": since.strftime(fmt), "toDateTime": until.strftime(fmt)}
    items, offset = [], 0
    while True:
        data = client.get_json(f"{CONGRESS_API}/bill", params={**params, "offset": offset})
        page = data.get("bills") or []
        items += page
        if not (data.get("pagination") or {}).get("next") or not page:
            return items
        offset += len(page)


def bill_detail(client: Client, api_key: str, key: tuple[int, str, str]) -> dict:
    congress, kind, number = key
    url = f"{CONGRESS_API}/bill/{congress}/{kind.lower()}/{number}"
    return client.get_json(url, params={"api_key": api_key, "format": "json"})["bill"]


def apply_updates(
    client: Client, api_key: str, members: dict[str, list[dict]], updates: list[dict], congress: int
) -> tuple[int, int]:
    """Fold the update feed into the stored lists: refresh title and latest action of bills already held,
    and add new bills of the current Congress sponsored by a current member. Returns (refreshed, added)."""
    held = {_key(b): b for bills in members.values() for b in bills}
    refreshed = added = 0
    for item in updates:
        key = _key(item)
        if key in held:
            fresh = _bill(item)
            held[key].update({k: fresh[k] for k in ("title", "latest_action") if fresh[k] is not None})
            refreshed += 1
            continue
        # A bill not held is new, or its sponsor is not a current member. Only a bill of this Congress can be
        # a current member's new bill; older ones would already be held.
        if key[0] != congress:
            continue
        detail = bill_detail(client, api_key, key)
        sponsor = next((s.get("bioguideId") for s in detail.get("sponsors") or []), None)
        if sponsor in members:
            bill = _bill(detail)
            members[sponsor].append(bill)
            held[key] = bill
            added += 1
    for bioguide in members:
        members[bioguide] = _sort(members[bioguide])
    return refreshed, added


def load_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    with gzip.open(path, "rt") as f:
        return json.load(f)


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as f:
        json.dump(state, f, separators=(",", ":"), sort_keys=True)


def collect(
    client: Client,
    bioguides: list[str],
    *,
    congress: int,
    now: dt.datetime,
    state_path: Path,
    api_key: str = CONGRESS_API_KEY,
    full_every: dt.timedelta = FULL_REFRESH_EVERY,
) -> dict[str, list[dict]] | None:
    """{bioguide: [bill, ...]}, or None when no API key is configured.

    The lists are kept in `state_path` between runs. A run re-reads every member's list when the state is
    missing or older than `full_every`; otherwise it reads only the update feed since the last run (plus an
    overlap), and the full list of members it has not seen before."""
    if not api_key:
        log.info("CONGRESS_API_KEY not set; skipping sponsored bills")
        return None
    state = load_state(state_path)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    full = state is None or now - dt.datetime.fromisoformat(state["full_at"]) >= full_every
    if full:
        members = {b: collect_member(client, b, api_key) for b in bioguides}
        log.info("sponsored bills: full refresh of %d members", len(members))
        state = {"full_at": stamp, "checked_at": stamp}
    else:
        stored = state["members"]
        new = [b for b in bioguides if b not in stored]
        members = {b: stored[b] if b in stored else collect_member(client, b, api_key) for b in bioguides}
        since = dt.datetime.fromisoformat(state["checked_at"]) - UPDATE_OVERLAP
        updates = updated_bills(client, api_key, since, now)
        refreshed, added = apply_updates(client, api_key, members, updates, congress)
        log.info("sponsored bills: %d updated in the feed, %d refreshed, %d new bills, %d new members",
                 len(updates), refreshed, added, len(new))
        state["checked_at"] = stamp
    state["members"] = members
    save_state(state_path, state)
    return members
