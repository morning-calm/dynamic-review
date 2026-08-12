"""Gate-2 re-review loop cut-off (2026-08-10).

The French reviewer's report: "every time I submit a review, it re-reviews in a sort of
never ending cycle." Root causes, all covered here:

  1. Every re-submit re-judged the reviewer's ENTIRE diff, and Sonnet's nondeterministic
     verdicts flagged a different subset each round (Reims2_A12_FR 2026-08-09:
     1 warn -> 1 warn -> 0 -> 1 warn on identical text). Fix: each successful report
     snapshots the diff it judged (auto_reviews.input_json) and later runs re-judge ONLY
     fields whose text changed since; a no-change re-submit writes a carried report with
     NO model call and NO bounce — the guaranteed exit.
  2. ingest() dropped 'resolved' answers on every re-report and matched carried answers on
     the exact verdict, so answers evaporated whenever the model re-flagged. Fix: on
     UNCHANGED fields any answer carries (the verdict is a verbatim copy anyway).
  3. An ERRORED report ran the delete-and-recreate ingest with an empty field list,
     wiping every answered finding. Fix: error reports are never ingested.
  4. The */5 cron had no overlap guard; two concurrent runs double-reviewed the same
     sessions (duplicate reports 43-60s apart on 2026-08-09). Fix: a non-blocking lock.
"""

import importlib.util
import sqlite3
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

_spec = importlib.util.spec_from_file_location(
    "claude_review", REPO / "scripts" / "claude_review.py")
claude_review = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(claude_review)

SID, TRIP = "sess_test", "Testville_A12_FR"


