# review-app — 2026-07-16

## Finalised-bus consumer (Stage-9 → app reverse handshake)

**Goal:** implement `D:\Dynamic Languages\Scripts\REVIEW_APP_FINALISED_BUS_PROMPT.md` —
consume the new read-only `review-audio/_bus/finalised_trips.json` that Stage 9 will
publish, so shipped trips are marked **Published** and sink to the bottom of the
Completed list, and re-approved-since-shipped trips show **Re-finalise pending**.

**What I did**
- `backend/app/review_bus.py`: `FINALISED_KEY` + `get_finalised_snapshot()` — best-effort
  GET keyed by trip_id; any failure (no creds / NoSuchKey pre-first-publish / network)
  → `{}` = "nothing finalised"; only unexpected errors log. App is strictly read-only
  on the key (mirror of the Scripts-never-writes-completed_trips rule).
- `backend/app/sessions.py`: `_parse_bus_ts` (tolerant ISO→epoch, naive=UTC),
  `_finalised_state` (join on the entry's `completed_at` with 1 s ISO-rounding slop:
  current approval newer → `restale`; else `finalised_at >= completed_at` → `shipped`).
  `completed()` fetches the bus fresh per load (no caching), adds `finalised` /
  `finalised_at` to each item, and stable-sorts shipped rows to the bottom.
- Frontend: `CompletedItem` gains the two fields; `CompletedPage` shows a **Published**
  (sky) / **Re-finalise pending** (amber) badge, greys shipped rows, prints the
  published timestamp, and labels the shipped section with a one-line divider.

**Verified:** `tsc -b` clean; backend syntax + live import ok; `_finalised_state`
exercised (shipped / restale-on-re-approval / absent / junk timestamps) — all correct;
`get_finalised_snapshot()` with no creds degraded silently to `{}`.

## Red-team + deploy (same session)

- **/red-opus** (clean-context Opus): no correctness bugs, no edits. Confirmed the R2
  read can't raise into `/api/completed`, restale-before-shipped ordering is right,
  JSX balances, gates pass. Its one cosmetic note (li-level `opacity-60` dimmed the
  Published divider) was applied — opacity moved to the inner row div.
- Committed **f9fad71**, pushed; **deployed to the Ubuntu laptop**: journal showed no
  reviewer traffic for 30 min → pull, `npm run build`, NOPASSWD
  `systemctl restart review-app.service`. Verified: review-app + review-tunnel active,
  tunnel 200, no R2-creds warning at service startup, `get_finalised_snapshot()` = 0
  trips (expected — Scripts hasn't published the key yet).

**Next steps**
- Scripts side (`stage9/completed.py` publisher) lands separately in dynamic-content;
  once it first publishes, shipped trips flip to Published on the next Completed load.
- Backlog updated (Done entry 2026-07-16).

---

## 23:20 — EU A12 gaps doc assessed + closed (both repos)

**Goal:** assess `REVIEW_APP_EU_A12_GAPS.md` (Scripts repo, EU A12 Drafting) and close the real gaps.

**Assessment:** all claims verified TRUE against code. Worse than the doc: `language_of`
called every EU trip "English", so the gender-voice fallback would pick an English voice too.

**What I did**
- review-app `85c95ec`: `speed_for_trip` mirrors `run_eu.is_low_level` (`_A12_`/`_Beg_` EU → 0.7,
  `_B1_` EU → 0.85); `language_of`/`voice_for_gender`/`LANGUAGE_FALLBACK_VOICE` know ES/FR/DE/IT;
  `sessions._LEVEL_SUFFIXES` labels EU A12 as "A12" → existing 3s SceneDesc beginner tail (= the
  pipeline's A12 pad; `_Beg_` EU correctly stays 0.4s).
- dynamic-content (uncommitted, for GitHub Desktop): `Trello/review_block.py` — `STRICT_DATA_ROOTS`
  (`Research and Writing/data`, source.json REQUIRED, no name-derived fallback — natives share the
  folder), EU `_A12_`/`_B1_` level suffixes, `derived_source_en_id` returns None for EU leveled ids
  (no `_EN` rung exists). NEW `Trello/make_eu_review_cards.py` — idempotent lane-6 card maker for
  the 33 EU families (dry-run verified; `--apply` NOT run). `stage_eu_level.py` now emits
  `source.json {"source_en_id": <_Beg parent>}` (swept into user commit `86c7b59`).
- Backfilled 33 `source.json` files into the A12 draft dirs (gitignored data; from draft.json `source`).

**Verified:** review-app spot-checks (speed/lang/level/tail matrix, EN/JP/ZH unchanged); Scripts
`review_block` chain (`Ainsa_A12_ES` → level ES-A12, source Ainsa_Beg, native=False, family index
correct, no bogus `_EN` keys); ruff (only pre-existing I001) + pytest 278 passed. Red-teamed via
clean-context Opus: no findings, no edits.

**Open / carried forward**
- Deploy `85c95ec` to the laptop (uvicorn restart + tunnel check) — NOT done yet.
- Run `make_eu_review_cards.py --apply --sync` (creates 33 Trello cards — Dave's call), then
  `export_review_trips.py` to refresh the manifest.
- Latent (red-team finding, triaged as defer): bare `_Beg` parent ids (Ainsa_Beg, 19 families)
  still parse as English/1.0x — only matters if a bare `_Beg` parent is ever itself reviewed in
  the app; needs a lookup-based language resolver, not id parsing.
- Commit review_block.py + make_eu_review_cards.py in dynamic-content (GitHub Desktop).

### 23:59 follow-up — deploy + red-fable + dynamic-content CI

- **Deployed** `85c95ec` to the laptop: both repos pulled, `review-app.service` restarted clean,
  tunnel active, `_BEG_` branch confirmed in the deployed file. LIVE.
- **Red-fable pass** (second clean-context review, per Dave, over ALL session changes incl. the
  red-opus'd ones): no bugs; one comment-only edit (why-comment on `_leveled_dir_exists`'s
  deliberate LEVELED_DATA_ROOTS-only scope). Verified + committed.
- **dynamic-content CI fixed** (was red after Dave's push): two workstation-only tests broke the
  dev-extra-only CI env — `test_es_demonyms` (module-level lemminflect import → importorskip,
  `855617f`) and, unmasked by that, `test_german_group_ids_loaded_from_docs` (needs the
  non-git `Germany/_docs.json` → skip-if-absent, `9d92420`). CI run for `9d92420`: **success**.
- Scripts backlog updated with the EU rollout items (cards `--apply`, bare-`_Beg` latent gap).
- Still pending Dave: `make_eu_review_cards.py --apply --sync` + manifest re-export.
