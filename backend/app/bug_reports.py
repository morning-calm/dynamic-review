"""Bug reports — a reviewer or admin flags a problem on a specific field, in any language.

At report time we snapshot the field's TEXT (current_text + the _ZH 4-script block) and its
WORKING + CANDIDATE audio into ``work/bug_reports/{id}/`` so we can see/hear exactly what the
reporter saw. Each report has a reply thread (``bug_report_messages``) so the admin (and me,
when investigating) can respond and the reviewer can reply back — all in the app.

Access: an admin sees/handles every report; a reviewer sees only their own. Snapshots and the
thread are served through the routes in ``routes_bugs.py``. Nothing here raises for a missing
audio snapshot — a text-only field simply has no audio.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from fastapi import HTTPException

from . import db
from .config import WORK_ROOT

_STATUSES = ("open", "investigating", "resolved")
_SNAP_ROOT = WORK_ROOT / "bug_reports"


def _field_path_out(frow) -> str:
    fp = frow["field_path"]
    if fp == "questionOption":
        return f"questionOption[{frow['option_index']}]"
    return fp


def _row(rid: int):
    r = db.query_one("SELECT * FROM bug_reports WHERE id=?", (rid,))
    if r is None:
        raise HTTPException(404, detail={"error": "no_report", "detail": f"bug report {rid}"})
    return r


def _assert_access(row, user) -> None:
    if not (user.is_admin or row["reporter"] == user.username):
        raise HTTPException(403, detail={"error": "forbidden",
                                         "detail": "not your bug report"})


def create(sid: str, fid: int, body: str, user) -> dict:
    """File a report against field ``fid`` of session ``sid``. Snapshots the field text +
    working/candidate audio. ``body`` is the reporter's description (any language)."""
    from . import sessions   # lazy: sessions pulls in heavy modules
    body = (body or "").strip()
    if not body:
        raise HTTPException(422, detail={"error": "empty", "detail": "describe the problem"})
    srow = sessions._session_row(sid)          # 404 if the session is gone
    frow = sessions._field_row(sid, fid)
    now = time.time()
    loc_raw = sessions._srow_get(frow, "localization_json")
    snapshot = {
        "field_path": _field_path_out(frow),
        "scene_index": frow["scene_index"],
        "current_text": frow["current_text"],
        "original_text": frow["original_text"],
        "working_text": frow["working_text"],
        "localization": json.loads(loc_raw) if loc_raw else None,
    }
    rid = db.execute(
        "INSERT INTO bug_reports(session_id,field_id,trip_id,scene_index,field_path,reporter,"
        "reporter_role,body,status,text_snapshot,reporter_seen_at,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?, 'open', ?, ?, ?, ?)",
        (sid, fid, srow["trip_id"], frow["scene_index"], _field_path_out(frow),
         user.username, user.role, body, json.dumps(snapshot, ensure_ascii=False),
         now, now, now))

    # Snapshot the audio the reporter was hearing (best-effort; text fields have none).
    audio_dir = _SNAP_ROOT / str(rid)
    try:
        audio_dir.mkdir(parents=True, exist_ok=True)
        dirs = sessions.work_dirs(sid)
        name = frow["mp3_name"]
        if name and (dirs["working"] / name).exists():
            shutil.copyfile(dirs["working"] / name, audio_dir / "working.mp3")
        cand = frow["candidate_mp3_path"]
        if cand and Path(cand).exists():
            shutil.copyfile(cand, audio_dir / "candidate.mp3")
        db.execute("UPDATE bug_reports SET audio_dir=? WHERE id=?", (str(audio_dir), rid))
    except Exception:  # noqa: BLE001 — a snapshot failure must not lose the report
        pass
    return get(rid, user)


def _serialize(row, *, with_detail: bool) -> dict:
    out = {
        "id": row["id"], "session_id": row["session_id"], "field_id": row["field_id"],
        "trip_id": row["trip_id"], "scene_index": row["scene_index"],
        "field_path": row["field_path"], "reporter": row["reporter"],
        "reporter_role": row["reporter_role"], "body": row["body"],
        "status": row["status"], "created_at": row["created_at"],
        "updated_at": row["updated_at"], "audio": {},
    }
    ad = row["audio_dir"]
    if ad:
        for which in ("working", "candidate"):
            if (Path(ad) / f"{which}.mp3").exists():
                out["audio"][which] = f"/api/bug-reports/{row['id']}/audio/{which}"
    msgs = db.query(
        "SELECT COUNT(*) AS n, MAX(created_at) AS last FROM bug_report_messages WHERE report_id=?",
        (row["id"],))[0]
    out["message_count"] = msgs["n"]
    out["last_message_at"] = msgs["last"]
    if with_detail:
        out["text_snapshot"] = json.loads(row["text_snapshot"] or "{}")
        out["messages"] = [
            {"author": m["author"], "author_role": m["author_role"],
             "body": m["body"], "created_at": m["created_at"]}
            for m in db.query(
                "SELECT * FROM bug_report_messages WHERE report_id=? ORDER BY id", (row["id"],))]
    return out


