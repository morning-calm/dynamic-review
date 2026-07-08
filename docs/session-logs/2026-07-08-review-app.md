# Session log — review-app — 2026-07-08

## Goals
1. Verify the KaohsiungLotusPond_HSK3_ZH approval (screenshot showed `hsk_lib unavailable
   (No module named 'jieba') — pinyin not regenerated` warnings ×7).
2. Mobile-friendliness review of the frontend (proposal only; no deploy while someone may
   be editing).
3. Rework the activity notifier: immediate emails on reviewer login / start / finish (no
   1-hour batching), and fix the "Admin was editing" misattribution when Ted logs in.

## What I did

### 1. Kaohsiung approval verification + staging REPAIR (applied)
- **Root cause:** the approve ran on the laptop, whose backend venv
  (`Desktop/Server/Scripts/.venv`) lacks `jieba`/`pypinyin`. `_zh_regen_pinyin` degraded to
  `''` and `zh_writeback` proceeded anyway → all 7 edited fields (scenes 7/13/17/19/24
  SceneDesc; 7/17 questionKey) were written **hanzi-only** to `Trips.quickTrips` (pinyin
  display line stripped) and `TripLocalizations …target.pinyin=''`. Verified live: unedited
  scenes had 2 lines, edited ones 1.
- **Repaired staging** with new one-off `scripts/repair_kaohsiung_pinyin.py` (dry-run then
  `--apply` from the workstation, which has jieba): regenerated pinyin from the confirmed
  zhuyin (to_pinyin(Hans) fallback on minor align warnings — the designed degradation),
  restored the 2-line Trip values + loc pinyin. Re-verified all 7 fields: **fixed**.
  Guard in the script: never touches a Trip line that diverged from Hans.
- **Hardened `zh_writeback`** (backend/app/sessions.py): approve now **409s
  (`pinyin_regen_failed`)** when regenerated pinyin comes back empty on a phonetics-bearing
  field, instead of silently stripping pinyin. dry_run still returns the full plan.
- **Laptop venv FIXED (dave approved):** `python -m ensurepip` (the venv had no pip!) then
  `python -m pip install jieba pypinyin` into
  `/home/dynamic-languages/Desktop/Server/Scripts/.venv`; verified `hsk_lib.zhuyin_to_pinyin`
  works end-to-end there. Import is lazy per-writeback → no backend restart needed.

### 2. Mobile UI review + QUICK WINS IMPLEMENTED (commit 1523a46, pushed)
Dave chose "quick wins first; splice editing stays desktop/tablet". Implemented (sm: variants
keep desktop identical): NavBar/UserMenu + trip-list/review-queue rows wrap at phone widths;
all MODAL_STYLEs got maxHeight 85vh + overflow auto; reviewer-typed textareas 16px on phones
(stops iOS zoom-on-focus); RegenerateControls/FlagControl touch-sized tap targets.
`npm run build` (tsc -b + vite) green. NOT live yet — the laptop serves built dist; needs
`npm run build` + restart there in a confirmed-idle window.

Original review (analysis, agent-produced):
Full report delivered in-conversation. Headlines: only ONE responsive breakpoint class in
the whole app (BugReportsPage); NavBar/UserMenu and trip-list rows overflow at 375px
(non-wrapping flex + shrink-0); 4 modal styles missing `maxHeight/overflow` (buttons
unreachable with keyboard open — ManualEditModal already has the fix to copy); sub-16px
inputs trigger iOS zoom-on-focus; tap targets ~mouse-sized; **selection-driven splice tools
are mouse-shaped** — needs a product decision: touch-first selection UX vs phone=listen/
flag/comment only. Quick wins are all Tailwind-class-level.

### 3. Activity notifier rework (commit c2e3005, PUSHED — dave approved)
- New **login events**: exact, from `auth_sessions` rows past a stored watermark
  (`login_watermark`), reviewer-role only (admin logins skipped). Silent migration on first
  run (no historical blast).
- **login/start/finish send IMMEDIATELY** (bypass the 1/hour gate); breaks stay batched.
  Daily cap raised 10 → 40 as backstop.
- **Misattribution fix:** starts/resumes attribute at `last_ts` (recent activity), not
  `first_ts` — an admin-seeded session picked up by ted now reads "ted".
- Updated `docs/activity-notifier.md` (incl. that it now runs on the LAPTOP via cron */15).
- Notifier deploys itself on push: laptop cron `git pull --ff-only` every 10 min; script is
  re-read per cron run → **no restart needed** for the notifier. The sessions.py guard DOES
  need a uvicorn restart (do when no one is editing).

## Verified
- Staging repair: re-fetched both docs after `--apply` — all 7 fields correct (2-line Trip
  values, populated pinyin). Untouched fields (loc questionKeys 13/19/24, never had pinyin)
  left alone.
