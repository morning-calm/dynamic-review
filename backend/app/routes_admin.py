"""Admin-only staging-wide endpoints (WS4 phase 1 — the Firefoo replacement's entry
point): search/open ANY staging trip, bypassing the Trello manifest and the
completed-trips exclusion that gate the normal reviewer flow.

Read/open only — every write still goes through the session editor + approve
(targeted single-`.update()` discipline in sessions.commit). STAGING ONLY."""

from __future__ import annotations

import threading
import time

from fastapi import APIRouter, Depends

from . import audio_core, auth, db, sessions
from .models import CreateSession

router = APIRouter(prefix="/api/admin")

# One light Firestore sweep of Trips ids + display fields, cached: search-as-you-type
# must not re-stream ~900 docs per keystroke. Refresh after TTL or on ?refresh=1.
_INDEX_TTL_SECONDS = 600
_index_lock = threading.Lock()
_index: dict = {"at": 0.0, "rows": []}


def _staging_index(force: bool = False) -> list[dict]:
    with _index_lock:
        if not force and _index["rows"] and time.time() - _index["at"] < _INDEX_TTL_SECONDS:
            return _index["rows"]
        from .staging import db as fb_db   # lazy: Firestore init on first use
        rows: list[dict] = []
        for snap in fb_db().collection("Trips").select(
                ["contentTitleKey", "folderName"]).stream():
            d = snap.to_dict() or {}
            rows.append({
                "trip_id": snap.id,
                "title": d.get("contentTitleKey") or snap.id,
                "folder_name": d.get("folderName") or "",
            })
        rows.sort(key=lambda r: r["trip_id"].lower())
        _index["rows"] = rows
        _index["at"] = time.time()
        return rows


@router.get("/staging-trips")
def staging_trips(q: str = "", refresh: int = 0,
                  admin=Depends(auth.require_admin)):
    """Search the WHOLE staging Trips collection by id/title substring (admin only).
    Every row also says where the trip sits in the review workflow, so the admin can
    see at a glance whether opening it resumes a session or seeds a fresh one."""
    rows = _staging_index(force=bool(refresh))
    needle = (q or "").strip().lower()
    if needle:
        rows = [r for r in rows
                if needle in r["trip_id"].lower() or needle in r["title"].lower()]
    out: list[dict] = []
    for r in rows[:200]:   # hard cap — the FE asks for narrower queries, not paging
        tid = r["trip_id"]
        has_session, status, edit_required = sessions._session_meta(tid)
        crow = db.query_one(
            "SELECT method, completed_by FROM completed_trips WHERE trip_id=?", (tid,))
        out.append({
            **r,
            "language": audio_core.language_of(tid),
            "has_session": has_session,
            "status": status,
            "edit_required": edit_required,
            "completed_method": crow["method"] if crow else None,
            "completed_by": crow["completed_by"] if crow else None,
        })
    return {"total": len(rows), "shown": len(out), "trips": out}


@router.post("/open")
def open_staging_trip(body: CreateSession, admin=Depends(auth.require_admin)):
    """Open (create or resume) a session for ANY staging trip — including a COMPLETED
    one (the reviewer flow 409s there; the admin editor is exactly for post-completion
    fixes). Seeding still requires resolvable MP3 masters (422 bad_folder otherwise)."""
    return sessions.create_or_resume(body.trip_id, admin, allow_completed=True)
