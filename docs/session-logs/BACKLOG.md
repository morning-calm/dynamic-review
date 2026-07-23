# BACKLOG — review-app

Running list of deferred / low-urgency work, ordered by priority. Enough context to
pick each up cold. Move finished items to **Done** (with date) rather than deleting.

Facts below were verified against the code on 2026-07-08 (file:line refs are the evidence).
Deploy model reminder: the **live host is the Ubuntu laptop** (`ssh review-laptop`); backend
code changes need a `systemctl restart review-app.service` in an idle window, FE changes need
`npm run build` on the laptop. **Never restart / write review.db while a reviewer is editing.**

---

## P0 — Waiting on dave (added 2026-07-13)

### 0a. Ted's 8 open bug reports: ElevenLabs voices 2nd tone as 3rd tone
**What:** reports #7–#14, filed 2026-07-09, ALL still `open`, zero replies. Same defect each
time: 楼 / 球 / 人 / 城市 — rising-tone syllables rendered as dipping tone. Some say the whole word
"can't generate".
**Why it matters:** it is BLOCKING Ted on `Taipei101_HSK12_ZH` (sess_67d43aae2c03) — he has 6
`edit_required` flags on it and has never submitted it.
**🔑 LIKELY CAUSE FOUND 2026-07-13 (session 2):** every one of the 8 reports is on one of the
**six fields Ted himself edited** on 07-09 19:37–19:40, and the mis-voiced word always sits
**directly adjacent to a word he added** (`楼喔`, `这颗球`, `人喔`, `这个城市`). 喔/这颗/这个 are
NOT in the script — he typed them into the Simplified box ("I add another word to make it sound
more natural") and the app voiced exactly that. So this is probably not a generic V3 defect:
**the added particle/measure-word is what breaks the tone of its neighbour.**
**Next:** dave's email (drafted, session-2 log) asks Ted to confirm the ORIGINAL audio says
them correctly. If yes → revert the six lines and all 8 reports die at once. If he wants to KEEP
the additions, we have to chase the TTS. **Then close the 8 reports.**
(Voice sanity-check still valid: `annasu` is female; `yu`/`jason` male.)

### 0b. ~~The 3 already-submitted trips will NOT bounce to Ted~~ — **DONE 2026-07-13**
Re-reviewed all three (`claude_review.py --sid`); all now `ai_review` with findings for Ted:
Taichung_HSK3 (1), Taipei101_HSK3 (1), KaohsiungLotusPond_HSK12 (3). Level noise gone as
predicted. See session-2 log.

### 0c. ~~Nobody has an email address set~~ — **DONE 2026-07-13**
`ted`'s address is set in the live DB, and `app_url` is now in the laptop's
`scripts/notifier_config.json`, so the findings email deep-links to
`https://review.dynamiclanguages.org/review/<sid>` (verified HTTP 200).
**Remaining:** `toshifumi` and `admin` still have no email set.

### 0d. Two design questions dave deferred
- Should a carried-forward `rejected` answer survive a **verdict change** (warning→needs_human
  currently RE-OPENS it)? Chosen behaviour: re-open. Change in `auto_review_ingest.ingest()` (the
  carry key includes `verdict`).
- Is **compound rescue** too lenient at HSK3 (it's what passes 老旧)? Changing it is a
  pipeline-wide policy call affecting the drafts too, not just review.

---

## P1 — Do next (high value, self-contained, no product decision)

### 0f. `manage.py reseed-trip <cid>` — make pipeline re-uploads a supported operation
**What:** one command that (a) `rm -rf work/_r2_seed_cache/<cid>`, (b) refuses if `presence`
shows a live reviewer on that trip, (c) reports any session on the trip with its corrected
takes (`working` ≠ `orig`) and whether R2 already holds each, and (d) with `--reseed` deletes
the session rows (`sessions` + `field_edits` + `audio_versions`) + `work/{sid}`.
**Why:** when dynamic-content re-publishes a queued trip (remediation batches), the app keeps
serving the OLD audio forever — the seed cache never re-downloads, and it's filled by the trip
LISTING, so "never opened" doesn't mean "not cached". On 2026-07-23 this was 50 trips and had
to be done by hand SQL on the live DB against a fresh backup. Producer side is now documented
(`Scripts/Trello/REVIEW_QUEUE_HANDOFF.md` § 5, `docs/adding-trips-to-review.md` § 5b) but the
app side is still improvised. Batches are recurring: FR/ES/ZH/IT remediation passes are queued.
**Size:** small. The logic exists as the one-off `/tmp/refresh_stale_sessions.py` (2026-07-23).

### 0e. ~~`revert()` ignores `localization_json`~~ — **FIXED + LIVE 2026-07-13 (b3a0d36)**
`revert()` now restores every text surface (`current_text`, `source_text`, the `_ZH` 4-script
block + `working_hans`) and re-mirrors the take to R2. Checking JP/EN found `source_text` was
never reset in ANY language. Verified on the live host against Ted's real Taipei101 scene-1
field. See the session-3 log.

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

### 6. S3-ogg last-resort audio fallback (only if the case ever bites)
**What:** when a published trip is opened in the admin editor and its mp3 masters resolve
NOWHERE (local trees gone AND no `review-audio/<cid>/` R2 objects), optionally fall back to
Stage 9's ogg outputs on AWS S3 as seed audio.
**Why parked:** today the backend never reads S3 by design — S3 holds Stage 9's *outputs*
(ogg + subtitles), not the mp3 masters, and splicing a lossy ogg re-encode degrades quality
(`resolve_audio_dir`: local → Audio Generation → R2 seed cache, `sessions.py:236`). The gap
is only trips published long ago that never passed through the review app and whose local
masters are gone → `422 bad_folder` on admin open. Preferred fix when it bites: restore the
masters to R2, not S3-read plumbing. Raised by dave 2026-07-09.

### 7. Mobile deeper work — **mostly DONE 2026-07-10** (decision: full review on mobile)
Shipped (see `2026-07-10-review-app.md`): selection persistence (`useTextSelection` — iOS
blur-collapse fixed, chip UI), grouped `<details>` toolbar on phones + "?" help, wake lock +
coverage beacon flush, remaining 16px inputs, admin-page touch targets. Still open:
- **Real-iPhone verification pass** (checklist in the 07-10 log) before calling it done.
- Sticky mini-player (deferred, unchanged).
- `preload="none"` for working tracks on phones (D3) — only after device check confirms
  duration still resolves on first play.

---

## P4 — Deferred / gated

### 8. Phase 3 auto-approve — **DEFERRED, needs Dave's explicit go + shadow-report track record**
Not to be built until we have results: enough shadow verdicts agreeing with Dave's manual
decisions to trust it. Design exists (`docs/auto-review-proposal.md:106-113`): opt-in config flag
**`AUTO_APPROVE_CLEAN=1`**; a session with clean Gate-1 AND an all-`ok` Claude report auto-approves
via the existing `approve()` path with `approved_by='auto'`. Flag not implemented anywhere yet.
When built: shadow/dry-run mode + kill-switch + per-language rollout before it writes for real.

### 9. Delete the stale `TripLocations/JapaneseTrips` staging doc
A leftover duplicate of `TripLocations/JPHistory` (same displayed name
"Discover_Past_Series", country Japan) whose `trips` list wrongly contains only the
Spanish `Cuevas_Trip` — it would mislabel Cuevas as Japan. The All-trips
location/country index (routes_admin.py `_staging_index`, 2026-07-09) works around it
with a same-name+country stale-duplicate guard; deleting the doc makes the guard
dead weight. One-line Firestore delete, verify Cuevas still maps to Spain after.

### 10. Laptop `systemctl daemon-reload`
Unit file changed on disk (warned 2026-07-08); restart works regardless. 10-second interactive
chore next time SSH'd in (password-gated).

### 11. Deploy the overlay/still R2 fallback backend (#3, 2026-07-10)
R2 mirror + backend fallback shipped for static-360 stills + overlays (app/images_r2.py; served
under `review-overlays/<base>_EN/` in the thumbs bucket). **Upload DONE 2026-07-10** from the
workstation: 423/423 images uploaded across 129 trips (2 absent-on-staging trips skipped:
Caerphilly_Castle_B1_EN, York_I_B2_EN); public URLs verified HTTP 200. **Remaining: deploy the new
backend to the laptop** (git pull + restart uvicorn) so it advertises/redirects to R2 when the
local file is absent — until then the laptop still 404s these images. Mirror-on-serve keeps R2 in
sync for new trips from any host with the files.

### 12. #5 re-seed existing _EN-source sessions (2026-07-10)
Seed now skips English quiz questions for a `_EN` trip that's only the source of a non-English
group. Existing sessions already seeded on such trips keep their question fields until re-seeded
(delete + re-open). Low urgency — only matters for the handful of already-opened affected trips.

### 13. #1 stage-5a description change (dynamic-content, 2026-07-10)
`D:\Dynamic Languages\Scripts\REVIEW_APP_TODO_stage5a_descriptions.md` — stage 5a must write the
advanced description in target + English for ALL levels (identical across levels) so the review-app
TG-description confirm has real data. Review-app side already displays + flags it. dave commits the
Scripts note via GitHub Desktop.

---

## Done
- **2026-07-16** — **Finalised-bus consumer** (f9fad71, LIVE on the laptop). The app now reads
  Stage 9's `review-audio/_bus/finalised_trips.json` (read-only, best-effort) and marks completed
  trips **Published** (shipped, greyed, sunk to bottom of Completed) or **Re-finalise pending**
  (re-approved since shipped). Red-teamed (Opus, no defects). Scripts-side publisher is the
  dynamic-content half; until its first publish the snapshot is empty and behaviour is unchanged.
- **2026-07-13 (session 2)** — **Blank-session incident + status-vocabulary consolidation**
  (af11d9a, LIVE). Opening an `ai_review` trip re-seeded a BLANK session that then shadowed the
  reviewer's real one (2 reached production; no work lost). Root cause was a hand-copied status
  list in TWO places; `backend/app/statuses.py` is now the single enumeration and
  `ACTIVE_STATUSES` is derived, so a status can't be half-added again. Red-teams found two more
  of the same class: `approving` missing from the resume list, and — worse —
  `structure._ACTIVE_STATUSES` missing `ai_review`, which would have let an admin scene
  insert/reorder silently desync a live reviewer's `scene_index`es. Plus: findings un-tick the
  one field they're about (after the CAS, only while the session is still the reviewer's — an
  Opus red-team catch), and the AI's remark + answer buttons now render inline at the field.
- **2026-07-08** — P1 #1 "Apply suggested fix" button (BE `apply_suggested_fix` +
  `POST /sessions/{sid}/auto-review/apply`, FE panel button) and P2 #4 prune of the dead
  Mandarin A/B code. Branch `backlog/apply-fix-and-ab-prune` (not merged/deployed yet —
  BE change needs a restart in an idle window). Verified: py_compile + import + `vite build`
  green; NOT end-to-end driven (needs a live `_ZH` session with an `auto_reviews` row on the
  laptop). #1 and #4 above stay listed until this branch is merged + deployed.

