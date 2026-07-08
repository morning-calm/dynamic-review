# BACKLOG — review-app

Running list of deferred / low-urgency work, ordered by priority. Enough context to
pick each up cold. Move finished items to **Done** (with date) rather than deleting.

Facts below were verified against the code on 2026-07-09 (file:line refs are the evidence).
Deploy model reminder: the **live host is the Ubuntu laptop** (`ssh review-laptop`); backend
code changes need a `systemctl restart review-app.service` in an idle window, FE changes need
`npm run build` on the laptop. **Never restart / write review.db while a reviewer is editing.**

---

## P1 — Do next (high value, self-contained, no product decision)

### 1. "Apply suggested fix" button on the Auto-review panel
**What:** a button next to each machine-verified suggested fix that writes the fix through the
normal localization update path, instead of the reviewer retyping it.
**Why:** Gate-2 already produces suggested fixes and post-verifies zh ones with `hsk_lib`
(`scripts/claude_review.py:164 verify_fixes` → `suggested_fix_verified`). The panel renders them
read-only today — applying is pure plumbing of an already-computed, already-verified value.
**Where:**
- Fix object shape (`claude_review.py:120-127`): `{scene, field, option, verdict, reasons,
  suggested_fix:{Hans?/Hant?/zhuyin?/en?}|null, suggested_fix_verified:true|false|null}`.
- Stored in `auto_reviews.report_json` (`backend/app/db.py:173-186`).
- Rendered at `frontend/src/pages/ChangesSummaryPage.tsx:378-443` (badge + reasons + "Suggested
  fix" block with the machine-verified / FAILED-verification label). **No apply endpoint exists.**
- Proposal specs the intended path (`docs/auto-review-proposal.md:100-101,116-118`): apply goes
  through the existing `update_localization` path.
**Guards:** only offer Apply when `suggested_fix_verified === true`; never for `false`/`null`.
Re-run Gate-1 (`auto_checks.run_checks`) after applying so a fix can't introduce a new blocker.
**Deploy:** new BE endpoint → restart; FE button → rebuild. Batch with P2/P3 into one idle window.

### 2. R2 manifest coverage reconciliation (reframed — the hooks already exist)
**What:** make sure every trip in `trips_to_review.json` has its mp3s in `review-audio/`, and
close the gap automatically.
**Why (corrected):** the staged runners DO push to R2 already — `run_levels.py:252` (CEFR EN,
in-process), `run_n4.py:40`/`run_n5.py:114` (JP), `run_5a.py:217` (HSK). The real failure on
2026-07-08 was that trips can enter the review **manifest** without going through a fresh staged
run (KP-delivered MP3s, re-generated audio, older trips), so R2 simply had no objects for the
125 that joined after the last bulk upload. So this is a **coverage** problem, not a missing hook.
**Options (pick one):**
- (a) Have `Trello/export_review_trips.py` (which already writes the manifest) also emit an R2
  coverage audit — list manifest trips with zero `review-audio/<cid>/` objects — so the gap is
  visible every export instead of surfacing as a reviewer "no audio" badge.
- (b) A scheduled `upload_review_audio_r2.py --manifest --skip-existing` (laptop/workstation cron)
  that fills gaps automatically. Bulk upload already supports `--manifest`.
**Interim rule (already in force):** after generating audio for a queued trip outside the staged
runners, run `py -3.12 upload_review_audio_r2.py --manifest`.

---

## P2 — Correctness / cleanup (batch into the same restart)

### 3. Stamp `user_id` on `field_edits`
**What:** add a `user_id` column, stamp it at edit time from the request's auth session.
**Why:** start/break attribution is currently inferred from login watermarks + token liveness
and has misattributed twice (both patched reactively — see 2026-07-08 log). An explicit column
makes it exact and retires that bug class.
**Where:** `backend/app/db.py:41-63` (`field_edits` has no `user_id`; `auth_sessions.user_id`
exists at `db.py:106` as the source). Touches the edit write path + a migration. Needs a restart.

### 4. Prune inert Mandarin A/B leftovers
**What:** delete the dead V2/V3 A/B audition code (retired 2026-07-02, V3-only).
**Where (all confirmed zero real callers):** route `GET /audio/{sid}/{fid}/ab/{ver}`
(`backend/app/routes_audio.py:81-84`) → `sessions.ab_audio_path` (`sessions.py:3275-3283`) →
`_ab_dir` (`sessions.py:329-332`); `_copy_audio_set` (`sessions.py:335-343`) has zero callers
anywhere; no FE references. Already flagged in `CLAUDE.md:258-259`. Pure deletion, restart to load.

---

## P3 — Deeper features (needs design or accumulated data)

### 5. Deterministic HSK-level Gate-1 check (+ JP Gate-1 checks)
**What:** add a vocab-level check to Gate-1 so above-band words block/warn deterministically
(the shadow reviewer keeps flagging 老旧/铁轨/etc. — Gate-1 doesn't catch level today).
**Why it's real work:** `backend/app/auto_checks.py` does script purity / Hant↔Hans / zhuyin
alignment / format only — no level check (it's a Phase-1 TODO `level_vocab_check` at
`docs/auto-review-proposal.md:84-85`). The Scripts-side logic to reuse is
`Research and Writing/HSK Mandarin/stages/level_check.py` (`run()` @225, `load_vocab()` @67,
`load_proper_nouns()` @97) — a "mechanical level GUIDE, never a hard gate". Reusing its
vocab-lookup in-process (or porting the band tables) is a genuine new integration. JP Gate-1
checks are greenfield.

### 6. Mobile deeper work — **needs a product decision first**
Touch-first splice-selection UX **or** explicitly declare splice desktop/tablet-only; sticky
mini-player; collapse the 17-button `RegenerateControls` row. Quick wins already shipped
(2026-07-08), so phones aren't broken — this is enhancement, blocked on the selection-UX call.

---

## P4 — Deferred / gated

### 7. Phase 3 auto-approve — **DEFERRED, needs Dave's explicit go + shadow-report track record**
Not to be built until we have results: enough shadow verdicts agreeing with Dave's manual
decisions to trust it. Design exists (`docs/auto-review-proposal.md:106-113`): opt-in config flag
**`AUTO_APPROVE_CLEAN=1`**; a session with clean Gate-1 AND an all-`ok` Claude report auto-approves
via the existing `approve()` path with `approved_by='auto'`. Flag not implemented anywhere yet.
When built: shadow/dry-run mode + kill-switch + per-language rollout before it writes for real.

### 8. Laptop `systemctl daemon-reload`
Unit file changed on disk (warned 2026-07-08); restart works regardless. 10-second interactive
chore next time SSH'd in (password-gated).

---

## Done
_(none yet — move items here with completion date)_
