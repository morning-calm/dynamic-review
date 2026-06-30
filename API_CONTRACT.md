# review-app — API contract (the integration truth)

Both `backend/` (FastAPI) and `frontend/` (React) build to **this** file. If you
change a shape, change it here first. The full design rationale is the plan at
`C:\Users\david\.claude\plans\i-need-some-software-generic-gizmo.md`.

## Conventions
- Backend: `http://127.0.0.1:8000` (bound to localhost only).
- Auth: every request carries header **`X-Review-Token: <token>`** (env `REVIEW_APP_TOKEN`; dev default `dev-token`). Missing/wrong → `401`.
- Frontend dev (`vite`, port 5173) **proxies** `/api` and `/audio` → `127.0.0.1:8000`.
- All JSON. Times in seconds (float). Errors: `{ "error": "<code>", "detail": "<human>" }` with status 400/401/404/409/422/500.
- **MP3 only** in this tool. No ogg, no S3, no subtitles/timings — Stage 9 owns those.

## Core objects

### Field
The atom the UI renders/edits. One per editable thing.
```jsonc
{
  "fid": 12,                          // DB id, stable within a session
  "scene_index": 3,                   // null for trip-level fields
  "field_path": "SceneDesc",          // see field_path table below
  "has_audio": true,                  // SceneDesc / questionKey / questionOption[k]
  "original_text": "…",               // immutable (the take's text at seed)
  "current_text": "…",                // edited value (autosaved)
  "flag": "none",                     // "none" | "done" | "edit_required"
  "comment": "",
  "splice_confidence": null,          // 0..1 after a combine, else null
  "played_coverage": [[0.0, 4.2]],    // contiguous seconds played of working audio
  "can_mark_done": false,             // server-computed (coverage>=95% or text-only)
  "audio": {                          // null entries when not applicable
    "original": "/audio/{sid}/12/original",
    "working":  "/audio/{sid}/12/working",
    "candidate": null,                // set after /regenerate
    "fallback":  null                 // set after /fallback
  },
  "versions": [                       // archived takes, newest last
    { "label": "3v0", "kind": "v0_original", "url": "/audio/{sid}/12/v/0" },
    { "label": "3v1", "kind": "splice",      "url": "/audio/{sid}/12/v/1" }
  ]
}
```

### field_path table
| field_path | scene_index | has_audio | maps to (mp3) | maps to (Firebase) |
|---|---|---|---|---|
| `contentTitleKey` | null | no | — | Trip `contentTitleKey` |
| `tripgroup_description` | null | no | — | **TripGroup** `descriptionTarget` (prose only) |
| `SceneDesc` | n | yes | `{n}.mp3` | `quickTrips[n].SceneDesc` |
| `titleKey` | n | no | — | `quickTrips[n].titleKey` |
| `questionKey` | n | yes | `{n}_q.mp3` | `quickTrips[n].questionKey` |
| `questionOption[k]` | n | yes | `{n}_a{k or ''}.mp3` | `quickTrips[n].questionOptionKeys[k]` |

### Scene (read model for rendering)
```jsonc
{
  "index": 3,
  "video_id": "1180027043",      // Vimeo id or null
  "is_static_image": false,
  "has_audio": true,
  "image_url": null,             // 360 still if is_static_image, else null
  "overlays": [ { "filename": "suncake.jpg", "url": "<r2-or-local>" } ],
  "fields": [ Field, … ]         // titleKey, SceneDesc, questionKey, questionOption[k]
}
```

### Session (GET response)
```jsonc
{
  "id": "sess_abc",
  "trip_id": "Edinburgh1_OldTownGreyfriars_EN",
  "folder_name": "Scotland/Edinburgh/Edinburgh1_OldTownGreyfriars_EN",
  "status": "in_review",         // "in_review" | "submitted"
  "voice": "isla",               // narrator voice name (registry key)
  "voice_display": "Isla",       // human label for the voice
  "speed": 0.7,                  // effective TTS speed (override or level/auto)
  "speed_override": null,        // per-session override, null = auto
  "model": "eleven_multilingual_v2", // effective EL model (override or by-voice)
  "model_override": null,        // per-session override, null = auto
  "trip_categories": ["UNESCO","Medieval"],   // read-only display
  "trip_fields": [ Field(contentTitleKey), Field(tripgroup_description) ],
  "scenes": [ Scene, … ]
}
```

## Endpoints

