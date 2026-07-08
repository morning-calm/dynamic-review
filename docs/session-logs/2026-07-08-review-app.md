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

## HANDOFF (session closed 2026-07-08 evening — pick up from here)

### 1. THE ONE BLOCKING STEP: restart the backend in an idle window
Ted was live at session close, so the last backend deploy is PENDING. When no one is
editing (check: last `field_edits.updated_at`, or just ask Ted):
```
ssh review-laptop
cd /home/dynamic-languages/Desktop/Server/review-app && git pull --ff-only   # likely already pulled by cron
sudo -n /usr/bin/systemctl restart review-app.service
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/trips    # expect 401 = up
```
The restart activates (all committed + already pulled, just not loaded by uvicorn):
- Gate-1 auto-checks with the **submit=warn / approve=block** split + punctuation-only demotion
- the `zh_writeback` empty-pinyin **409 guard**
- `GET /api/sessions/{sid}/auto-checks` + `/auto-review` endpoints (the FE Auto-review panel
  on the Changes-summary page shows data only after this restart)
Everything else from today is ALREADY LIVE: notifier (immediate login/start/finish +
auto-review verdicts, token-based attribution, cron */5), Gate-2 shadow reviewer (cron */5),
mobile quick wins incl. the clipped Resume/Open fix (dist was rebuilt BEFORE Ted's session),
R2 audio for the 125 trips, manifest without the 2 retired B1s.
Optional while there: `sudo systemctl daemon-reload` (needs password — unit file changed on
disk; restart works regardless).

### 2. Email Ted
`docs/ted-zh-sibling-corrections-2026-07-08.md` is the ready-to-send write-up (his 34
fields, all corrections listed, your message text at the top). Already delivered in-chat too.

### 3. Watch the shadow reports
First three auto-review verdicts (Taichung_HSK3 0ok/11warn, Taipei101 2ok/4warn/2flag,
Taichung_HSK12 7ok/1warn) — compare against your own judgment when approving. New
submissions get reviewed within ~5 min and emailed. When you trust it, Phase 3
(auto-approve clean reports) is the next build — NOT enabled, needs your explicit go.
Note: approving the 3 submitted ZH trips will now hard-require the sibling consistency
that was already fixed in their sessions — they should pass clean.

### 4. Verify-on-next-occurrence (no action unless wrong)
- Next time Ted logs in/starts/finishes: emails should say **ted** (token-based attribution)
  and arrive within ~5 min.
- Trip list on the laptop: "no local audio" badges should be gone for Taiwan EN +
  Tokyo_06-10 (first load re-resolves via R2 seed cache; first click of each trip may be
  slower while it downloads).
- If a usage-limit period happens: `backend/autoreview.log` should show "usage-limit
  backoff" lines and reviews resume after reset (no error spam).

### Backlog (uncommitted-to, in rough priority)
- Phase 3 auto-approve (config flag design in docs/auto-review-proposal.md §3).
- "Apply suggested fix" button on the Auto-review panel (fixes are already machine-verified).
- Stamp `user_id` on field_edits for exact start/break attribution (backend, restart needed).
- R2 upload hooks for the JP/Taiwan audio flows (until then: re-run
  `py -3.12 upload_review_audio_r2.py --manifest` after generating audio for queued trips).
- Mobile deeper work: touch splice-selection UX (or declare splice desktop/tablet-only),
  sticky mini-player, collapse the 17-button RegenerateControls row.
- level_check.py vocab reuse for a deterministic HSK-level Gate-1 check; JP Gate-1 checks.
- Prune inert Mandarin A/B leftovers (`ab_audio_path` etc., flagged in CLAUDE.md).
- Mobile deeper work deferred: touch-first splice selection UX (or explicitly keep splice
  desktop/tablet-only); sticky mini-player; collapsing the 17-button RegenerateControls row.
- systemd `daemon-reload` on the laptop (see note above).
- The `--test`/`--force` flags and MailWizz transport unchanged.

## Next steps
Confirm the first immediate login email when a reviewer next logs in; sanity-check the app
on a phone now that the quick wins are live.

## Follow-up (later — new session, same day) — THE BLOCKING RESTART DONE
Picked up the handoff's one blocking step: restarted the backend in a confirmed-idle window.
- **Idle verified first:** last `field_edits.updated_at` = 2026-07-08 13:58 (~116 min idle);
  `git pull` already up to date (cron).
- **Restart:** `sudo -n systemctl restart review-app.service` → `/api/trips` 401 (up),
  cloudflared still running (PID 99883, untouched), `/api/sessions/x/auto-checks` now 401
  (endpoint loaded — was the tell it hadn't been live).
- **Data-safety identical pre/post:** sessions {approved 2, in_review 9, submitted 3},
  field_edits 865, live_tokens 19. Reviewers stay logged in.
- **Now live** (was pending on this restart): Gate-1 auto-checks submit=warn/approve=block
  split + punct-only demotion, `zh_writeback` empty-pinyin 409 guard, `/auto-checks` +
  `/auto-review` endpoints (Changes-summary Auto-review panel now shows data).

Remaining open (non-blocking, carried forward): email Ted the sibling-corrections write-up
(`docs/ted-zh-sibling-corrections-2026-07-08.md`); watch the first shadow-report verdicts +
first token-attributed login email; optional laptop `sudo systemctl daemon-reload` (unit
file changed on disk). Plus the standing backlog above (Phase-3 auto-approve, "apply
suggested fix" button, user_id on field_edits, JP/Taiwan R2 upload hooks, mobile deeper work).

## Backlog write-up + two items implemented (branch, not deployed)
Orchestrated with subagents (sonnet to ground the codebase facts; a Fable Phase-3 assessment
was started then stopped — Phase 3 stays deferred per dave until we have shadow-report results).
- **`docs/session-logs/BACKLOG.md` created** (the protocol file didn't exist) — ordered P1–P4,
  each entry grounded in file:line. **Correction the grounding caught:** the session-log claim
  "JP/Taiwan flows have NO R2 hook" is WRONG — `run_n4.py:40`/`run_n5.py:114` (JP) and
  `run_5a.py:217` (HSK) already call `upload_review_audio_r2.py`. The real gap is manifest
  *coverage* (trips can join the review manifest without a fresh staged run), so that backlog
  item was reframed to a coverage/reconciliation task, not "add hooks".
- **Implemented on branch `backlog/apply-fix-and-ab-prune` (commit a3783eb):**
  - P1 "Apply suggested fix" — `POST /sessions/{sid}/auto-review/apply` → `apply_suggested_fix`
    (applies the latest Gate-2 report's machine-verified fix to a `_ZH` field via
    `update_localization`, re-runs Gate-1, returns updated field + fresh Gate-1). `_ZH`-only;
    refuses `suggested_fix_verified=False`; FE button on the Auto-review panel, shown only when
    editable + verified. Note: report exists at `submitted` (locked), so the button acts once the
    admin sends back to `changes_requested`/`in_review`.
  - P2 pruned the inert Mandarin A/B code (route + `ab_audio_path`/`_ab_dir`/`_copy_audio_set`).
- **Verified:** backend py_compile + import (symbol present / A/B gone); `tsc -b && vite build`
  green. **NOT end-to-end driven** — needs a running backend + a `_ZH` session with an
  `auto_reviews` row (that data is on the laptop). **NOT deployed** — BE change needs a restart;
  branch left for review/merge (didn't push to main — cron auto-pulls main to the laptop).

## MERGED + DEPLOYED (idle window, dave's go)
- Merged `backlog/apply-fix-and-ab-prune` → main (no-ff, `d394b63`), pushed.
- Laptop deploy at idle 144 min (last edit 13:58, 19 live tokens): `git pull` → `npm run build`
  (bundle `index-liwrCf-b.js`, identical hash to the workstation build — deterministic) →
  `systemctl restart review-app.service`.
- **Verified:** service `active/running`, `ExecMainStatus=0` (clean boot ⇒ the full router set
  imported, so the new `/auto-review/apply` route loaded and the A/B code is gone); `/api/trips`
  → 401 (up); cloudflared still running (PID 100465); **data identical pre/post** — sessions
  {approved 2, in_review 9, submitted 3}, field_edits 865, live_tokens 19 (reviewers stay in).
  Route table proven locally (apply present / ab gone); an authenticated live click of the Apply
  button still wants a real `_ZH` session with an `auto_reviews` row (do on next such submission).

## Next steps (updated)
Drive the Apply button against a real `_ZH` report when one next has a suggested fix (send the
session back to `changes_requested` so it's editable). Everything else on BACKLOG.md is captured.
Phase 3 remains OFF pending shadow-report results.

---

## 2nd session (evening) — feature planning: recall / presence / admin edit / Stage-9-in-app / 4b bug intake

**Goal:** Dave asked for a proposal (no build yet) covering: reviewer "get my submission back",
admin inline editing on the approve page — which grew to include reviewer+admin presence,
Stage-9→publish managed by the app, a staging/live editor, and surfacing stage-4b VR/web bug
reports next to the SceneDesc.

**What I did**
- Explored (agents): review-app lifecycle/state machine + admin page reusability; library-app +
  dynamic-languages-backend + the Unity VR app for existing bug-report plumbing. Key finds: no
  post-submit reviewer transition and no "admin mid-review" concept exist; editor components are
  readOnly-parameterized (reusable on the approve page); a full bug-report pipeline already
  exists in BOTH client apps → callable `submitReport` → Firestore `UserReports` (+Slack), but
  with NO structured trip/scene fields (VR embeds contentId+scene in a text blob; web sends
  nothing).
- Wrote the agreed proposal: **`docs/workflow-features-proposal.md`** (all decisions inline).
- Decisions locked with Dave: "Recall submission" (submitter-only auto-grant; request+reason if
  admin mid-review/approved; approved grant = un-complete + changes_requested behind a warning);
  presence heartbeats for reviewers AND admins; admin inline edit with no listen gate;
  Stage-9/publish = publisher-mode app on the workstation reading a locally-stored prod key over
  an R2 `review-bus/` job bus, human-clicked publishes with diff, prod state to the laptop via
  R2 snapshots; "edit Live" stays staging→publish (no direct prod writes).

**Verified:** planning session — no code changes, nothing to run.

**Next steps:** build order per the proposal (presence+recall → admin inline edit → 4b bug
intake (backend+web ours; VR = Chris ask) → staging-wide search/non-text editors → job bus +
publisher mode, off-hours, dry-run-only prod path).

## BUILT: Blocks 1+2 of the workflow features (branch feature/recall-presence-admin-edit)
- **Backend:** `presence` + `recall_requests` tables (db.py); heartbeat/presence/recall-state/
  recall/requests/count/resolve endpoints (sessions.py + routes_sessions.py); `assert_editable`
  now admits ADMIN edits at `submitted` (approve-page touch-ups; approving/approved stay locked);
  `field_edits.edited_by` audit stamp via a `db.CURRENT_EDITOR` contextvar set in the auth
  middleware (event-loop context — a sync-dependency set would be lost to the threadpool copy).
- **Frontend:** `usePresence.ts` (heartbeat + poll hooks), `PresenceBadge`, `RecallControl`
  (auto-grant / reason modal / waiting+declined banners); ReviewPage + ChangesSummaryPage
  heartbeat + recall; ChangesSummaryPage gains the admin **Edit inline** section (trip header +
  per-scene SceneCard with the full toolbox, SaveStatusProvider-wrapped) and "edited by" chips;
  ReviewQueuePage pins recall requests (grant/decline modal, approved-trip warning) + presence
  dots; TripListPage presence dots; UserMenu amber recall badge on Review queue (admin).
- **Contract:** API_CONTRACT.md — new endpoint rows, recall/presence workflow bullets,
  `edited_by` on Field; removed the stale (pruned 2026-07-08) `/audio/…/ab/{ver}` row.
- **Verified:** backend import OK; `tsc -b && vite build` green; **22/22 end-to-end checks**
  against a live local uvicorn (port 8123, dev review.db): auto-recall CAS, admin-heartbeat
  block → 409 reason_required → request, submitter-only 403, admin list/count, decline+note
  visible to reviewer, grant → changes_requested + review_note, approved-trip grant →
  un-complete + reopen, reviewer 403 vs admin 200 edit at `submitted` (edited_by stamped),
  admin 403 at approving/approved, presence list. Dev DB state + test users fully restored.
- **NOT deployed** — branch only (laptop cron auto-pulls main, so merging = deploying the BE;
  FE needs `npm run build` + service restart there in an idle window).

## Next steps (updated)
Merge feature/recall-presence-admin-edit → main + laptop deploy in an idle window (dave's go);
then Blocks 3–5 per docs/workflow-features-proposal.md.
