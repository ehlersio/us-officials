"""Bills each current member of Congress sponsored, from the Congress.gov API.

Needs CONGRESS_API_KEY (a free api.data.gov key). Without one this collector is skipped, and the build
publishes no sponsored-bill fields rather than empty ones."""

from __future__ import annotations

import logging

from ..config import CONGRESS_API, CONGRESS_API_KEY
from ..http import Client

log = logging.getLogger(__name__)

PUBLISHER = "Library of Congress, Congress.gov API"
_PAGE = 250
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


def collect(client: Client, bioguides: list[str], api_key: str = CONGRESS_API_KEY) -> dict[str, list[dict]] | None:
    """{bioguide: [bill, ...]}, or None when no API key is configured."""
    if not api_key:
        log.info("CONGRESS_API_KEY not set; skipping sponsored bills")
        return None
    return {b: collect_member(client, b, api_key) for b in bioguides}
