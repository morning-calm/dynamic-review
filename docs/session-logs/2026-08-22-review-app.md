# review-app — 2026-08-22

## Session: final-check dev-test support + first fixes

**Goals:** get the dev stack up for dave's final-check test
(`docs/final-check-dev-test.md`), then fix what the test surfaced.

**What I did:**
- Ran `Trello/export_review_trips.py --no-push` (manifest had no final-check
  lanes); lanes 10 (12) / 10b (15) now present, no lane-11 cards. Started
  backend (`REVIEW_APP_PUBLISHER=1`, :8000) + Vite (:5173). Note: Vite binds
  IPv6-only — use `http://localhost:5173`, not 127.0.0.1.
- **Pin-placer coordinates fixed** (`LocationEditor.tsx`): the 18-unit
  long-axis model was incomplete. In `MapPanel.prefab` the pins' parent Image
  sits inside **MapMask**, a stretched child with sizeDelta **(−3.5, −3)** —
  so pin coordinates address a rect of `(rootW−3.5) × (rootH−3)` units, where
  root = the ResizePanel 18-unit box, and the sprite is stretched
  (preserveAspect off) to fill it. New `mapUnitSize()` per-axis conversion;
  verified by rendering all 8 country maps with the staging pins overlaid —
  Japan/UK/Taiwan spot-checked exact (Tokyo on 東京, Inverness, Taipei).
- **Keyword-check playback fixed** (`final_checks.get_keywords` +
  `KeywordCheckPanel` tooltip): keyword (speak-and-repeat) scenes have **no
  `{i}_a.mp3`** — the spoken word is the `{i}_q.mp3` clip. The panel's player
  pointed every row at `kind=answer`, so all keyword rows 404'd silently. Now
  keyword rows serve the question clip.
- **Mixed Harry/Andrea voices on `A._A. Milne and Rudyard Kipling_A12_EN`
  investigated**: spectrum-clustered all 33 master clips — narration is one
  female voice throughout; Q&A splits cleanly BY SCENE: keyword clips
  (1,3,5,7) + the whole scene-8 row match the narration voice; the whole
  scene-2/4/6 rows are a male voice. Some of those clips are pipeline-era
  originals (Lavf60.3.100), so the mix is (at least partly) in the original
  generation batch, not just review regens. This trip predates the
  voice-choice record (no `staging_choices.json`, empty `voice_overrides`,
  session `sess_36af149d850d` seeded 2026-07-01 voice=andrea by guess) — the
  known "voice is a best-effort guess" caveat. CONTENT fix still needed (see
  TODOs).

**Verified:** `pytest tests/test_final_checks.py` 16 passed; `tsc -b` +
`npm run build` clean; backend restarted (no --reload) and healthy.

