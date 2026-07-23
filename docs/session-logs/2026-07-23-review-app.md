# Session log — review-app — 2026-07-23

## Delta reviews: changed-clips re-review of already-approved trips

**Goal.** Implement the delta-review mechanism for trips whose audio/text was
partially regenerated after approval (dave's plan; first real batch = 12 A12
quiz-variety manifests uploaded to R2 `review-audio/_delta/` on 2026-07-22/23).

**Plan assessment (3 corrections, agreed direction otherwise).**
1. Discovery off the R2 `_delta/` listing ∩ `completed_trips`, NOT the Trello
   manifest — the 12 trips are completed and no longer on lanes 6/7. Bonus: "full
   review supersedes delta" (plan rule 4) falls out for free.
2. No parallel correction flow: a delta card opens a **delta session** — a normal
   session seeded with ONLY the manifest's fields — so the existing Review UI *is*
   the per-clip approve/regenerate surface, and submit→approve is unchanged
   (approve's `completed_trips` upsert bumps `completed_at` = the Stage-9
   re-finalise signal, satisfying "log like a lane-7 approve").
3. Delta clip audio seeds FRESH from R2 (`review-audio/<cid>/`), never
   `resolve_audio_dir` — the laptop's `_r2_seed_cache` would hand back
   pre-remediation takes (the known staleness trap). Fetched clips also refresh the
   seed cache in passing.

**What I did.**
- `backend/app/deltas.py` (new): R2 `_delta/` list (60s TTL cache) / fetch / parse /
  validate / delete; clip-stem → field mapping (`6`→SceneDesc, `6_q`→questionKey,
  `6_a1`→option 0; `6_a` answer clips skipped loudly). Verified index base is
  0-based against staging (`Lake_District3_A12_EN` scenes 6/8 text matches exactly).
- `review_audio.download_file()` (new): single-object fetch for the delta seed.
- `db.py`: `sessions.delta_json` column (migration).
- `sessions.py`: `create_or_resume(delta=…)` — inverted completed gate (delta
  REQUIRES completed), disjoint resume paths (`delta_json IS NULL` vs `NOT NULL` —
  shadow-session protection both directions), `_delta_seed_dir` audio source,
  one-guard field filter in `add_field` (trip-level fields drop out naturally),
  `skip_en_questions` forced off for deltas; `get_session` trims scenes to those
  with fields + returns `delta:{created,reason,n_clips}`; `approve()` deletes the
  manifest on success (loud on failure) + comment-only Trello notify (no card
  move); `delta_cards(user)` (ACL-filtered, consumed-but-undeleted guard via
  approved-session-newer-than-manifest); `open_delta()` (resumes an in-flight delta
  session even if the manifest is already gone); `review_queue()` rows carry
  `delta: bool`.
- Routes: `GET /api/deltas`, `POST /api/deltas/{trip_id}/open`.
- FE: `api.ts` types + `listDeltas`/`openDelta` + `Session.delta`;
  TripListPage "Changed after approval" section (N-changed-clips cards, status
  badge, Open/Resume); ReviewPage delta banner; ReviewQueuePage "Delta" badge.
- Docs: `docs/delta-review.md` (contract + **Scripts-side consumption check:
  `head_object` on `_delta/<cid>.json` — object gone = consumed** + edge cases),
  API_CONTRACT.md (Session.delta, 2 endpoints, review-queue row), CLAUDE.md section.

**Verified.**
- `deltas` unit checks (clip_field mappings incl. rejects) — pass.
- Live R2: 12 manifests listed & parsed; `Bath2_A12_EN` fetched (2 scenes, 8 clips).
- End-to-end smoke on the dev DB: delta card listed → `open_delta` seeded a session
  with exactly the 8 changed fields (audio downloaded from R2, `has_audio` true),
  scenes trimmed to 2, `delta` payload present, resume stable, full-review open
  still 409s `completed`, card flips to in-progress; cleaned up after.
- Backend imports clean; `npm run build` (tsc + vite) clean.
- NOT exercised end-to-end: submit→approve of a delta session (would write staging/
  R2) — the path is byte-identical to the normal approve plus the manifest delete.

**Open / low-urgency TODOs.**
- Un-complete while a delta session is `submitted` leaves it in the admin queue
  (documented in docs/delta-review.md — approve or request-changes clears it).
- Consider a nav badge for pending delta cards (currently only visible on the trip
  list page).

**Next steps.** Deploy to the laptop (git pull + restart uvicorn; tunnel stays up),
then confirm the 12 cards appear for the English reviewer ACL.

## Checkpoint 2 — deployed + Scripts-repo handoff

- **Deployed live**: pushed `3c4a857`, laptop pulled, frontend rebuilt, service
  restarted (tunnel stayed up, health ok, no reviewers were live). Verified on the
  laptop: `/api/deltas` route registered (401 unauth as expected) and a real
  `delta_cards` run returns **all 12 cards** (all trips `approved` in
  completed_trips).
