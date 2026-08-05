# BACKLOG — review-app

Running list of deferred / low-urgency work, ordered by priority. Enough context to
pick each up cold. Move finished items to **Done** (with date) rather than deleting.

Facts below were verified against the code on 2026-07-08 (file:line refs are the evidence).
Deploy model reminder: the **live host is the Ubuntu laptop** (`ssh review-laptop`); backend
code changes need a `systemctl restart review-app.service` in an idle window, FE changes need
`npm run build` on the laptop. **Never restart / write review.db while a reviewer is editing.**

---

## P0 — Waiting on dave (added 2026-07-13)

### 0l. refresh_review_app.py deferred hardening (added 2026-07-30, from the red-opus pass)
Four judgment calls the red-team reported but did not change, all fail-safe today:
(a) `stage9.completed.load_completed()` falls back to the stale workstation
`completed_trips.json` if R2 is unreachable — consider requiring `source() == "r2"`;
(b) bus-says-completed + audit-HANDS-OFF-for-another-reason still uploads a delta
manifest the app ignores until the trip is (re-)completed — warned, dangling object;
(c) a manifest whose stems are all non-reviewable (e.g. `<i>_a`) uploads with a loud
warning instead of being skipped; (d) the fixed 600 s ssh timeout could abort a very
large `warm`. Context: `docs/session-logs/2026-07-30-review-app.md` 23:55 addendum.

### 0k. Jedburgh1_TownAbbey_EN scene 12 staging text carries a literal `REMOVED` marker (added 2026-07-30)
The SceneDesc ends the real outro at "See you then." then continues with `REMOVED` and two
orphaned paragraphs (Jethart Hand Ba'; Romanesque arches) that belong to other scenes and
exist NOWHERE else in the family — deleting them loses the only copy, so it's a writer's
call. The 2026-07-30 pronunciation-fix clip correctly stops at "See you then.", so the
reviewer sees junk text under audio that doesn't say it (intended: puts the defect in
front of a human). Also: `ValidateTripSceneDesc.py` (Scripts repo) reports the trip CLEAN —
add a rule for `REMOVED`-style editorial markers.

### 0. CEFR partial-audio laptop refresh (added 2026-07-26)
Seven staging-only trips have new review-audio masters on R2:
`Strasbourg3_A12_FR`, `Strasbourg5_A12_FR`, `Girona_A12_ES`,
`Florence3_A12_IT`, `Abbotsford_B1_EN`, `Melrose_B1_EN` and
`Jerez_CascoAntiguo_B1_ES`. Jerez B1 was a full first-time 10-scene Sara set
because no prior local or R2 master existed. It was then rewritten in all ten
scenes to B1 Mid, 2.653, and the complete Sara set was regenerated and uploaded
again. The manifest has been refreshed and remains valid.

**Next:** on the Ubuntu laptop, pull the review-app commit and follow
`docs/reviews/2026-07-26-cefr-partial-audio-refresh.md`: run
`scripts/refresh_trips.py audit`, obey CLEAR/RESEED/HANDS-OFF, then `verify`.
Never clear/reseed a trip with reviewer work or current presence. Abbotsford and
Melrose stay at human review until re-approved.

If the original seven-trip refresh already ran, repeat the guarded audit,
clear/reseed and verify steps for Jerez alone because all ten of its R2 objects
changed again.

**2026-07-26 live check:** the read-only audit returned `CLEAR` for all seven;
none has a session and no reseed is needed. The mutation was postponed because
`french` was actively reviewing `Monaco1_FR` (fresh heartbeat on two checks).
In the next globally idle window, rerun the audit, then `clear`, `warm`, and
`verify` all seven.

**2026-07-26 14:18 BST monitor check:** still deferred: `french` had a fresh
editing heartbeat for `sess_7e2ef0014e33`. No audit or cache mutation was run.

**DONE 2026-07-26 14:24 BST:** the live host was globally idle; all seven audits
were `CLEAR`, then their seed caches were cleared, warmed, and verified. `verify`
exited 0 with all 291 cached MP3s matching R2. No service restart was performed.
Do not repeat this completed refresh unless a later producer upload supplies a new
explicit handoff and a fresh guarded audit permits it.

**2026-07-26 14:32 BST monitor check:** presence remained globally idle (zero
heartbeats in 15 minutes), but the completed refresh was deliberately not repeated.
The desktop automation disable handler was unavailable; treat further monitor runs as
read-only until it can be disabled.

**2026-07-26 14:37 BST monitor check:** the completed 14:24 BST verification remains
authoritative and no refresh was repeated. The read-only SSH presence-query attempt connected
but could not run because the laptop lacks `sqlite3` and its fallback command was not accepted;
no live mutation occurred. Keep this automation read-only until it can be disabled.