| Method · Path | Body | Returns |
|---|---|---|
| `GET /api/health` | — | `{ "ok": true }` |
| `GET /api/trips` | — | `[ { "trip_id", "title", "folder_name", "has_session", "status" } ]` (trips with local MP3 masters under the configured roots) |
| `GET /api/voices` | — | `{ "voices": [ {"name","display","gender","language","country","model"} ], "models": ["eleven_multilingual_v2","eleven_v3"] }` — the approved-voice registry for the narration picker. |
| `POST /api/sessions` | `{ "trip_id": "…" }` | `Session` — **creates or resumes** the trip's open session. `422` if folderName isn't a ≥2-segment Scotland/England path. |
| `GET /api/sessions/{sid}` | — | `Session` (full state, for resume) |
| `POST /api/sessions/{sid}/narration` | `{ "voice"?, "speed"?, "model"?, "clear_speed"?, "clear_model"? }` | `Session` — correct the trip's narrator voice/speed/model mid-review. Omit a field to leave it; `clear_*` drops an override back to auto. Any take **regenerated under the old settings** is reset to the master (text edits kept); untouched master audio + coverage are preserved. `422` on unknown voice/model or speed out of `0.5–1.2`. |
| `PUT /api/sessions/{sid}/fields/{fid}` | `{ "current_text": "…" }` | `Field` — autosave. Resets `played_coverage` + drops `flag` off `done` if text changed. |
| `POST /api/sessions/{sid}/fields/{fid}/regenerate` | `{ "mode": "segment"|"whole"|"highlight", "range": {"start":int,"end":int}? }` | `Field` with `audio.candidate` set **ASAP**. `segment` diffs current vs original; `highlight` uses `range`; `whole` re-voices the whole field. Non-Latin / numeral-dense / Gemini-fallback → `flag:"edit_required"` and no candidate (whole-regen advised). |
| `POST /api/sessions/{sid}/fields/{fid}/combine` | — | `Field` — splices candidate into working (SceneDesc) or replaces (whole/Q&A); archives prior take to `versions`; sets `splice_confidence`; may auto-set `flag:"edit_required"`. |
| `POST /api/sessions/{sid}/fields/{fid}/fallback` | `{ "extent": "sentence"|"scene"|"custom", "text": "…"?, "description": "…" }` | `Field` — generates a **standalone ElevenLabs** clip (`audio.fallback`), sets `flag:"edit_required"`, stores the description for the admin. |
| `POST /api/sessions/{sid}/fields/{fid}/import-mp3` | multipart `file=<mp3>` | `Field` — **admin** replaces working `{i}.mp3` with a hand-edited file; archives prior take. |
| `POST /api/sessions/{sid}/fields/{fid}/played` | `{ "ranges": [[s,e],…] }` | `{ "played_coverage": [[s,e],…], "can_mark_done": bool }` — merges coverage of the **current** working audio. |
| `POST /api/sessions/{sid}/fields/{fid}/flag` | `{ "flag": "none"|"done"|"edit_required" }` | `Field` — `409` if `done` requested but `can_mark_done` is false. |
| `POST /api/sessions/{sid}/fields/{fid}/comment` | `{ "text": "…" }` | `Field` |
| `POST /api/sessions/{sid}/fields/{fid}/revert` | — | `Field` — restores `original_text` + `v0` audio. |
| `GET /audio/{sid}/{fid}/{which}` | — (`which` ∈ `original｜working｜candidate｜fallback`) | `audio/mpeg`, **HTTP Range supported** |
| `GET /audio/{sid}/{fid}/v/{n}` | — | `audio/mpeg` (archived version n), Range supported |
| `GET /api/sessions/{sid}/download` | — | `application/zip` — all originals + every version + current `{i}.mp3` |
| `POST /api/sessions/{sid}/submit` | — | `{ "ok": bool, "validation": [ {scene_index,field_path,issue} ], "written": [field_path…], "awaiting_stage9": true }` — validates, then writes changed **text** to staging Trip + TripGroup desc/categories; corrected `{i}.mp3` masters are already in place for Stage 9. **No S3/ogg.** |

## Notes for implementers
- `fid` is the `field_edits.id`. The frontend never constructs audio paths — it uses the URLs in `Field.audio` / `Field.versions`.
- `can_mark_done` is **server-authoritative**; the client also disables the button, but the server re-checks on `/flag`.
- After `combine`/`import`/`revert`/text-edit, the working audio changed → server clears coverage; the client must reload that field's audio element (new content at the same URL).
- `regenerate` should return as soon as the ElevenLabs clip is ready (candidate audible); the heavy splice/align happens on `combine`.