def _make_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE sessions(id TEXT PRIMARY KEY, trip_id TEXT, status TEXT,
                              updated_at REAL, submitted_by TEXT);
        CREATE TABLE field_edits(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,
            scene_index INTEGER, field_path TEXT, option_index INTEGER,
            current_text TEXT DEFAULT '', original_text TEXT DEFAULT '',
            source_text TEXT DEFAULT '', original_source TEXT DEFAULT '',
            flag TEXT DEFAULT 'none');
    """)
    con.execute("INSERT INTO sessions VALUES(?,?,?,?,?)",
                (SID, TRIP, "submitted", time.time(), "reviewer"))
    for scene, field, cur, orig in [(1, "SceneDesc", "Le chat noir.", "Le chat."),
                                    (2, "SceneDesc", "La mer bleue.", "La mer.")]:
        con.execute("INSERT INTO field_edits(session_id, scene_index, field_path,"
                    " option_index, current_text, original_text) VALUES(?,?,?,NULL,?,?)",
                    (SID, scene, field, cur, orig))
    con.commit()
    return con


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "review.db"
    con = _make_db(db)
    monkeypatch.setattr(claude_review, "DB_PATH", db)
    monkeypatch.setattr(claude_review, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(claude_review, "LOCK_PATH", tmp_path / "autoreview.lock")
    monkeypatch.setattr(sys, "argv", ["claude_review.py"])

    calls = []   # each entry = the field keys the model was actually asked to judge

    def fake(verdicts_by_key):
        """A model whose per-field verdicts the test scripts, and that records its input."""
        def call(diff):
            keys = [(f["scene"], f["field"], f["option"]) for f in diff["fields"]]
            calls.append(keys)
            return {"summary": "fake",
                    "fields": [{"scene": s, "field": f, "option": o,
                                "verdict": verdicts_by_key.get((s, f, o), "ok"),
                                "reasons": ["fake reason"], "suggested_fix": None}
                               for s, f, o in keys]}
        return call

    yield con, calls, fake, monkeypatch
    con.close()


def _status(con):
    return con.execute("SELECT status FROM sessions WHERE id=?", (SID,)).fetchone()[0]


def _findings(con):
    return con.execute("SELECT scene_index, verdict, status, response_note "
                       "FROM auto_review_findings ORDER BY scene_index").fetchall()


def _resubmit(con):
    # Strictly after the newest report row: Windows time.time() ticks at ~16 ms, so a
    # bare time.time() here can land in the SAME tick as the created_at the runner just
    # wrote — pending_sessions' strict `created_at < updated_at` then judges the session
    # already-reviewed and the test flakes (~30% on this box). A human re-submit can't
    # land within one tick of the cron's report write, so the nudge only de-flakes the
    # test, it doesn't paper over a live ordering bug.
    latest = con.execute(
        "SELECT COALESCE(MAX(created_at), 0) FROM auto_reviews").fetchone()[0]
    con.execute("UPDATE sessions SET status='submitted', updated_at=? WHERE id=?",
                (max(time.time(), latest + 0.001), SID))
    con.commit()


def _answer_all(con, action, note=""):
    con.execute("UPDATE auto_review_findings SET status=?, response_note=?, "
                "responded_by='reviewer', responded_at=? WHERE status='open'",
                (action, note, time.time()))
    con.commit()


def test_resubmit_without_changes_is_the_cutoff(env):
    con, calls, fake, mp = env
    mp.setattr(claude_review, "call_claude", fake({(1, "SceneDesc", None): "warning"}))
    claude_review.main()
    assert len(calls) == 1 and len(calls[0]) == 2     # first run: full review
    assert _status(con) == "ai_review"                # flagged -> bounced

    _answer_all(con, "rejected", "keeping my version")
    _resubmit(con)
    # Second run scripts a DIFFERENT flag (the nondeterminism that drove the loop) —
    # but the model must not be consulted at all: nothing changed since the snapshot.
    mp.setattr(claude_review, "call_claude", fake({(2, "SceneDesc", None): "warning"}))
    claude_review.main()
    assert len(calls) == 1, "no text changed — the model must not be called again"
    assert _status(con) == "submitted", "carried report must not bounce the session"
    rows = _findings(con)
    assert [(r["status"], r["response_note"]) for r in rows] == \
        [("rejected", "keeping my version")], "the answered finding must survive untouched"
    # and the session has left the pending queue (the carried report row is newer)
    assert claude_review.pending_sessions(con) == []


def test_only_the_edited_field_is_rereviewed(env):
    con, calls, fake, mp = env
    mp.setattr(claude_review, "call_claude", fake({(1, "SceneDesc", None): "warning"}))
    claude_review.main()
    _answer_all(con, "rejected", "non, c'est bon")
    con.execute("UPDATE field_edits SET current_text='La mer turquoise.' "
                "WHERE session_id=? AND scene_index=2", (SID,))
    _resubmit(con)
    mp.setattr(claude_review, "call_claude", fake({(2, "SceneDesc", None): "warning"}))
    claude_review.main()
    assert calls[-1] == [(2, "SceneDesc", None)], "only the edited field goes to the model"
    assert _status(con) == "ai_review"                # the fresh flag legitimately bounces
    rows = _findings(con)
    assert [(r["scene_index"], r["status"]) for r in rows] == [(1, "rejected"), (2, "open")]
    assert rows[0]["response_note"] == "non, c'est bon", \
        "the standing rejection carries even though scene 2 re-opened"
    # answer the new one without editing -> next run is the cutoff again
    _answer_all(con, "resolved")
    _resubmit(con)
    claude_review.main()
    assert len(calls) == 2 and _status(con) == "submitted"
    assert all(r["status"] != "open" for r in _findings(con))


def test_resolved_answer_carries_on_unchanged_field(env):
    con, calls, fake, mp = env
    mp.setattr(claude_review, "call_claude", fake({(1, "SceneDesc", None): "warning"}))
    claude_review.main()
    _answer_all(con, "resolved")          # answered WITHOUT editing (e.g. listened again)
    _resubmit(con)
    claude_review.main()
    assert len(calls) == 1 and _status(con) == "submitted"
    assert [r["status"] for r in _findings(con)] == ["resolved"]


def test_error_report_neither_ingests_nor_wipes_answers(env):
    con, calls, fake, mp = env
    mp.setattr(claude_review, "call_claude", fake({(1, "SceneDesc", None): "warning"}))
    claude_review.main()
    _answer_all(con, "rejected", "keeping it")
    _resubmit(con)
    con.execute("UPDATE field_edits SET current_text='Le chat noir et blanc.' "
                "WHERE session_id=? AND scene_index=1", (SID,))
    con.commit()

    def boom(diff):
        raise RuntimeError("claude exited 1: transient")
    mp.setattr(claude_review, "call_claude", boom)
    claude_review.main()
    assert _status(con) == "submitted", "an errored run must not change the workflow"
    assert [(r["status"], r["response_note"]) for r in _findings(con)] == \
        [("rejected", "keeping it")], "an errored run must not wipe answered findings"
    row = con.execute("SELECT status, input_json FROM auto_reviews "
                      "ORDER BY created_at DESC LIMIT 1").fetchone()
    assert row["status"] == "error" and row["input_json"] is None, \
        "an error row is recorded but never becomes the incremental baseline"


def test_lock_is_exclusive(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_review, "LOCK_PATH", tmp_path / "autoreview.lock")
    first = claude_review._acquire_lock()
    assert first is not None
    assert claude_review._acquire_lock() is None, "a second concurrent run must bow out"
    first.close()
    third = claude_review._acquire_lock()
    assert third is not None
    third.close()
