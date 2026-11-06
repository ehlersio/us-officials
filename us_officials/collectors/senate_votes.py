"""Senate roll-call votes for one Congress from Voteview (UCLA).

senate.gov blocks automated clients, so the Senate's own pages are linked rather than fetched. Voteview
republishes each vote with the Senate's roll number (clerk_rollnumber) and each member's bioguide id."""

from __future__ import annotations

import csv
import io
import logging

from ..config import SENATE_VOTE_URL, VOTEVIEW_BASE
from ..http import Client

log = logging.getLogger(__name__)

PUBLISHER = "Voteview (UCLA), from U.S. Senate roll-call records"

# Voteview cast codes. 0 means "not a member at the time" and is dropped.
_CAST = {
    "1": "Yea", "2": "Paired Yea", "3": "Announced Yea",
    "4": "Announced Nay", "5": "Paired Nay", "6": "Nay",
    "7": "Present", "8": "Present", "9": "Not Voting",
}  # fmt: skip


def _csv(client: Client, kind: str, congress: int) -> list[dict] | None:
    """One Voteview file, or None when it does not exist yet (a new Congress before its first votes)."""
    resp = client.get(f"{VOTEVIEW_BASE}/{kind}/S{congress}_{kind}.csv")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return list(csv.DictReader(io.StringIO(resp.content.decode("utf-8"))))


def build(rollcalls: list[dict], votes: list[dict], members: list[dict], congress: int) -> list[dict]:
    bioguide = {m["icpsr"]: m["bioguide_id"] for m in members if m.get("chamber") == "Senate" and m.get("bioguide_id")}
    positions: dict[str, dict[str, str]] = {}
    for v in votes:
        cast = _CAST.get(v["cast_code"])
        member = bioguide.get(v["icpsr"])
        if cast and member:
            positions.setdefault(v["rollnumber"], {})[member] = cast
    out = []
    for rc in rollcalls:
        if rc.get("chamber") != "Senate":
            continue
        session, roll = int(rc["session"]), int(rc["clerk_rollnumber"])
        out.append(
            {
                "chamber": "senate",
                "congress": congress,
                "session": session,
                "roll": roll,
                "date": rc["date"],
                "question": rc.get("vote_question") or None,
                "description": rc.get("vote_desc") or rc.get("dtl_desc") or None,
                "bill": rc.get("bill_number") or None,
                "result": rc.get("vote_result") or None,
                "source": {
                    "publisher": PUBLISHER,
                    "url": f"{VOTEVIEW_BASE}/rollcalls/S{congress}_rollcalls.csv",
                    "official_url": SENATE_VOTE_URL.format(congress=congress, session=session, roll=roll),
                },
                "positions": positions.get(rc["rollnumber"], {}),
            }
        )
    return out


def collect(client: Client, congress: int) -> list[dict]:
    files = [_csv(client, kind, congress) for kind in ("rollcalls", "votes", "members")]
    if any(f is None for f in files):
        log.warning("Voteview has no Senate files for the %dth Congress yet; publishing no Senate votes", congress)
        return []
    return build(*files, congress)
