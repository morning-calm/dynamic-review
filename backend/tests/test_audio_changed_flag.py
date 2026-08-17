"""`Field.audio_changed` — the Changes page's audio-only-alteration badge (2026-08-17).

The admin's Changes summary used to list only TEXT diffs, so a field whose audio was
regenerated/spliced with the text untouched showed nothing at all. serialize_field now
exposes `audio_changed` = working take hash != pristine v0 hash — the same test approve
uses to decide what to promote. The v0 hash is cached by (path, mtime, size) so a
reseed that replaces the pristine file invalidates the cache.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import sessions  # noqa: E402


def _frow(**over):
    base = {"id": 5, "has_audio": 1, "mp3_name": "3.mp3", "field_path": "SceneDesc",
            "scene_index": 3, "option_index": None,
            "original_text": "t", "current_text": "t", "working_text": None,
            "source_text": "", "original_source": "", "flag": "none", "comment": "",
            "splice_confidence": None, "played_coverage_json": "{}",
            "original_coverage_json": "{}", "working_audio_hash": None,
            "candidate_mp3_path": None, "fallback_mp3_path": None,
            "version_cursor": None, "current_mp3_path": "x"}
    base.update(over)
    return base


@pytest.fixture
def serialize(monkeypatch, tmp_path):
    """serialize_field with the DB/session plumbing stubbed out; returns the payload."""
    for d in ("working", "candidate", "orig", "versions"):
        (tmp_path / d).mkdir()
    monkeypatch.setattr(sessions, "work_dirs", lambda sid: {
        k: tmp_path / k for k in ("working", "candidate", "orig", "versions")})
    monkeypatch.setattr(sessions.db, "query", lambda *a, **k: [])
    monkeypatch.setattr(sessions, "_clips_for", lambda sid, fid: [])
    monkeypatch.setattr(sessions, "_is_zh_session", lambda sid: False)
    monkeypatch.setattr(sessions, "_trip_id_cached", lambda sid: "Trip_EN")
    monkeypatch.setattr(sessions, "_can_accept_text_as_voiced", lambda *a: False)
    return lambda frow: sessions.serialize_field("sess_x", frow)


def test_unchanged_audio_not_flagged(serialize, tmp_path):
    (tmp_path / "orig" / "3.mp3").write_bytes(b"PRISTINE")
    h = sessions._file_hash(tmp_path / "orig" / "3.mp3")
    assert serialize(_frow(working_audio_hash=h))["audio_changed"] is False


def test_edited_audio_flagged(serialize, tmp_path):
    (tmp_path / "orig" / "3.mp3").write_bytes(b"PRISTINE")
    assert serialize(_frow(working_audio_hash="deadbeef00000000"))["audio_changed"] is True


def test_no_working_hash_means_unchanged(serialize, tmp_path):
    # A field with no working take yet (e.g. pre-pick _ZH) must not claim a change.
    (tmp_path / "orig" / "3.mp3").write_bytes(b"PRISTINE")
    assert serialize(_frow(working_audio_hash=None))["audio_changed"] is False


def test_missing_pristine_is_not_a_change(serialize):
    assert serialize(_frow(working_audio_hash="deadbeef00000000"))["audio_changed"] is False


def test_reseed_invalidates_orig_hash_cache(serialize, tmp_path):
    orig = tmp_path / "orig" / "3.mp3"
    orig.write_bytes(b"PRISTINE")
    old_hash = sessions._file_hash(orig)
    assert serialize(_frow(working_audio_hash=old_hash))["audio_changed"] is False
    # Reseed swaps the pristine file (different size → different stat key): the cached
    # hash must not survive, so the SAME working hash now reads as changed.
    orig.write_bytes(b"NEW PRISTINE BYTES")
    assert serialize(_frow(working_audio_hash=old_hash))["audio_changed"] is True
    new_hash = sessions._file_hash(orig)
    assert serialize(_frow(working_audio_hash=new_hash))["audio_changed"] is False
