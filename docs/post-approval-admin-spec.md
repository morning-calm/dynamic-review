# Post-approval admin workflow — user spec & build plan (DRAFT for approval)

*2026-08-21 — covers: the "Final VR Check" admin UI (Trello lanes 10/10b/11), the
workstation Publisher app (lane 11 → 12 + local copies + S3), and the way forward on
new-category search. Grounded in a survey of `D:\Dynamic Languages\Scripts`, the
library-app repo, and the Unity checkout at `D:\Projects\dynamic-languages`.*

---

## 0. Architecture in one paragraph

**One codebase, two instances.** The laptop review app stays what it is — the
staging-only human-check surface — and gains a new admin **"Final check"** workflow
covering checks 1–7 below. The **Publisher app is the same review-app repo running on
this workstation** with `REVIEW_APP_PUBLISHER=1` (this mode, the R2 `_bus` job queue,
and a `publish_trip_text.py` runner **already exist** in `routes_admin.py` /
`review_bus.py` / `publish_inbox.py`). We extend that skeleton into a full publish
console that shells the existing Scripts-repo tools. Nothing is frozen into an .exe:
"app version only executable here" = a `publisher.cmd` launcher that starts uvicorn on
`127.0.0.1:8010` serving the built SPA, opened in an Edge/Chrome `--app=` window (looks
and feels like a desktop app, zero packaging pain — PyInstaller would fight ffmpeg,
whisper, and the Scripts-repo imports). The prod key never moves: it stays
`firebase_production_key2.json` in the Scripts root, readable only by the workstation
instance. The laptop instance keeps zero prod credentials and hands work to the
workstation via the existing R2 `_bus`.

```
Laptop (staging only)                      Workstation (publisher)
─────────────────────                      ───────────────────────
Final-check UI (checks 1–7)   ── R2 bus ── Publish console (lane 11→12)
writes staging Firebase                    runs Scripts: gate → publish →
queues publish/asset jobs                  bump version → Trello → S3/R2/local
```

---

## 1. New-category search — way forward

What shipped today (sibling check on the Descriptions page) matches categories against
**TripGroup descriptions** in the same TripLocation. The deeper question — "does any
older trip in this country actually *fit* the new category?" — needs richer signals
than the description text.

**Recommendation: do NOT add a country field to ContentEnrichment.** The survey
confirmed it has none, but country is already derivable two ways: `folderName`'s first
segment (what `enrich_trip.py` itself uses) and the TripLocations→TripGroups index (what
the sibling check I shipped uses). Adding a denormalised country to ~900 enrichment docs
creates one more thing to keep in sync for no query we can't already do.

**Instead, fold the search into Final-check step 2 (categories) using ContentEnrichment
content signals**, and let it run wherever the Firestore read is cheap:

- Backend builds a per-country index: trip → `{applicableCategories,
  newCategorySuggestions, themes, keyVocabulary, namedEntities}` (one
  `ContentEnrichment` sweep with `select()`, TTL-cached like the TripGroup index).
- When the admin confirms a **new** category, the check returns country-mates whose
  enrichment fields or descriptions mention it — a strictly better version of today's
  description-only match, still deterministic and instant.
- Optional later upgrade (workstation, batch): a Gate-2-style `claude -p` judgment pass
  "does trip X fit category Y?" over the candidates, run offline and cached — never in
  the request path.

This works identically on laptop and workstation (both read staging), so it does not
force the categories feature onto the publisher app — but it lands *inside* the
Final-check UI, which is where you'll be confirming categories anyway.

---

## 2. The "Final check" workflow (laptop app, admin-only)

A new page: **Final check** — one checklist per TripGroup family, seeded from
completed/approved trips (lane 10 = `10 · Human Final VR Check`). Each check is a
card with its own Done flag stored in a new `final_checks` table
(`tg_id, check_key, state, by, at, note`); all-green unlocks "Ready to publish"
(queues the bus job + moves the Trello card to 11). Same pattern as the existing
tripdesc workflow: SQLite state, targeted staging writes, nothing auto-applies.