- Notifier: `py_compile` + synthetic test (scratch DB copy): login event detected once
  (watermark advances, no dup), immediate bypass works with hourly gate active, break-only
  batch still gated, subject/body render. ALL PASS.
- sessions.py: py_compile OK. (Guard not exercised end-to-end — would need a broken-env
  approve; logic is a pre-APPLY check on the already-computed plan.)

## Deployed (same day, dave's go-ahead; last edit was 13.5h earlier — no one editing)
- Laptop notifier cron */15 → ***/5** (login emails now land within 5 min).
- `npm run build` on the laptop (new bundle `index-Clr5iE46.js` confirmed served) +
  `sudo -n /usr/bin/systemctl restart review-app.service`.
- **Data-safety verified before AND after restart** (review state is all in review.db +
  work/ on disk): sessions {approved:2, in_review:8, submitted:3}, 806 field_edits,
  19 live auth tokens — identical after; reviewers stay logged in. cloudflared untouched
  and running; local API answers (401 unauth as expected).
- Note: systemd warned "unit file changed on disk — run daemon-reload"; restart still used
  the old unit definition. Harmless now; run a daemon-reload at some point.

## Afternoon session — Ted's sibling-field audit + corrections + UI warning
Dave asked: analyse ALL of Ted's submitted ZH content for the Hans-changed-but-siblings-not
pattern, fix everything, write it up for Ted, add a soft UI warning.
- **Audit:** the Kaohsiung pattern held everywhere — 34 changed fields across 4 trips
  (Kaohsiung approved; Taichung_HSK3 / Taipei101_HSK3 / Taichung_HSK12 submitted, sess ids
  0ef71bcd/db4ac31f/16e3026e), ALL with only Hans edited. Also: traditional chars mixed into
  Hans (舊/颱風/損/現場/飲料/賣/進 — same pronunciation, audio unaffected), one en typo by
  Ted ("moves to to"), 4 fields where en needed a meaning update.
- **Corrections:** built full Hant/zhuyin(+Hans normalization+en) table
  (scratchpad corrections.json), machine-verified all 34 (to_simplified(Hant)==Hans exact;
  zhuyin_to_pinyin full-confirm vs new Hans — same check as the approve). Applied:
  Kaohsiung → staging directly (loc target.Hant/zhuyin all 7; s17 Hans typo 走進→走进 incl.
  Trip line-1; s19 en incl. Trip SceneDescEn); 27 pending-session fields → laptop review.db
  localization_json cur (fresh R2 backup taken first; 843 min idle). Spot-checks pass.
- **Write-up for Ted:** `docs/ted-zh-sibling-corrections-2026-07-08.md` — dave's email text +
  every correction listed per trip/scene, ready to email.
- **UI warning (soft, non-blocking; dave + AI agree not a blocker):**
  LocalizationEditor — amber hint when some of Hans/Hant/zhuyin/en changed and others didn't;
  EditableField — hint when target text changed but the English sibling (source_text) wasn't
  updated. Build green; deployed via dist rebuild (static serve — no restart needed).

## Evening session — auto-review Phase 1+2 SHIPPED (dave approved the proposal)
- **Gate 1** `backend/app/auto_checks.py` wired into `sessions.validate` (rides submit/
  approve/FE validation): zh script purity, Hant↔Hans correspondence, zhuyin alignment
  (all hsk_lib, lazy, degrade-to-warn) BLOCK; partial-sibling-edit warn; en-not-updated
  note; format checks. Validated: pre-correction data → 61 blocks matching the hand audit
  exactly; corrected data → 0 blocks (note-level only); EN/JP sessions → clean, no noise.
  (Test quirk: scp'd review.db WITHOUT -wal → stale copy = accidental pre-correction
  regression fixture. Copy db+wal or use .backup next time.)
- **Gate 2** `scripts/claude_review.py` (SHADOW: reports only, never mutates): submitted
  sessions w/o fresh report → diff JSON → headless `claude -p --model sonnet` (CLI verified
  working on the laptop, ~$0.06-0.15/review, 50-130s) → per-field verdicts (meaning/quality/
  level/Q&A) → zh suggested fixes post-verified with hsk_lib → auto_reviews row. Fail-open.
  Laptop cron ***/5** added (logs backend/autoreview.log). Model/effort via env
  REVIEW_CLAUDE_MODEL (default sonnet, default effort).
- **First 3 shadow reports generated**: Taichung_HSK3 0ok/11warn (老旧 above-HSK3 + en
  doesn't carry "worn-out" nuance — legit), Taipei101 2ok/4warn/2needs-human (攀巖 variant,
  scene-9 commas, one likely-mistaken "Hant unchanged" flag on scene 4 — shadow-mode noise,
  acceptable), Taichung_HSK12 7ok/1warn (铁轨 above HSK1-2). Verdict quality: genuinely
  useful, errs cautious.
