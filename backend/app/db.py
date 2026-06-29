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

CREATE INDEX IF NOT EXISTS ix_fields_session ON field_edits(session_id);
CREATE INDEX IF NOT EXISTS ix_versions_field ON audio_versions(field_id);
CREATE INDEX IF NOT EXISTS ix_sessions_trip ON sessions(trip_id, status);
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


def update_fields(field_id: int, **cols: Any) -> None:
    """Patch named columns on a field_edits row, always bumping updated_at."""
    cols.setdefault("updated_at", time.time())
    sets = ", ".join(f"{k}=?" for k in cols)
    params = tuple(cols.values()) + (field_id,)
    execute(f"UPDATE field_edits SET {sets} WHERE id=?", params)


def touch_session(session_id: str) -> None:
    execute("UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), session_id))
