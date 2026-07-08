"""Admin-only staging-wide endpoints (WS4 phase 1 — the Firefoo replacement's entry
point): search/open ANY staging trip, bypassing the Trello manifest and the
completed-trips exclusion that gate the normal reviewer flow.

Read/open only — every write still goes through the session editor + approve
(targeted single-`.update()` discipline in sessions.commit). STAGING ONLY."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

from fastapi import APIRouter, Depends, HTTPException

from . import audio_core, auth, db, review_bus, sessions, structure
from .config import SCRIPTS_ROOT
from .models import (CreateSession, QueueJob, RunJob, StructureAdd,
                     StructureCategories, StructureRemove, StructureReorder,
                     StructureStaticImages, StructureSwapVideo)

router = APIRouter(prefix="/api/admin")

# Publisher mode: the WORKSTATION instance of the app (the one machine holding the
# production key). The laptop deploy must never set this — it only queues/views jobs.
PUBLISHER_MODE = os.environ.get("REVIEW_APP_PUBLISHER") == "1"

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


# --------------------------------------------------------------------------- #
# Scene-structure editor (WS4 phases 2–3): direct STAGING writes, admin-only,
# refused (409 active_session) while any active session exists on the trip.
# --------------------------------------------------------------------------- #
@router.get("/structure/{trip_id}")
def get_structure(trip_id: str, admin=Depends(auth.require_admin)):
    return structure.get_structure(trip_id)


@router.post("/structure/{trip_id}/reorder")
def post_structure_reorder(trip_id: str, body: StructureReorder,
                           admin=Depends(auth.require_admin)):
    return structure.reorder(trip_id, body.order, body.base, admin)


@router.post("/structure/{trip_id}/remove")
def post_structure_remove(trip_id: str, body: StructureRemove,
                          admin=Depends(auth.require_admin)):
    return structure.remove(trip_id, body.index, body.base, admin)


@router.post("/structure/{trip_id}/add")
def post_structure_add(trip_id: str, body: StructureAdd,
                       admin=Depends(auth.require_admin)):
    return structure.add(trip_id, body.position, body.base, admin,
                         video_url=body.video_url, is_static=body.is_static,
                         scene_id=body.scene_id)


@router.post("/structure/{trip_id}/swap-video")
def post_structure_swap_video(trip_id: str, body: StructureSwapVideo,
                              admin=Depends(auth.require_admin)):
    return structure.swap_video(trip_id, body.index, body.video_url, body.rekey,
                                body.base, admin, scene_id=body.scene_id)


@router.post("/structure/{trip_id}/static-images")
def post_structure_static_images(trip_id: str, body: StructureStaticImages,
                                 admin=Depends(auth.require_admin)):
    return structure.set_static_images(trip_id, body.index, body.filenames,
                                       body.base, admin)


@router.post("/structure/{trip_id}/categories")
def post_structure_categories(trip_id: str, body: StructureCategories,
                              admin=Depends(auth.require_admin)):
    return structure.set_categories(trip_id, body.categories, admin)


# --------------------------------------------------------------------------- #
# Pipeline: R2 review-bus publish handshake (WS4 phase 4)
# --------------------------------------------------------------------------- #
@router.post("/pipeline/queue")
def queue_pipeline_job(body: QueueJob, admin=Depends(auth.require_admin)):
    """Queue a staging→production TEXT publish request for a trip. Writes a job object
    to the R2 bus — nothing executes until a human runs it on the workstation
    (publisher mode below, or `publish_inbox.py` in the Scripts repo)."""
    return review_bus.queue_job(body.kind, body.trip_id, admin, body.note)


@router.get("/pipeline/jobs")
def pipeline_jobs(trip_id: str = "", admin=Depends(auth.require_admin)):
    return {"publisher_mode": PUBLISHER_MODE,
            "jobs": review_bus.list_jobs(trip_id or None)}


@router.get("/drift/{trip_id}")
def drift(trip_id: str, admin=Depends(auth.require_admin)):
    """Staging vs production DISPLAY-TEXT drift for one trip, diffed against the
    workstation-exported prod snapshot on the bus (`publish_inbox.py snapshot`).
    The laptop never reads production directly — no prod credential of any kind."""
    from .staging import get_trip
    snap = review_bus.prod_snapshot(trip_id)
    if snap is None:
        return {"trip_id": trip_id, "snapshot_at": None, "fields_differ": None}
    try:
        stg = get_trip(trip_id)
    except SystemExit as e:
        raise HTTPException(404, detail={"error": "no_staging_trip", "detail": str(e)})
    prod = snap.get("trip") or {}
    differ: list[str] = []
    for f in ("contentTitleKey", "descriptionTarget", "descriptionHome"):
        if (stg.get(f) or "") != (prod.get(f) or ""):
            differ.append(f)
    s_scenes = stg.get("quickTrips") or []
    p_scenes = prod.get("quickTrips") or []
    if len(s_scenes) != len(p_scenes):
        differ.append(f"scene_count ({len(p_scenes)} live vs {len(s_scenes)} staging)")
    else:
        for i, (ss, ps) in enumerate(zip(s_scenes, p_scenes)):
            for f in ("titleKey", "titleKeyEn", "SceneDesc", "SceneDescEn",
                      "questionKey", "questionKeyEn", "questionOptionKeys",
                      "questionOptionKeysEn"):
                if (ss or {}).get(f) != (ps or {}).get(f):
                    differ.append(f"quickTrips[{i}].{f}")
    return {"trip_id": trip_id, "snapshot_at": snap.get("fetched_at"),
            "fields_differ": differ}


@router.post("/pipeline/run")
def run_pipeline_job(body: RunJob, admin=Depends(auth.require_admin)):
    """PUBLISHER MODE ONLY (403 otherwise — the laptop can never execute): run a queued
    publish job via the Scripts repo's `publish_trip_text.py`. Default is a DRY RUN
    (field-level diff, no write); a real write requires apply+i_am_sure here AND rides
    that script's own `--apply --i-am-sure` gates with the workstation-local prod key."""
    if not PUBLISHER_MODE:
        raise HTTPException(403, detail={
            "error": "not_publisher",
            "detail": "this instance is not running in publisher mode "
                      "(REVIEW_APP_PUBLISHER=1 on the workstation only)"})
    job = review_bus.get_job(body.job_id)
    if job.get("kind") != "publish":
        raise HTTPException(422, detail={"error": "bad_kind", "detail": job.get("kind")})
    trip_id = str(job.get("trip_id") or "")
    if not trip_id or trip_id.startswith("-"):
        # Defence in depth (queue_job validates too): never pass an argv that argparse
        # could read as a flag on the production-writing script.
        raise HTTPException(422, detail={"error": "bad_trip_id", "detail": trip_id})
    apply_write = bool(body.apply and body.i_am_sure)
    cmd = [sys.executable, str(SCRIPTS_ROOT / "publish_trip_text.py"), trip_id]
    if apply_write:
        cmd += ["--apply", "--i-am-sure"]
    try:
        # utf-8 explicitly: text=True alone decodes with the Windows locale codepage,
        # which garbles/raises on the script's CJK diff output.
        proc = subprocess.run(cmd, cwd=str(SCRIPTS_ROOT), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=300)
        log = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        status = ("failed" if proc.returncode != 0
                  else ("done" if apply_write else "dry_run"))
    except subprocess.TimeoutExpired as e:
        # Don't leave the job looking 'queued' after a hung run — surface the failure
        # on the job object itself (the bus is deliberately not silent-best-effort).
        log = ((e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes)
               else (e.stdout or ""))
        log += f"\n!! publish_trip_text.py timed out after {e.timeout:.0f}s"
        status = "failed"
    except OSError as e:
        log = f"!! could not launch publish_trip_text.py: {e}"
        status = "failed"
    return review_bus.update_job(
        body.job_id, status=status, log=log[-8000:],
        resolved_by=getattr(admin, "username", None) or "",
        resolved_at=time.time())
