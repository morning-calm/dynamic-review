"""
SQLite persistence (WAL) for review state. Single shared connection with
``check_same_thread=False``; every access is serialized behind one RLock because
FastAPI runs our sync route handlers across a worker threadpool.

Tables (per the design plan; a few columns added for clean version lookup):
  sessions       — one open review per trip
  field_edits    — the atom the UI edits (text + audio state + coverage)
  audio_versions — archived takes (v0 pristine + each splice/import/fallback)
"""

from __future__ import annotations

import contextvars
import sqlite3
import threading
import time
from typing import Any

from .config import DB_PATH

_LOCK = threading.RLock()
_CONN: sqlite3.Connection | None = None

# Who is performing the current request (set by the auth middleware in main.py, which
# runs in the request's task context so it propagates into threadpool'd handlers).
# update_fields stamps it into field_edits.edited_by — a best-effort audit hint (some
# low-level writes bypass update_fields), used to show "touched by admin" on the diff.
CURRENT_EDITOR: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "CURRENT_EDITOR", default=None)


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id                  TEXT PRIMARY KEY,
    trip_id             TEXT NOT NULL,
    folder_name         TEXT NOT NULL,
    voice               TEXT NOT NULL,
    voice_settings_json TEXT NOT NULL,
    orig_loudness_json  TEXT NOT NULL DEFAULT '{}',
    cleaned_orig_json   TEXT NOT NULL DEFAULT '{}',
    loaded_trip_json    TEXT NOT NULL,
    trip_categories_json TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL DEFAULT 'in_review',
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS field_edits (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL,
    scene_index         INTEGER,
    field_path          TEXT NOT NULL,
    option_index        INTEGER,
    has_audio           INTEGER NOT NULL DEFAULT 0,
    mp3_name            TEXT,
    original_text       TEXT NOT NULL DEFAULT '',
    current_text        TEXT NOT NULL DEFAULT '',
    flag                TEXT NOT NULL DEFAULT 'none',
    comment             TEXT NOT NULL DEFAULT '',
    current_mp3_path    TEXT,
    candidate_mp3_path  TEXT,
    fallback_mp3_path   TEXT,
    fallback_desc       TEXT NOT NULL DEFAULT '',
    working_audio_hash  TEXT,
    splice_confidence   REAL,
    splice_meta_json    TEXT,
    played_coverage_json TEXT NOT NULL DEFAULT '{}',
    updated_at          REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS audio_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    field_id    INTEGER NOT NULL,
    scene_index INTEGER,
    n           INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    path        TEXT NOT NULL,
    label       TEXT NOT NULL,
    created_at  REAL NOT NULL
);

-- Manual-edit workspace: free-standing clips per field (TTS-generated or imported),
-- auditioned/regenerated/deleted, one of which can be promoted to the working take.
CREATE TABLE IF NOT EXISTS manual_clips (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    field_id    INTEGER NOT NULL,
    text        TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'generated',   -- generated | imported
    comment     TEXT NOT NULL DEFAULT '',            -- instructions to the admin for this take
    path        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- Auth: admin-provisioned users (no self-signup) + opaque bearer/cookie tokens.
-- languages_json = JSON array of narration languages a reviewer may see (admins bypass).
-- auth_sessions stores sha256(token) ONLY — the raw handle is never persisted.
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    username       TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'reviewer',   -- admin | reviewer
    languages_json TEXT NOT NULL DEFAULT '[]',
    active         INTEGER NOT NULL DEFAULT 1,
    created_at     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,       -- sha256 hex of the opaque token
    user_id    INTEGER NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- Append-only approvals audit (who promoted what to staging, and when).
CREATE TABLE IF NOT EXISTS approvals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    trip_id      TEXT NOT NULL,
    approved_by  TEXT NOT NULL,
    approved_at  REAL NOT NULL,
    written_json TEXT NOT NULL DEFAULT '[]'
);

-- Completed queue: a finished trip (admin-approved, or admin-marked-complete for work
-- done in the old system) leaves the active review list and appears here. One row per
-- trip_id; method='approved' carries the approved session_id, method='manual' has none.
CREATE TABLE IF NOT EXISTS completed_trips (
    trip_id      TEXT PRIMARY KEY,
    completed_by TEXT NOT NULL,
    completed_at REAL NOT NULL,
    method       TEXT NOT NULL,             -- approved | manual
    session_id   TEXT,                      -- approved session id; NULL for manual
    note         TEXT NOT NULL DEFAULT ''
);

-- In-app prioritisation: an admin "pins" a trip to the top of the reviewer list. The
-- Trello card order is the base; pinned trips float above it (newest pin first).
CREATE TABLE IF NOT EXISTS trip_priority (
    trip_id   TEXT PRIMARY KEY,
    pinned_by TEXT NOT NULL,
    pinned_at REAL NOT NULL
);

