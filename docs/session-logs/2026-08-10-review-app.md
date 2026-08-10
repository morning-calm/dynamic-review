# 2026-08-10 — review-app

## Gate-2 auto-review: never-ending re-review cycle (French reviewer report)

**Goal:** the French reviewer reported that every re-submit triggers another AI review of
the same texts — "a sort of never ending cycle... I'm guessing it's reviewing my edits
each time." Investigate and fix, with reviewers actively using the app.

**Diagnosis (live DB, `auto_reviews` on the laptop):**
- Every re-submit re-ran Gate 2 over the reviewer's **entire diff** (all edits since the
  session was seeded), and Sonnet's verdicts are nondeterministic — each round flagged a
  slightly different subset, so the answer→re-submit→re-flag cycle rarely converged.
  Reims2_A12_FR on 2026-08-09: 1 warn → 1 warn → 0 warn → 1 warn on identical text; her
  FR sessions had 2–5 reports each.
- The */5 cron had **no overlap lock**: a sweep of ~8 sessions × slow Claude calls outlived
  5 minutes, and the next cron fired concurrently — duplicate reports 43–60 s apart on the
  same sessions (Cassis 11:50:39 + 11:51:30), each ingest deleting-and-recreating findings
  the reviewer had just answered.
- Bonus bug found reading the code: an **errored** report still ran ingest with an empty
  field list, which `DELETE`s every finding for the session — wiping the reviewer's
  answers/notes the admin is owed (would have fired during the 08-04/05 CLI-logout outage
  retries).

**Fix (commit on main):**
- `scripts/claude_review.py` — incremental re-review: each successful report snapshots the
  diff it judged (`auto_reviews.input_json`, migrated in `ensure_table` + `db.py`); later
  runs send **only fields whose text changed since that snapshot** to Claude and carry the
  previous verdicts for the rest. A re-submit with **no text changes writes a carried
  report with NO model call and NO bounce** — the session goes straight to the admin.
  That is the cut-off the reviewer asked about. `--sid` remains a forced full review.
- `scripts/claude_review.py` — non-blocking single-instance lock
  (`backend/autoreview.lock`, flock/msvcrt) so overlapping cron runs bow out.
- `scripts/claude_review.py` — errored reports are recorded but **never ingested** (no
  more findings wipe) and never become the incremental baseline.
- `backend/app/auto_review_ingest.py` — `ingest(..., changed_keys=)`: findings on
  **unchanged** fields carry ANY previous answer (`resolved` included, verdict-mismatch
  proof — the verdict is a verbatim copy anyway); changed fields keep the old strict rule
  (rejected/deferred, exact verdict; resolved = fresh look).

**Verified:** new `backend/tests/test_auto_review_loop.py` (5 tests: cutoff on unchanged
re-submit, only-edited-field re-review + answer carry, resolved-carry, error-no-wipe,
lock exclusivity) — all pass; full backend suite 156/156 pass.

**Deploy:** push to main; the laptop `git pull --ff-only` cron (*/10) picks it up and the
cron runner imports fresh each tick — no uvicorn restart needed (the API doesn't call
`ingest`; the `input_json` column is added by the runner's own migration on first run).
Verified on the laptop after pull (see below).

**Open / low-urgency TODOs:**
- Pre-migration reports have no `input_json`, so each currently-submitted session gets one
  last FULL review before the incremental baseline exists — expected, self-healing.
- A finding on a field the reviewer reverts to its original text disappears with its note
  (pre-existing behavior; the question is moot, but the note is lost).
- Consider surfacing "carried forward" reports distinctly on the Changes page (currently
  visible via the summary suffix only).

**Next steps:** tell the French reviewer the cycle now terminates: answering the AI's
items and re-submitting without further edits goes straight to the admin; only fields she
edits after a review get looked at again.
