"""Priority scoring + pin ordering on the trip list (2026-08-05).

The admin sequences a translator's work with numeric scores (higher = sooner); scored
trips order above pins, pins above the Trello base order, and everything unscored keeps
the manifest order (stable sort). pinned_at uses 0 — not NULL — as the "not pinned"
sentinel, because live DBs created before the score column carry NOT NULL on pinned_at.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sessions import _apply_priority_order, _CLIP_MP3_RE  # noqa: E402


def _trips(*ids):
    return [{"trip_id": t} for t in ids]


def test_unscored_list_keeps_manifest_order():
    trips = _trips("A", "B", "C")
    _apply_priority_order(trips, {})
    assert [t["trip_id"] for t in trips] == ["A", "B", "C"]
    assert all(t["priority"] is None and t["pinned"] is False for t in trips)


def test_scored_trips_order_first_highest_score_first():
    trips = _trips("A", "B", "C", "D")
    prio = {"C": {"pinned_at": 0, "score": 5.0},
            "D": {"pinned_at": 0, "score": 10.0}}
    _apply_priority_order(trips, prio)
    assert [t["trip_id"] for t in trips] == ["D", "C", "A", "B"]
    assert trips[0]["priority"] == 10.0 and trips[0]["pinned"] is False


def test_scores_order_above_pins_and_pins_above_the_rest():
    trips = _trips("A", "B", "C", "D")
    prio = {"B": {"pinned_at": 100.0, "score": None},     # pinned only
            "C": {"pinned_at": 200.0, "score": None},     # pinned later → above B
            "D": {"pinned_at": 0, "score": 1.0}}          # any score beats any pin
    _apply_priority_order(trips, prio)
    assert [t["trip_id"] for t in trips] == ["D", "C", "B", "A"]


def test_pinned_and_scored_row_ranks_by_its_score_and_keeps_the_pin_flag():
    trips = _trips("A", "B")
    prio = {"A": {"pinned_at": 100.0, "score": 2.0},
            "B": {"pinned_at": 0, "score": 7.0}}
    _apply_priority_order(trips, prio)
    assert [t["trip_id"] for t in trips] == ["B", "A"]
    assert trips[1]["pinned"] is True and trips[1]["priority"] == 2.0


def test_equal_scores_keep_manifest_order():
    trips = _trips("A", "B", "C")
    prio = {"B": {"pinned_at": 0, "score": 3.0},
            "C": {"pinned_at": 0, "score": 3.0}}
    _apply_priority_order(trips, prio)
    assert [t["trip_id"] for t in trips] == ["B", "C", "A"]


def test_null_pinned_at_reads_as_not_pinned():
    trips = _trips("A")
    _apply_priority_order(trips, {"A": {"pinned_at": None, "score": None}})
    assert trips[0]["pinned"] is False


# --- the duration clip filter: what counts as "audio to review" -----------------------
def test_clip_filter_takes_scene_question_and_option_clips_only():
    for name in ("3.mp3", "12.mp3", "3_q.mp3", "3_a1.mp3", "3_a12.mp3"):
        assert _CLIP_MP3_RE.match(name), name
    for name in ("3_a.mp3",        # the unreviewed answer clip
                 "3v1.mp3",        # archived correction
                 "3_fallback.mp3", "cover.mp3", "3.wav", "3_q.mp3.bak"):
        assert not _CLIP_MP3_RE.match(name), name
