"""House roll-call votes from the Clerk's XML files, collected incrementally.

Parsed roll calls are kept in data/raw/house_votes_<year>.jsonl (committed), so a run only fetches the
roll numbers after the last one it has."""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from pathlib import Path

from defusedxml import ElementTree

from ..config import HOUSE_CLERK_ROLL_URL
from ..http import Client

log = logging.getLogger(__name__)

PUBLISHER = "Office of the Clerk, U.S. House of Representatives"

# Recorded votes say Aye/No, yea-and-nay votes say Yea/Nay; both are published as Yea/Nay. Anything else
# (the Speaker election records candidate names) is kept as cast.
_POSITIONS = {"yea": "Yea", "aye": "Yea", "nay": "Nay", "no": "Nay", "present": "Present", "not voting": "Not Voting"}
_SESSION = re.compile(r"(\d+)")


def raw_path(raw_dir: Path, year: int) -> Path:
    return raw_dir / f"house_votes_{year}.jsonl"


def load(raw_dir: Path, year: int) -> list[dict]:
    path = raw_path(raw_dir, year)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def dump(raw_dir: Path, year: int, votes: list[dict]) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    votes = sorted(votes, key=lambda v: v["roll"])
    raw_path(raw_dir, year).write_text("".join(json.dumps(v, sort_keys=True) + "\n" for v in votes))


def _text(root, path: str) -> str | None:
    el = root.find(path)
    return el.text.strip() if el is not None and el.text else None


def parse_roll(xml: bytes, url: str) -> dict | None:
    """One roll call, or None when the Clerk has no file for that number yet."""
    root = ElementTree.fromstring(xml)
    if root.tag != "rollcall-vote":
        return None
    meta = root.find("vote-metadata")
    positions = {}
    for rv in root.iter("recorded-vote"):
        leg = rv.find("legislator")
        cast = (rv.findtext("vote") or "").strip()
        if leg is None or not leg.get("name-id"):
            continue
        positions[leg.get("name-id")] = _POSITIONS.get(cast.lower(), cast)
    session = _SESSION.match(_text(meta, "session") or "")
    return {
        "chamber": "house",
        "congress": int(_text(meta, "congress")),
        "session": int(session.group(1)) if session else None,
        "roll": int(_text(meta, "rollcall-num")),
        "date": dt.datetime.strptime(_text(meta, "action-date"), "%d-%b-%Y").date().isoformat(),
        "question": _text(meta, "vote-question"),
        "description": _text(meta, "vote-desc"),
        "bill": _text(meta, "legis-num"),
        "vote_type": _text(meta, "vote-type"),
        "result": _text(meta, "vote-result"),
        "source": {"publisher": PUBLISHER, "url": url},
        "positions": positions,
    }


def collect_year(client: Client, raw_dir: Path, year: int, *, max_new: int | None = None) -> list[dict]:
    """All roll calls for `year`: the stored ones plus any newer the Clerk has published."""
    votes = load(raw_dir, year)
    roll = max((v["roll"] for v in votes), default=0) + 1
    new = 0
    while max_new is None or new < max_new:
        url = HOUSE_CLERK_ROLL_URL.format(year=year, roll=roll)
        resp = client.get(url)
        if resp.status_code == 404:
            break
        resp.raise_for_status()
        vote = parse_roll(resp.content, url)
        if vote is None:
            break
        votes.append(vote)
        new += 1
        roll += 1
    if new:
        log.info("house %d: %d new roll calls (through %d)", year, new, roll - 1)
        dump(raw_dir, year, votes)
    return votes