def list_reports(user) -> list[dict]:
    """Admin: every report (newest first). Reviewer: only their own."""
    if user.is_admin:
        rows = db.query("SELECT * FROM bug_reports ORDER BY "
                        "CASE status WHEN 'open' THEN 0 WHEN 'investigating' THEN 1 ELSE 2 END, "
                        "updated_at DESC")
    else:
        rows = db.query("SELECT * FROM bug_reports WHERE reporter=? ORDER BY updated_at DESC",
                        (user.username,))
    return [_serialize(r, with_detail=False) for r in rows]


def get(rid: int, user) -> dict:
    row = _row(rid)
    _assert_access(row, user)
    # Viewing marks the thread seen for the reporter (drives their unread badge).
    if not user.is_admin and row["reporter"] == user.username:
        db.execute("UPDATE bug_reports SET reporter_seen_at=? WHERE id=?", (time.time(), rid))
    return _serialize(row, with_detail=True)


def add_message(rid: int, body: str, user) -> dict:
    row = _row(rid)
    _assert_access(row, user)
    body = (body or "").strip()
    if not body:
        raise HTTPException(422, detail={"error": "empty", "detail": "empty message"})
    now = time.time()
    db.execute(
        "INSERT INTO bug_report_messages(report_id,author,author_role,body,created_at) "
        "VALUES(?,?,?,?,?)", (rid, user.username, user.role, body, now))
    # Bump updated_at; if the REPORTER wrote it, they've obviously seen the thread.
    if not user.is_admin and row["reporter"] == user.username:
        db.execute("UPDATE bug_reports SET updated_at=?, reporter_seen_at=? WHERE id=?",
                   (now, now, rid))
    else:
        db.execute("UPDATE bug_reports SET updated_at=? WHERE id=?", (now, rid))
    return get(rid, user)


def set_status(rid: int, status: str, user) -> dict:
    """Admin-only (enforced at the route). open | investigating | resolved."""
    row = _row(rid)
    if status not in _STATUSES:
        raise HTTPException(422, detail={"error": "bad_status",
                                         "detail": f"status must be one of {_STATUSES}"})
    db.execute("UPDATE bug_reports SET status=?, updated_at=? WHERE id=?",
               (status, time.time(), rid))
    return get(rid, user)


def counts(user) -> dict:
    """Badge counts. Admin: number of OPEN reports. Reviewer: number of their reports with an
    admin reply they haven't seen yet."""
    if user.is_admin:
        n = db.query_one("SELECT COUNT(*) AS n FROM bug_reports WHERE status='open'")["n"]
        return {"role": "admin", "open": n}
    n = db.query_one(
        "SELECT COUNT(DISTINCT b.id) AS n FROM bug_reports b "
        "JOIN bug_report_messages m ON m.report_id=b.id "
        "WHERE b.reporter=? AND m.author_role='admin' AND m.created_at > b.reporter_seen_at",
        (user.username,))["n"]
    return {"role": "reviewer", "unread": n}


def audio_path(rid: int, which: str, user) -> Path:
    row = _row(rid)
    _assert_access(row, user)
    if which not in ("working", "candidate") or not row["audio_dir"]:
        raise HTTPException(404, detail={"error": "no_audio", "detail": which})
    p = Path(row["audio_dir"]) / f"{which}.mp3"
    if not p.exists():
        raise HTTPException(404, detail={"error": "no_audio", "detail": which})
    return p


def open_reports_digest() -> list[dict]:
    """Plain summary of every OPEN report — for the CLI / a scheduled check that surfaces new
    reports to the admin (and to me when investigating). No auth (local/offline use only)."""
    rows = db.query("SELECT * FROM bug_reports WHERE status!='resolved' ORDER BY created_at")
    return [{"id": r["id"], "trip_id": r["trip_id"], "field_path": r["field_path"],
             "scene_index": r["scene_index"], "reporter": r["reporter"],
             "status": r["status"], "body": r["body"], "created_at": r["created_at"]}
            for r in rows]
