# review-app — requirements traceability

Every requirement the user stated (original brief + the four refinement rounds),
mapped to where it's implemented and its status after build + 2 code red-teams +
live integration. Legend: **✅ verified** = exercised live against the running
backend/real staging; **✅ built** = implemented + compiles/builds, not exhaustively
run; **🎧 ear-pending** = correct by code/metrics, awaits the human listen (the
load-bearing check by design); **🔵 by-design** = intentionally deferred to stage 2.

| # | Requirement (user's words, condensed) | Where | Status |
|---|---|---|---|
| R1 | Reviewer sees the full text like the GDoc, **including images** | FE Review view: per-scene text + Vimeo + overlays; BE read-model | ✅ verified |
| R2 | Text editable; on edit, **original + new both visible**; tripdesc/titlekey/scenedesc/question/option | FE `EditableField` + `diff-match-patch`; BE `original_text`/`current_text` | ✅ built |
| R3 | "Generate audio" = corrected text **+ a few words either side**; backend **cuts & edits the audio back together** | BE `audio_splice.py` (boundary-aware anchor regen + mp3-PCM splice; red-team C1 fixed) | 🎧 ear-pending |
| R4 | Corrected audio available **ASAP**, before combine | BE `/regenerate` → `audio.candidate` immediately; FE candidate player | ✅ built |
| R5 | **Combine** splices into the original; reviewer relistens | BE `/combine`; FE working player (URL-`?v=`-keyed reload) | 🎧 ear-pending |
| R6 | Regenerate the **whole block**; regenerate a **highlighted section** even with no text change | BE `/regenerate` `whole`/`highlight`; FE textarea-offset selection | ✅ built |
| R7 | Flags **done** / **edit required**; edit-required = splice imperfect → human | BE `flag` + auto-flag on low confidence/seam-fail; FE `FlagControl` | ✅ verified |
| R8 | **done** only if the **whole audio played through at least once** | BE coverage gate (`can_mark_done` ≥95%, range-clamped, reset-on-change); FE seek-proof `timeupdate` | ✅ verified |
| R9 | **Comment box** per section | BE `/comment`; FE `CommentBox` (+unload flush) | ✅ built |
| R10 | Reviewer **confirms** when all ready (pending edit-required) | FE session state surfaced to admin `ChangesSummary` | ✅ built |
| R11 | Admin checks changes (orig+new), fixes edit-required, **submits to staging**, checks staging | FE `ChangesSummary`; BE `/submit` (live re-fetch → Trip text; TripGroup no longer written — see R13) | ✅ verified |
| R12 | Two roles Reviewer/Admin; **stage 1 = single combined admin** | Schema designed-in; v1 single role, no login wall | 🔵 by-design |
| R13 | ~~Description editing targets the TripGroup~~ **Superseded 2026-08-14**: field no longer seeded (redundant for reviewers); legacy sessions write Trip `descriptionTarget` only. Family descriptions get their own flow — `docs/tripgroup-description-review-proposal.md` | 🔵 superseded |
| R14 | **Autosave** so a reviewer can leave partway and resume | BE SQLite persists everything; FE debounce + keepalive flush + retry; `POST /sessions` resumes | ✅ built |
| R15 | Standalone **audio file + description** for the admin's manual edit when splicing fails | BE `/fallback` (ElevenLabs clip + note); FE "Send to manual edit" | ✅ built |
| R16 | Admin can **download all** audio — originals + corrected versions | BE `/download` (zip); FE token-authenticated fetch→blob | ✅ built |
| R17 | Correct version is `{i}.mp3` (ready); other versions in a separate folder, e.g. `1v1.mp3` | BE versioning: `{i}.mp3` canonical + `versions/{i}v{n}.mp3` (max-suffix counter) | ✅ built |
| R18 | Work in **MP3**; convert to ogg + delete mp3s from S3 **in Stage 9** | Whole tool is mp3; `/submit` hands off; Stage 9 does ogg/S3 | ✅ built |
| Arch | Mirror the **library-app** stack (React/Vite/Tailwind), local-first, deploy later | FE mirrors `D:\Projects\WebApp\library-app`; localhost + token; proxy `/api`,`/audio`,`/overlays` | ✅ verified |

## Verification log (this pass)
- Backend **compiles + imports** clean; frontend **`npm run build` + lint** clean (97 modules).
- **Live integration** (uvicorn + real staging `Edinburgh1_OldTownGreyfriars_EN`): auth 401/200; 36 trips; session seed (voice `isla` from cache, 16 scenes); read-model = bare `video_id`, `?v=` audio URLs, `[contentTitleKey, tripgroup_description]` trip-fields; audio **Range 206**; coverage **clamp** `[[10,25.23]]`; **done-gate 409**; `edit_required` 200; overlays 200.
- **Splice correctness** (code-reviewed + agent live first/last-sentence tests): C1 boundary-cut bug fixed (no new-audio truncation / old-take slivers); S1 pause-aligned anchors + seam gate → `edit_required` on a shallow non-boundary seam; S4 peak-limit only the insert.
- **Staging + masters confirmed pristine** before and after (scene-3 = "economics", `3.mp3` md5 unchanged).

## Outstanding (human calls, not blockers)
- 🎧 **Listen to a boundary-edit splice** (first/last sentence) — the path that was broken and is newly fixed; mid-sentence already ear-confirmed.
- A real **`/submit`** on a genuine review (path verified; the write itself was author-tested earlier and reverted).
- A **browser click-through** (the API + build are verified; this is the UI equivalent of a VR check).
- **Stage 2:** real login/roles, deploy (Vercel SPA + hosted backend), per-trip voice override on the TripGroup.