- **Surfacing**: auto_reviews table (db.py); GET /sessions/{sid}/auto-checks +
  /auto-review; ChangesSummaryPage "Auto-review" panel (chip + flagged fields + suggested
  fixes w/ verification badge); notifier immediate `auto_review` events (dry-run verified —
  first verdicts email goes out next cron tick).
- Deployed: pull + dist rebuild + service restart (idle 892 min). All verified live.

## Late follow-ups (dave's questions, commit 611d260, deployed)
- **Gate-1 blocking softened per dave:** submit NEVER hard-blocks — Gate-1 blockers show as
  "[will block approval]" warnings; approve keeps them hard (staging protection).
  validate() gained mode="submit"|"approve". Punct-only Hant/Hans diff = warn not block
  (scene-24 regression test). Word-level mismatch still blocks at approve.
- **Runner limit-awareness (real bug found by dave's question):** an error report row used
  to permanently silence a session. Now: usage-limit failure → NO row, hourly backoff via
  backend/autoreview_state.json (gitignored), session stays queued and auto-reviews when
  the subscription limit resets; other errors → one error row, 6h retry.
- **Mobile:** TripListPage action group (Lane/Status/Pin/Mark complete/Open-Resume) now
  wraps on phones — Resume/Open were clipped off-screen at 375px (dave's screenshot).

## Evening 2 — audio availability + attribution + retirements (Ted LIVE, no restarts)
- **Rule in force: Ted editing → NO backend restart / review.db writes.** All fixes below
  are restart-free (notifier = separate process via git pull; manifest = re-read per
  request; R2 = data-side).
- **"admin started Taipei101_HSK12_ZH" (13:12) misattribution #2:** Ted holds a days-old
  persistent token → no fresh login row → the 12h specialist-login rule missed → fell to
  most-recent-login (admin). Fixed: specialist wins when they hold a VALID token at the
  activity time (`live_tokens_by_user` on auth_sessions.expires_at). Pushed, self-deployed.
- **"no local audio" (Taiwan EN, Tokyo_06-10 EN/JP) root cause:** the backend ALREADY has
  an R2 seed-cache fallback in resolve_audio_dir (migration Phase 2) and `reviewable`
  includes it — but those trips joined the manifest AFTER the last bulk R2 upload, so
  `review-audio/` simply had no objects for them. The laptop's Audio Generation tree is
  ~empty (1 MB) by design; R2 is the transport. **Fix: re-ran
  `upload_review_audio_r2.py --manifest` → 125 trips / 6,208 files / 0 errors.** Verified
  on the laptop: Taipei101_EN + Tokyo_06_HieShrine (EN & N4_JP) now resolve via
  work/_r2_seed_cache. no_audio leftovers all expected (HSK3 ZH = _voice_test sourced;
  Caerphilly_Castle_B1 not yet generated; Tokyo_08_EN already approved).
  ⚠ OPERATIONAL RULE: when new audio is generated on the workstation for manifest trips,
  re-run the bulk upload (CEFR EN has the stage-5c hook; JP/Taiwan flows have NO hook yet).
- **Retired from review queue:** Canterbury_B1_EN + Lake_District2_B1_EN added to EXCLUDE
  in Trello/export_review_trips.py (committed to dynamic-content) + manifest re-exported/
  pushed (129 trips, both gone). Sticks across future exports.
- (A tar-sync of audio onto the laptop was attempted then found unnecessary — R2 was the
  designed path all along; the auto-mode classifier rightly blocked host writes anyway.)

## Open / carried forward
- **Auto-review Phase 3** (auto-approve clean reports) — NOT enabled; needs dave's shadow-
  mode confidence first. Also possible: "apply suggested fix" button; level_check.py vocab
  reuse for a deterministic HSK-level gate; JP-specific Gate-1 checks.
- **JP/Taiwan audio-generation flows have no R2 upload hook** — new audio for manifest
  trips needs a manual `upload_review_audio_r2.py --manifest` (or add hooks like 5c).
- **Exact start/break attribution** — stamp user_id on field_edits (backend change; next
  restart window).
- Mobile deeper work deferred: touch-first splice selection UX (or explicitly keep splice
  desktop/tablet-only); sticky mini-player; collapsing the 17-button RegenerateControls row.
- systemd `daemon-reload` on the laptop (see note above).
- The `--test`/`--force` flags and MailWizz transport unchanged.

## Next steps
Confirm the first immediate login email when a reviewer next logs in; sanity-check the app
on a phone now that the quick wins are live.
