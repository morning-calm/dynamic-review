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

import sqlite3
import threading
import time
from typing import Any

from .config import DB_PATH

_LOCK = threading.RLock()
_CONN: sqlite3.Connection | None = None


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
    """Patch named columns on a field_edits row, always bumping updated_at."""
    cols.setdefault("updated_at", time.time())
    sets = ", ".join(f"{k}=?" for k in cols)
    params = tuple(cols.values()) + (field_id,)
    execute(f"UPDATE field_edits SET {sets} WHERE id=?", params)


def touch_session(session_id: str) -> None:
    execute("UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), session_id))