### Check 1 — Description re-read
Read-only render of `descriptionHome`/`descriptionTarget` (reusing the tripdesc data)
with an **Edit** escape hatch that reopens the tripdesc item. One button: *Read &
confirmed*.

### Check 2 — Categories confirm + cross-trip search
The category editor shipped today (all-in-use chips with counts, "never used before"
section, sibling-fit panel), upgraded with the ContentEnrichment country index from §1.
Confirming here is the durable record; the panel lists country-mates that may fit any
newly-added category.

### Check 3 — TripGroup `contentTitleKey`
Inline edit of the TripGroup's title key with the current production value alongside
(from the `_bus/prod-snapshot` the workstation exports — same drift pattern as
`drift()` today). Targeted `.update()` on staging.

### Check 4 — TripLocation + map pin
The survey nailed the mechanics, so this is fully buildable:

- **TripLocation editor**: `contentId`, `locationTitleKey`, `skyboxTextureId`, and the
  `trips[]` **order** (drag-to-reorder; order = tile order in the headset). Writes are
  targeted updates mirroring `09c_gdoc_to_triplocation.py` (never clobber other fields).
- **Skybox picker**: a dropdown of every `skyboxTextureId` currently used across
  TripLocations **plus** a committed manifest of shipped skybox texture names generated
  from the Unity checkout (`scripts/export_skybox_manifest.py`, run on the workstation,
  committed like the VideoIds snapshot). ⚠ A *new* skybox cannot be "uploaded" from the
  web — skyboxes are Unity build assets (7680×3840 equirect, lane 8); the UI can only
  reference names that will exist in the build. Picking a name not in the manifest gets
  a loud warning, not a block.
- **Pin placer**: the VR pin system is a *flat* rect — CurvedUI bends it only at render
  time — with origin at the map image's **top-left**, +x right, −y down, long axis
  scaled to **18 units** (`MapPanelController.maxMapSize`). So a 2D web placer is a
  faithful editor: we commit the 8 country map PNGs from
  `D:\Projects\dynamic-languages\Assets\_Sprites\Maps` into the app (small, static),
  render the right one per `CustomizableMenus/{LANG}_Trip_Menu.MapName`, overlay the
  existing `Pins`/`ExtraMapButtons`, and click-to-place computes
  `xPos = 18·px/wpx`, `yPos = −18·(hpx/wpx)·py/hpx` (short-axis scaled by aspect).
  Writes append/update the pin in the **staging** menu doc via the same discipline as
  `vr_english_prep.add_map_pin` (read-modify-update of `Pins` only). Pin size
  (~0.59×0.62 units) is drawn to scale so overlap is visible.
- Production pins are *not* touched here — they ship via the Publisher (§4), which
  gains a targeted "publish menu pin" op (no script exists today; the manual prod pin
  add in `docs/plans/2026-08-19-triplocation-release-order.md` is exactly the gap).

### Check 5 — Static image timing + credits
- **Timing check**: per scene with `staticImages[]`, an audio player over the working
  `<i>.mp3` (already served by the app) with a timeline strip: current
  `appearAtSeconds`/`disappearAtSeconds` markers, the scene's **subtitles** (the app
  regenerates cue text from Whisper the same way `stage9/subtitles.py` does — or
  cheaper: display the SceneDesc with the word timeline we already compute for
  splicing), and two buttons — **Set appear = playhead**, **Set disappear = playhead**.
  Writes the two fields on the staging scene entry (same constants surfaced as hints:
  min 6s, max 15s, ≥1s gap, from `stage9/image_timing.py`).
- **Replace/upload image**: uploading a new overlay from the laptop can update staging
  + R2 review copies, but the canonical distribution (Drive + local OGG folders + S3
  for every rung) is `stage10_static_check.py replace` on the workstation — so the
  laptop UI takes the upload, stages it to R2, and queues a bus job the Publisher runs
  through that script. No silent divergence.
