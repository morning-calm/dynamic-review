"""
Best-effort Trello workflow sync — move/annotate a trip's review card when the trip
leaves the review queue.

Semantics (dave, 2026-07-19):
  approved        → move the card to lane 9 (Finalise/…/Upload to AWS) + comment
  manual complete → comment only (nothing was written; a silent lane move could mislead)
  un-complete     → comment only (never auto-move back — a human may have acted on it)

Card identity comes from the manifest's `card_url` (the Trello shortlink), creds from
the Scripts `.env` (`TRELLO_API_KEY` / `TRELLO_TOKEN` — same as Trello/trello_common.py
in dynamic-content). Everything is fire-and-forget on a daemon thread and swallows all
errors: Trello being down/unconfigured must never fail an approve. Missing creds log a
warning once per call so a silently-no-op laptop is visible in the uvicorn log.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import urllib.parse
import urllib.request

from .config import MANIFEST_PATH

log = logging.getLogger("uvicorn.error")

LANE9_LIST_ID = "6a1b5715a002ceb1a7bc3c74"   # "9 · Finalise, subtitles, … Upload to AWS"
_API = "https://api.trello.com/1"
_SHORTLINK_RE = re.compile(r"/c/([A-Za-z0-9]+)")


def _creds() -> dict | None:
    key, token = os.environ.get("TRELLO_API_KEY"), os.environ.get("TRELLO_TOKEN")
    return {"key": key, "token": token} if key and token else None


def _card_shortlink(trip_id: str) -> str | None:
    """trip_id → Trello card shortlink, via the manifest's card_url."""
    try:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        for t in data.get("trips") or []:
            if t.get("trip_id") == trip_id and t.get("card_url"):
                m = _SHORTLINK_RE.search(t["card_url"])
                if m:
                    return m.group(1)
    except Exception:  # noqa: BLE001
        pass
    return None


def _req(method: str, path: str, params: dict) -> None:
    url = f"{_API}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def _run(trip_id: str, move: bool, comment: str) -> None:
    creds = _creds()
    if creds is None:
        log.warning("trello: sync skipped for %s — TRELLO_API_KEY/TRELLO_TOKEN not in "
                    "the Scripts .env", trip_id)
        return
    card = _card_shortlink(trip_id)
    if card is None:
        log.warning("trello: sync skipped for %s — no card_url in the manifest", trip_id)
        return
    try:
        if move:
            _req("PUT", f"/cards/{card}", {**creds, "idList": LANE9_LIST_ID})
        _req("POST", f"/cards/{card}/actions/comments", {**creds, "text": comment})
        log.info("trello: %s — %s", trip_id,
                 "moved to lane 9 + comment" if move else "comment posted")
    except Exception as exc:  # noqa: BLE001
        log.warning("trello: sync failed for %s: %s", trip_id, exc)


def notify(trip_id: str, *, move: bool, comment: str) -> None:
    """Fire-and-forget; never raises, never blocks the caller."""
    try:
        threading.Thread(target=_run, args=(trip_id, move, comment),
                         daemon=True, name=f"trello-{trip_id}").start()
    except Exception:  # noqa: BLE001
        pass
