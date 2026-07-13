"""Ingest a Gate-2 (Claude) report into triage findings — the ONE place that decides what
a reviewer must answer for, and when a submitted trip bounces back to them.

Imported by BOTH:
  * scripts/claude_review.py (the cron runner, raw sqlite3, no FastAPI) — writes the report
  * backend/app/sessions.py  (the API) — reads/answers findings
so the rule "which verdicts need a human response" cannot drift between them. Deliberately
has NO heavy imports (no config/firebase/db module): it takes an open sqlite3 connection.

Workflow (dave, 2026-07-13):
    submit -> Gate 2 runs -> any warning/needs_human?
        yes -> findings created, session CAS'd 'submitted' -> 'ai_review'
               (back in the REVIEWER's queue, editable again; admin approve is blocked
               because approve() only claims from 'submitted')
        no  -> nothing created, session stays 'submitted' -> straight to the admin
    reviewer answers every finding (resolved | rejected+why | deferred-to-admin)
        -> re-submit returns it to 'submitted'
"""
from __future__ import annotations

import json
import sqlite3
import time

from .statuses import EDITABLE_STATUSES   # stdlib-only too — keeps this module importable
                                          # by the cron runner without FastAPI/config

# The verdicts a reviewer must respond to. 'ok' is informational and never a triage item.
ACTIONABLE_VERDICTS = ("warning", "needs_human")

# What a reviewer may answer. 'rejected' REQUIRES a note (it's the admin's only explanation
# of why the AI's suggestion was overruled); 'deferred' hands an English/source issue to the
# admin, where a note is welcome but optional.
RESPONSES = ("resolved", "rejected", "deferred")
NOTE_REQUIRED = ("rejected",)

# The findings table lives HERE, not in db.py's SCHEMA: the cron runner creates it with the
# same DDL, so the API and the runner can never disagree about the table. db.init() applies
# this too (one source, both callers).
FINDINGS_DDL = """
-- One triage item per non-clean Gate-2 verdict (warning | needs_human). Created when a
-- report lands; the SUBMITTING REVIEWER must answer each before the trip returns to the
-- admin (dave, 2026-07-13):
--   resolved = actioned the suggestion   rejected = kept their version, note says why
--   deferred = an English/source issue, so it's the admin's call, not the reviewer's
-- status='open' is the untriaged state that holds a session in 'ai_review'.
CREATE TABLE IF NOT EXISTS auto_review_findings (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT NOT NULL,
    report_id      INTEGER NOT NULL,
    trip_id        TEXT NOT NULL DEFAULT '',
    scene_index    INTEGER,
    field_path     TEXT NOT NULL,
    option_index   INTEGER,
    verdict        TEXT NOT NULL,
    reasons_json   TEXT NOT NULL DEFAULT '[]',
    fix_json       TEXT,
    fix_verified   INTEGER,
    status         TEXT NOT NULL DEFAULT 'open',
    response_note  TEXT NOT NULL DEFAULT '',
    responded_by   TEXT,
    responded_at   REAL,
    created_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_findings_session ON auto_review_findings(session_id, status);
CREATE INDEX IF NOT EXISTS ix_findings_report ON auto_review_findings(report_id);
"""


