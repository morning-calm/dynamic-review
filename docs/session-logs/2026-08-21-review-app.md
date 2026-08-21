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
