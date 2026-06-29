# review-app backend (FastAPI)

Internal tool backend: load a staged English `_EN` trip, edit its text + audio in a
GUI, surgically **splice** corrected phrases into the original takes, and hand off
**corrected text → staging** + **corrected `{i}.mp3` masters → local** for Stage 9
(which owns ogg/subtitles/timings/S3). Implements `../API_CONTRACT.md` exactly.

## Run

```bash
cd backend
py -3.12 -m pip install -r requirements.txt        # into the global 3.12 interpreter
REVIEW_APP_TOKEN=dev-token py -3.12 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- Binds to `127.0.0.1` only. Every `/api/*` request (except `/api/health`) needs
  header `X-Review-Token: <REVIEW_APP_TOKEN>` (dev default `dev-token`).
- `/audio/*` and `/overlays/*` are token-exempt: browser `<audio>`/`<img>` elements
  cannot set custom headers, and the localhost-only bind is the control there.
- Reads `D:\Dynamic Languages\Scripts\.env` for `ELEVENLABS_API_KEY` / `GEMINI_API_KEY`
  and uses `firebase_staging_key.json` there via the reused `stage9.common.db()`.

## How it reuses the Scripts codebase

`app/config.py` puts `D:\Dynamic Languages\Scripts` (and `…/Research and Writing/stages`)
on `sys.path` and loads that `.env` — import it first. Then:

| Reused | From | How |
|---|---|---|
| `db`, `get_trip`, `paths_for`, `COUNTRY_CFG` | `stage9.common` | imported as-is |
| `transcribe_words` | `stage9.whisper_timing` | imported; serialized behind one GPU lock |
| `load_overrides`/`apply_overrides`/`prompt_rule` | `pronunciation_overrides` | imported |
| `VOICES`, `clean_text`, `validate_and_clean`, `generate_audio`, `strip_url_lines` | `RegenerateSceneAudio-EditMe.py` | **ported** into `app/audio_core.py` (hyphenated filename + import side effects) |
| `trip_categories_from_description` | `09_gdoc_to_firebase.py` | **ported** into `app/staging.py` (that module pulls google_auth at import) |
| `generate_with_timestamps` | — | **new** (`/with-timestamps` EL endpoint + char→word aggregation) |

Never modifies the Scripts repo; the master MP3 tree is read-only until `/submit`.

## Layout

```
app/
  config.py        sys.path/.env bootstrap, constants, token, CORS
  db.py            SQLite (WAL, serialized) — sessions / field_edits / audio_versions
  models.py        Pydantic request bodies
  staging.py       Firebase read + targeted submit writes + category re-derive (merge)
  audio_core.py    ported TTS/cleaning + new with-timestamps + anchor-eligibility
  audio_io.py      ffmpeg mp3<->wav + numpy energy/level/peak + length-preserving splice
  audio_splice.py  the splice engine: plan_segment (regenerate) + do_splice (combine)
  sessions.py      seed/resume, field serialisation, all field ops, submit
  routes_*.py      REST + Range audio/overlay/download
  main.py          FastAPI app (token middleware, error shaping, startup)
work/{sid}/        orig/ (pristine v0) · working/ (current) · versions/ · candidate/ · fallback/
review.db          SQLite state (gitignored)
```

## Audio / versioning model

On seed each master `{i}.mp3` is copied to `work/{sid}/orig/` (pristine v0) and
`working/`. **Masters are never mutated during review.** `combine`/`import` write the
working copy and archive a `versions/{stem}v{n}.mp3`; segment splices are always taken
from the pristine original. `/submit` promotes only changed working files to the
masters (prior master archived under `mp3_dir/versions/`).

Whisper transcription and Gemini cleaning of the *original* are computed lazily on
first segment-splice and cached (Whisper → `metadata_dir` on disk; cleaned-orig → the
session row), so seeding never blocks on the GPU/network. Same cached values as
seed-time caching, just on demand.

**Voice resolution** (`sessions.resolve_voice`): per-trip override in
`backend/voice_overrides.json` (`{"<trip_id>": "isla"}`) > the voice actually chosen at
staging, read from the trip's local cache
`Research and Writing/data/<trip>/staging_choices.json` (`{"voice": "Isla", …}`) >
country default. So the session uses the real chosen voice, not a guess.

## The splice engine (SceneDesc only)

`/regenerate` (`plan_segment`): diff cleaned-orig vs cleaned-new tokens → changed span
→ pick pause-aligned **non-numeric / non-hanzi / non-override** anchor words → read cut
times from **raw Whisper `word.start/.end`** → verify anchors == spoken words → EL
`/with-timestamps` for the anchor-context phrase. Any failed guard (Gemini fallback,
unlocatable/mismatched anchor, non-Latin span) → `flag:"edit_required"`, no candidate.

`/combine` (`do_splice`): refine each cut to a local RMS-energy minimum (±120 ms),
level-match the candidate middle to the **longest retained original context**, assemble
`orig[:tL] + cand[tL':tR'] + orig[tR:]` in PCM with equal-power edge fades (not
`acrossfade`), peak-limit ≤ −1.5 dBTP, score `splice_confidence` (low → auto
`edit_required`). One mp3 re-encode; pristine original retained.

## Verified

See `../REQUIREMENTS.md` for the full status map. Live-verified on real staging trip
`Edinburgh1_OldTownGreyfriars_EN`: health/auth, trips, seed/resume, Range audio +
versions + overlays, text autosave + coverage gate (done 95%, reset-on-change),
comment/flag/revert, **whole-regenerate + combine**, **segment splice** (Whisper +
`/with-timestamps` + PCM splice; human-confirmed no seam, identical retained audio,
correct level), K3 non-Latin guard, import-mp3, fallback clip, download zip,
**voice resolution** (reads isla from `staging_choices.json`), submit
**validation-refusal** + non-destructive category merge, and a **full live `/submit`
end-to-end** (text → staging Trip, `3.mp3` promoted to master + prior archived,
`awaiting_stage9:true`) which was then restored to pristine.

**Red-team hardening (round 2):** C1 (clip-boundary cuts never refined inward —
verified on first- and last-sentence splices: new audio not truncated, no old-take
sliver) + S1 (anchors expand to sentence/clause boundaries so seams land in pauses;
non-boundary cuts gated on a real energy dip → `edit_required`; confidence
0.4·anchor + 0.4·seam + 0.2·dur) + S2 (numeral-fallback whole-regens flagged) + S3
(Whisper cache content-hash refresh) + S4 (peak-limit only the candidate, never the
retained original) + S5 (`quickTrips` written only when a scene field changed) + S6
(version suffix = max+1) + S7 (import re-encoded to 44100/mono, off the event loop) +
S8 (`/played` ranges clamped to the clip) + S9 (bare `video_id`, null `image_url`
when unresolved) + audio URL `?v=<hash>` cache-bust. All verified.
