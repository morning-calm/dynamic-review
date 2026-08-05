"""Notifier attribution: presence heartbeat beats every heuristic (2026-08-05).

The KochiCity_N3_JP incident, replayed: an ADMIN opened a Japanese trip (seeding all its
field rows — `touched` > 0 — with no edited_by stamp), Toshifumi held a live 14-day
token, and the specialist-token heuristic reported "toshifumi started KochiCity_N3_JP".
The presence table had the exact answer (admin, editing, that session id, 12 s after the
seed) and was never consulted. `actor_for` now reads presence first.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from activity_notifier import actor_for  # noqa: E402

NOW = 1_785_924_011.0   # the real seed timestamp

USERS = [
    {"id": 1, "username": "admin", "role": "admin",
     "langs": ["English", "Japanese", "Mandarin"]},
    {"id": 2, "username": "toshifumi", "role": "reviewer", "langs": ["Japanese"]},
]
# toshifumi logged in the day before and (like every reviewer) holds a live token.
LOGINS = {2: [NOW - 24 * 3600]}
LIVE_TOKENS = {1: True, 2: True}


def _sess(**over):
    base = {"last_editor": None, "last_editor_ts": None}
    base.update(over)
    return base


def test_kochi_incident_presence_wins_over_the_specialist_token():
    """Admin opens a JP trip; presence says admin; toshifumi's live token must not win."""
    presence = {"sess_c1d63c6153cf": [("admin", NOW + 12)]}
    who = actor_for(_sess(), "sess_c1d63c6153cf", "Japanese", NOW,
                    USERS, LOGINS, LIVE_TOKENS, presence)
    assert who == "admin"


def test_without_presence_the_old_heuristic_still_applies():
    """No presence row, no stamp → the specialist live-token rule (unchanged fallback)."""
    who = actor_for(_sess(), "sess_x", "Japanese", NOW, USERS, LOGINS, LIVE_TOKENS, {})
    assert who == "toshifumi"


def test_stale_presence_is_ignored():
    """A presence row well BEFORE the activity is someone who left — not evidence."""
    presence = {"sess_x": [("admin", NOW - 3600)]}
    who = actor_for(_sess(), "sess_x", "Japanese", NOW, USERS, LOGINS, LIVE_TOKENS, presence)
    assert who == "toshifumi"


def test_most_recent_presence_wins_when_two_users_touched_the_session():
    presence = {"sess_x": [("toshifumi", NOW + 5), ("admin", NOW + 90)]}
    who = actor_for(_sess(), "sess_x", "Japanese", NOW, USERS, LOGINS, LIVE_TOKENS, presence)
    assert who == "admin"


def test_edited_by_stamp_still_wins_over_the_heuristic_without_presence():
    s = _sess(last_editor="admin", last_editor_ts=NOW - 60)
    who = actor_for(s, "sess_x", "Japanese", NOW, USERS, LOGINS, LIVE_TOKENS, {})
    assert who == "admin"


def test_presence_slightly_before_the_activity_is_accepted():
    """The page heartbeats before the first save lands — a row a few seconds early is
    the same person, not noise (PRESENCE_ATTR_SLACK)."""
    presence = {"sess_x": [("admin", NOW - 30)]}
    who = actor_for(_sess(), "sess_x", "Japanese", NOW, USERS, LOGINS, LIVE_TOKENS, presence)
    assert who == "admin"