- **Credits**: no `CustomizableMenus/Credits` doc exists anywhere yet — this is
  greenfield. Proposal: one staging doc `CustomizableMenus/Credits` shaped
  `{ "countries": { "Germany": [ {"image": "<filename>", "tripId": …, "credit":
  "<attribution text>", "sourceUrl": …} ] } }`, seeded from the existing
  `{name}.attribution.txt` files (`07_static_images.py` writes them for every
  Wikimedia fetch) by a one-off workstation script, then appended by the upload flow
  above whenever an externally-sourced image lands. The VR app reads it per country.
  **Needs your sign-off on the shape before anything writes it** (the app team consumes
  it).

### Check 6 — Keyword check (lane 10's "check keywords")
Reuses library-app's engine, which is a faithful port of the VR app's scorer:

- Copy `speechCheck.ts` (+ its generated parity vectors/tests) and the mic-session
  stack (`useMicSpeech`, VAD-gated capture, `SpeechBubble`) from
  `D:\Projects\WebApp\library-app`.
- Per question/keyword scene: play the answer audio, show the accepted set
  (`questionOptionKeys[0]` / `questionKey` + current `additionalAnswerKeys`), let the
  admin **speak the answer**; Azure's N-best (Lexical + ITN) is scored locally, and any
  reasonable variant Azure heard that *fails* the current keys is offered as a one-tap
  **add to `additionalAnswerKeys`** (add-only, collision-checked against other options —
  the same rules as `stage9/answer_keys.py`, which stays the automated first pass; this
  UI is the human top-up).
- **Azure key**: a NEW key on its own Azure resource with a hard spend cap, stored
  backend-side in the review-app env (never shipped to the browser); the backend mints
  10-minute tokens exactly like library-app's `getSpeechToken` callable. Admin-only
  route, so exposure is one user.

### Check 7 — Thumbnails
- Shows the TripGroup's current `thumbnailTextureId` + the live R2 object from
  `dynamic-languages-thumbs` (public domain already exists).
