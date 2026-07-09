# review-app — 2026-07-09

## Red-team review of the direct scene-structure editor (96d6de8)

**Goal:** adversarial review of `backend/app/structure.py` + the new
`/api/admin/structure/*` routes + `StructureEditorPage.tsx`; fix real defects,
commit locally on main (no push, no deploy).

**What I did** (commit `690d3a3`):
- Made every structural op a **Firestore transaction**: re-read Trip, re-check
  `base` fingerprint inside the txn, write quickTrips + renumbered
  TripLocalizations atomically. Outer retry for read-time `Aborted` (the
  `@firestore.transactional` decorator only retries commit-time aborts —
  verified against the installed 2.21 source); persistent contention → 409.
- **sceneId assignment vs real data:** staged videoUrls are bare Vimeo ids, so
  the mp4-stem regex never fired → every add/rekey minted a duplicate atom.
  Now: registry lookup `videoIds array_contains vid` reuses the existing atom;
  else derive from the VideoIds filename stem (`725676693` →
  `s20220508-141520`, matches the real registry doc); else filename fallback;
  all mint/derive still only via the shared `scene_ids` ruleset.
- `_registry_add_use` no longer clobbers an existing atom's pinned
  `currentVideoId` (the `remap` docs) or `kind` (`flat` atoms exist).
- Requested `scene_id` reuse now 422s (`unknown_scene_id`) when the atom is
  not in the registry — no more phantom registry docs from typos.
- Registry updates post-commit + best-effort: failures land in `warnings` +
  the audit row (with `scene_id_how` provenance), never a 500 hiding an
  applied write.
