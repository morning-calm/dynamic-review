"""Stage-4b external bug reports: mirror the staging Firebase ``UserReports`` docs that
carry structured scene context (submitted from the customer web/VR apps) into the local
``external_reports`` table, and surface them next to the SceneDesc in the review UI.

Design (docs/workflow-features-proposal.md, feature 6):
  * Sync is PER TRIP and ON DEMAND (session page load / explicit refresh) — an equality
    query on ``context.contentId`` (auto-indexed map subfield), never a full-collection
    stream. Best-effort: a staging hiccup must never break a review page.
  * ``status`` (open/acknowledged/resolved) is OUR triage state: set locally, mirrored
    onto the Firestore doc (``reviewStatus``/``reviewedBy``) best-effort, and never
    clobbered by a re-sync.
  * ``scene_id`` is resolved at ingest from the trip's ``quickTrips[i].sceneId`` (the
    WS1 backfill) so a report survives future scene reorders; display still keys off
    ``scene_index`` today.
  * Old blob-only reports (no ``context``) are invisible here by design — only the
    structured payloads (backend `feature/scene-scoped-bug-reports`) are queryable.
"""

from __future__ import annotations

import json
import time

from fastapi import HTTPException

from . import db

_STATUSES = ("open", "acknowledged", "resolved")


def _row_dict(r) -> dict:
    return {
        "id": r["id"],
        "trip_id": r["trip_id"],
        "scene_index": r["scene_index"],
        "scene_id": r["scene_id"],
        "source": r["source"],
        "report_type": r["report_type"],
        "categories": json.loads(r["categories_json"] or "[]"),
        "body": r["body"],
        "reporter": r["reporter"],
        "created_at": r["created_at"],
        "status": r["status"],
        "resolved_by": r["resolved_by"],
        "resolved_at": r["resolved_at"],
    }


def _scene_ids_for(trip: dict | None) -> dict[int, str]:
    """scene_index -> sceneId from a loaded trip doc (WS1 backfill); {} when absent."""
    out: dict[int, str] = {}
    for i, sc in enumerate((trip or {}).get("quickTrips") or []):
        sid = (sc or {}).get("sceneId")
        if sid:
            out[i] = str(sid)
    return out


def sync_trip(trip_id: str, trip: dict | None = None) -> int:
    """Pull staging UserReports rows for one trip (equality on context.contentId) into
    external_reports. INSERTs new docs only — local triage state is never overwritten.
    Returns how many NEW rows landed. Raises on staging failure (callers decide whether
    that is fatal; the GET route treats it as best-effort)."""
    from .staging import db as fb_db   # lazy: Firestore init on first use
    snaps = (fb_db().collection("UserReports")
             .where("context.contentId", "==", trip_id).stream())
    scene_ids = _scene_ids_for(trip)
    now = time.time()
    new = 0
    for snap in snaps:
        d = snap.to_dict() or {}
        ctx = d.get("context") or {}
        scene_index = ctx.get("sceneIndex")
        try:
            scene_index = int(scene_index) if scene_index is not None else None
        except (TypeError, ValueError):
            scene_index = None
        created = d.get("createdOn")
        created_ts = getattr(created, "timestamp", lambda: None)()
        cats = d.get("categories") or []
        if not isinstance(cats, list):
            cats = []
        inserted = db.execute_rowcount(
            "INSERT OR IGNORE INTO external_reports"
            "(id,trip_id,scene_index,scene_id,source,report_type,categories_json,"
            "body,reporter,created_at,status,synced_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,'open',?)",
            (snap.id, trip_id, scene_index,
             scene_ids.get(scene_index) if scene_index is not None else None,
             str(ctx.get("source") or ""), str(d.get("reportType") or ""),
             json.dumps([str(c) for c in cats], ensure_ascii=False),
             str(d.get("report") or ""), str(d.get("reporter") or ""),
             created_ts, now))
        new += inserted
    return new


def list_for_trip(trip_id: str) -> list[dict]:
    rows = db.query(
        "SELECT * FROM external_reports WHERE trip_id=? "
        "ORDER BY scene_index IS NULL, scene_index, created_at", (trip_id,))
    return [_row_dict(r) for r in rows]


def for_session(sid: str, refresh: bool = False) -> dict:
    """The session page's fetch: (optionally best-effort re-sync, then) this trip's
    reports + a per-scene open count for the chips."""
    from . import sessions
    srow = sessions._session_row(sid)
    trip_id = srow["trip_id"]
    sync_error = None
    if refresh:
        try:
            trip = json.loads(srow["loaded_trip_json"] or "{}")
            sync_trip(trip_id, trip)
        except Exception as e:  # noqa: BLE001 — staging down must not break the page
            sync_error = str(e)[:200]
    reports = list_for_trip(trip_id)
    return {"trip_id": trip_id, "reports": reports, "sync_error": sync_error}


def set_status(report_id: str, user, status: str) -> dict:
    """ADMIN triage. Mirrors the state onto the staging doc best-effort (the review app
    is the mediating staging writer) — a mirror failure never fails the local update."""
    if status not in _STATUSES:
        raise HTTPException(422, detail={"error": "bad_status", "detail": status})
    row = db.query_one("SELECT * FROM external_reports WHERE id=?", (report_id,))
    if not row:
        raise HTTPException(404, detail={"error": "no_report", "detail": report_id})
    now = time.time()
    resolved = status == "resolved"
    db.execute(
        "UPDATE external_reports SET status=?, resolved_by=?, resolved_at=? WHERE id=?",
        (status, getattr(user, "username", None) if resolved else row["resolved_by"],
         now if resolved else row["resolved_at"], report_id))
    try:
        from .staging import db as fb_db
        fb_db().collection("UserReports").document(report_id).update({
            "reviewStatus": status,
            "reviewedBy": getattr(user, "username", None) or "",
        })
    except Exception as e:  # noqa: BLE001 — see docstring
        print(f"[external-reports] status mirror skipped: {e}")
    return _row_dict(db.query_one("SELECT * FROM external_reports WHERE id=?",
                                  (report_id,)))