def ingest(con: sqlite3.Connection, sid: str, trip_id: str, report_id: int,
           report: dict) -> int:
    """Create a finding per actionable verdict and, if any need an answer, bounce the
    session back to the reviewer. Returns the number of OPEN findings (the ones the
    reviewer still has to answer — carried-forward answers don't count).

    Idempotent-ish by construction: claude_review.py writes ONE report row per run and
    passes that row's id, so a re-review makes a fresh report with fresh findings. Older
    findings for the session are dropped first — a superseded report's items are stale, and
    keeping them would block the session forever on questions nobody can answer any more.

    EXCEPT the reviewer's standing answers: a 'rejected' ("keeping my version" — the text
    did NOT change) or 'deferred' ("the admin's call") answer is CARRIED FORWARD onto a
    re-reported finding with the same (scene, field, option, verdict). Without this, the
    routine re-review after a re-submit would delete the rejection note the admin is owed
    and re-open the same question forever (reject -> re-submit -> re-flag -> reject ...).
    'resolved' answers are NOT carried: the reviewer edited that text, so a re-flag is a
    fresh judgment of their fix and deserves a fresh look.
    """
    fields = [f for f in (report.get("fields") or [])
              if f.get("verdict") in ACTIONABLE_VERDICTS]
    now = time.time()
    carried = {}   # (scene, field, option, verdict) -> (status, note, by, at)
    for r in con.execute(
            "SELECT scene_index, field_path, option_index, verdict, status,"
            " response_note, responded_by, responded_at FROM auto_review_findings"
            " WHERE session_id=? AND status IN ('rejected','deferred')", (sid,)):
        carried[(r[0], r[1], r[2], r[3])] = (r[4], r[5], r[6], r[7])
    con.execute("DELETE FROM auto_review_findings WHERE session_id=?", (sid,))
    opened: list[tuple] = []          # (scene, field, option) per finding needing an answer
    for f in fields:
        key = (f.get("scene"), f.get("field") or "", f.get("option"), f.get("verdict"))
        status, note, by, at = carried.get(key) or ("open", "", None, None)
        if status == "open":
            opened.append((f.get("scene"), f.get("field") or "", f.get("option")))
        con.execute(
            "INSERT INTO auto_review_findings(session_id, report_id, trip_id, scene_index,"
            " field_path, option_index, verdict, reasons_json, fix_json, fix_verified,"
            " status, response_note, responded_by, responded_at, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, report_id, trip_id, f.get("scene"), f.get("field") or "",
             f.get("option"), f.get("verdict"),
             json.dumps(f.get("reasons") or [], ensure_ascii=False),
             json.dumps(f["suggested_fix"], ensure_ascii=False) if f.get("suggested_fix") else None,
             (None if f.get("suggested_fix_verified") is None
              else int(bool(f.get("suggested_fix_verified")))),
             status, note, by, at, now))
    if opened:
        # CAS from 'submitted' ONLY: if the admin already claimed it ('approving') or
        # approved it while the model was thinking, leave the workflow alone — the
        # findings are still recorded and visible, they just don't reopen a closed trip.
        con.execute(
            "UPDATE sessions SET status='ai_review', updated_at=? "
            "WHERE id=? AND status='submitted'", (now, sid))
        # Un-tick the fields the AI is asking about, so the reviewer's own "all done" gate
        # lands them on each one (dave, 2026-07-13). ONLY those fields — the rest of the
        # trip keeps the ticks they earned. Playback coverage is NOT cleared, so once
        # they've answered they can re-mark done without re-listening to the whole take (a
        # text edit clears coverage by itself, which is the case that SHOULD re-listen).
        # 'edit_required' is left alone: it's the reviewer's own louder flag.
        #
        # AFTER the CAS, and only while the session is the REVIEWER's: if the admin claimed
        # it mid-report the CAS above no-op'd, so there is no bounce to land on and their
        # per-field state must not be rewritten under an in-flight (or finished) approve.
        # A recall/request-changes that raced the model leaves it editable — the un-tick is
        # still wanted there, so this reads the status back rather than trusting the CAS.
        row = con.execute("SELECT status FROM sessions WHERE id=?", (sid,)).fetchone()
        if row and row[0] in EDITABLE_STATUSES:
            for scene, field, option in opened:
                con.execute(
                    "UPDATE field_edits SET flag='none' WHERE session_id=? AND scene_index IS ?"
                    " AND field_path=? AND option_index IS ? AND flag='done'",
                    (sid, scene, field, option))
    con.commit()
    return len(opened)


def open_count(con: sqlite3.Connection, sid: str) -> int:
    row = con.execute("SELECT COUNT(*) FROM auto_review_findings "
                      "WHERE session_id=? AND status='open'", (sid,)).fetchone()
    return int(row[0]) if row else 0
