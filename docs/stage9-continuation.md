# Stage-9 continuation — handoff for the dynamic-content session (2026-07-02)

Written at the end of the review-app session that shipped the completed-trips export.
The next session runs in `D:\Dynamic Languages\Scripts` (its own CLAUDE.md; it does NOT
see the review-app project memory) — point it at this file.

## What the review app now provides (all live, on `main`, pushed)

1. **`D:\Projects\WebApp\review-app\completed_trips.json`** — the Stage-9 handshake.
   Machine-readable mirror of the review app's `completed_trips` table; gitignored
   (server-written), rebuildable any time with
   `py -3.12 D:\Projects\WebApp\review-app\scripts\export_completed.py` (read-only DB open).
   Shape:
   ```json
   {"generated_at": "<iso>", "source": "review.db completed_trips",
    "trips": [{"trip_id": "...", "method": "approved|manual", "completed_by": "...",
               "completed_at": "<iso local-tz>", "session_id": "sess_…|null",
               "note": "", "language": "English|Japanese|Mandarin",
               "family": "<name-derived family base, same key as trips_to_review.json>"}]}
   ```
   - **`method` is load-bearing.** `approved` = the review app promoted corrected
     `<i>.mp3` masters IN PLACE **and** wrote the corrected text to staging (Firebase);
     canonical takes are also mirrored to R2 `review-audio/<contentID>/`. `manual` =
     workflow marker only — NOTHING was written; finalise from the masters as they exist.
   - **Current-state snapshot, not a log.** Un-complete REMOVES the row. Absence ≠ undo S3.
   - Refreshed on approve / manual complete / un-complete (atomic write, safe to read
     any time).

2. `trips_to_review.json` — unchanged contract (Trello lanes 6/7, priority order).

## What the Scripts session should build

1. **`stage9/completed.py` reader** (~20 lines): load the JSON; expose e.g.
   `pending(ledger) -> [trip]` = trips whose `completed_at` is NEWER than Stage 9's own
   "finalised" ledger entry (a tiny local JSON keyed by trip_id → finalised_at). The
   ledger-comparison rule correctly handles un-complete → re-review → re-approve
   (fresh `completed_at` → re-finalise).

2. **`COUNTRY_CFG` entries for Japan and Taiwan** in `stage9/common.py` — the hard
   blocker. `paths_for` currently `SystemExit`s for every `_JP`/`_ZH` trip, so
   `stage9_finalise` cannot resolve mp3/ogg dirs for the JP N4/N5 + ZH HSK trips now
   completing. Recommend the GreatBritain pattern (`review_audio_r2: True` — fetch
   corrected masters from `review-audio/<cid>/` at finalise) rather than depending on
   local promoted files. Known audio locations if local paths are wanted:
   - Japan Day Series (older, LIVE): `D:\Dynamic Languages\Japanese\Trips\Day Series\{MP3,Ogg}\<loc>\<trip>`
   - KP-era / leveled: `D:\Dynamic Languages\Scripts\Audio Generation\<trip>` (mp3) and
     `Audio Generation\ogg\<trip>` (ogg + the `{i}.jpg` stills + flat overlay images)
   - Japan `_EN` masters: `Audio Generation\Sent to KP\MP3\<trip>`

3. **CJK subtitle decision (needs dave's call FIRST)**: stage-9 subtitles assume
   `whisper_lang: en`. For `_JP`/`_ZH` the human-reviewed text itself (kana/hanzi)
   should drive the subtitles, not ASR — decide format/pipeline before the first JP
   finalise; it shapes what the new COUNTRY_CFG entries need.

## Gotchas the review-app session learned (relevant to stage 9)

- `review_block.source_en_id_of` now has a name-derived fallback for draft dirs
  without `source.json` (older JP generation) — already committed in dynamic-content
  (`bbf7deb`, on `main` AND `chore/dev-tooling-tests-skills`).
- `export_review_trips.py` prints an audit of audio-ready drafts on NO lane-6/7 card —
  currently flags Tokyo_01/02 N4 + a batch of Scotland A12/B1 as unqueued (dave's triage).
- 3 trips still have no audio (`Blaenavon_B1_EN`, `Caerphilly_Castle_B1_EN`,
  `Lake_District2_B1_EN`) — they upload when their 5c runs.
- Whisper word times absorb pauses into neighbouring words and whisper HALLUCINATES
  completions of truncated words — never use ASR timing/transcripts as ground truth for
  cut placement or truncation detection (full war story:
  `D:\Projects\WebApp\review-app\docs\splice-end-cutoff-analysis.md`).