**2026-07-26 14:42 BST monitor check:** `french` was again active in
`sess_7e2ef0014e33` within the 15-minute global presence window. No audit or cache operation
ran. The refresh remains complete and any future trigger stays read-only unless a new explicit
producer handoff authorizes it.

**2026-07-26 14:42 BST completion:** the one-shot Codex automation was permanently paused
after the active-reviewer check. No recurring action remains; a later producer upload needs a
new explicitly authorized, guarded refresh task.

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

**🔗 Independent corroboration (2026-07-29):** activating Gate 1's Hant↔Hans check on the live
host (opencc install, see 07-29 log) flagged `sess_67d43aae2c03` sc6 questionOption[1] — Hans
`这颗球是白色的` vs Hant `球是白色的`. That is the **same 这颗 Ted typed into the Simplified box**:
he added it there and never updated the Traditional. So the "added word" diagnosis above is now
confirmed from a second, mechanical direction, and it is Ted's edit — not the draft — that
introduced it. **Reverting the six lines therefore closes the 8 bug reports AND clears a real
Gate-1 approve-blocker at the same time**, which strengthens the case for the revert option.
If he keeps the additions, the Traditional (and zhuyin) must be updated to match or the trip
cannot be approved. Mandarin review is moving Ted → John (see 0h), so this needs a decision
either way before John inherits the trip.

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

### 0i. Hant↔Hans blocks on variant + durative in the SAME field (added 2026-07-29)
**What:** `auto_checks._hant_correspondence` passes a field if EITHER the forward comparison
(`to_traditional(hans) == hant`) or the reverse (`to_simplified` of both) matches. A field
containing **both** a reviewer's own variant AND a durative 着 satisfies neither and still hard-
blocks at approve — e.g. Hans `里面坐着人` / Hant `裏面坐著人`. Forward emits 裡面 (not 裏面) so it
misses; reverse trips on 着/著.
**Why it's parked:** pre-existing (the old reverse-only check blocked this too, plus every
durative), rarer than either alone, and zero live instances in the 2026-07-29 audit of all 56
edited 4-script rows. A real fix means normalising variants (裡/裏, 台/臺, …) before comparing —
a policy call about which Traditional variants are acceptable, shared with the pipeline, not a
patch. Revisit if a reviewer actually hits it.
**Related, also unflagged:** a durative typed as 著 *in the Hans box* is not caught, because 著
is legitimately Simplified in 著名. Character purity can't distinguish them; left to Gate 2 /
the human read.

### 0j. Don't `uv sync` the laptop's Scripts venv (added 2026-07-29)
8 of the 11 packages in the Scripts pyproject's `nlp` extra are absent from the live venv
(`spacy`, `sudachipy`, `sudachidict-core`, `kiwipiepy`, `lemminflect`, `pyspellchecker`,
`wordninja`, `text2num`). That is **correct** — only `jieba`, `pypinyin` and `opencc` are
reachable from the review-app; the rest are pipeline-side morphology. A full `uv sync` on the
live host would pull all of them and could upgrade packages under a running service. If a
Scripts dep is ever needed there, install it **targeted and version-matched to the workstation**,
as opencc was on 2026-07-29.

### 0h. Set John's email (added 2026-07-29)
Reviewer `john` (Mandarin, replaces Ted) was created without an email —
`manage.py set-email --username john --email …` once known, else the activity notifier
and ai_review findings emails silently skip him.

