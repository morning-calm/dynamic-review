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
- **Review workflow (submit → approve):** a reviewer edits (`in_review`), then `submit` (validate-only, **no writes**) → `submitted` (locked read-only). An admin reviews the diff and either `approve` (writes staging text + promotes the corrected `{i}.mp3` masters) → `approved`, or `request-changes` (→ `changes_requested`, editable again). **Masters/staging are written only on approve, never on reviewer submit.** For English the admin submits + approves in one pass. **Exception to the submit lock:** an **admin** may use every editing route while a session is `submitted` (approve-page inline touch-ups); `approving`/`approved` stay read-only for everyone.
- **Recall (reviewer takes a submission back):** `POST /sessions/{sid}/recall` — the **submitter** (or an admin) auto-recalls a `submitted` session back to `in_review` **unless an admin is live on it** (presence heartbeat within ~2 min) or it's already `approved`; those paths require a reason and create a **recall request** the admin resolves from the review queue (grant → `changes_requested` with the reason as `review_note`, un-completing an approved trip first; decline → note shown to the requester).
- **Presence:** session pages heartbeat `POST /sessions/{sid}/heartbeat` every ~30s with a context string; `GET /api/presence` lists everyone live (heartbeat ≤120s old) for trip-list/queue dots. An admin's live presence on a `submitted` session is the recall "mid-review" signal.
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
  "working_text": "…",                // what the WORKING take says (seeded to
                                      // original_text; re-set at each combine). JP gates
                                      // "Generate from edit" on the kana line vs THIS,
                                      // not the seed (_ZH sibling: working_hans).
  "flag": "none",                     // "none" | "done" | "edit_required"
  "comment": "",
  "edited_by": null,                  // who last changed this field (best-effort audit;
                                      // approve page badges non-submitter edits)
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
  "audio_unavailable": false,    // trip expects audio but session seeded with none (admin text-only open)
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
  `regenerate`), Q&A stays whole-regen. Hanzi is edited in the 4-script block; the
  narration-selection ops (highlight / alt-text / trim-noise / pause tools) read the
  reviewer's selection in the **Simplified (Hans)** textarea — their `range`/`pos` char
  offsets index into `localization.cur.Hans`, not `current_text`.
- On **approve**, an `_ZH` trip's reviewed text writes back to `TripLocalizations/{id}`
  (`target.{Hans,Hant,zhuyin}` + `home.en`, regenerated `target.pinyin`, `status:"reviewed"`)
  and the derived Trip doc `quickTrips[i]` (`SceneDesc`/`questionKey`/`questionOption` =
  `Hans⏎pinyin`, `titleKey` = `Hans⏎en`). **No mp3 masters are promoted** (audio is
  finalised later in the HSK pipeline from the chosen version).

