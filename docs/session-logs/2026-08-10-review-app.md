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

---

## Later session — per-clip V3 override in Fix pronunciation / Create new

**Goal:** dave: the Fix-pronunciation popout needs a per-clip switch to eleven_v3 when
the v2 voice can't say a word right however it's spelled — without the session-wide
Narration-bar Model dance (flip → keep/discard modal → regen → remember to flip back).

**What I did (Option A — per-request override, nothing persisted):**
- `models.Regenerate` / `ClipCreate` / `ClipRegen` gained `model: Optional[str]`;
  routes pass it through. `sessions._validated_model` 422s unknown ids (same contract
  as set_narration). `sessions.regenerate` + `_render_clip` use it for that ONE call —
  the next regenerate is back on the session model.
- `audio_core.v3_voice_settings` sanitizer, applied inside `generate_audio` +
  `_generate_with_timestamps` whenever `model_id == "eleven_v3"`: drops `speed`/`style`,
  snaps off-grid `stability` to 0.5. This also fixes the LATENT bug in the session-wide
  Model dropdown: andrea + every EU voice carry stability 0.75, which v3 rejects — any
  v2→v3 switch on those voices would have 4xxed at ElevenLabs.
- Provenance (dave's detail 3): `plan.meta["model_id"]` is stamped on every candidate;
  `combine` appends "[auto] voiced with eleven_v3 (one-off model override)" to the field
  comment when the candidate's model ≠ the session model, so the admin knows why one
  clip sounds different. Deliberately NOT a new `audio_versions.kind` — the enumerated
  kind set is load-bearing (`_can_accept_text_as_voiced` treats non-{v0_original,splice}
  as hand-made).
- FE: new `narrationContext.ts` (session model+speed, provided by ReviewPage +
  AdminInlineEdit — avoids threading props through SceneCard/AudioFieldBlock memo
  chains). "Voice with the V3 model" checkbox in the WHOLE-BLOCK Fix-pronunciation
  modal (Q&A fields) and in Create new (the narration hand path: v3 take → Save &
  insert → waveform). Offer shows only when `session.model === eleven_multilingual_v2`
  (CJK sessions are v3 already); when checked on a slowed trip (A12 0.7 / B1 0.85) an
  amber warning says v3 ignores speed → full-speed clip (dave's detail 2). Redo
  candidate re-rolls with the same model (`lastRegen.model`).
- Scope decision (per the approved recommendation): the SPLICED Fix-pronunciation
  variant stays on the session model — a v3 phrase inside a v2 take is a model-timbre
  seam; revisit if whole-block v3 quality proves the seam acceptable.

**Verified:** new `backend/tests/test_per_clip_model_override.py` (7 tests: sanitizer
drops/snaps/doesn't-mutate, whole registry survives v3, validated-model pass/422) —
pass; full backend suite 163/163 pass; `tsc -b` clean; `npm run build` clean.

**Open / low-urgency TODOs:**
- ClipRow "Re-voice" (saved Create-new takes) has no V3 checkbox — re-voicing a saved
  v3 take re-renders it on the session model. Draft-stage regeneration covers the flow.
- User guides (`docs/user-guides/`) not yet updated with the V3 checkbox.

**Next steps:** deploy = commit + push both repos' usual path (this repo only), laptop
pull + `npm run build` for `frontend/dist` (REVIEW_APP_SERVE_FRONTEND=1) + uvicorn
restart; dave triggers /red-opus himself if wanted (no auto red-team).

**Follow-up (same session):** both leftovers closed — ClipRow "Re-voice" (saved
Create-new takes) got its own "with V3" checkbox, and the user guides were updated
(admin-guide.en.md §2 tool list + Create-new steps + §3 don't-flip-the-trip-model note;
quick-reference.html new V3 note; user-guides README maintainer note — CJK guides
untouched on purpose, the offer never shows on v3 sessions). Re-verified: backend
163/163, tsc + npm run build clean. Red-team (/red-opus) launched next per dave.

**Red-team (/red-opus) outcome:** clean-context Opus pass found NO correctness bugs in
the feature. Its edits (all verified hunk-by-hunk, behavior-preserving): model-id string
literals centralised into `narrationContext.ts` (`V3_MODEL`/`offersV3`/`v3IgnoresSpeed` —
the enumerated-set discipline); the ReviewPage/AdminInlineEdit `narrationInfo` memos
re-keyed on `[model, speed]` VALUES instead of `[session]` (the object is replaced on
every field update, so the old dep made the memo a no-op — real fix in AdminInlineEdit);
combine's whole-branch note append aligned with the segment branch's non-clobbering form;
the new test file's docstring corrected to what it actually pins. Reported-not-fixed
(agreed, deferred): `gen_speed` passed to `trim_trailing_breath` stays at the session
speed for a V3 take on a slowed trip → trim windows ~1.4x too wide; benign direction
(retained breath, never truncation — letter_end floor holds), pre-dates the feature,
and the thresholds are listening-calibrated. One-liner if ever wanted:
`gen_speed = 1.0 if model_id == "eleven_v3" else _effective_speed(srow)`.

**Flaky test found & fixed (pre-existing, Gate-2 e09eeb1, NOT this feature):**
`test_auto_review_loop.py::test_only_the_edited_field_is_rereviewed` failed ~30% on
Windows — `time.time()` ticks at ~16 ms here, so `_resubmit`'s `updated_at` could land
in the SAME tick as the prior report's `created_at`, and `pending_sessions`' strict
`<` judged the session already-reviewed. Test-only fix: `_resubmit` now sets
`updated_at = max(now, MAX(auto_reviews.created_at) + 1ms)`. 10/10 green after; a live
re-submit can't land within one tick of the cron's write, so no production change.

**Final verify (my own run):** backend import ok, 163/163 tests, npm run build clean,
eslint clean. Not committed — awaiting dave's go-ahead.
