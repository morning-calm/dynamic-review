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

- **Deleted both stale sessions** via a reviewed script (`/tmp/refresh_stale_sessions.py`
  on the laptop; source in this session's scratchpad). Claude's permission classifier
  refused the destructive SQL twice — inline and as a script — so **dave ran it**:
  Canary_Wharf (42 `field_edits`, 32 `audio_versions`) and Westminster_II (51 / 36) plus
  both `work/{sid}` dirs removed. 57 sessions remain, **0 orphaned `field_edits`**.

**Verified (read-only, no session created — `/tmp/verify_refresh.py`).**
Sampled Canary_Wharf s4, Westminster_II s4, York_III s2:
- seed cache absent on all three → the next resolve must re-download from R2;
- 0 sessions on each → the next open seeds from live staging;
- R2 `<i>_q.mp3` LastModified **2026-07-23 11:23–11:34 UTC** (today's regeneration) while
  the narration `<i>.mp3` is still **2026-07-08** — independently confirming the Scripts
  claim that no narrations were touched;
- staging carries the REWRITTEN questions, and York_III s2's staging text
  ("Where does this old bridge go?") matches the regen log's `ok 2_q.mp3` line **verbatim**
  — i.e. text and audio are the same content, which is the whole point of the refresh.

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

**Open / next.**
- The 50 EN trips are fully refreshed — nothing outstanding there. A human listen in the
  app is still the real backstop (the checks above prove the bytes and text line up, not
  that the voice is right).
- FR (P2) lands next: all 15 already carry a stale seed cache and **none is completed**
  (so no delta manifests). Sessions on `Hyeres_A12_FR` + `Alps1_A12_FR` (both idle, zero
  reviewer work) will need the same re-seed; `Monaco1_A12_FR` was pulled from the batch
  because its reviewer was live in it.
- BACKLOG P1 0f: `manage.py reseed-trip <cid>` so this stops being hand SQL on a live DB
  (with a presence guard + a corrected-takes report) — FR/ES/ZH/IT batches are queued
  behind this same procedure.

## Checkpoint 4 — FR (P2) batch refresh

**Scope correction from dave:** the batch changed **12** cids, not the 15 on the P2
worklist. `Monaco1_A12_FR` was pulled (translator live in it) and `Strasbourg5_A12_FR` +
`Le_Malzieu-Ville_A12_FR` were deliberate keep-ones — **no staging write, no audio**, so
nothing to clear on their account. Changed scenes are questions only (`{i}_q`, `{i}_a`,
`{i}_a1..a3`); no SceneDesc, narration, overlays or structure.

**Pre-flight audit** (`/tmp/fr_audit.py`, read-only) — all 12 carried a stale seed cache
(5.6–6.1 d old, i.e. pre-batch). Only 2 had sessions: `Alps1_A12_FR` (sess_ff83087a30cf,
idle 5.0 d) and `Hyeres_A12_FR` (sess_83bc5803982b, idle 6.1 d), **both with 0 text edits,
0 flags, 0 corrected takes**. Nobody was live in any of the 12. The only presence on the
host was `french` in `Monaco1_A12_FR` (3.2 min old) — which holds **5 text edits, 39 flags
and 4 corrected takes** (`6.mp3`, `8.mp3`, `9.mp3`, `10_a2.mp3`). Pulling Monaco1 from the
batch protected real work.

**Done.**
- review.db backed up first: R2 `_db-backups/review-20260723-122522.db` +
  `/tmp/review.db.pre_fr_refresh_0723`.
- **Cleared the 12 seed caches** (`/tmp/fr_clear_caches.py`), which asserts afterwards that
  each of the 12 is gone AND that the 3 untouched trips still hold theirs. Guard passed:
  Monaco1 35 files, Strasbourg5 29, Le_Malzieu-Ville 36 — intact. 246 caches remain.
- **Re-seeded the 2 sessions** (`/tmp/fr_reseed_sessions.py`, run by dave — the classifier
  blocks live-DB deletes for me). It re-checks every guard before writing anything (trip
  must be in the changed-12, no presence in 15 min, no edits/flags/corrected takes) and
  aborts otherwise, so it cannot touch Monaco1 by mistake. Both guard sets passed; Alps1
  (74 `field_edits`, 47 `audio_versions`) and Hyeres (36 / 22) removed with their work
  dirs. **55 sessions remain, 0 orphaned `field_edits`, Monaco1 session intact.**

**Verified — content side** (`/tmp/fr_verify.py`, read-only; checked rather than trusted):
- all 12 staging docs `update_time` **2026-07-23 12:07–12:08 UTC**; every listed scene's
  `{i}_q`/`{i}_a1` on R2 **12:14–12:15 UTC today**;
- every one of those scenes' **narration `{i}.mp3` still 07-17** — independently confirms
  the questions-only claim;
- the 3 untouched trips show staging `update_time` **07-16/07-17** — no write from this
  batch — caches intact, Monaco1's session intact;
- all 16 rewritten questions are distinct across the batch (no new shared template).

**Verified — the refresh actually took.** Minutes after the clear, all 12 caches were
**repopulated** (`cache present True` again) — the LISTING behaviour, exactly as
documented, and the first time we've watched it happen. So the meaningful check isn't
"is the cache gone" but "does it hold the new bytes": for all 16 changed scenes the cached
`{i}_q.mp3` is **byte-identical (sha1) to the object on R2 right now**. All 12 trips show
0 sessions, so the next open seeds fresh text + this audio. That is the refresh proven
end-to-end rather than assumed.

**Observation for the ES/ZH/IT batches (not a defect):** `Alps1_A12_FR` s15 is now
«Où sommes-nous ?» — unique here, but it's the one frame in the batch that could become a
new template if reused. Worth keeping off the reusable-frame list. Scripts side's call.

**Note for the reseed tooling (BACKLOG 0f):** "clear the cache" is not a durable state —
it refills on the next list. The command should therefore be *clear + verify against R2*,
not just `rm -rf`, and should be run AFTER the producer's upload completes, never before.

**Reviewer-side confirmation.** Dave's spot-check opened a NEW Hyeres session
(`sess_baba7e726312`) which seeded s1 «Où allons-nous après le château ?» and s5 «Où est
cette église ?» with their options — the rewritten staging text, against the freshly-cached
audio. The loop is proven from the app's side, not just from R2/Firestore.

## Checkpoint 5 — P3 (Spanish) pre-flight: clear to proceed

Dave asked whether admin/`spanish` logins on Spanish trips block the P3 batch
(`/tmp/es_preflight.py`, read-only, 16 P3 cids):
- **14/16 have no session.**
- **2 have sessions** — `Ainsa_A12_ES` (sess_f3013b23b6dc) and `Besalu_A12_ES`
  (sess_0c6ac6bd945f) — `edited_by` **admin only**, 0 text edits, 0 flags, 0 corrected
  takes; coverage rows only (43 / 76 fields played). Opened and listened to, not worked on.
- **The `spanish` reviewer has never edited a field** and has submitted/approved nothing
  anywhere — checked globally, not just on these 16.
- **None is in `completed_trips`** → no delta manifests (rule 6), plain full-review path.
- All 16 are cached (17–61 files) → all need the clear after the ES upload; Ainsa + Besalu
  also need the re-seed.
- Live at the time: `french` in Monaco1 (0.3 min) and admin in Hyeres + Hyde_Park — none in
  the P3 set.

**Verdict: clear to start P3.** Same procedure as FR: upload → hand over the changed cids →
clear + verify bytes → re-seed the 2 sessions.

## Checkpoint 6 — making the procedure survive a fresh context

Dave asked whether memory/docs are good enough for a cold session. Audited them; found
**four gaps** and closed all four:
1. The proven tooling lived in `/tmp` on the laptop — **wiped on reboot**. Now committed as
   **`scripts/refresh_trips.py`** (`audit|clear|verify|reseed|run`), consolidating the four
   one-off scripts. `audit` gives a per-trip **CLEAR / RESEED / HANDS OFF** verdict from
   sessions + presence + reviewer work + completed status; `reseed` deletes only when the
   trip is in the named list, status is `in_review`, no presence in 15 min, and there are no
   edits/flags/corrected takes. Verified on the live host: audit reproduced the ES findings
   exactly (14/2/0), `reseed --dry-run` cleared Ainsa+Besalu, and pointing it at
   `Monaco1_A12_FR` **aborted** with "has french LIVE in it; holds work (edits=7 flags=50
   takes=4)".
2. The memory file predated the FR batch — no refill/verify knowledge. Rewritten.
3. The MEMORY.md index hook still said "for an already-opened trip", the exact wrong mental
   model. Replaced.
4. `docs/adding-trips-to-review.md` § 5b never mentioned the refill, the ordering rule, or
   verification. Added, with the command sequence.

**Bug found by the new tool, in the tool.** `verify` flagged 4 MISMATCHes on
`Monaco1_A12_FR` — which turned out to be precisely the french reviewer's corrected takes
(`6/8/9.mp3`, `10_a2.mp3`, written 12:11–12:18 UTC while they worked). R2 legitimately
holds a *newer* take than the seed cache there. Confirmed the discriminator is the
documented `review-audio/<cid>/originals/<name>` archive key: present on all 4 corrected
clips, absent on uncorrected `7.mp3`. `verify` now checks that marker and reports such
clips as expected. Without the fix the tool would cry "refresh did NOT take" on every trip
a reviewer has ever corrected.

CLAUDE.md, `REVIEW_QUEUE_HANDOFF.md` § 5 and BACKLOG 0f (now closed) all point at the
script; the producer's deliverable is just a list of changed cids, one per line.

## Checkpoint 7 — ES (P3) batch refresh

Handoff: `Scripts/docs/plans/2026-07-23-review-app-seed-clear-es.md` — **13 changed cids**
(not the 16 on the worklist; `Cadaques`, `Sevilla2`, `Zaragoza3` keep their deliberate
surviving instance and got no write). 14 MCQ rewrites, 5 clips per changed scene, 70 clips.

**Tool gained two things first** (the gap BACKLOG 0f left open):
- `warm` — re-pulls the cache from R2 immediately instead of waiting for a lazy trip-list
  load, so `verify` can run at once.
- `verify --changed <file>` — takes the producer's `cid: 4,7` scene list and asserts the
  claim that matters: each changed scene's quiz clip on R2 is NEWER than that trip's
  narration, and the narration itself didn't move.

**Done** (backup first: `_db-backups/review-…db`): audit → clear (13/13, 295 other caches
untouched) → warm (13/13) → verify.
- **All 13 trips: every cached mp3 matches R2** (MD5/ETag).
- **All 14 changed scenes pass**: quiz clips 2026-07-23 **13:05 UTC**, narration still
  **07-16/07-17**. Questions-only confirmed for the third batch running.

**⚠ Stopped short of the re-seed, as the handoff instructed.** It said "no reviewer
sessions expected … if `review.db` does show an ES session, stop and say so". Two do:

    [RESEED] Ainsa_A12_ES   sess_f3013b23b6dc in_review idle 6.2d | edits 0 flags 0 coverage 43 | edited_by ['admin']
    [RESEED] Besalu_A12_ES  sess_0c6ac6bd945f in_review idle 5.6d | edits 0 flags 0 coverage 76 | edited_by ['admin']

These are **dave's own admin look-arounds**, which he described earlier in this session
("I may have opened some Spanish trips as admin… no actual spanish translator has
reviewed these"), and both hold **zero work product** — the `spanish` reviewer has still
never edited a field or submitted anything anywhere. So the handoff's *premise* (no
translator work to protect) holds; only its expectation of zero sessions was off. Re-seeding
them is safe but it is dave's call and dave's command.

## Session close

- Commits `a6c5ee7`, `ab3aa11`, `00cbd6d`, `6858b68` **pushed** to `origin/main`.
- Scripts-repo edits left uncommitted for dave's GitHub Desktop:
  `Trello/REVIEW_QUEUE_HANDOFF.md` § 5 (+ Footguns bullet),
  `upload_review_audio_r2.py` docstring, tracker hard rules 7 & 8.
- The `WORKING_*.py` cleanup reminder is dave's other session's call — deliberately not
  actioned here.