-- Bug reports: a reviewer/admin flags a problem on a specific field, in any language.
-- A snapshot of the field's text + working/candidate audio is captured at report time so
-- we see exactly what they saw. bug_report_messages is the reply thread (reviewer <-> admin).
CREATE TABLE IF NOT EXISTS bug_reports (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT,
    field_id       INTEGER,
    trip_id        TEXT NOT NULL DEFAULT '',
    scene_index    INTEGER,
    field_path     TEXT NOT NULL DEFAULT '',
    reporter       TEXT NOT NULL,
    reporter_role  TEXT NOT NULL DEFAULT 'reviewer',
    body           TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'open',      -- open | investigating | resolved
    text_snapshot  TEXT NOT NULL DEFAULT '{}',        -- JSON: current_text/localization/etc at report time
    audio_dir      TEXT,                              -- work/bug_reports/{id}/ (working.mp3 / candidate.mp3)
    reporter_seen_at REAL NOT NULL DEFAULT 0,         -- last time the reporter viewed the thread (unread calc)
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS bug_report_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id   INTEGER NOT NULL,
    author      TEXT NOT NULL,
    author_role TEXT NOT NULL DEFAULT 'reviewer',
    body        TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    FOREIGN KEY (report_id) REFERENCES bug_reports(id)
);

-- Live presence: who has which session open right now (reviewer OR admin), heartbeated
-- by the FE every ~30s with a human-readable context ("Scene 4 · SceneDesc — editing").
-- A row is "live" while updated_at is within PRESENCE_LIVE_SECONDS; stale rows are
-- pruned opportunistically on each heartbeat. Also drives the recall "admin mid-review"
-- check, so a reviewer can't yank a session out from under a reviewing admin.
CREATE TABLE IF NOT EXISTS presence (
    username   TEXT NOT NULL,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'reviewer',
    context    TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL,
    PRIMARY KEY (username, session_id)
);

-- Recall requests: a reviewer asks for their SUBMITTED trip back when it can't be
-- auto-recalled (admin mid-review, or already approved). Admin grants (send back) or
-- declines from the review queue, where open requests are pinned.
CREATE TABLE IF NOT EXISTS recall_requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    trip_id         TEXT NOT NULL DEFAULT '',
    requested_by    TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'open',   -- open | granted | declined
    created_at      REAL NOT NULL,
    resolved_by     TEXT,
    resolved_at     REAL,
    resolution_note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS auto_reviews (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    trip_id      TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL,
    model        TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'ok',    -- ok | error
    ok_count     INTEGER NOT NULL DEFAULT 0,
    warn_count   INTEGER NOT NULL DEFAULT 0,
    flag_count   INTEGER NOT NULL DEFAULT 0,
    report_json  TEXT NOT NULL DEFAULT '{}'     -- Gate-2 per-field verdicts (see claude_review.py)
);