- **Upload** replaces it: laptop writes R2 + the staging field (mirroring
  `upload_thumbnails_r2.py`'s naming), and queues a bus job so the Publisher also
  copies the file into the canonical local tree
  (`D:\Dynamic Languages\Images\App thumbnails\…`) at publish time — the local tree is
  workstation-only, so the copy can't happen from the laptop.
- "Look for local version" runs on the **workstation** instance (it can see the tree)
  and reports via the same job object.

---

## 3. Trello integration

The Final-check page shows and drives lane state via the existing CLI wrappers
(`Trello/trello_move.py`, resolution by Content-ID custom field): entering final check
= card sits in 10; keyword/static work done → `stage10b.py` (recall quizzes + keyword
copy) is a Publisher job with `--apply-trello` (10b → 11); "Ready to publish" moves to
11; a successful publish moves to 12. All moves are logged on the check record; Trello
API creds are already in the Scripts `.env`. (Laptop has no Trello creds today — moves
are queued as bus jobs and executed workstation-side, keeping all external creds on one
machine.)

---

## 4. The Publisher app (workstation only)

Extend the existing `REVIEW_APP_PUBLISHER=1` mode into a **Publish console** page:

1. **Inbox** — the `_bus/jobs` list (exists), now with job kinds beyond `publish`:
   `publish_text`, `publish_docs` (full Trips/TripGroups/TripLocations copy),
   `publish_pin`, `replace_overlay`, `thumbnail_local_copy`, `stage10b`, `trello_move`.
2. **Pre-flight** — one click runs `publish_audio_gate.py --report` and renders the
   READY / BLOCKED / ALREADY-BROKEN-LIVE buckets. Publishing a blocked trip requires
   the gate's own scoped override, surfaced explicitly.
3. **Publish** — the big one. `PublishTrips-Select.py` is interactive (stdin prompts),
   so it gets a thin **non-interactive sibling in the Scripts repo**
   (`publish_trips_cli.py`: same `copy_document`/`copy_trip_group`/
   `copy_trip_location` functions imported, argv-driven, dry-run default,
   `--apply --i-am-sure` gates like `publish_trip_text.py`). The UI shows the dry-run
   diff, warns loudly on the **TripLocation cascade** (publishing a location publishes
   every group on the tile) and the gate's sibling-relabel rule, then applies.
4. **Post-publish, in order** — `BumpContentVersion.py --prod` (cache-bust),
   `trello_move.py --to 12`, `Content_DocIDs.md` append, prod-snapshot refresh
   (`publish_inbox.py snapshot`) so the laptop's drift indicators go green.
5. **Local copies & S3** — buttons wrapping `tripdocs_local.py --upload`,
   `static_pic_4k_s3.py`, `upload_thumbnails_r2.py`, `stage9_finalise.py --steps …`
   re-runs, each streamed to a log pane on the job object.

Every script call follows the `run_pipeline_job` pattern already proven: subprocess
with explicit utf-8, timeout, log tail onto the job, dry-run default, argv validated
against the flag-injection guard. **The console never reimplements a script — it only
sequences them**, so the CLI paths keep working unchanged.

**Launch**: `scripts/publisher.cmd` — sets `REVIEW_APP_PUBLISHER=1
REVIEW_APP_SERVE_FRONTEND=1`, starts uvicorn on `127.0.0.1:8010`, opens
`msedge --app=http://127.0.0.1:8010`. Desktop shortcut = the "app". No tunnel, no
exposure; the workstation DB is separate from the laptop's (review state stays on the
laptop; the publisher's own state is the R2 bus + Trello + Firebase, so DB divergence
doesn't matter).

---

## 5. Build plan (phases, each shippable alone)

| # | Phase | Scope | Size |
|---|-------|-------|------|
| 1 | Final-check framework | `final_checks` table, checklist page seeded from approved trips, checks **1–3** (desc re-read, categories + ContentEnrichment country search, contentTitleKey w/ prod drift) | M |
| 2 | TripLocation + pin | Location editor, trips reorder, skybox manifest + picker, map pin placer (maps committed, coord transform, staging write) | M–L |
| 3 | Static images | Timing editor (player + set-appear/disappear), overlay upload → R2 + bus job, Credits doc schema + seeder (**after you approve the shape**) | M |
| 4 | Keywords | Azure capped key + token route, speechCheck/mic port, add-to-additionalAnswerKeys UI | M |
| 5 | Thumbnails | Current-thumb view, upload → R2 + staging, local-copy bus job | S |
| 6 | Publisher console | `publish_trips_cli.py` shim (Scripts repo), job kinds, gate report, publish flow + post-publish sequence, `publisher.cmd` | L |

Suggested order: 1 → 6a (just the shim + publish flow, so the *existing* text-publish
gets its UI early) → 2 → 3 → 4 → 5 → 6 rest. Phases 2/3/6 each get a red-team pass
before deploy (prod-writing surface).

## 6. Decisions I need from you

1. **Credits doc shape** (§2.5) — one `CustomizableMenus/Credits` doc keyed by country,
   or per-country docs (`Credits_DE`…)? Needs agreeing with the VR app side.
   Yes one per country.
2. **Keyword-check Azure**: new capped-spend key on its own resource — you create it,
   I wire it. OK? Yes OK.
3. **Pin publish**: happy for the Publisher to gain a targeted prod
   `CustomizableMenus` pin write (currently a fully manual edit)?
4. **Final-check seeding**: every `completed_trips` family, or only lane-10 Trello
   cards? (I'd start from lane 10 — it's the explicit signal.)
5. Category LLM pass (§1, optional): want it, or is the deterministic
   enrichment-index search enough to start?