### Bug report
A reviewer/admin flags a problem on a specific field, in any language, from the field's control
row. Creating one snapshots the field's text + working/candidate audio so we see exactly what the
reporter saw. Each report has a reply thread (reviewer ↔ admin).
```jsonc
{
  "id": 7,
  "session_id": "sess_abc", "field_id": 12,
  "trip_id": "KaohsiungLotusPond_HSK3_ZH", "scene_index": 3, "field_path": "SceneDesc",
  "reporter": "ted", "reporter_role": "reviewer",
  "body": "简体字这里有错误…",                     // the description (any language)
  "status": "open",                              // open | investigating | resolved
  "created_at": 0.0, "updated_at": 0.0,
  "message_count": 1, "last_message_at": 0.0,
  "audio": { "working": "/api/bug-reports/7/audio/working", "candidate": null }, // snapshots (absent for text fields)
  // detail fetch (GET /api/bug-reports/{id}) only:
  "text_snapshot": { "current_text": "…", "localization": { /* _ZH block */ } },
  "messages": [ { "author": "dave", "author_role": "admin", "body": "…", "created_at": 0.0 } ]
}
```
Access: an **admin** sees/handles every report; a **reviewer** sees only their own. Status changes
are admin-only. The audio-snapshot GET authenticates via the httpOnly cookie (browser `<audio>`).

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
| `POST /api/sessions/{sid}/fields/{fid}/regenerate` | `{ "mode": "segment"\|"whole"\|"highlight"\|"alt", "range": {"start":int,"end":int}?, "alt_text": "…"? }` | `Field` with `audio.candidate` set **ASAP**. `segment` diffs current vs the working take's text; `highlight` re-voices the `range` selection; `alt` voices `alt_text` in place of the selection (`mode:"whole"` + `alt_text` voices it as the whole field); `whole` re-voices the whole field. **Selection reference text:** EN + `_JP` = `current_text` (the narration textarea; `_JP` selections must touch the KANA line → `409 kana_line_only` otherwise); `_ZH` = `localization.cur.Hans`. English engine: non-Latin / numeral-dense / Gemini-fallback → `flag:"edit_required"`, no candidate. **CJK (`_ZH` hanzi / `_JP` kana) SceneDesc:** `segment`/`highlight` attempt a surgical char-level splice via the isolated forced aligner (highlight works with UNCHANGED text — the usual re-pronounce case); on any uncertainty `segment`/`highlight` whole-regenerate and return transient `"cjk_fallback": true` (UI hint: the whole clip changed), while `alt` sets `flag:"edit_required"` with no candidate (the alt is never voiced as the whole field or dropped). `segment` with the VOICED line unchanged (JP kanji-only / ZH non-Hans edit) → `409 spoken_line_unchanged`. |
| `POST /api/sessions/{sid}/fields/{fid}/combine` | — | `Field` — splices candidate into working (SceneDesc) or replaces (whole/Q&A); archives prior take to `versions`; sets `splice_confidence`; may auto-set `flag:"edit_required"`. |
| `POST /api/sessions/{sid}/fields/{fid}/trim` | `{ "start": int, "end": int }` | `Field` — **trim highlighted noise** (direct working-take edit, no candidate/combine): char range in the selection reference text (see `regenerate`). Selection overlapping words → strip non-speech blips inside that window; selection on a gap/punctuation → blank the pause to clean silence. Archives a version; drops any pending candidate. CJK locates the range via the forced aligner — `409 aligner_unavailable` / `409 text_audio_mismatch` when it can't (direct edits never fall back silently). |
| `POST /api/sessions/{sid}/fields/{fid}/insert-silence` | `{ "pos": int, "seconds": 1.0 }` | `Field` — extend the pause at the TEXT caret `pos` (selection reference text as above). Only ever lengthens a REAL pause — `409 no_pause` in connected speech. Archives a version; drops any pending candidate. |
| `POST /api/sessions/{sid}/fields/{fid}/remove-silence` | `{ "pos": int, "seconds": 1.0 }` | `Field` — the inverse: shorten the pause at the caret by up to `seconds`, taken from the middle of the silence run (word release/onset untouched, ≥0.25s natural pause always kept). `409 no_pause` / `409 no_excess_pause`. Archives a version; drops any pending candidate. |
| `POST /api/sessions/{sid}/fields/{fid}/trim-silence` | — | `Field` — normalize the TRAILING pause to the trip's level requirement (beginner ≈3s kept, others trimmed). No-op when already correct. |
| `POST /api/sessions/{sid}/fields/{fid}/trim-candidate` | `{ "delta_ms": float }` | `Field` — nudge how much is trimmed off the END of the pending candidate before combining (+ trims more, − restores; re-derived from the pristine candidate). |
| `POST /api/sessions/{sid}/fields/{fid}/undo` · `/redo` | — | `Field` — step the working take back/forward through the archived versions (v0 = pristine master). `409` at either end. Restoring clears any pending candidate. |
| `POST /api/sessions/{sid}/fields/{fid}/fallback` | `{ "extent": "sentence"|"scene"|"custom", "text": "…"?, "description": "…" }` | `Field` — generates a **standalone ElevenLabs** clip (`audio.fallback`), sets `flag:"edit_required"`, stores the description for the admin. |
| `POST /api/sessions/{sid}/fields/{fid}/import-mp3` | multipart `file=<mp3>` | `Field` — **admin** replaces working `{i}.mp3` with a hand-edited file; archives prior take. |
| `POST /api/sessions/{sid}/fields/{fid}/played` | `{ "ranges": [[s,e],…] }` | `{ "played_coverage": [[s,e],…], "can_mark_done": bool }` — merges coverage of the **current** working audio. |
| `POST /api/sessions/{sid}/fields/{fid}/flag` | `{ "flag": "none"|"done"|"edit_required" }` | `Field` — `409` if `done` requested but `can_mark_done` is false. |
| `POST /api/sessions/{sid}/fields/{fid}/comment` | `{ "text": "…" }` | `Field` |
| `POST /api/sessions/{sid}/fields/{fid}/revert` | — | `Field` — restores `original_text` + `v0` audio. |
| `GET /audio/{sid}/{fid}/{which}` | — (`which` ∈ `original｜working｜candidate｜fallback`) | `audio/mpeg`, **HTTP Range supported** |
| `GET /audio/{sid}/{fid}/v/{n}` | — | `audio/mpeg` (archived version n), Range supported |
| `GET /api/sessions/{sid}/download` | — | `application/zip` — all originals + every version + current `{i}.mp3`. **Admin only** (`403` for reviewers): the bundle is for editing takes in a desktop audio editor + re-importing. |
| `POST /api/sessions/{sid}/submit` | — | `{ "ok": bool, "validation": [ {scene_index,field_path,issue} ] }` — reviewer/admin (own language): **validates only, no writes**; on `ok` flips the session to `submitted` (locked read-only, awaiting admin). Hard-fail issues keep it `in_review`. |
| `POST /api/sessions/{sid}/approve` | — | `{ "ok": bool, "validation": […], "written": [field_path…], "promoted_mp3": [name…], "awaiting_stage9": true }` — **admin only.** Writes changed **text** to staging Trip + TripGroup desc/categories and promotes the corrected `{i}.mp3` masters (archiving prior). `409` if the session isn't `submitted`; if live staging drifted so validation now fails, returns `ok:false` and reverts to `submitted`. **No S3/ogg** (Stage 9). |
| `POST /api/sessions/{sid}/request-changes` | `{ "note": "…" }` | `{ "ok": true }` — **admin only.** Sends a `submitted` trip back to the reviewer (`changes_requested`) with a note; `409` from other states. |
| `GET /api/review-queue` | — | `[ { "sid","trip_id","title","language","submitted_by","submitted_at","edit_required" } ]` — **admin only.** Sessions awaiting approval. |
| `GET /api/completed` | — | `[ { "trip_id","title","language","method","completed_by","completed_at","session_id" } ]` — the **completed queue** (approved + admin-marked). **Both roles**; reviewers filtered to their language(s), admins see all. `method` ∈ `approved｜manual`; `session_id` is the approved session (null for `manual`). Sorted newest first. |
| `POST /api/trips/{trip_id}/complete` | `{ "note"?: "…" }` | `{ "ok": true }` — **admin only.** Manual (bypass) completion for work done in the old system; idempotent upsert (`method="manual"`, no session). **Writes NOTHING to staging or masters** — a workflow marker only. `200` even if the trip has no session. |
| `DELETE /api/trips/{trip_id}/complete` | — | `{ "ok": true }` — **admin only.** Un-complete: removes the trip from the completed queue so it returns to `GET /api/trips` and is openable again. Idempotent. |
| `POST /api/trips/{trip_id}/pin` | — | `{ "ok": true }` — **admin only.** Pin a trip to the top of the reviewer list (above the Trello base order). Idempotent; re-pinning moves it back to the top. |
| `DELETE /api/trips/{trip_id}/pin` | — | `{ "ok": true }` — **admin only.** Un-pin — the trip returns to the Trello base order. |
| `POST /api/sessions/{sid}/heartbeat` | `{ "context": "…" }` | `{ "ok": true }` — presence ping (~30s from the session pages), allowed in **any** session state. Context is a short human-readable string ("editing", "reviewing (admin)"). |
| `GET /api/presence` | — | `[ { "username","role","context","updated_at","sid","trip_id","session_status" } ]` — everyone live (heartbeat ≤120s). Reviewers see their language(s) only. |
| `GET /api/sessions/{sid}/recall` | — | `{ "status","can_recall","auto","blocker": "approved"\|"admin_reviewing"\|null, "request": RecallRequest\|null }` — what the Recall button should offer this caller now. |
| `POST /api/sessions/{sid}/recall` | `{ "reason": "…" }` | `{ "ok", "recalled", "status"? , "request_id"?, "existing"? }` — **submitter or admin.** Auto-grant (CAS `submitted`→`in_review`) when no admin is live; otherwise `409 reason_required` without a reason, else creates/returns the open recall request. `403` for non-submitters; `409 bad_state` from editable states. |
| `GET /api/recall-requests?status=open` | — | `RecallRequest[]` + `session_status`/`completed_method`/`title`/`language` — **admin only.** Pinned atop the review queue. |
| `GET /api/recall-requests/count` | — | `{ "open": n }` — **admin only** (nav badge). |
| `POST /api/recall-requests/{rid}/resolve` | `{ "action": "grant"\|"decline", "note": "…" }` | `{ "ok", "session_status" }` — **admin only.** Grant: session → `changes_requested` (reason/note into `review_note`); an `approved` trip is **un-completed first**. Decline: note shown to the requester. `409` if already resolved. |
| `POST /api/sessions/{sid}/fields/{fid}/bug-report` | `{ "body": "…" }` | `BugReport` — file a problem report on this field (any language); snapshots the field text + working/candidate audio. Language-scoped like other session routes. |
| `GET /api/bug-reports` | — | `BugReport[]` — **admin:** all reports (open first); **reviewer:** only their own. |
| `GET /api/bug-reports/count` | — | `{ "role", "open"? , "unread"? }` — badge counts (admin=open reports; reviewer=their reports with an unseen admin reply). |
| `GET /api/bug-reports/{rid}` | — | `BugReport` incl. `messages` + `text_snapshot`. **Admin or the report's owner** (else `403`); viewing marks it seen for the reporter. |
| `POST /api/bug-reports/{rid}/messages` | `{ "body": "…" }` | `BugReport` — add a reply to the thread (admin or owner). |
| `POST /api/bug-reports/{rid}/status` | `{ "status": "open\|investigating\|resolved" }` | `BugReport` — **admin only.** |
| `GET /api/bug-reports/{rid}/audio/{which}` | — (`which` ∈ `working｜candidate`) | `audio/mpeg` snapshot captured at report time; cookie-auth. `404` if absent. |
| `GET /api/sessions/{sid}/external-reports?refresh=1` | — | `{ "trip_id", "reports": ExternalReport[], "sync_error": string\|null }` — stage-4b web/VR reports for this session's trip, mirrored from staging `UserReports` (structured `context` payloads only). `refresh=1` best-effort re-syncs from staging first; a staging hiccup returns the cached rows + `sync_error` instead of failing. Language-scoped like other session routes. `ExternalReport = { id, trip_id, scene_index, scene_id, source, report_type, categories[], body, reporter, created_at, status: "open"\|"acknowledged"\|"resolved", resolved_by, resolved_at }`. |
| `POST /api/external-reports/{report_id}/status` | `{ "status": "open"\|"acknowledged"\|"resolved" }` | `ExternalReport` — **admin only** triage. Mirrored onto the staging doc (`reviewStatus`/`reviewedBy`) best-effort. Leaving `resolved` clears `resolved_by`/`resolved_at`. |
| `GET /api/admin/staging-trips?q=…&location=…&country=…&refresh=1` | — | `{ "total", "shown", "trips": [ { "trip_id","title","folder_name","location","country","locations": string[],"countries": string[],"language","has_session","status","edit_required","completed_method","completed_by" } ], "locations": string[], "countries": string[] }` — **admin only.** Substring search (`q`) over the WHOLE staging `Trips` collection (server-cached index, TTL 10 min; `refresh=1` forces a re-sweep), plus independent case-insensitive `location`/`country` filters (both may be set together) that match **any** entry of a row's `locations`/`countries` — a trip can sit in several TripLocations (city + theme). Derived via `TripLocations.trips[]` (TripGroup ids) → `TripGroups.trips[].tripId`; per-row `locations`/`countries` are all deduped values (empty arrays when unmapped), and `location`/`country` are their ", "-joined display strings. Top-level `locations`/`countries` are sorted distinct non-empty values from the FULL index (not the filtered rows) — for populating filter dropdowns. Hard-capped at 200 rows (`total` = matches before the cap, after filters). |
| `POST /api/admin/open` | `{ "trip_id": "…" }` | `Session` — **admin only.** Create/resume a session for ANY staging trip, **including a completed one** (the reviewer flow 409s there). Unresolvable MP3 masters do NOT 422 here (unlike the reviewer flow): the session seeds TEXT-ONLY — every audio field degrades to `has_audio:false` and the `Session` carries `audio_unavailable:true` (soft FE warning). |
| `POST /api/admin/pipeline/queue` | `{ "trip_id", "kind": "publish", "note"? }` | `BusJob` — **admin only.** Queue a staging→prod TEXT publish request on the R2 review bus (`_bus/jobs/`). NOT best-effort: R2 failure → `502 bus_unavailable`; bad trip id → `422 bad_trip_id`. Nothing executes until a human runs it on the workstation. `BusJob = { id, kind, trip_id, note, requested_by, requested_at, status: "queued"\|"dry_run"\|"done"\|"failed", resolved_by?, resolved_at?, log? }`. |
| `GET /api/admin/pipeline/jobs?trip_id=…` | — | `{ "publisher_mode": bool, "jobs": BusJob[] }` — **admin only.** Newest jobs on the bus (optionally one trip's; bounded fetch, newest-first). |
| `POST /api/admin/pipeline/run` | `{ "job_id", "apply"?: bool, "i_am_sure"?: bool }` | `BusJob` — **admin only + PUBLISHER MODE only** (`403 not_publisher` unless the instance runs with `REVIEW_APP_PUBLISHER=1` — the workstation). Shells the Scripts repo's `publish_trip_text.py`. Default DRY RUN; a real production write needs `apply` **and** `i_am_sure`, and still rides the script's own `--apply --i-am-sure` gates. Timeout/launch failure marks the job `failed` with the log. |
| `GET /api/admin/drift/{trip_id}` | — | `{ "trip_id", "snapshot_at": ts\|null, "fields_differ": string[]\|null }` — **admin only.** Staging vs production display-text drift, diffed against the workstation-exported prod snapshot on the bus (`publish_inbox.py snapshot`); `snapshot_at: null` = no snapshot yet. No prod credential is read. |
| `GET /api/admin/structure/{trip_id}` | — | `{ trip_id, title, tripgroup_id, categories, scenes:[{index,scene_id,video_url,is_static_image,has_audio,title,desc_snippet,thumb_url,static_images}], base, localization_doc, recent_ops }` — **admin only.** Scene-structure read model; `base` is the concurrency fingerprint every structure op must echo back. |
| `POST /api/admin/structure/{trip_id}/reorder` | `{ "order":[old_index…], "base" }` | `{ ok, warnings, structure }` — **admin only; IMMEDIATE staging write** (all structure ops). Carries sceneIds (never re-mints); renumbers TripLocalizations indexes in the same op. `409 active_session` while any active session exists on the trip; `409 state_changed` on stale `base`. Structural ops (reorder/remove/add) warn that positional media (mp3/ogg/srt) must be re-staged before finalise/publish. |
| `POST /api/admin/structure/{trip_id}/remove` | `{ "index", "base" }` | Same shape — drops the scene + its localization entry, renumbers later scenes, releases the `Scenes` registry use (unless the atom is still used elsewhere in the trip). |
| `POST /api/admin/structure/{trip_id}/add` | `{ "position", "base", "video_url"?, "is_static"?, "scene_id"? }` | Same shape — inserts an empty-text scene. sceneId resolution (shared `scene_ids` ruleset, Scripts repo): supplied existing atom (`422 unknown_scene_id` if not in the registry) → a videoId already registered to an atom REUSES it → derived from the VideoIds capture-timestamp stem → minted opaque. Registry `usedBy` gains the trip (an existing atom's `currentVideoId`/`kind` are preserved). |
| `POST /api/admin/structure/{trip_id}/swap-video` | `{ "index", "video_url", "rekey", "base", "scene_id"? }` | Same shape — `rekey:false` = same footage/new encode (sceneId KEPT; registry gains the videoId and it becomes `currentVideoId`); `rekey:true` = genuinely different scene (atom id supplied/reused-by-videoId/derived/minted; old registry use released; translations keyed to the old id orphan until re-authored + recompiled). Trip+localization write is a single Firestore transaction (all structure ops); registry updates run after it best-effort — a failure is returned in `warnings`, never a 500. |
| `POST /api/admin/structure/{trip_id}/static-images` | `{ "index", "filenames", "base" }` | Same shape — sets the scene's flat-overlay filename refs (files must exist in the image trees to render). |
| `POST /api/admin/structure/{trip_id}/categories` | `{ "categories" }` | Same shape — sets `tripCategories` verbatim on the TripGroup (+ the Trip doc's copy when present). NB: approve re-derives the semantic set from the description's "Trip Type:" line — keep them consistent. |

## Notes for implementers
- `fid` is the `field_edits.id`. The frontend never constructs audio paths — it uses the URLs in `Field.audio` / `Field.versions`.
- **Media auth:** `/audio/*`, `/overlays/*`, and `/api/sessions/{sid}/download` are language-scoped and require auth; browser `<audio>`/`<img>` send the httpOnly `review_session` cookie (set at login) since they can't set a header. The FE fetches all `/api` with `Authorization: Bearer` + `credentials:'include'`, and on any `401` clears the token and returns to the login page.
- `can_mark_done` is **server-authoritative**; the client also disables the button, but the server re-checks on `/flag`.
- After `combine`/`import`/`revert`/text-edit, the working audio changed → server clears coverage; the client must reload that field's audio element (new content at the same URL).
- `regenerate` should return as soon as the ElevenLabs clip is ready (candidate audible); the heavy splice/align happens on `combine`.
