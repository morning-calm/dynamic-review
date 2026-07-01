# review-app — API contract (the integration truth)

Both `backend/` (FastAPI) and `frontend/` (React) build to **this** file. If you
change a shape, change it here first. The full design rationale is the plan at
`C:\Users\david\.claude\plans\i-need-some-software-generic-gizmo.md`.

## Conventions
- Backend: `http://127.0.0.1:8000` (bound to localhost only).
- **Auth (see "Auth & roles" below):** every request except `POST /api/login` + `GET /api/health` must present a session token — `Authorization: Bearer <token>` for all writes; media/download **GET**s may instead use the httpOnly `review_session` cookie. Missing/invalid → `401`. Two roles (`admin`/`reviewer`); reviewers are scoped to their trip languages → `403` otherwise.
- Frontend dev (`vite`, port 5173) **proxies** `/api`, `/audio`, `/overlays` → `127.0.0.1:8000`.
- All JSON. Times in seconds (float). Errors: `{ "error": "<code>", "detail": "<human>" }` with status 400/401/404/409/422/500.
- **MP3 only** in this tool. No ogg, no S3, no subtitles/timings — Stage 9 owns those.

## Auth & roles
- **Login:** `POST /api/login {username,password}` → `{ "token", "user": {"username","role","languages"} }` and sets `Set-Cookie: review_session=<token>; HttpOnly; SameSite=Lax; Path=/` (`; Secure` when env `REVIEW_APP_COOKIE_SECURE=1`). `401` on bad creds (generic — no user-enumeration). `POST /api/logout` revokes the token + clears the cookie; `GET /api/me` → `{username,role,languages}`.
- **Token transport:** send `Authorization: Bearer <token>` on every request. State-changing requests (POST/PUT/DELETE) **must** use the header — a cookie alone is rejected (CSRF defence). Browser `<audio>`/`<img>`/download **GET**s authenticate via the httpOnly cookie (they can't set a header). Tokens are opaque, DB-backed (revocable), and expire (default 14d).
- **Roles:** `admin` (sees all trips, approves, writes staging) and `reviewer` (scoped to `languages`; corrects + submits). Language = the trip's narration language (`_EN`→English, `_JP`→Japanese, `_ZH`→Mandarin). English has no reviewer — admins handle it. Accounts are admin-provisioned via `backend/manage.py` (no signup).
- **Scoping:** `GET /api/trips` is filtered to the caller's language(s); `POST /api/sessions` and every `/api/sessions/{sid}/*` + media/download route return `403` if the trip's language isn't the caller's (admins bypass).
- **Review workflow (submit → approve):** a reviewer edits (`in_review`), then `submit` (validate-only, **no writes**) → `submitted` (locked read-only). An admin reviews the diff and either `approve` (writes staging text + promotes the corrected `{i}.mp3` masters) → `approved`, or `request-changes` (→ `changes_requested`, editable again). **Masters/staging are written only on approve, never on reviewer submit.** For English the admin submits + approves in one pass.
- **Completed queue:** a finished trip — **admin-approved** (auto-completed on approve) or **admin-marked-complete** (`POST /api/trips/{id}/complete`, for work already done in the old system) — leaves `GET /api/trips` and appears in `GET /api/completed` (both roles; reviewers see their languages). Completed trips are **view-only**; an admin **un-completes** (`DELETE /api/trips/{id}/complete`) to return one to the active queue (`POST /api/sessions` on a completed trip → `409`). Completion is per `trip_id` (a Trello card family completes trip-by-trip). Manual completion **bypasses** the mark-done/submit/approve gates and **writes nothing to staging or masters**.

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
  "status": "in_review",         // in_review | submitted | approving | approved | changes_requested
  "submitted_by": null,          // username, once submitted
  "approved_by": null,           // username, once approved
  "review_note": "",             // admin's note when changes_requested
  "voice": "isla",               // narrator voice name (registry key)
  "voice_display": "Isla",       // human label for the voice
  "speed": 0.7,                  // effective TTS speed (override or level/auto)
  "speed_override": null,        // per-session override, null = auto
  "model": "eleven_multilingual_v2", // effective EL model (override or by-voice)
  "model_override": null,        // per-session override, null = auto
  "trip_categories": ["UNESCO","Medieval"],   // read-only display
  "is_zh": false,                // Mandarin 4-script + A/B-audio mode (see below)
  "language": "English",         // narration language: "English"|"Mandarin"|"Japanese" — gates the CJK SceneDesc controls
  "preferred_version": null,     // _ZH only: "v2" | "v3" | null (the per-trip audio pick)
  "trip_fields": [ Field(contentTitleKey), Field(tripgroup_description) ],
  "scenes": [ Scene, … ]
}
```

### Mandarin (`_ZH`) additions
The three prepared HSK3 trips are reviewed in a distinct mode (`Session.is_zh = true`),
gated on two ElevenLabs A/B audio takes existing on disk. It is **additive** — every field
below is absent/`false`/`null` for non-`_ZH` trips, which are unchanged.
- **`Field.localization`** (`_ZH` fields only, else absent) — the editable 4-script block,
  `cur` = live edited value, `orig` = seed value (for diffing):
  ```jsonc
  { "cur":  { "Hans": "…", "Hant": "…", "zhuyin": "…", "en": "…" },
    "orig": { "Hans": "…", "Hant": "…", "zhuyin": "…", "en": "…" },
    "working_hans": "…" }   // Hans the WORKING take currently says; re-baselined at each
                            // combine (absent before the first). Drives whether "Generate
                            // from edit" has anything new + is the OLD text for the splice.
  ```
  The trip **description** field carries a 3-key block (`Hans`/`Hant`/`en`, **no** `zhuyin`).
  Pinyin is **never** shown/edited — it is regenerated from the confirmed `zhuyin` at approve.
- **`Field.audio.v2` / `.v3`** (`_ZH` only) — the two A/B takes for side-by-side audition.
  **Before the version pick**, the splice slots (`original`/`working`/`candidate`/`fallback`)
  are all `null` (audio is A/B, not spliced) and there is **no coverage gating** (`can_mark_done`
  is server-`true` once the field exists — the human A/B listen is the backstop). **After the
  pick** the chosen take is promoted to `working`, `v2`/`v3` drop, and the field regenerates/
  combines like any other language — SceneDesc gets the **surgical CJK splice** (see
  `regenerate`), Q&A stays whole-regen. Hanzi is edited in the 4-script block, so the
  narration-selection ops (highlight / alt-text / trim-noise / insert-pause) are hidden.
- On **approve**, an `_ZH` trip's reviewed text writes back to `TripLocalizations/{id}`
  (`target.{Hans,Hant,zhuyin}` + `home.en`, regenerated `target.pinyin`, `status:"reviewed"`)
  and the derived Trip doc `quickTrips[i]` (`SceneDesc`/`questionKey`/`questionOption` =
  `Hans⏎pinyin`, `titleKey` = `Hans⏎en`). **No mp3 masters are promoted** (audio is
  finalised later in the HSK pipeline from the chosen version).

## Endpoints

| Method · Path | Body | Returns |
|---|---|---|
| `GET /api/health` | — | `{ "ok": true }` **(unauthenticated)** |
| `POST /api/login` | `{ "username","password" }` | `{ "token", "user": {"username","role","languages"} }` + `Set-Cookie: review_session`. `401` generic on bad creds. **(unauthenticated)** |
| `POST /api/logout` | — | `204` — revokes the presented token + clears the cookie. |
| `GET /api/me` | — | `{ "username","role","languages" }` — the caller's identity (FE bootstraps from this). |
| `GET /api/trips` | — | `[ { "trip_id","title","folder_name","lane","level","family","has_session","status","edit_required","reviewable","pinned" } ]` — **filtered to the caller's language(s)** (admins see all). **Excludes completed trips** (they move to `GET /api/completed`). **Ordered:** admin-**pinned** trips first (newest pin first), then Trello card order (the manifest's base sort). |
| `GET /api/voices` | — | `{ "voices": [ {"name","display","gender","language","country","model"} ], "models": ["eleven_multilingual_v2","eleven_v3"] }` — the approved-voice registry for the narration picker. |
| `POST /api/sessions` | `{ "trip_id": "…" }` | `Session` — **creates or resumes** the trip's session (resumes any `in_review`/`submitted`/`changes_requested` one). **`403`** if the trip's language isn't the caller's. **`409`** if the trip is **completed** (an admin must un-complete it first). `422` if folderName isn't a valid path. |
| `GET /api/sessions/{sid}` | — | `Session` (full state, for resume) |
| `POST /api/sessions/{sid}/narration` | `{ "voice"?, "speed"?, "model"?, "clear_speed"?, "clear_model"? }` | `Session` — correct the trip's narrator voice/speed/model mid-review. Omit a field to leave it; `clear_*` drops an override back to auto. Any take **regenerated under the old settings** is reset to the master (text edits kept); untouched master audio + coverage are preserved. `422` on unknown voice/model or speed out of `0.5–1.2`. |
| `PUT /api/sessions/{sid}/fields/{fid}` | `{ "current_text": "…" }` | `Field` — autosave. Resets `played_coverage` + drops `flag` off `done` if text changed. |
| `PUT /api/sessions/{sid}/fields/{fid}/localization` | `{ "script": "Hans\|Hant\|zhuyin\|en", "text": "…" }` | `Field` — **`_ZH` only.** Autosave one script of the 4-script block. `422` if the script isn't editable on that field (e.g. `zhuyin` on the description). Drops `flag` off `done` when the value changes. No audio side-effects (Chinese audio is A/B). |
| `POST /api/sessions/{sid}/version` | `{ "version": "v2"\|"v3" }` | `Session` — **`_ZH` only.** Set the trip's preferred ElevenLabs A/B version. `422` on an unknown version. |
| `POST /api/sessions/{sid}/fields/{fid}/regenerate` | `{ "mode": "segment"|"whole"|"highlight", "range": {"start":int,"end":int}? }` | `Field` with `audio.candidate` set **ASAP**. `segment` diffs current vs original; `highlight` uses `range`; `whole` re-voices the whole field. Non-Latin (English engine) / numeral-dense / Gemini-fallback → `flag:"edit_required"` and no candidate (whole-regen advised). **CJK (`_ZH` hanzi / `_JP` kana) SceneDesc:** `segment` attempts a surgical char-level splice via the isolated forced aligner; when it can't cut cleanly it whole-regenerates the narration and returns `"cjk_fallback": true` on the Field (transient — a UI hint that the whole clip changed, not persisted). `_ZH`/`_JP` ignore `range` (no selection-based ops). |
| `POST /api/sessions/{sid}/fields/{fid}/combine` | — | `Field` — splices candidate into working (SceneDesc) or replaces (whole/Q&A); archives prior take to `versions`; sets `splice_confidence`; may auto-set `flag:"edit_required"`. |
| `POST /api/sessions/{sid}/fields/{fid}/fallback` | `{ "extent": "sentence"|"scene"|"custom", "text": "…"?, "description": "…" }` | `Field` — generates a **standalone ElevenLabs** clip (`audio.fallback`), sets `flag:"edit_required"`, stores the description for the admin. |
| `POST /api/sessions/{sid}/fields/{fid}/import-mp3` | multipart `file=<mp3>` | `Field` — **admin** replaces working `{i}.mp3` with a hand-edited file; archives prior take. |
| `POST /api/sessions/{sid}/fields/{fid}/played` | `{ "ranges": [[s,e],…] }` | `{ "played_coverage": [[s,e],…], "can_mark_done": bool }` — merges coverage of the **current** working audio. |
| `POST /api/sessions/{sid}/fields/{fid}/flag` | `{ "flag": "none"|"done"|"edit_required" }` | `Field` — `409` if `done` requested but `can_mark_done` is false. |
| `POST /api/sessions/{sid}/fields/{fid}/comment` | `{ "text": "…" }` | `Field` |
| `POST /api/sessions/{sid}/fields/{fid}/revert` | — | `Field` — restores `original_text` + `v0` audio. |
| `GET /audio/{sid}/{fid}/{which}` | — (`which` ∈ `original｜working｜candidate｜fallback`) | `audio/mpeg`, **HTTP Range supported** |
| `GET /audio/{sid}/{fid}/v/{n}` | — | `audio/mpeg` (archived version n), Range supported |
| `GET /audio/{sid}/{fid}/ab/{ver}` | — (`ver` ∈ `v2｜v3`) | `audio/mpeg` — **`_ZH` only.** The V2/V3 A/B take, Range supported, cookie-auth + language-scoped like other media. `404` if the take/field is absent. |
| `GET /api/sessions/{sid}/download` | — | `application/zip` — all originals + every version + current `{i}.mp3` |
| `POST /api/sessions/{sid}/submit` | — | `{ "ok": bool, "validation": [ {scene_index,field_path,issue} ] }` — reviewer/admin (own language): **validates only, no writes**; on `ok` flips the session to `submitted` (locked read-only, awaiting admin). Hard-fail issues keep it `in_review`. |
| `POST /api/sessions/{sid}/approve` | — | `{ "ok": bool, "validation": […], "written": [field_path…], "promoted_mp3": [name…], "awaiting_stage9": true }` — **admin only.** Writes changed **text** to staging Trip + TripGroup desc/categories and promotes the corrected `{i}.mp3` masters (archiving prior). `409` if the session isn't `submitted`; if live staging drifted so validation now fails, returns `ok:false` and reverts to `submitted`. **No S3/ogg** (Stage 9). |
| `POST /api/sessions/{sid}/request-changes` | `{ "note": "…" }` | `{ "ok": true }` — **admin only.** Sends a `submitted` trip back to the reviewer (`changes_requested`) with a note; `409` from other states. |
| `GET /api/review-queue` | — | `[ { "sid","trip_id","title","language","submitted_by","submitted_at","edit_required" } ]` — **admin only.** Sessions awaiting approval. |
| `GET /api/completed` | — | `[ { "trip_id","title","language","method","completed_by","completed_at","session_id" } ]` — the **completed queue** (approved + admin-marked). **Both roles**; reviewers filtered to their language(s), admins see all. `method` ∈ `approved｜manual`; `session_id` is the approved session (null for `manual`). Sorted newest first. |
| `POST /api/trips/{trip_id}/complete` | `{ "note"?: "…" }` | `{ "ok": true }` — **admin only.** Manual (bypass) completion for work done in the old system; idempotent upsert (`method="manual"`, no session). **Writes NOTHING to staging or masters** — a workflow marker only. `200` even if the trip has no session. |
| `DELETE /api/trips/{trip_id}/complete` | — | `{ "ok": true }` — **admin only.** Un-complete: removes the trip from the completed queue so it returns to `GET /api/trips` and is openable again. Idempotent. |
| `POST /api/trips/{trip_id}/pin` | — | `{ "ok": true }` — **admin only.** Pin a trip to the top of the reviewer list (above the Trello base order). Idempotent; re-pinning moves it back to the top. |
| `DELETE /api/trips/{trip_id}/pin` | — | `{ "ok": true }` — **admin only.** Un-pin — the trip returns to the Trello base order. |

## Notes for implementers
- `fid` is the `field_edits.id`. The frontend never constructs audio paths — it uses the URLs in `Field.audio` / `Field.versions`.
- **Media auth:** `/audio/*`, `/overlays/*`, and `/api/sessions/{sid}/download` are language-scoped and require auth; browser `<audio>`/`<img>` send the httpOnly `review_session` cookie (set at login) since they can't set a header. The FE fetches all `/api` with `Authorization: Bearer` + `credentials:'include'`, and on any `401` clears the token and returns to the login page.
- `can_mark_done` is **server-authoritative**; the client also disables the button, but the server re-checks on `/flag`.
- After `combine`/`import`/`revert`/text-edit, the working audio changed → server clears coverage; the client must reload that field's audio element (new content at the same URL).
- `regenerate` should return as soon as the ElevenLabs clip is ready (candidate audible); the heavy splice/align happens on `combine`.