**Partial family release (dave's question, N4+EN before N5/N3):** current
behavior confirmed in `publish_trips_cli.plan_trip` / `copy_trip_group`:
publishing one rung of a family whose prod TripGroup is missing or whose
`trips[]` differs from staging **cascades the group + EVERY rung staging
lists** (trips[] draws the level buttons), gated only by the audio gate — an
unreviewed N5/N3 would go live with the N4. No partial-release model exists.
Proposal sketched in the wrap-up message (filtered trips[] publish + release
ledger + later `--add-rung` style expansion).

## Second checkpoint — root cause, remediation, partial release (same day)

**Voice-mix ROOT CAUSE (confirmed):** the 2026-07-22 quiz-variety scripts guessed
each trip's narrator — `WORKING_regen_quiz_audio_0722.py` hardcoded Andrea for its
6 trips; `WORKING_fix_lane9_quizzes_0722.py` used an f0 probe on scene-1 narration
(its own comment flags "borderline reads"). Wrong guesses regenerated whole Q&A
rows in the wrong voice; the delta review only showed the CHANGED clips, so dave
fixed some by ear but the rest survived (his own recollection, verified).

**Full-catalog sweep** (119 English A12/B1 rungs, LTAS spectrum classifier vs
harry/andrea/isla narration references — validated 468/468 on labeled trips;
tooling in the session scratchpad `voiceid.py`/`sweep_voices.py`, CSV
`voice_sweep.csv`): 192 confident off-voice clips.
- FIXED TODAY (unambiguous — Andrea trips given harry Q&A): **Milne** scenes
  2/4/6 (15 clips), **Kent_Coastline II** scenes 2/4 (10 clips). Re-voiced from
  staging text via `Scripts/Audio Generation/WORKING_revoice_wrong_voice_0822.py`
  (archives `.bak_revoice_20260822`, targeted R2 upload, stale ogg purge — NOTE
  both trips have DUPLICATE master dirs, AG + Quicktrips; both copies fixed, the
  Quicktrips one is what `resolve_audio_dir` serves). Verified all-Andrea by
  classifier. `refresh_review_app.py --clips` run: laptop DB backed up, delta
  manifests uploaded (completed trips → delta-review flow, as intended).
- **PHASE 2 — NEEDS DAVE'S CALL:** Lake_District1/2/3 + The_West End (~162 clips)
  are the REVERSE: harry-majority narration + tooltip "Guide: Harry", but their
  07-22 Q&A regens used Andrea, and their `staging_choices.json` files say
  Andrea (conflict!). Rows prefilled but commented out in the WORKING script.
- Low-margin single-clip flags on ~29 trips (mostly Isla/Scottish) = probable
  classifier noise; ear-check list in `voice_sweep.csv`.
- Kent II `2_a3` had a reviewer take (July delta) — it was still harry-voiced, so
  replacing was right; prior bytes archived `.bak_revoice_20260822`.

**Partial family release SHIPPED** (`Scripts/publish_trips_cli.py`):
- `--trip` now publishes a PARTIAL group: prod trips[] = live rungs + this trip
  (staging order/text, parallel levels[] filtered), withheld rungs listed loudly;
  live rungs' buttonText/tooltipText/levels refresh from staging in the same
  write; audio gate runs on the going-live set only. The prod group doc IS the
  release ledger. Verified dry-run: Milne (3 rungs, no withheld) +
  Tokyo_06_HieShrine_Beg_N4_JP (PARTIAL 1/4, 3 withheld listed) — the N4-before-
  N5/N3 flow works. (That trip also surfaced staging levels[] 3 ≠ trips[] 4.)
- NEW `--sync-group-text TGID|all`: released-trips button/tooltip/levels drift vs
  staging, targeted update on apply, membership never changed. Dry-run on the
  Milne group found real drift ('Beginner'→'Intermediate', 'Int/Adv'→
  'Intermediate+' — the standardisation lag dave predicted).
- Review-app Publisher: new "Standing checks" tool **TripGroup text drift**
  (blank target = all) → `--sync-group-text`; backend tool spec + FE row.

**Verified:** backend 217 tests pass; tsc + build clean; publish_trips_cli
py_compile + live dry-runs; backend restarted (publisher mode) + healthy.

## Third checkpoint — ear-check pack + policy decisions (same day)

- **Ear-check pack built:** `D:\Dynamic Languages\voice-ear-check-2026-08-22\index.html`
  (+116 audio copies). Section 0 = who-is-who calibration; section 1 = the four
  phase-2 trips as ONE A/B decision each (narration vs regenerated Q&A); section 2
  = per-trip reference vs each low-margin suspect clip ("same voice?").
- **levels[] rule fixed** (dave: the EN rung is NOT a level): `align_levels` in
  publish_trips_cli maps levels[] to trips[] by buttonText prefix; a partial N4
  release now publishes exactly ["Beginner+ (N4)"]. Unit-checked (N4-only, N4+EN,
  parallel UK) + Tokyo_06_HieShrine dry-run no longer warns.
- **POLICY (dave): TripGroup text drift is EXPECTED and the all-sweep is NOT to be
  run yet** — prod text syncs gradually, per family as lower-level rungs release,
  until the next VR app version ships (a large batch of prod Firebase changes
  waits on that update, which is unscheduled). Publisher tool label + comment
  updated to say so.

**Bug fixed — page "disappears" (blank app) on final-check pages:**
`StaticImagesPanel.onLoadedMetadata` read `e.currentTarget.duration` INSIDE the
setState updater — React nulls currentTarget after the handler, the updater runs
later, so with many audio players loading at once (Abbotsford_EN, 8 scenes) it
threw `Cannot read properties of null` and unmounted the whole app (no error
boundary). Reproduced + verified fixed headlessly (puppeteer-core over installed
Chrome — scratchpad `repro.js`); backend was blameless (all 200s). Rule: never
read `e.currentTarget` inside a state-updater closure.

## Fourth checkpoint — phase-2 re-voice executed (dave's ear-check verdicts)

**Dave's verdicts:** section 1 → HARRY is correct for all four trips (Andrea was
wrong); section 2 singles → all fine, no action (classifier noise confirmed).

**Executed:** 166 clips re-voiced Andrea→Harry from staging text (LD1 41, LD2 56
incl. narration 9.mp3, LD3 32+5_q, West End 36 incl. narration 7.mp3) via the
WORKING script; 0 failures; targeted R2 uploads; LD3's duplicate Quicktrips MP3
dir synced + stale Quicktrips OGGs purged for all four; classifier re-verified
all-Harry (LD3 5_q — the keyword clip "lake", missed by the sweep's margin — was
caught in verification and re-voiced too). `refresh_review_app.py --clips` run:
- **LD3 = completed** → delta manifest uploaded (scenes 5,6,8,9,10,14,15,16,20)
  — the changed-clips review session dave asked for.