- Post-commit detection + loud warning when a session was opened mid-op
  (guard race can't be fully prevented across SQLite/Firestore).
- `set_categories` 404s on a bogus trip BEFORE touching the TripGroup.
- FE: NaN/clamp guard on the add-position input; de-duplicated scene list key.
- API_CONTRACT.md add/swap-video rows updated.

**Verified:** `py_compile` + `import app.main` green; `npm run build` green.
Behavioral suite against STAGING with a fully synthetic disposable trip
(`Fable_RT_Structure_EN` + TripGroup + TripLocalizations + synthetic
`Scenes/s2099…` docs): 31 checks — reorder/remove/add loc renumbering, stale
base 409, threaded race (one ok / one 409), active-session 409,
unknown-scene-id 422, atom reuse by videoId, dup-atom usedBy retention, pinned
currentVideoId/kind preservation, swap rekey vs non-rekey registry semantics,
derive-from-stem, categories, bogus-trip 404, audit rows. 3× ALL PASS; every
synthetic staging doc and local structure_ops/session row deleted
(`leftovers: none` verified). Real staging trips touched READ-only.

**Noted, not fixed (deliberate/accepted):**
- Fingerprint doesn't cover videoUrl when a sceneId is present — a concurrent
  non-rekey swap-video isn't caught by `base` on an index-targeted op; identity
  is still correct, so left as designed.
- Same stem-derived id already in the trip gets `-b` via `ensure_unique`
  (mirrors `assign_scene_ids`); explicit same-atom reuse is via `scene_id`.
- Design intents left alone per owner decision: direct writes outside sessions,
  index-keyed TripLocalizations, positional media warning-only, verbatim
  categories.

**Next steps:** none pending for this feature; unpushed commits remain local
on main per instructions.

## Second-pass red-team of 96d6de8 (independent re-read)

Re-reviewed the original commit + current post-fix code (`690d3a3`). Confirms
the prior severity ranking; no new ship-blockers. Residual (still open, not
fixed in either pass):

- `_new_scene_id` runs *inside* the txn `mutate` (non-txn registry reads +
  `mint_opaque` re-rolls on Aborted retry — last successful commit wins, but
  concurrent first-adds of the same unregistered videoId can still mint two
  atoms before either registry write lands).
- Fingerprint still identity-only (no videoUrl when sceneId present).
- `set_categories` still no `active_session` / no TG+Trip transaction / no base.
- Positional-media warning is soft (no publish/finalise hard gate).
- Empty-trip remove allowed; new-scene template is a minimal field subset;
  rekey UI has no `scene_id` override (API has it); rekey keeps stale text by
  design.

No code changes in this pass — report only.

## Second-pass CLOSED (session resumed after usage cut-off, later 2026-07-09)

The section above was recovered from a git stash (usage ran out mid-pass; the
entry was never committed). Resolution of the residuals:

**Fixed (this session):**
- `set_categories` — TG + Trip now written in one Firestore **batch** (atomic:
  both land or neither); an active session on the trip now produces a
  **warning** (not a 409 — categories aren't index-addressed, so no desync
  risk, but that session's submit re-derives tripCategories from the
  description and can overwrite the verbatim edit).
- `remove` — refuses to remove the trip's only remaining scene
  (`422 last_scene`; an empty quickTrips breaks readers).

**Accepted (documented, deliberately not fixed):**
- `_new_scene_id` dup-mint race on concurrent first-adds of the same
  unregistered videoId — single-admin tool, registry is repairable metadata,
  fix would need a registry-side txn for marginal benefit.
- Fingerprint identity-only (owner call, unchanged); positional-media warning
  stays soft (owner call); minimal new-scene template + no FE `scene_id`
  override on rekey + rekey-keeps-stale-text (all by design / FE nicety only).

**Verified:** py_compile + import green; 6/6 live checks against a disposable
synthetic staging trip (`Fable_RT_Cats_EN` + TripGroup): last-scene 422,
atomic TG+Trip categories write, no-warning when idle, warning + write-applies
with an active session row. All synthetic docs + local sessions/structure_ops
rows deleted (`leftovers: none` verified).

## Dave's S3 question (answered, no code change)

Q: is audio pulled from AWS S3 when a published trip is edited (post-Stage-9 audio
lives there)? **A: no — by design.** `resolve_audio_dir` (sessions.py:236) goes
local Quicktrips masters → Audio Generation trees → R2 `review-audio` seed cache;
S3 is never read. S3 holds Stage 9's OUTPUTS (ogg + subtitles), not the mp3
masters the splice engine needs — splicing an ogg re-encode would degrade quality.
A published trip is editable while its masters exist locally or on R2 (mirrored at
approve + by the bulk uploader). Gap only for long-ago-published trips that never
passed through the app AND lost their local masters → `422 bad_folder`; parked as
BACKLOG #6 (preferred fix = restore masters to R2, not S3-read plumbing).

## FEATURE: All-trips Location + Country filters (orchestrated, sonnet agent)

Dave asked for two separate filters on the admin All-trips (/staging) page:
**TripLocation** and **country**. Mapping (FIREBASE_STRUCTURES.md + verified
live): `TripLocations.trips[]` = TripGroup ids → `TripGroups.trips[].tripId` =
Trip ids; `locationName`/`locationCountry` on the TripLocation doc.

- **Backend** (`routes_admin.py`): `_staging_index` also sweeps TripLocations +
  TripGroups (same lock/TTL); each row gains `locations[]`/`countries[]`
  (any-match filtering — 13 TGs legitimately sit in several TripLocations, e.g.
  London + "UK Theme") plus ", "-joined `location`/`country` display strings.
  `staging_trips` gained independent case-insensitive `location=`/`country=`
  params (applied before the 200 cap) + distinct `locations`/`countries` lists
  for the dropdowns.
- **Stale-duplicate guard**: leftover `TripLocations/JapaneseTrips` doc dupes
  JPHistory's name but lists the Spanish Cuevas_Trip (would mislabel it Japan).
  Same-name docs keep only the biggest — RE-KEYED by me to (name, country):
  name-alone would drop the legit same-name Alps docs (DE/FR/IT) the moment
  their trip counts diverge. Proper fix = delete the stale doc (BACKLOG #9).
- **FE**: StagingSearchPage gains "All countries"/"All locations" selects wired
  into the debounced fetch; row meta shows location · country. api.ts types
  extended. API_CONTRACT.md row updated.
- **Verified**: py_compile + import green; `npm run build` green; live against
  staging: 862 trips indexed, 709 mapped (~82%); assertion suite ALL PASS —
  Cuevas ES-not-JP, Alps 3-country survival, Christmas_Lights matches under
  both London AND UK Theme, Japan=116/tokyo=29/taipei+Taiwan=7 spot filters,
  countries list exact.

**Deploy note (superseded same day):** initially left local; dave then asked
for a live deploy — see the deploy section below.

## FEATURE: text-only editing when audio is unavailable (dave's ask)

Admin `/api/admin/open` no longer 422s (`bad_folder`) when no MP3 masters
resolve locally or on R2: the session seeds TEXT-ONLY (each audio field
degrades via the existing per-field `master.exists()` fallback) and the
Session payload gains `audio_unavailable: true`; ReviewPage shows a soft
amber banner (playback/regenerate/splice disabled per-field, text edits +
submit/approve work normally). The reviewer flow keeps the hard 422.
Verified live with a disposable synthetic staging trip: reviewer 422
preserved, admin seed text-only + flag, text edit works, flag persists,
real sessions with audio stay unflagged; all test docs/rows cleaned.

## DEPLOYED to the laptop (dave's explicit go — "I want to test them live")

All 7 commits pushed to origin/main (`61efb1d..c172604`) and deployed:
- **Idle verified first:** last field_edit 2026-07-08 21:23 (785 min), live
  presence 0.
- **⚠ Incident during restart:** service crash-looped —
  `ModuleNotFoundError: scene_ids` (structure.py's Scripts-repo import; first
  laptop deploy of the structure editor). Root cause: the laptop's
  `dynamic-content` checkout was months behind and **has NO auto-pull cron**
  (only the review-app repo does). Fix: verified `scene_ids.py` is
  stdlib-only + committed on origin, laptop tree clean → `git pull --ff-only`
  in `~/Desktop/Server/Scripts` → restart → healthy. RULE: deploying
  review-app code that imports NEW Scripts modules needs a manual
  dynamic-content pull on the laptop first.
- **Verified:** service `active`, `/api/trips` 401 (up), new admin routes
  loaded (401 not 404), FE bundle `index-lPdzXmwL.js` (hash identical to the
  workstation build), cloudflared runs as systemd `review-tunnel.service`
  (untouched), public `https://review.dynamiclanguages.org/` → 200 SPA +
  401 API. **Data identical pre/post:** sessions {approved 3, in_review 10,
  submitted 2}, field_edits 906, live tokens 19 — reviewers stay logged in.
- Still pending on the laptop: interactive `systemctl daemon-reload`
  (unit-file-changed warning persists; harmless, backlogged).

**Now live for testing:** All-trips Location/Country filters, text-only
admin editing of audio-less trips (soft warning), structure editor
(/structure/:tripId via the All-trips Structure button), atomic
set_categories + last-scene guard, and the earlier structure-editor arc.