CREATE INDEX IF NOT EXISTS ix_presence_session ON presence(session_id, updated_at);
CREATE INDEX IF NOT EXISTS ix_recall_status ON recall_requests(status, created_at);
CREATE INDEX IF NOT EXISTS ix_recall_session ON recall_requests(session_id, created_at);
CREATE INDEX IF NOT EXISTS ix_autoreviews_session ON auto_reviews(session_id, created_at);
CREATE INDEX IF NOT EXISTS ix_bugreports_status ON bug_reports(status, created_at);
CREATE INDEX IF NOT EXISTS ix_bugreports_reporter ON bug_reports(reporter, created_at);
CREATE INDEX IF NOT EXISTS ix_bugmsgs_report ON bug_report_messages(report_id);
CREATE INDEX IF NOT EXISTS ix_fields_session ON field_edits(session_id);
CREATE INDEX IF NOT EXISTS ix_versions_field ON audio_versions(field_id);
CREATE INDEX IF NOT EXISTS ix_sessions_trip ON sessions(trip_id, status);
CREATE INDEX IF NOT EXISTS ix_clips_field ON manual_clips(field_id);
CREATE INDEX IF NOT EXISTS ix_auth_sessions_user ON auth_sessions(user_id);
CREATE INDEX IF NOT EXISTS ix_auth_sessions_expires ON auth_sessions(expires_at);
CREATE INDEX IF NOT EXISTS ix_completed_at ON completed_trips(completed_at);
"""


def init() -> None:
    global _CONN
    with _LOCK:
        if _CONN is not None:
            return
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.executescript(SCHEMA)
        # Lightweight migrations: per-session voice tuning overrides (added after
        # the initial schema shipped). CREATE TABLE IF NOT EXISTS won't add columns
        # to an existing sessions table, so add them here if absent.
        have = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
        if "speed_override" not in have:
            conn.execute("ALTER TABLE sessions ADD COLUMN speed_override REAL")
        if "model_override" not in have:
            conn.execute("ALTER TABLE sessions ADD COLUMN model_override TEXT")
        fcols = {r["name"] for r in conn.execute("PRAGMA table_info(field_edits)")}
        if "original_coverage_json" not in fcols:
            conn.execute("ALTER TABLE field_edits ADD COLUMN "
                         "original_coverage_json TEXT NOT NULL DEFAULT '{}'")
        if "source_text" not in fcols:
            conn.execute("ALTER TABLE field_edits ADD COLUMN "
                         "source_text TEXT NOT NULL DEFAULT ''")
        if "original_source" not in fcols:
            conn.execute("ALTER TABLE field_edits ADD COLUMN "
                         "original_source TEXT NOT NULL DEFAULT ''")
        if "working_text" not in fcols:
            # the text the CURRENT working audio says — so successive splices accumulate
            # on the combined take instead of restarting from the pristine master.
            conn.execute("ALTER TABLE field_edits ADD COLUMN "
                         "working_text TEXT NOT NULL DEFAULT ''")
        if "version_cursor" not in fcols:
            # which audio_versions.n the working take currently sits on, for undo/redo.
            # NULL = the latest version (no undo applied yet).
            conn.execute("ALTER TABLE field_edits ADD COLUMN version_cursor INTEGER")
        if "edited_by" not in fcols:
            # last user who changed this field (via update_fields) — audit hint for the
            # approve page's "touched by admin" badge.
            conn.execute("ALTER TABLE field_edits ADD COLUMN edited_by TEXT")
        if "localization_json" not in fcols:
            # Mandarin (_ZH) 4-script block for this field: JSON
            # {"cur":{Hans,Hant,zhuyin,en}, "orig":{…}}. NULL for every non-_ZH field
            # (and for _ZH fields with no TripLocalizations entry, e.g. contentTitleKey).
            conn.execute("ALTER TABLE field_edits ADD COLUMN localization_json TEXT")
        ccols = {r["name"] for r in conn.execute("PRAGMA table_info(manual_clips)")}
        if "comment" not in ccols:
            conn.execute("ALTER TABLE manual_clips ADD COLUMN "
                         "comment TEXT NOT NULL DEFAULT ''")
        # Submit -> approve workflow columns (auth/roles feature). Additive; existing
        # in_review/submitted rows stay valid under the widened status value set.
        if "submitted_by" not in have:
            conn.execute("ALTER TABLE sessions ADD COLUMN submitted_by TEXT")
        if "approved_by" not in have:
            conn.execute("ALTER TABLE sessions ADD COLUMN approved_by TEXT")
        if "review_note" not in have:
            conn.execute("ALTER TABLE sessions ADD COLUMN review_note TEXT")
        # Mandarin (_ZH) 4-script + A/B-audio review mode (additive; 0/NULL for every
        # existing and every non-_ZH session).
        if "is_zh" not in have:
            conn.execute("ALTER TABLE sessions ADD COLUMN is_zh INTEGER NOT NULL DEFAULT 0")
        if "preferred_version" not in have:
            # the trip's chosen ElevenLabs A/B version: 'v2' | 'v3' | NULL (undecided).
            conn.execute("ALTER TABLE sessions ADD COLUMN preferred_version TEXT")
        conn.commit()
        _CONN = conn


def _conn() -> sqlite3.Connection:
    if _CONN is None:
        init()
    assert _CONN is not None
    return _CONN


def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with _LOCK:
        cur = _conn().execute(sql, params)
        return cur.fetchall()


def query_one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    with _LOCK:
        cur = _conn().execute(sql, params)
        return cur.fetchone()


def execute(sql: str, params: tuple = ()) -> int:
    """Run a write; returns lastrowid. Serialized + committed."""
    with _LOCK:
        cur = _conn().execute(sql, params)
        _conn().commit()
        return cur.lastrowid


def execute_rowcount(sql: str, params: tuple = ()) -> int:
    """Run a write; returns the number of rows affected (for compare-and-set claims,
    e.g. the submit->approve CAS). Serialized + committed."""
    with _LOCK:
        cur = _conn().execute(sql, params)
        _conn().commit()
        return cur.rowcount


def update_fields(field_id: int, **cols: Any) -> None:
    """Patch named columns on a field_edits row, always bumping updated_at (and stamping
    edited_by from the request context when known — see CURRENT_EDITOR)."""
    cols.setdefault("updated_at", time.time())
    editor = CURRENT_EDITOR.get()
    if editor:
        cols.setdefault("edited_by", editor)
    sets = ", ".join(f"{k}=?" for k in cols)
    params = tuple(cols.values()) + (field_id,)
    execute(f"UPDATE field_edits SET {sets} WHERE id=?", params)


def touch_session(session_id: str) -> None:
    execute("UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), session_id))