- **LD1/LD2/West End = never reviewed** (no laptop sessions) — no delta possible;
  seed caches cleared/warmed/verified vs R2, so their eventual FULL review hears
  the corrected Harry audio. (Dave assumed all were fully reviewed — only LD3 was.)

**Voice-mix remediation is now COMPLETE** — all 6 affected trips fixed, singles
cleared by ear.

## Fifth checkpoint — final-check UX round (dave's feedback)

**Jedburgh1_TownAbbey_A12_EN "publishing errors" explained:** nothing failed and
nothing was written. (1) `publish_docs` ran as a DRY RUN — plan printed (trip new
in prod, PARTIAL 1/3 group, add-to-location needed), zero prod writes by design;
Apply is the write. (2) `trello_move to=11` DID apply but matched no card — the
trip has NO Trello card (its final check was started from the audit section), so
"moved 0; 1 skipped"… and the job still said "done". Fixed: `trello_move.py
--strict` (exit 2 on unresolved identifiers, with an explanation), passed by both
review-app call sites — a no-op move now surfaces as a failed job.

**Static image timing decimals:** appear/disappear are now 0.1s-granular
end-to-end (SetTiming floats, `set_image_timing` rounds to 1dp, FE inputs
step=0.1, playhead buttons round to 0.1s; warning text via `_secs`). Safe: the
headset field is a Unity float (`QuickTripScene.appearAtSeconds`).