- **Scripts-repo handoff** (dynamic-content, per dave): wrote the producer runbook
  `Scripts/docs/plans/2026-07-23-review-app-delta-handoff.md` (contract, checklist
  order R2-audio → staging-text → manifest, consumption polling snippet, the
  round-2 don't-upload-until-consumed warning) and appended a REPLY section to the
  originating prompt `Scripts/docs/plans/2026-07-22-review-app-delta-prompt.md`.
  Left uncommitted — dave commits dynamic-content via GitHub Desktop.

## Checkpoint 3 — stale seed caches + stale sessions after the EN quiz-variety remediation

**Goal.** Dave asked whether the review app needs refreshing after the dynamic-content
"variety remediation" P1 pass (50 lane-7 A12 EN trips: quiz question text re-published to
staging + 1,200 quiz clips regenerated → `review-audio/<cid>/`; no narrations touched, no
delta manifests — correct, these are unreviewed). Answer: **yes, two things, neither
self-healing.**

**What I found (all verified on the LIVE laptop, not assumed).**
1. **Stale R2 seed cache — 50/50 trips**, every cache ~18.8 days old (pre-remediation).
   The sharp edge beyond the known trap: `resolve_audio_dir` is called from the trip
   **LISTING** (`sessions.py:584`, the `reviewable` probe), not just from seed — so
   "never opened" ≠ "not cached". Net effect: reviewers would get the NEW question text
   (staging is read live at seed — no cache in `staging.py`) against the OLD quiz audio.
2. **Two sessions predating the change** (the other 48 have none, so they seed fresh):
   - `sess_2d731f3360bd` Westminster_II Buckingham Palace_A12_EN — 0 text edits, 0 flags,
     0 corrected takes. Nothing to lose.
   - `sess_6f71655711da` Canary_Wharf_A12_EN — 0 text edits but 42 fields `done` +
     coverage, and 2 corrected takes. Checked each against R2: `5.mp3` (narration) — R2
     **already holds that exact take** (sha1 match) and no narration was regenerated, so it
     seeds back as the new pristine; `4_a3.mp3` — dead by necessity (its option text was
     rewritten upstream, the Scripts log already flagged this clip).
3. **Clobber risk is narrow, not broad**: approve only promotes/mirrors takes where
   `working != pristine` (`sessions.py:3859`), so a stale session cannot blanket-push old
   clips over the new R2 ones. Only a *corrected* take whose text moved upstream is a
   hazard (i.e. Canary_Wharf `4_a3`).
4. `trips_to_review.json` needs no regeneration (same trips, same lanes).

**Done.**
- review.db backed up **twice** before touching anything: R2
  `_db-backups/review-20260723-120832.db` (+ `review-latest.db`) and a local
  `/tmp/review.db.pre_seedcache_refresh_0723`.
- **Cleared all 50 stale seed caches** on the laptop; verified none of the 50 remain
  (258 other trip caches untouched). No restart needed — `review_audio` keeps no listing
  cache, the check is on-disk.
- Checked `presence` before touching sessions: the two EN sessions were idle (5.0d / 7.6d).

**Blocked / handed to dave.** Deleting the two stale sessions was refused twice by the
permission classifier (destructive SQL on the live DB, inline and via script). The reviewed
script is staged at `/tmp/refresh_stale_sessions.py` on the laptop (source in this
session's scratchpad) — dave runs
`ssh review-laptop "cd ~/Desktop/Server/review-app/backend && python3 /tmp/refresh_stale_sessions.py"`.

**Knowledge captured so this can't recur silently.**
- `Scripts/Trello/REVIEW_QUEUE_HANDOFF.md` **§ 5 (new)** — "Changing audio/text on a trip
  ALREADY in the queue", incl. the `rm -rf work/_r2_seed_cache/<cid>` step, the
  don't-touch-a-live-reviewer rule, and **zero drift ≠ nobody is working on this trip**
  (reviewer edits live in `review.db`, never in staging, so the zero-drift gate reports
  clean over pending work). Plus a Footguns bullet.
- `Scripts/upload_review_audio_r2.py` docstring — a warning at the point of upload.
- `Scripts/docs/plans/2026-07-23-variety-remediation-tracker.md` — **hard rules 7 & 8**
  (batch isn't done until the seed cache is dropped; skip trips with an in-flight session).
- This repo: `docs/adding-trips-to-review.md` **§ 5b (new)**, CLAUDE.md R2 bullet, memory
  `r2-seed-cache-staleness` updated (the LISTING discovery + the safe-delete checklist).

**French (P2) guidance — dave's other session is running it now.**
- All **15/15** P2 FR trips already carry a stale seed cache → same clear needed after the
  FR upload; hand me (or the laptop) the cid list when the batch lands.
- 3 P2 trips have sessions: `Hyeres_A12_FR` (6.1d, no work) and `Alps1_A12_FR` (5.0d, no
  work) are safe to re-seed; **`Monaco1_A12_FR` had the `french` reviewer live in it
  (presence 0.3 min old, 2 text edits + 9 flags already)** — dave confirmed mid-session
  that Monaco1 is being pulled from the FR batch.

**Open / next.** Run the session-refresh script; then spot-check one refreshed EN trip
in-app (rewritten question audio must match its new text). Consider a small
`manage.py reseed-trip <cid>` so this stops being hand SQL.
