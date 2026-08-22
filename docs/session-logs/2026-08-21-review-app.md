# 2026-08-21 — review-app

## Tripdesc: production-existence seed guard + cleanup

**Goal:** stop the family-description queue seeding TripGroups that were already
checked (any group live on production Firebase has shipped ⇒ checked — dave's rule);
clean out the ~wrongly-seeded backlog. Constraint: the prod Firebase key must NOT
go to the Ubuntu laptop.

**What I did**
- `Scripts/Trello/export_review_trips.py` (dynamic-content): new
  `export_prod_tripgroups()` — streams PRODUCTION `TripGroups` ids (ids only,
  `select([])`) using the workstation-only `firebase_production_key2.json`, writes
  `prod_tripgroups.json` at this repo's root, committed/pushed with the manifest.
  Best-effort: key missing / auth failure / EMPTY listing keeps the previous
  snapshot (a wrongly-empty snapshot would over-seed; stale only over-seeds
  recently-published groups — safe direction).
- `backend/app/config.py`: `PROD_TRIPGROUPS_PATH`.
- `backend/app/tripdesc.py`: `seed_from_manifest` skips (with log line) any family
  whose tg_id is in the snapshot; returns `skipped_prod` count. Missing/unreadable
  snapshot degrades loudly to seed-everything (warn once). `seed_trips` /
  `backfill_tripdesc.py` deliberately ignore the snapshot — the "review it anyway"
  escape hatch.
- `backend/scripts/cleanup_tripdesc_prod.py`: deletes UNTOUCHED rows only
  (`pending_en` + `en_by IS NULL` + `en_text = en_original`) whose group is on
  prod; dry-run default, refuses without a snapshot.
- Commits: review-app `619336b` (code) + export commit (manifest + snapshot,
  320 prod ids); Scripts change left for dave to commit via GitHub Desktop.

**Verified**
- `pytest tests/test_tripdesc.py` → 16 passed (3 new: prod-skip, missing-snapshot
  degrade, backfill-ignores-snapshot).
- Export run: 320 prod TripGroup ids written.
- Deployed to laptop (git pull, `sudo -n systemctl restart review-app.service`);
  review-app + review-tunnel both active.
- Cleanup dry-run then `--apply` on live review.db: **168 rows deleted** (JP
  Tokyo/Shikoku, EU Beg families, all UK trips — exactly the wrongly-seeded class).
  31 rows remain: Kyoto batch, Monaco FR/IT, Tokyo_06-10, Hida/Takayama, Korean,
  Mandarin, + the 5 deliberately-backfilled Scotland families.

**Open / low-urgency**
- **Monaco1IT/Monaco2IT survived** — dave named Monaco IT as already-checked, but
  those groups are NOT in the prod snapshot, so by the agreed rule they stay
  pending_en (one redundant admin approval each, or approve/ignore). Flagged to dave.
- Scripts-side change uncommitted (dave commits dynamic-content via GitHub Desktop).
- Pre-existing export audit still lists many audio-ready drafts on no lane-6/7 card
  (Yokohama/Yamanakako/Xiangde/etc.) — untouched by this work.

## Trip-list cache (90s listing fix)

**Goal:** `GET /api/trips` went 15s → ~90s after a few-hundred-trip JP batch landed —
every listing did a Firestore `get_trip` + an audio probe (incl. the R2 seed-cache
fallback, which re-LISTs R2 on every request for trips whose audio isn't up yet) per
manifest entry. Fix the latency and cut the Firebase staging reads.

**What I did:**
- New `trip_list_cache` table (`db.py` schema — plain CREATE IF NOT EXISTS, no
  migration needed): trip_id, title, folder_name, reviewable, duration_sec, fetched_at.
- `sessions._list_trips_from_manifest` now serves ENTIRELY from that table (+ manifest
  + one bulk `_session_meta_map()` — 2 queries instead of 2/trip). Never-seen entries
  are fetched synchronously once; stale rows are served as-is and re-fetched by a
  background single-flight daemon thread (`_refresh_trip_cache_async`).
- TTLs in `config.py`, env-overridable: `TRIP_CACHE_TTL_REVIEWABLE` = **12h** (present
  audio stays present, titles effectively immutable), `TRIP_CACHE_TTL_UNREVIEWABLE` =
  **30 min** (those rows are waiting for audio/staging to land; this also
  negative-caches the fruitless R2 probes that dominated the 90s). At ~300 pending
  trips that's ~14k background Firestore reads/day worst-case, and only while people
  are actually listing (refresh piggybacks on requests) — vs ~600 reads per listing
  before.
- Startup pre-warm thread (`main.py` → `sessions.warm_trip_cache`) so the first
  request after a deploy is fast too.
- Immediate invalidation: `sessions.invalidate_trip_cache(ids)`; `refresh_trips.py
  clear` now also deletes the trips' cache rows (best-effort, pre-migration-safe).
- CLAUDE.md trip-list section updated.

**Verified:** new regression tests `backend/tests/test_trip_list_cache.py` (4 tests:
warm listing = zero fetches, TTL asymmetry + stale-served-then-refreshed, invalidate,
missing staging doc). Full suite: **195 passed**. `from app import main` boots clean.
Not yet verified on the laptop (needs deploy + restart).

**Next steps:** deploy to the laptop (git pull in `~/Desktop/Server/review-app`,
restart uvicorn, confirm cloudflared still up), then time `GET /api/trips` warm —
expect sub-second. First listing after deploy pays the one-time sweep unless the warm
thread finishes first.

## Later session — Trip descriptions categories + mobile AI-review dropdown

**Goals:** (1) Trip descriptions page: offer ALL in-use categories as one-tap adds,
clearly separate never-used ones, and on adding a category scan same-country/playlist
siblings for older trips that may fit it (Edinburgh/Castle, Monastery examples).
(2) Mobile ⋮ menu: AI-review rows were drowning every other link (dave's screenshot).

**What I did:**
- `tripdesc.used_categories()` — union of `tripCategories` across all staging
  TripGroups (reuses `_tripgroup_index`), with per-category counts.
- `tripdesc.category_check(tg_id, category)` — new `_triplocations_index` (TTL-cached
  TripLocations stream) finds siblings sharing a location; reports which mention the
  category word in their live descriptions but lack the tag (snippet included), plus
  which already carry it, and whether the category is new to the vocabulary. Read-only.
- Routes `GET /api/tripdesc/categories` + `GET /api/tripdesc/{tg}/category-check`
  (admin; registered before the `/{tg_id}` catch-all), api.ts clients + `CategoryCheck` type.
- `TripDescPage`: "In use on other trips — tap to add" chip list (with counts);
  enrichment proposals outside the vocabulary in their own sky-boxed "Never used
  before" section; every add (chip or typed) triggers the sibling-fit check and shows
  a dismissible amber panel listing likely-fitting siblings / already-tagged ones.
  Panel auto-clears if the category is removed.
- `UserMenu` MobileMenu: >1 waiting AI-review trips now fold into a single
  "AI review · N trips ▸" expandable row (scrollable sub-list); 1 trip still links direct.

**Verified:** `pytest tests/test_tripdesc.py` — 19 passed (3 new tests for
used_categories + category_check); frontend `tsc -b` clean.

**Next:** deploy = pull on the laptop, `npm run build` (REVIEW_APP_SERVE_FRONTEND=1
serves dist), restart uvicorn (tunnel stays). Not done this session.

## Later still — post-approval admin workflow spec

**Goal:** spec + plan for dave's next arc: admin "Final VR Check" UI (7 human checks)
on the laptop app + a workstation Publisher app wrapping the Scripts repo's lane
9/10/11 tooling, with production publish.

**What I did:** three parallel surveys (Scripts repo publish pipeline + Trello lanes;
library-app keyword/Azure design; Unity checkout for the CustomizableMenus pin system)
→ wrote `docs/post-approval-admin-spec.md`. Key findings baked in: publisher skeleton
already exists (REVIEW_APP_PUBLISHER + R2 _bus + publish_trip_text runner); pins are
flat-rect top-left-origin 18-unit coords (web placer is faithful; maps in the Unity
checkout); PublishTrips-Select is stdin-interactive → needs an argv shim;
ContentEnrichment has no country field → derive from folderName/TripLocations, don't
denormalise; no Credits doc exists anywhere (greenfield, shape needs dave's sign-off);
library-app speechCheck.ts + mic stack are the reusable keyword pieces (no authoring
UI exists to copy). 5 decisions listed for dave at the end of the spec.

**Next:** await dave's approval + answers to the 5 decision points; then Phase 1.

## Wrap-up — red-team, commit, deploy

- **Red-team (clean-context Fable):** one edit — E741 rename `l`→`loc` in
  tripdesc.py (my new code had added an 8th ruff violation). Findings triaged, none
  acted on: `mentions` field is currently always true (kept — API shape, future-proofs
  non-mentioning siblings); `_mention_snippet` lower()-offset nit (cosmetic);
  FE `usedCats` not refreshed after adding a new category within the session
  (informational panel, acceptable). Independently verified: ruff = 7 pre-existing
  only, pytest 198 passed, FE build clean.
- **Committed & pushed** `219a299` (code + tests + spec + this log).
- **Deployed to the laptop:** git pull, `npm run build`, `sudo -n systemctl restart
  review-app.service`. Verified: review-app + review-tunnel both `active`, app 200,
  new `/api/tripdesc/categories` route registered (401 unauthenticated).
- **Session closed.** Next: dave reviews `docs/post-approval-admin-spec.md` in a
  higher-effort session before implementation (5 open decisions in its §6).

## Follow-up — country-scope the category vocabulary

Dave: Spanish categories must not show on Japanese trips. `used_categories` now takes
`tg_id` and scopes the vocabulary to the group's country via `_triplocations_index`
(global fallback + `scope` field when the group sits in no TripLocation);
`category_check.is_new` judged against the same scoped set; route takes `?tg_id=`,
FE passes it. Verified: 22 tripdesc tests (4 new: scoped counts, cross-country
exclusion, fallback, country-scoped is_new), 201 total pass, ruff 7 pre-existing,
FE build clean. Deployed to laptop.

## Later session — spec v2 (dave's §6 answers) + Phase 1 BUILT

**Goal:** fold dave's §6 answers into `docs/post-approval-admin-spec.md`, resolve the
remaining decisions, then build Phase 1 of the Final-check workflow.

**Spec v2 decisions (all resolved with dave, in-session):**
- Credits: per-country docs `CustomizableMenus/Credits_<Country>` — full country name
  (`Credits_Germany`), matching COUNTRY_CFG/ACL conventions. VR-side read still to be
  agreed before the seeder writes anything.
- Azure keyword key: approved; dave creates the capped resource (blocks Phase 4 only).
- Pin flow: UI placer → x,y to staging CustomizableMenus immediately; prod only at
  publish via a new targeted Publisher op.
- Work-list granularity ("seeding", now in plain english in §2): per TRIP, driven by
  lane-10/10b/11 Trello cards; checks stored at the level they're true at (group/
  location checks shared across siblings) so a single trip can be released without
  waiting for its family. First-of-family publish carries TripGroup/pin (dry-run diff
  shows which docs); `publish_trips_cli.py` (Phase 6) gets a `--trip` single-trip mode.
- Category search: deterministic enrichment-index only; LLM pass → BACKLOG.
- Build start: Phase 1 first, then 6a.

**Phase 1 built (this session):**
- *Scripts repo* (`dynamic-content`, NOT committed by me — dave commits via GitHub
  Desktop): `Trello/export_review_trips.py` gains `FINAL_LANES=(10,10b,11)` +
  `collect_final()` — manifest gets `final_check` entries (card lane wins over block
  row lanes; cids still in 6/7 skipped; EXCLUDE honoured; no duration probe) and a
  board-wide `card_lanes` {cid: furthest lane} map. `publish_inbox.py` snapshot now
  includes `trip_group` (prod TripGroup fields via one cached TripGroups sweep) for
  the check-3 drift display.
- *Backend*: `final_checks` + `final_check_extra` tables (db.py); `final_checks.py`
  (work list from manifest+extras, scope-resolved check states, audit of completed
  trips on no lane-10+ card, ContentEnrichment country index + `_enrichment_matches`
  with the tripdesc-style naive-singular probe, targeted TripGroup writes for
  contentTitleKey/tripCategories, reopen-description escape hatch into tripdesc);
  `routes_final.py` (`/api/final/*`, all admin-only); registered in main.py.
- *Frontend*: `/final-check` list page (lane chips, n/7 progress, audit + "Start
  final check") and `/final-check/:tripId` detail page (checks 1–3 with tooling:
  desc re-read + Edit-escape-hatch, full category editor incl. sibling-fit AND
  enrichment country-mates panel, title-key edit with prod-drift colouring; checks
  4–7 manual-tick with per-phase help text). Admin-only routes, nav link + indigo
  badge (desktop + mobile ⋮), api.ts types/calls, API_CONTRACT.md updated.

**Verified:** backend pytest 209 passed (8 new in `tests/test_final_checks.py`:
scope sharing/isolation, audit, manual start, count, key/state validation,
country-scoped enrichment matches); `tsc -b` clean; `npm run build` clean;
py_compile on both Scripts-repo files.

**Not done / next:**
- NOT deployed to the laptop; NOT committed yet (dave to review first). Scripts-repo
  changes need a dave commit in dynamic-content, then a manifest re-export
  (`py -3.12 Trello/export_review_trips.py`) to populate `final_check`, and a
  workstation `publish_inbox.py snapshot <cid>` re-run for any trip whose title-key
  drift should show.
- Next build steps: Phase 6a (publish_trips_cli shim + publish flow), then Phase 2
  (TripLocation + pin placer).

## Continued — phases 6a, 2 and 5 BUILT (same session, dave: "keep going")

**Goal:** continue the spec build past Phase 1 without stopping, to a natural
local-test gate.

**Phase 6a — Publisher console + publish CLI:**
- Scripts repo: NEW `publish_trips_cli.py` — argv sibling of PublishTrips-Select.py
  (importlib-loads it, reusing its copy fns + audio gate + both Firebase handles).
  Modes: `--trip` (single-trip release; plan/diff; auto-carries the TripGroup+rungs
  cascade when the prod group is missing OR its trips[] differs; warns when no prod
  TripLocation lists the group), `--group`, `--location` (loud cascade warning),
  `--add-to-location` (targeted prod trips[] insert at staging-relative position),
  `--pin` (staging pin → prod CustomizableMenus upsert, Pins or ExtraMapButtons).
  Dry-run default; writes need `--apply --i-am-sure`.
- Backend: bus kinds widened (`review_bus.JOB_KINDS`, models Literal);
  `run_pipeline_job` dispatches per kind (text→publish_trip_text, docs/pin/
  add-to-location→publish_trips_cli, thumbnail_local_copy→inline R2 download to
  `App thumbnails\_from_review_app\`); NEW `GET /api/admin/publisher-mode` +
  `POST /api/admin/pipeline/gate-report` (publish_audio_gate --report, 900s).
- FE: `/publisher` page (gate report, queue-any-kind form, job inbox with Dry run +
  two-step Apply, log panes, post-publish manual-commands note); rose nav link
  gated on publisher mode. `scripts/publisher.cmd` launcher (8010, Edge --app).

**Phase 2 — TripLocation + map pin:**
- Assets: the 8 country map PNGs committed to `frontend/public/maps/` (from the
  Unity checkout `Assets\_Sprites\Maps`); NEW `scripts/export_skybox_manifest.py` →
  committed `skybox_manifest.json` (214 ids = S3 `360_Skyboxes/` listing (209 — the
  runtime source of truth, `DownloadManager.GetSkyboxUrl`) ∪ the Unity build tree).
- Backend: `GET/PUT /api/final/{tid}/location` + `PUT .../pin` — TripLocation read
  model (docs listing the family, every `*_Trip_Menu` with Pins/ExtraMapButtons,
  skybox used+manifest vocab); targeted writes (title key / skybox / trips REORDER
  — permutation-guarded 422; membership stays with the pipeline), staging pin
  upsert (read-modify-update of the one array; ExtraMapButtons entries update in
  place). Coordinate model verified in MapPanelController.cs: long axis = 18 units,
  origin top-left, +x/−y; FE converts via units-per-pixel = 18/max(w,h).
- FE: `LocationEditor.tsx` (fields + ↑/↓ tile order + PinPlacer: click-to-place on
  the committed map, other pins gray, ExtraMapButtons amber, draft indigo, drawn to
  headset scale 0.59×0.62 units) wired into Final-check check 4.

**Phase 5 — Thumbnails:**
- Backend: `GET/POST /api/final/{tid}/thumbnail` — stem + public URL + R2 HEAD;
  upload = JPEG-magic + 8MB checks → R2 `dynamic-languages-thumbs/<stem>.jpg`
  (bucket ROOT, per upload_thumbnails_r2.py) + staging `thumbnailTextureId`
  (existing stem kept; first upload mints `"<tg_id> - EN Trip app thumb"`) +
  best-effort `thumbnail_local_copy` bus job.
- FE: ThumbnailPanel in check 7 (live image, replace-upload, cache-busted reload).

**Verified:** backend 214 tests pass (5 new: save_location permutation/mismatch
guards + reorder write, save_pin append→upsert + ExtraMapButtons-in-place, via a
fake Firestore); ruff clean on all new/changed files (only the 7 pre-existing
findings repo-wide); `tsc -b` + `npm run build` clean; publish_trips_cli +
publish_inbox py_compile clean. NOT run: publish_trips_cli against real Firebase
(needs dave's eyes — it's the prod-writing surface), the export against Trello.

**Docs:** API_CONTRACT.md (all new endpoints + job kinds), spec build-status note,
NEW `docs/final-check-dev-test.md` (dave's local test runbook — start there).

**Stopped here deliberately:** natural test gate. Dave to dev-test locally per the
runbook (esp. the pin-placer coordinate check against a known pin, and dry-run-only
on the Publisher). Remaining: phase 3 (timing editor + credits — credits also gated
on VR-side agreement), phase 4 (Azure key), phase 6-rest (post-publish buttons,
ready-to-publish auto-queue + Trello moves). Nothing committed in either repo.

## Continued — phases 3 + 4 BUILT; dave's corrections applied (all 7 checks tooled)

**Dave's corrections (mid-build):**
- Thumbnails: the local tree `D:\Dynamic Languages\Images\App thumbnails` is the
  canonical hand-organized archive (~812 jpgs). `thumbnail_local_copy` now REPLACES
  the family's existing jpg in place (exact stem, then the upload_thumbnails_r2
  matching rule); only a brand-new family lands in `_from_review_app\`. Direction
  is always app→tree.
- Credits: the VR app ALREADY reads a single `CustomizableMenus/Credits` doc
  (`CreditsPanel.cs`: `{credits:[{header,entries[]}]}` — one button, all credits).
  Keep that format, append-only. SUPERSEDES the per-country-docs decision; no
  VR-side work, no bulk seeder ("add credits when needed").
- Static images/audio must land under the country folders of `D:\Dynamic Languages`
  → overlay replacement routes through `stage10_static_check.py replace` (local
  store + Drive + OGG trees + S3), never a hand-rolled copy.

**Phase 3 — static images + credits:**
- Backend: `GET .../static-images` (scenes with staticImages[] + house rules),
  `PUT .../static-images/timing` (int appear/disappear, mirrors stage10
  set-timing's fresh-read→whole-quickTrips write; house rules = warnings, only
  appear<disappear hard), `POST .../static-images/replace` (R2 review-overlays
  stage + `replace_overlay` bus job → workstation shells stage10 replace on the
  R2-downloaded file), scene-audio route (`{i}.mp3`/`_q`/`_a`/`_a{n}`, Range,
  cookie-auth) + overlay image route (local → R2 redirect). Credits:
  `GET/POST /api/final/credits` (append-only, format-preserving, creates the doc
  in the app's shape). `publish_trips_cli.py --credits` + `publish_credits` job
  kind (refuses when prod holds blocks staging lacks).
- FE: `StaticImagesPanel` (audio player + per-overlay timeline strip +
  =playhead buttons + replace upload + the Credits append panel) wired into
  check 5.
- Fixed en route: `/api/final/credits` + `/speech-token` had to register BEFORE
  the `/{trip_id}` catch-all (FastAPI matches in registration order).

**Phase 4 — keyword check:**
- Speech stack ported from library-app: `speechCheck.ts` VERBATIM + its 57
  machine-generated C#-parity vectors (all pass under vitest, newly added +
  `npm test`), micSession/micCapture/micStream verbatim, `azureSpeechService.ts`
  with ONE divergence (token from our backend instead of a Firebase callable),
  `backendScoring.ts` stubbed off. NEW `adminMicCheck.ts` — lean armWindow
  sibling exposing the raw N-best (the whole point: offering heard-but-failing
  forms as additions) and releasing the mic device per utterance.
- Backend: `GET /api/final/speech-token` (Azure STS via AZURE_SPEECH_KEY/REGION
  from the Scripts .env — loaded at config import already; 10-min tokens, 8-min
  cache, 503 azure_not_configured until dave creates the key),
  `GET .../keywords` read model, `POST .../answer-keys` (ADD-ONLY;
  409 collides_with_option per the stage9/answer_keys.py safety rule;
  409 duplicate_key).
- FE: `KeywordCheckPanel` in check 6 — play answer clip, speak (dynamic-import
  Azure chunk), N-best chips each rescored solo, one-tap "+ add" for failing
  forms, typed add fallback, availability banner when the key is missing.
- Azure setup instructions written into the runbook §0b (F0 free tier = the
  hard spend cap; key+region into the Scripts .env).

**Verified:** backend 217 tests pass (3 new: credits format/dupes, answer-key
collision/dup/add, timing warnings-not-blocks), ruff clean on changed files,
`tsc -b` clean, `npm run build` clean, vitest 57/57 speech parity. NOT run
against real Azure (needs dave's key) or real Firebase writes.

**Docs:** API_CONTRACT.md (9 new endpoint rows + job kinds), spec §2.5/§6 credits
re-decision + build status (all 7 checks tooled; phase 6-rest remains), runbook
updated (checks 5/6 test steps, Azure §0b, thumbnail in-place semantics).
Awaiting dave's local dev test; nothing committed in either repo.

## Continued — phase 6-rest BUILT: the spec is fully implemented

**Dave's inputs:** Azure key live in the Scripts .env as `Azure_Key1` (+
`AZURE_SPEECH_REGION=uksouth`) — speech_token now reads those names (uksouth
default). Thumbnails: app uploads are NEW families only → the local copy lands in
the RELEVANT folder of the App-thumbnails tree (country top folder from the
TripLocation country, regional subfolder matched from the trip's folderName
segments, e.g. "Scotland/…" → UK Trips\Scotland; same-stem overwrite kept as a
safety net; unresolvable → _from_review_app\). Confirmed the S3↔local rule:
overlays reach the local country folders via stage10 replace; thumbnails via the
above; audio via the existing approve→master-promote path.

**Phase 6-rest:**
- Ready to publish: `POST /api/final/{tid}/ready` (all 7 checks green or
  `409 checks_open`) queues `publish_docs` + `trello_move` (note to=11) bus jobs;
  emerald header button on the checklist when all-green.
- `trello_move` job kind: publisher dispatch runs `Trello/trello_move.py --to
  <lane> <cid>` with its native `--dry-run` when not applying.
- Tool rack: `POST /api/admin/pipeline/tool` (publisher-only, whitelisted):
  post-publish sequence — bump_version (BumpContentVersion --prod), trello_move
  (→12 default), docids_append (dated line under an auto section at the END of
  Content_DocIDs.md — deliberately minimal, the doc is curated), snapshot
  (publish_inbox.py) — plus wrappers: tripdocs (tripdocs_local, apply=--upload),
  static_pic_4k (build --cids, apply=--apply), upload_thumbs (--dry-run native),
  stage10b (apply=--apply-trello), stage9_finalise (requires --steps; 90-min
  timeout). Every run lands as a kind-"tool" bus job; long runs on a daemon
  thread stamping the job at the end (`_finish_subprocess_job` shared tail).
  Write-tools with no native dry-run return a described dry_run, never invented
  flags. Target/steps/lane regex-validated (flag-injection guard).
- FE: PublisherPage ToolsPanel (target+steps inputs, ordered post-publish rows +
  wrapper rows, Dry/Apply per row) replacing the manual-commands note;
  FinalCheckPage Ready-to-publish button; api runTool/readyFinalCheck;
  BusJobKind + labels widened.
- Logins for the test: workstation dev DB already had `admin` (dave's usual
  password); created `dave` (admin, password dave-final-check — change via
  manage.py reset-password).

**Verified:** backend 217 tests pass, ruff clean on changed files, imports clean,
tsc + `npm run build` clean, vitest 57/57. Runbook + API_CONTRACT + spec updated
(spec now marked FULLY IMPLEMENTED; remaining work = whatever the test round
surfaces). Nothing committed in either repo.

## Wrap-up (2026-08-22 early) — red-fable pass + session close

**Red-team (clean-context Fable, both repos, full session scope).** Five real
correctness bugs found and fixed by the reviewer; I hand-traced and verified all
five, plus every quality edit:
1. `publish_trips_cli.publish_credits` compared exact (header, entries) blocks —
   the NORMAL append flow (staging adds an entry under an existing header) would
   have been refused as "production has blocks staging lacks" on every publish
   after the first. Now per-header ENTRY containment (prod-only content still
   refuses). Traced OK.
2. `publish_trips_cli.publish_pin` could duplicate a map button when production
   held the LocationId in the OTHER array (Pins vs ExtraMapButtons) — now
   detected and refused. OK.
3. Pin-placer markers were off by half a pin: the reviewer checked the Unity
   prefabs — MapPinButton pivot (0.5,0.5) means anchoredPosition is the CENTER,
   MapExtraButton (0.5,0) bottom-center — and added the matching CSS translates
   (-50%,-50% / -50%,-100%). Call sites verified. (The 18-unit scaling itself
   was confirmed correct.)
4. Azure token double-caching: backend serves mints from an 8-min cache and the
   ported client cached its copy another 9 min = up to ~17 min on a 10-min
   token = auth-failed recognizers. Client TTL now 60 s. OK.
5. `adminMicCheck` was missing micSession's post-await guard: stop() during
   recognizer construction leaked a live Azure connection. Guard added. OK.
Quality edits verified: CHECKS tooling flags un-staled, count_open simplified,
tool-runner thread try/except (stuck-"queued" guard), "tool" removed from the
queue select + run buttons hidden on tool jobs, stale docstrings fixed.
**One defect in the review itself, fixed by me:** it wrote two literal NUL bytes
into LocationEditor.tsx (the order.join separator) — file read as binary;
replaced with the backslash-u0000 STRING ESCAPE (same behavior) + a stray JSX
bracket re-indented.
Reviewer's deferred findings all triaged AGREE-and-defer (one-sided timing-gap
warning; --location pre-gate copy is pre-existing imported behavior; audit
breadth deliberate; local-overlay-wins irrelevant on the laptop; location-scope
re-key conservative).

**Gates re-run by me after the repair (all green):** backend ruff = exactly the
7 pre-existing findings, pytest 217 passed; FE tsc clean, vitest 57/57, build
clean; Scripts ruff = only the pre-existing publish_inbox I001, py_compile ok.
(Reviewer also ran the Scripts fast pytest: 2608 passed; 6 pre-existing failures
in test_ko_feature_selection.py, unrelated to this session's files.)

**Test doc rewritten** as the simple version dave asked for
(docs/final-check-dev-test.md): login dave/dave-final-check (or admin), start
commands, a 10-step Final-check walkthrough, 3-step Publisher walkthrough,
dry-run-only rule with the two safe Applies named.

**Session closed.** Nothing committed in either repo. NEXT SESSION: dave tests
per the guide, we fix what he finds, then commit both repos, export with push,
laptop deploy (test doc "Then" section).