**Publisher "Releases" board (the redesign, phase 1):** new group-centric section
at the top of the Publisher — one card per TripGroup with per-rung pipeline
status: LIVE (from prod snapshot) / READY TO PUBLISH (7/7) / in final check
(link) / review done (start-final-check link) / in review (lane) / not started;
group badge says whether publish UPDATES the existing prod group or CREATES it.
Backing: `GET /api/final/releases` (`final_checks.releases()`), prod snapshot
extended — `export_review_trips.export_prod_tripgroups` now writes
`trips_by_group` (live rung ids; ids only leave prod), `tripdesc._tripgroup_index`
docs now carry ordered `trips`. "Post-publish & tools" got a plain-language
caption (it's the AFTER-a-publish chores rack, publishes nothing).

**Verified:** 217 backend tests pass (one fixed for the float format), tsc +
build clean, releases endpoint smoke-tested (Jedburgh shows A12 ready 7/7,
siblings 5/7), backend restarted.

**Credits auto-fill: PROPOSED, not built** (drafting sidecars
`RW data/<base>/static_images/<filename>.attribution.txt` hold Source/Author/
Licence/Credit; Credits doc convention = "Trips to <X> Images" headers) — see the
session reply for the design; awaiting dave's go.

## Sixth checkpoint — dave's second feedback round, all built

**Trello (one card per FAMILY — dave):** the lane-11 no-match was normal; per-rung
status lives in the card's [review] block. Built: `published=YYYY-MM-DD` row
annotation (review_block.py parse/render + preserve_existing_rows carries it
through rebuilds), NEW `Trello/mark_published.py --cid` (finds the family card by
block membership, stamps the row; dry-run tested on Jedburgh), auto-stamp after a
successful publish/publish_docs APPLY (routes_admin, best-effort, log appended),
and trello_move.resolve gained a **[review]-block membership step** so a rung cid
resolves to its family card (Jedburgh cid now finds "2a Jedburgh I: Town & Abbey").
OPEN QUESTION for dave: the queued to=11 move now moves the FAMILY card when one
rung is ready — recommend dropping the move (published= is the record) or moving
only when every rung is stamped.

**Pending-delta gate:** `final_checks._pending_deltas()` (deltas.list_all) —
final-check list rows get `pending_delta` + "back in review" badge, the trip page
gets a banner + Ready-to-publish hidden, releases() gets status `re_review`.
Note: Milne/Kent II deltas were CONSUMED (approved) before this landed, so Milne
correctly sits in final check again; LD3 still pending shows the new state.

**Releases board v2:** sort (ready first / content id / trip location) + country
filter; inline lazy **staging→prod diff** per group (`GET /api/final/releases/
{tg}/diff` — diffs the staging group vs the newest rung's `publish_inbox.py
snapshot` trip_group; per-rung trips[] membership + buttonText/tooltipText rows;
clear hints when no snapshot / group not in prod). Verified headless (screenshot).

**Timing decimals part 2:** live per-scene playhead readout `t = 12.3s / 45.6s`
(onTimeUpdate/onSeeked, 0.1s) next to each Static-images audio player.

**Credits auto-fill BUILT:** `GET /api/final/{tid}/credit-proposals`
(final_checks.credit_proposals — parses the drafting sidecars
`<RW root>/<base>/static_images/<fn>.attribution.txt` via sessions._image_base_ids,
formats house-style entries `[source-url] [By author, licence]`, header "Trips to
<place> Images" with GreatBritain→the UK, dedups vs the live Credits doc, statuses
proposed/already_added/needs_hand_edit/no_attribution); FE "Propose credits from
drafting data" button + one-click add rows in check 5. Smoke-tested on
Abbotsford_A12_EN (4 proposed, 2 no-sidecar).

**Verified:** 217 backend tests, tsc + build clean, headless smoke of Publisher /
final-check list / trip page, releases + diff + proposals smoke-tested, backend
restarted. Next: red-fable review of the round.

## Seventh checkpoint — red-fable review + dave's answers folded in

**Red-fable (clean-context Fable) review, verified:** found and fixed TWO real
bugs in `group_diff` — (1) it compared the FULL staging doc against the
snapshot's REDUCED trip_group entry, so every unsnapshotted field and every rung
read as drift (fixed: compare only snapshot-carried fields with ''/[] norm;
trips[] membership falls back to prod_tripgroups.json when the snapshot predates
the export); (2) the prod_missing branch was unreachable (best-snapshot required
trip_group≠None; fixed with a newest-groupless fallback). Producer half:
`publish_inbox.py` snapshot now carries trips[] (rode into dave's 9e1425ce).
Hand-traced both fixes — correct. Cosmetic: `bad` parenthesised in
credit_proposals (precedence verified as-intended), stale int-comments updated,
parse_block docstring. Gates re-verified by me: 217 pass, tsc+build clean, ruff
= the 7 pre-existing only, Scripts ruff clean.

**Reviewer findings triaged:**
- ready_to_publish had NO backend pending-delta gate → AGREED, added 409
  `pending_delta` (server-side twin of the hidden FE button).
- releases(): `live` outranks `re_review`, so a live trip's pending delta is
  invisible on the board → DEFERRED to dave (would distort live_count if swapped).
- in_fc `done>0` makes an in-review sibling show "in final check n/7" (shared
  group-scope checks) → accepted as designed.
- align_levels duplicate-buttonText edge, --strict early-return paths,
  preserve_existing_rows duplicate-cid — flagged, no action (unreachable/loud).

**Dave's answers implemented:**
1. Family Trello card moves ONLY when every rung is stamped: ready_to_publish no
   longer queues the to=11 move; `mark_published.py --move-complete-to LANE`
   moves the card when the stamp completes the family; the publish apply hook
   passes `--move-complete-to 12` (Live on App). Dry-run verified (1/3 stamped →
   no move). FE texts updated (toast, tooltip, release-order help).
2. Sidecars for credits auto-fill were NOT on the laptop (gitignored):
   .gitignore re-includes `static_images/*.attribution.txt`; 315 sidecars staged
   → dave committed all Scripts changes as 9e1425ce. Laptop pull required.

## Eighth checkpoint — dave's third round

- **LIVE ⟳ delta marker:** releases() rungs carry `pending_delta` separately from
  status; a LIVE rung with a pending delta keeps the LIVE badge + gets a ⟳ marker
  and an explanatory tooltip (live audio = OLD version until approved+republished).
- **LIVE-APP BUG fixed — vanishing regenerate:** in `AudioReview.tsx`, BOTH
  coverage-POST responses spread the closure-captured `field` into
  `onFieldUpdate`. Sequence: audio finishes → /played POST fires → Regenerate-All
  resolves (candidate appears) → the POST response lands and overwrites parent
  state with the pre-regen field → **the candidate vanishes from the UI** (it
  exists server-side — the "second" regenerate was double ElevenLabs spend).
  Fixed with a `latestField` ref merged at response time; no other `...field,`
  async-spread sites exist. NOTE: fix is in the (uncommitted) review-app repo —
  reaches the live laptop only at the next review-app deploy.
- **Scripts committed + pushed** (`1ad2450d` — mark_published --move-complete-to
  + parse_block docstring; only session files, the Italian-ladder workstream's
  edits left untouched). Pre-commit ruff-format hook reformatted on the way in.
- **Laptop:** dynamic-content pulled (at 1ad2450d, sidecars present) and a NEW
  cron added: `*/10 * * * * cd ~/Desktop/Server/Scripts && git diff --quiet &&
  git pull --ff-only >> …/backend/scriptspull.log` (dirty-tree guard; mirrors the
  existing review-app auto-pull). This closes the known stale-Scripts-checkout
  failure class.

## Ninth checkpoint — second red-fable pass (follow-up delta)

Fresh clean-context review of the post-first-review hunks. ONE real bug found,
fixed, verified, committed: `mark_published.py`'s already-stamped early return
skipped the `--move-complete-to` check — if the run that stamped the FINAL rung
died between the desc PUT and the move PUT, every retry exited 0 as "no-op" and
the card was stranded off lane 12 forever. Fix: the no-op path skips only the
desc PUT and still evaluates the (idempotent) move. Hand-traced the diff, gates
re-verified (217 pass, ruff clean), committed `f6777b4f`, pushed, laptop pulled.
Scrutiny log (no action): import mechanics via sys.path[0]=Trello/ verified;
resolve_list_key("12") label-token semantics correct; retired-sibling rows block
the auto-move by design (manual tool = escape hatch); latestField fix judged
correct/complete (gen guard covers take changes, ref covers same-take mutations);
⟳ marker only on live status is fine (re_review status covers the rest).

## Tenth checkpoint — release wizard + self-serve diff

- **Diff error fixed** ("no prod snapshot for any rung"): `group_diff` now (a)
  answers prod_missing straight from prod_tripgroups.json for a group not in prod
  (publish_inbox writes NO snapshot for a not-live rung, so the old hint was
  unsatisfiable for new families), and (b) in publisher mode AUTO-RUNS
  `publish_inbox.py snapshot <first live rung>` when the group is live but
  unsnapshotted. Verified: Milne diff now self-serves (7 drift rows incl. the
  stale Beginner/Int-Adv labels); Tokyo_06_HieShrine → "first publish CREATES it".
- **Release wizard SHIPPED** (`/publisher/release/:tripId`, "Publish…" button on
  READY rungs): guided stages confirming each — 1 publish docs (dry-run plan →
  apply; notes the auto Trello stamp) · 2 add-to-location (if the plan warned
  tile-unreachable) · 3 map pin · 4 bump content version · 5 DocIDs log ·
  6 refresh prod snapshot — all through the EXISTING bus jobs/whitelisted tools
  (find-or-queue, poll-to-settle for background tools), nothing new can execute.
  Status chips todo/dry run/done/skipped/failed, apply behind confirmation,
  non-ready warning banner. tsc+build clean, 217 tests, headless smoke OK.

## Eleventh checkpoint — P1 spec settled + answer-key delete shipped

**Wizard P1 spec (dave-confirmed through three refinement rounds):**
- Recall-quiz eligibility: leveled rungs only (stage10b rule) AND ≥1 KEYWORD
  scene (questions-only trips get none) AND `*_EN` rungs only for UK families.
  Verified vs data: all 198 quiz carriers fit; quiz-less UK A12s are just
  pre-10b pipeline position, not editorial picks.
- Execution surfaces mapped: laptop app queues/displays; EVERYTHING that touches
  files/creds (stage 9, stage 10b incl. the station keyword-audio copy, publish,
  post-publish, Trello) runs on the workstation Publisher. Readiness columns are
  read-only → both.
- Ordering: VR staging check = wizard step 0 AFTER 10b (the lanes already
  reflect this — keyword/quiz testing now lives in the app's final checks).
  VR gate is WIZARD-ONLY (no server enforcement; admin trusted); differentiated
  per item (play changed trips vs check group button/tooltip text on the tile).

**Shipped now: additionalAnswerKeys DELETE** (dave added a test key on
Bath1_A12_EN to remove): `final_checks.delete_answer_key` + POST
`/{tid}/answer-keys/delete` + × button on the teal chips in KeywordCheckPanel
(add stays collision-checked; options/correct answer stay pipeline-owned).
Gates green, backend restarted.

## Twelfth checkpoint — Wizard P1 BUILT

- **Readiness columns** on releases(): `finalised` ('shipped'/'restale'/None from
  the Stage-9 bus ledger via sessions._finalised_state) and `recall_quiz`
  ('present'/'missing'/'na' — TTL-cached field-only recallQuiz sweep + the
  three-part eligibility rule `_QUIZ_EN_RE`/`_QUIZ_TL_RE` + keyword-scene probe
  only for suffix-eligible quiz-less rungs). Board shows amber "re-finalise
  pending" / "recall quiz missing" chips. Verified: UK leveled rungs = missing,
  natives/legacy = na.
- **Family-scope wizard** `/publisher/release-family/:tgId` ("Publish family…"
  on group cards with ≥1 ready): rung checkboxes (ready pre-ticked) with
  readiness warnings; step 1 = one publish sub-step per selected rung.
- **VR check = step 0** (wizard-only, localStorage 24h prefill): per-rung tick
  (new-to-prod: "play it through, quiz+keywords included"; live re-release:
  "play the changed parts") + a group-text tick when the diff shows drift.
  ALL Apply buttons gated on step-0 complete; dry runs always allowed.
- **Failure tiers**: failed steps show a "what failed" line extract + Tier-1
  remedy buttons (stage10b / thumbs / 4K / tripdocs re-runs via existing tools)
  + Tier-2 **"Investigate with Claude (Opus · high)"** → new endpoint
  `POST /api/admin/pipeline/investigate` (publisher-only) writes a diagnostic
  bundle to `Scripts/Logs/publish-diagnostics/<job>.md` and opens a terminal
  running `claude --model opus --effort high` pre-briefed on it (CLI flags
  verified). Tier 3 dropped (dave: always on the workstation).
- Verified: gates green (217, tsc, build, ruff = the 7 pre-existing), releases
  smoke, headless family-wizard screenshot (checkboxes/step-0 gate/steps render).

## Thirteenth checkpoint — P2 + P3 BUILT

**P3 — 4K-assets ledger (Scripts, commit 137ccd0c pushed + laptop pulled):**
`static_pic_4k_s3.py` gap/build --apply record fully-covered rungs to
`_bus/static4k.json` on R2 (finalised-bus pattern). First runs: borders batch
verified complete (12 rungs onto the ledger), and the gap sweep CAUGHT a real
hole — Milne A12's two static scenes (5, 6) had no -4k.jpg; built + uploaded +
recorded. App reads it via `review_bus.get_static4k_snapshot()`.
NOTE: my commit initially aborted on the ruff-format hook and the push carried
the Italian-ladder session's two local commits (e8a5f205, 251feb13) — deliberate
committed work, no harm; my change re-committed as 137ccd0c.

**P2 — remaining columns + scopes:**
- `four_k` column ('built'/'missing'/'na') + `keyword_copy` ('copied'/'missing'/
  'na' — TL families' native _EN rung vs the leveled rung's keyword questionKeys,
  the CopyKeywordsfromBegtoEn predicate). One cached `_doc_probe` per rung serves
  quiz/4K/keyword-copy eligibility (lazy: only rungs lacking a ledger/quiz entry
  fetch a full doc; cache is process-lifetime).
- Board chips: "4K stills missing", "EN keywords not copied"; **Run stage 10b**
  one-click on group cards with quiz/keyword gaps (targets the family base).
- **Tile scope**: location chips on group cards → `/publisher/release-location/
  :locName` — the wizard in multi-group mode (per-family rung checkboxes across
  the whole tile, per-group add-to-location steps, one pin/bump/docids/snapshot).
  Headless-verified: Scottish Borders tile shows all 4 families with readiness.

**Verified:** tsc+build clean, 217 tests, releases payload carries all 4 columns,
tile wizard screenshot, `/api/final/releases` ~0.15s warm.

## Fourteenth checkpoint — third red-fable pass (wizard/readiness batch), verified

Fresh clean-context review of the P1+P2+P3 batch. FIVE real bugs found+fixed
(all hand-verified by me, gates re-run: 217 pass, tsc+build clean, ruff = the 7
pre-existing):
1. **Pin step targeted the location display NAME, not the TripLocation DOC ID**
   — 18/116 staging TripLocations diverge (Ainsa→"Aragon", Alps_*→"Alps"…), so
   an Ainsa-tile release would queue a pin for a nonexistent doc. Fix:
   `_loc_full_index()` (TTL-cached), releases() locations gain `id`, wizard pins
   target the id (tile mode picks the doc bearing the tile's name).
2. `_doc_probe`'s kw cache never expired → a fixed "EN keywords not copied"
   chip stayed amber until restart. Fix: the sweep refresh clears it.
3. `_recall_have` re-streamed ALL Trips every call when the sweep was
   legitimately empty (freshness keyed on non-empty result). Fix: clock-only.
4. VR-gate untick silently resurrected from localStorage on the next selection
   change (re-arming Apply). Fix: untick removes the stored stamp.
5. `jobFor` with a blank target polled ALL jobs and could latch onto another
   location's queued pin job. Fix: loud "no target — skip it" error.
Plus docstrings: `_four_k_state` phantom 'no_record' state removed; Scripts
static_pic_4k header now admits gap's ledger side-effect (re-committed
c9d50eda, pushed, laptop pulled).
Notable traced-and-accepted: tool-poll caps at 80s (slow snapshot shows
check-the-inbox), keyword-copy source = first leveled rung (fail-safe),
investigate argv injection surface clean (only the sanitised bundle path
reaches argv), Milne-style tg ids with spaces 422 cleanly on the 10b remedy.

**Docs:** NEW `docs/PUBLISHER.md` (how to run the workstation console —
publisher.cmd → 127.0.0.1:8010, two-instance model, setup, troubleshooting) +
README pointer.

## Fifteenth checkpoint — dave's live-test findings, fixed

1. **Milne "Ready to publish" + "Run stage 10b" both errored — ROOT CAUSE:
   `review_bus._TRIP_ID_RE` only allowed `[A-Za-z0-9_-]`,** so every cid with
   dots/spaces/commas ("A._A. Milne…", "Kent_Coastline II_…", "Hyde_Park, …")
   422'd at queue time — a latent bug for ALL spacey ids, not just Milne. Fixed:
   regex widened (still alnum-anchored, no flag-looking argv); run_tool's
   job_target no longer word-splits ("A._A." mangling). VERIFIED live: both
   Milne actions now succeed (publish job queued; 10b tool running).
2. **Jedburgh 10b was actually a full SUCCESS** — the toast's "check your inbox"
   meant the background tool's log lands in the Publisher Job inbox. Log shows:
   recall quizzes written (A12+B1, 11 items, native EN skipped per the rule),
   keyword copy skipped (GB), docs built, verify 3/3 PASS. Toast reworded to
   say all that plainly. ALSO: the tool spec appended `--apply-trello` on apply,
   contradicting the only-move-when-all-published policy — removed (10b's
   Trello step now just prints); rack label updated.
3. **Replace image now replaces the SHOWN image and is REVERSIBLE:**
   root cause of "nothing changed" = local trees outrank R2 in overlay serving
   and replace writes R2 only → `overlay_image` now lets a NEWER R2 copy win
   (per-file HEAD mtime compare + ?v= cache-bust); FE busts the <img> after
   replace/revert. Reversibility: replace backs the old object up as
   `<key>.prev`; NEW revert endpoint + button restores it (or deletes the
   replacement when it was a first-ever replace of a local-only image, so the
   untouched original serves again) and re-queues canonical distribution.
4. Milne A12 recall quiz: 10b launched for the family (running at log time).

## Sixteenth checkpoint — fourth red-fable pass + Milne 10b done. SESSION END.

**stage10b target fix (found verifying Milne):** `--families` takes TripGroup
DOC IDS verbatim — my `_Trip`-suffix stripping broke legacy groups ("TripGroup
not found in staging"). Both call sites now pass tg_id unmodified. The corrected
Milne run: **recallQuiz written to the A12 (8 items)**, natives correctly
no-quiz, Trello step dry-run-only (policy fix confirmed working). The
tripdocs-step `ingest.json` ERRORs are a legacy-family limitation (pre-ingest
generation — trip docs can't build), NOT a regression; release unaffected.

**Fourth red-fable pass (the fix batch) — THREE more real fixes, verified:**
1. Revert could delete a never-replaced overlay's ONLY hosted copy (the
   ensure_uploaded mirror / a producer's gallery still with no local file).
   Fix: replace stamps `Metadata.replaced=1`; revert's delete path requires the
   marker (else 404). replace→revert→revert now 404s instead of deleting the
   restored original. (Pre-fix test objects lack the marker — only relevant if
   any exist.)
2. The revert route was `async def` with blocking R2 calls ON the event loop
   (stalls every concurrent request) → plain def (threadpool).
3. `_TARGET_RE` still rejected apostrophes + had a dead 200-char cap → aligned
   with the widened bus regex.
Scrutiny highlights: widened trip-id flow safe end-to-end (jobs keyed by job id,
argv lists never shell, trello matching re.escape'd); overlay HEAD cost
acceptable + self-healing local-vs-R2 mtimes; stage10b apply-flag now a no-op by
design. Gates re-verified by me: 217 pass, ruff = 7 pre-existing, tsc+build
clean. Backend restarted; both dev servers healthy.

**STATE FOR THE NEXT SESSION:**
- review-app repo: ~30 files changed, ALL UNCOMMITTED (the whole day's work:
  pin fix, keyword fixes, timing decimals, credits auto-fill, Releases board,
  release wizard, readiness columns, pending-delta gating, vanishing-regenerate
  fix, answer-key delete, replace/revert overlays, PUBLISHER.md). Next: dave
  re-tests (Milne publish flow now works; Replace/Revert; Bath1 ×), then commit
  + laptop deploy (git pull + npm run build + service restart; laptop review-app
  auto-pull cron exists, dist build + restart are manual).
- Scripts repo: committed + pushed through c9d50eda; laptop at head; Scripts
  auto-pull cron live (*/10, ff-only, dirty-guard).
- Milne A12: READY, quiz written, 4K built — a complete first wizard candidate.
- LD3 delta re-review still pending reviewer-side.
- NO --sync-group-text all-sweep until the VR app update ships.
- `export_review_trips` final_check entries carry no `voice` field (lane-6/7
  entries do) — add for parity so late regens resolve correctly.
- Both repos uncommitted; laptop deploy after dave's test.
- Rest of dave's final-check test still in progress.

---

## Session 2 (~15:30–18:30) — feedback round: perf caches, Publishing Queue, release batches, renames

**Goals:** dave's 11-item feedback round on the publisher/final-check build, plus two
additions (R2 archive workflow, social posts in release prep).

**What was done**
- **Perf (the big one):** the Releases board's per-rung Firestore probes are now
  materialised in a new `release_probe_cache` table (trip_list_cache pattern: serve
  stale, sync-fetch only never-seen rungs in a 16-thread pool, background single-flight
  refresh, startup pre-warm in main.py, explicit invalidation when a publish/tool job
  applies). The two `_bus/*.json` snapshots + the job listing got short-TTL caches in
  review_bus (30s/15s, serve-stale-on-failure, invalidated on completion/publish events
  via `invalidate_snapshots()` / `invalidate_jobs_cache()`). `_recall_have` /
  `_loc_full_index` got single-flight locks; `_triplocations_index` got `.select()`;
  the doubled completed_trips query merged. Completed page: titles from
  trip_list_cache (was: serial `_trip_meta` Firestore gets after every restart), bus
  read TTL'd. Tests: `tests/test_release_perf_caches.py` (12) — full suite 229 passed.
- **Publishing Queue (was "Releases — by TripGroup"):** renamed; groups nest under
  TripLocation headers with a Group-by Location|Batch|None toggle; multi-select at
  rung/group/bucket level → "Save as release batch…" / "Open in wizard →"; inline bus-job
  chips per family (`releases()` now returns `groups[].jobs`); ToolsPanel titles
  shortened + tooltips added (had none); the static "full release, in order" block
  collapsed into an "ℹ Release order" popover; new collapsible "Recently published"
  section.
- **Release batches:** new `release_batches` table + `/api/final/batches` CRUD +
  Trello import (one batch per card on the "TG Release Schedule" lane, family tables
  resolved to groups; cards are a seed, the app copy is the contract). Wizard gained
  `/publisher/release-batch/:id` scope. Batches carry a social-post readiness chip
  (`release_batches.social_state` probes `Comms\Social Posts` + news.json; 'unknown'
  off-workstation).
- **Publish ledger:** new `published_trips` table stamped when a publish/publish_docs
  job applies (routes_admin), `/api/final/published?months=12`, plus
  `scripts/backfill_published_trips.py` (parses Trello `published=` via Scripts'
  review_block — dry-run default, not yet run).
- **Renames/nav/badges:** "Final check" → **"Release prep"** (user-visible strings only);
  nav reordered flat (Review queue · Descriptions · AI review · Release prep · Publisher ·
  Completed · All trips · Bug reports, same on mobile); Review-queue badge now =
  submitted-awaiting-admin via new `GET /api/review-queue/count` (was recall-requests).
- **Social posts:** drafts moved `Scripts/social/posts/` → `D:\Dynamic Languages\Comms\Social Posts\`
  (4 files); Scripts `social/README.md` + `STAGE_10B.md` §7/7b updated; drafting is a
  Sonnet-high agent per release batch — runbook `docs/social-post-drafting.md`.
- **R2 archive:** NO cleanup ran (dave: nothing on R2 is prod-published — verified; the
  earlier off-lane list was wrong to frame as "published", lesson memorised). Go-forward
  tool `scripts/archive_review_audio.py`: audit/archive/restore, download→verify→delete
  per trip, HARD prod-Firebase check (firebase_production_key2.json, the publish_inbox
  credential), refuses reserved prefixes + in-queue trips, dry-run default.
- Also: stale Claude shells from earlier sessions cleaned (4 idle waiter pairs);
  API_CONTRACT.md updated for every new endpoint.

**Verified:** backend pytest 229 passed; `npx tsc -b --noEmit` clean; eslint clean on
touched files; dev uvicorn restarted on the new code, starts clean (new tables created);
archive script exercised dry-run against live R2+prod. NOT verified: hands-on UI pass
(dave to test), laptop deploy, Trello backfill run, red-team (dave triggers).

**Open / decisions for dave:** (1) run the published_trips Trello backfill? (2) want a
hard "social posts" checklist item per trip, or is the batch chip enough? (3) archive
tool stays CLI-only or gets a publisher tool row? (4) nav badge for recall requests was
dropped from the top bar (page still shows them) — confirm. (5) commit both repos after
testing; laptop deploy needs Scripts pull + backend restart.

### Red-team pass (red-fable, ~19:00) — verified

Clean-context Fable review of the whole session scope. Two real correctness fixes,
both hand-verified by the main session:
1. **archive_review_audio.py restore hole**: a partial delete + re-run rewrote
   `_manifest.json` with only the objects still on R2, silently dropping the
   already-deleted files from restore's view (bytes were in the backup but never
   re-uploaded). Fixed by unioning manifests by key. Verified: --force-redownload
   never wipes the local folder, so the union is sufficient.
2. **Wizard batch mode over-selection**: pre-ticked every ready rung of every
   touched family — a partial trip pick would ship excluded siblings. Now
   intersects with `batch.resolved.trip_ids`.
Plus: API_CONTRACT stage10b row corrected (no --apply-trello), `_recall_eligible()`
extracted (one copy of the quiz predicate), `invalidate_recall_sweep()` accessor,
unused import dropped. Gates re-run independently after: ruff = 7 pre-existing only,
229 tests pass, tsc/build clean.

Deferred judgment calls (not changed): wizard awaitJob 80s vs long tool runs
(misleading "failed" on slow steps); multi-family wizard remedies target groups[0];
batch rename onto an existing name → 500 not 422; partial-delete re-run needs
--force-redownload (friction only).