### 0f. ~~Make pipeline re-uploads a supported operation~~ — **DONE 2026-07-23 (`scripts/refresh_trips.py`)**
`audit` (per-trip CLEAR / RESEED / HANDS OFF from sessions + presence + reviewer work +
completed status) · `clear` (guarded: asserts no other trip's cache moved) · `verify`
(cached bytes vs R2 by MD5/ETag, no downloads; treats a clip with an
`review-audio/<cid>/originals/<name>` twin as an expected reviewer correction, not staleness)
· `reseed` (deletes sessions only when the trip is in the named list, status is `in_review`,
no presence in 15 min, and there are no edits/flags/corrected takes) · `run` (audit→clear).
Exercised on the EN-50 and FR-12 batches. **⚠ Claude's permission classifier blocks the
live-DB delete, so dave runs the `reseed` step by hand.** Remaining nice-to-have: fold it
into `manage.py` and teach `verify` to take the producer's changed-scene list so it can
check *only* the clips that were supposed to move.

### 0e. ~~`revert()` ignores `localization_json`~~ — **FIXED + LIVE 2026-07-13 (b3a0d36)**
`revert()` now restores every text surface (`current_text`, `source_text`, the `_ZH` 4-script
block + `working_hans`) and re-mirrors the take to R2. Checking JP/EN found `source_text` was
never reset in ANY language. Verified on the live host against Ted's real Taipei101 scene-1
field. See the session-3 log.

### 1. ~~"Apply suggested fix" button on the Auto-review panel~~ — **DONE + LIVE (confirmed 2026-07-29)**
The "no apply endpoint exists" note below was stale. Verified in the code on 2026-07-29 while
tracing the Gate-2 fix: `POST /sessions/{sid}/auto-review/apply` at `routes_sessions.py:268`
→ `sessions.apply_suggested_fix` (`sessions.py:1612`), with all three designed guards in place
(_ZH only; 409 `fix_unverified` when `suggested_fix_verified is False`; Gate-1 re-run after
applying). FE: `AutoReviewPanel.tsx:110` gates the button on `suggested_fix_verified === true`
and `:140` renders the failed-check badge. The 2026-07-08 branch was evidently merged and
deployed. Original description kept below for context.

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
and has misattributed THREE times (2026-07-07, 2026-07-11, and 2026-08-05 — "toshifumi
started KochiCity_N3_JP" when admin merely opened it; opening seeds all field rows with no
`edited_by`). The 08-05 fix makes the notifier read the **presence table first**
(`actor_for` → `presence_by_session`), which covers every observed case exactly; this
column remains the durable fix for activity with no presence heartbeat (beacon writes, etc.).
**Where:** `backend/app/db.py:41-63` (`field_edits` has no `user_id`; `auth_sessions.user_id`
exists at `db.py:106` as the source). Touches the edit write path + a migration. Needs a restart.

### 3b. ~~English `hasTextChange` compares against `original_text`~~ — **OBSOLETE 2026-08-05**
"Generate from edit" (and with it `hasTextChange`/`spokenLine`) was removed entirely —
dave, 2026-08-05: unreliable one-word candidates combining at the wrong spot. Text edits
are voiced via highlight → Regenerate highlighted / Regenerate All. See the 08-05 log.

### 4. ~~Prune inert Mandarin A/B leftovers~~ — **DONE (confirmed 2026-07-29)**
Verified by grep on 2026-07-29: `ab_audio_path`, `_copy_audio_set` and the `/ab/` route have
**zero occurrences** anywhere in `backend/app/`. Matches CLAUDE.md's note that they were pruned
2026-07-08. Original description kept below for context.

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
- **2026-07-30** — **Zhuyin autosave normalizer** (`a06c02f`, LIVE). After Kaohsiung
  and Taipei101 were approval-blocked by joined syllables / trailing neutral-tone dots,
  `zhuyin_normalize.py` now canonicalizes only a unique whole-field parse with exactly
  one valid syllable per Hans character; ambiguous, invalid, dependency-missing, and
  recursion-limit inputs fail closed for Gate 1. Autosave safely adopts the normalized
  server value without clobbering newer typing. Clean-context red-team found and fixed
  the overlong-run `RecursionError` edge case. Verified 9/9 tests against the real
  1,674-spelling map, Ruff/compile/build green, and deployed to the Ubuntu laptop:
  app+tunnel active, public HTTP 200, both Taipei regressions normalized live.
- **2026-07-29** — **Hant↔Hans false positive on the durative 着** (`82067f0`, LIVE). OpenCC
  doesn't round-trip 着: `s2tw` correctly writes it 著 for Taiwan, but 著 is also valid
  Simplified (著名), so the reverse-only comparison hard-blocked correct text at approve.
  Reported from the dynamic-content side, which hit it in its own copy first. Fixed in both
  copies here (`auto_checks._hant_correspondence`, forward-with-reverse-fallback; and
  `claude_review.verify_fixes`, kept fail-closed). Red-teamed — the pass found a real hole in
  the first cut (a broken `t2s` with a working `s2tw` would have blocked where the old code was
  silent) and it was closed. **Also discovered during deploy:** `opencc` was missing from the
  live venv, so this check and the script-purity check had **never run in production**;
  installed `opencc-python-reimplemented==0.1.7` (see 0j) and both are now active, costing
  exactly one true-positive flag. Fail-silent dep class documented in `backend/requirements.txt`
  + CLAUDE.md. Full detail in the 07-29 session log.
- **2026-07-26** — CEFR partial-audio cache refresh for Strasbourg3_A12_FR, Strasbourg5_A12_FR, Girona_A12_ES, Florence3_A12_IT, Abbotsford_B1_EN, Melrose_B1_EN, and Jerez_CascoAntiguo_B1_ES. Guarded run completed at 14:24 BST: audit was CLEAR for all seven; clear + warm completed; verify exited 0 with 291 cached MP3s matching R2. Do not rerun clear/warm without a new producer handoff.
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