## Workflow-features build (proposal agreed 2026-07-08 — docs/workflow-features-proposal.md)
- [x] Block 1: presence heartbeat (reviewers+admins) + Recall submission + queue pinning/badge — BUILT 2026-07-08 (branch feature/recall-presence-admin-edit; verified 22/22 local e2e; awaiting merge+deploy)
- [x] Block 2: admin inline editing on approve page — BUILT 2026-07-08 (same branch; gate + Edit-inline section + edited_by audit; awaiting merge+deploy)
- [x] Block 3: stage-4b bug intake — BUILT 2026-07-08 except library-app half (dave's dirty video branch) + VR payload (Chris ask). Backend-fns branch feature/scene-scoped-bug-reports; review-app ingest on feature/blocks-3-5
- [x] Block 4 PHASE 1 (search/open) BUILT 2026-07-08 on feature/blocks-3-5. [x] Phase 2 BUILT 2026-07-08 late session (structure.py direct editor, 20/20 live checks vs disposable staging trip). Remaining phase-3 wire-up: build_locstrings recompile hook on approve/publish (blocked on bucket IAM grant) + sceneId-keyed localization (lands with compiler integration)
- [x] Block 5 BUILT 2026-07-08 (feature/blocks-3-5 + Scripts publish_inbox.py be823f2) — verified dry-run-only end-to-end; real publishes remain human-gated (--apply --i-am-sure)
- [ ] request_changes allows status='approving' (pre-existing, flagged by red-team 2026-07-08): same clobber shape as the fixed resolve_recall race — BUT it doubles as the only unstick path if an approve crashes mid-flight (session stranded in 'approving'). Fix needs design, not a blanket 409: e.g. allow from 'approving' only when updated_at is older than ~5 min (a live approve finishes in seconds), else 409 approve_in_progress.
