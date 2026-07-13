"""The session-status vocabulary — the ONE place any set of statuses is enumerated.

WHY THIS MODULE EXISTS (dave, 2026-07-13): "which statuses are still live?" was written out
by hand in TWO places — `sessions.create_or_resume`'s resume lookup and
`structure._ACTIVE_STATUSES` — and adding `ai_review` missed the first. Opening a trip that
sat in `ai_review` then re-seeded a BLANK session which, being the newest, permanently
shadowed the reviewer's real one: their edits, flags and findings all looked "reverted".
Two of those blanks reached production before it was caught. A hand-copied status list has
now been wrong twice, so it is derived from one enumeration instead.

ADDING A STATUS: add it to `ALL_STATUSES` (and to TERMINAL/EDITABLE if it belongs there).
Everything that asks "is this session still live?" / "can the reviewer still edit it?"
derives from these tuples, so a new status cannot be half-added again.

Stdlib-only, no imports from this package: `auto_review_ingest` (which db.py imports, and
which the cron runner `scripts/claude_review.py` imports WITHOUT FastAPI/config) needs it.
"""
from __future__ import annotations

# Every value `sessions.status` may hold. There is no DB CHECK constraint — this tuple IS
# the vocabulary.
ALL_STATUSES = ("in_review", "submitted", "approving", "approved", "changes_requested",
                "ai_review")

# Done: the text is in staging and the corrected masters are promoted, so a fresh open
# re-seeds from them rather than resuming. `approved` is the ONLY terminal status.
TERMINAL_STATUSES = ("approved",)

# Still live. Two callers, same meaning: resume this session instead of seeding a new one
# (`sessions.create_or_resume`), and refuse a structural scene edit under it
# (`structure._assert_no_active_session` — insert/remove/reorder desyncs its scene_indexes).
ACTIVE_STATUSES = tuple(s for s in ALL_STATUSES if s not in TERMINAL_STATUSES)

# The REVIEWER may still edit text/audio/flags/narration. `submitted`/`approving` belong to
# the admin (an admin — and only an admin — may still edit while `submitted`; see
# `sessions.assert_editable`), and `approved` is terminal. `changes_requested` and
# `ai_review` are both "handed back to the reviewer": `ai_review` = Gate 2 returned findings
# and they need to edit to action a suggestion (the admin can't approve meanwhile, because
# approve() only claims from `submitted`).
EDITABLE_STATUSES = ("in_review", "changes_requested", "ai_review")
