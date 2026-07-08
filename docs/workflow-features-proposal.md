# Workflow features proposal — recall, presence, admin inline edit, Stage-9/publish in-app, staging/live editor, 4b bug intake

**Date:** 2026-07-08 · **Status:** agreed with Dave (decisions inline), not yet built.
Builds on WS4 of the approved infra plan (`~/.claude/plans/you-are-fable-an-moonlit-hamster.md`
§ WS4) and the Session-2 prompt in `D:\Dynamic Languages\Scripts\docs\FABLE_SESSION_PLAN_2026-07-07.md`.
Grounding facts (state machine, components, endpoints) verified 2026-07-08 by code exploration.

## Decisions locked (Dave, 2026-07-08)

1. Button name: **"Recall submission"**; auto-grant gated to the submitter (`submitted_by`); admins always can.
2. Presence heartbeat for **reviewers AND admins** — Dave wants to open the app and see who is editing what, live.
3. Approved-trip recall grant = **un-complete + `changes_requested`** behind a warning showing how far downstream (Stage 9 / publish) the trip got.
4. Admin inline edits: **no listen gate**; status stays `submitted`, Approve stays live.
5. Stage-9/publish execution: **Option C — "publisher mode"** of the same app run on the workstation reading a **locally stored prod key** (never in repo, never on the laptop), layered on the **R2 `review-bus/` job bus** (CLI `publish_inbox.py` kept as scriptable fallback).
6. Production publishes: **human-clicked with a shown staging→prod diff** at launch; auto-run is a later opt-in.
7. Laptop app sees live/prod state via **prod snapshots exported to R2** by the publisher instance (no prod credential of any kind on the laptop).
8. "Edit Live" = edit **staging** (single source of truth, per WS4 decision — direct prod editing rejected) → targeted publish of changed fields + wire-file recompile, now a button.

## Current-state facts the design rests on

- Statuses: `in_review | submitted | approving | approved | changes_requested`
  (`frontend/src/api.ts:48`); editable set = `in_review, changes_requested`
  (`sessions.py:840 _EDITABLE_STATUSES`, enforced by `assert_editable` → `scope_sid_editable`).
- Reviewer submit = validate-only + `status='submitted'` (`sessions.py:3062`); admin approve =
  CAS claim `submitted→approving` → re-validate vs live staging → `commit()` (Firebase text +
  master promotion + R2 mirror) → `approved` + `completed_trips` upsert (`sessions.py:3079`);
  send back = `request_changes` → `changes_requested` + `review_note` (`sessions.py:3133`).
- **No** reviewer→editable transition after submit exists; **no** "admin is mid-review" concept
  (`approving` is transient inside the approve call only); **no** session owner column — only
  `submitted_by`/`approved_by` usernames.
- Admin page = `ChangesSummaryPage.tsx` (`/admin/:sid`) — read-only diff; the reviewer editor
  (`SceneCard` + `EditableField`/`ZhFieldBlock`/`RegenerateControls`/`FlagControl`/`CommentBox`
  /`AudioReview`) already takes `readOnly` and is reusable as-is.
- Queue = `GET /api/review-queue` + `ReviewQueuePage`; nav badge pattern = bug-reports polling
  (`UserMenu.tsx` → `api.bugCounts()` 60s).
- Pipeline handshake today = one-way `completed_trips.json`; Stage 9 + `publish_trip_text.py`
  are workstation CLI; prod creds removed from the laptop 2026-07-07.

---

## Feature 1 — Presence system (reviewers + admins)

- New table `presence(session_id, username, role, context, updated_at)` (or per-user single row,
  UPSERT). FE heartbeats every ~30 s from `ReviewPage` and `ChangesSummaryPage` with a context
  string (e.g. `"Scene 4 · SceneDesc — editing"`, `"playing audio"`, `"viewing diff"`).
  Live = heartbeat within ~2 min. Bearer-authed POST, trivially cheap.
- Surfaced:
  - Trip list + review queue: live dot + "● Yuki — editing Scene 3, active 12 min".
  - Recall logic: live **admin** heartbeat on a `submitted` session = "mid-review".
  - `GET /api/presence` (admin) + optionally a small JSON export next to `completed_trips.json`
    so pipeline scripts' "don't disturb an active session" check becomes a real query.

## Feature 2 — Recall submission

- `POST /api/sessions/{sid}/recall` (scope_sid):
  - **Auto-grant:** caller == `submitted_by` (or admin), `status='submitted'`, no live admin
    heartbeat → CAS `UPDATE sessions SET status='in_review' WHERE id=? AND status='submitted'`
    (approve's existing CAS makes the race safe; loser gets 409).
  - **Request path** (approved, or admin mid-review): reason required → row in new
    `recall_requests(id, session_id, trip_id, requested_by, reason, status open/granted/declined,
    created_at, resolved_by, resolved_at, resolution_note)`. Reviewer sees a "recall requested —
    waiting for admin" banner.
- Admin: requests pinned (amber) at top of the review queue + count badge on the nav link
  (clone the bugCounts pattern). Actions: **Send back** (grant → `request_changes` with the
  reason into `review_note`) or **I'll fix it** (decline/resolve + edit inline via Feature 3).
- **Approved trips:** grant = `uncomplete_trip` + `changes_requested`, behind a warning that
  states downstream progress (from the Stage-9 job/ledger state once Feature 4 lands: not
  finalised / finalised / published) and queues the re-run after re-approval.
- Optional: laptop activity notifier emails Dave on new recall requests (read-only on review.db,
  same as existing notifier).

## Feature 3 — Admin inline editing on the approve page

- Backend: widen `assert_editable` — allow edits when `status='submitted'` **and** caller is
  admin. One gate change opens the entire toolbox (regenerate/splice/highlight/alt/trim/pauses/
  import-mp3/comments, CJK paths + their 409 guards). Reviewers stay locked; `approving`/
  `approved` stay read-only for all.
- Frontend: **Edit** button per SceneDesc / changed field / edit-required item on
  `ChangesSummaryPage` expands the real `SceneCard` / `AudioFieldBlock` / `ZhFieldBlock` with
  `readOnly=false`; diff refreshes on collapse. Page keeps its layout; editing is opt-in per scene.
- Stamp `edited_by` on `field_edits` writes so the diff can show "touched by admin".
- No listen gate (decision 4); admin heartbeat (Feature 1) protects against a concurrent recall.

## Feature 4 — Stage 9 → publish managed by the app

**Topology:** laptop instance = staging-only control plane; workstation instance =
**publisher mode** (`REVIEW_APP_PUBLISHER=1`, `PROD_KEY_PATH=<file outside repo>`), 127.0.0.1
only, executes with local creds. Transport = **R2 `review-bus/`** (same one-way-bus idiom as
`completed_trips.json`, cross-machine).

- Laptop app: per-trip **Pipeline panel** (Approved → Finalised → Published) with "Queue
  finalise" / "Queue publish" actions that write job objects to `review-bus/` (trip id, changed
  fields, requester, timestamp); renders job status/logs read from the bus.
- Publisher instance (workstation): inbox UI listing queued jobs; per-job **staging→prod field
  diff preview** + dry-run; one human click runs Stage 9 (`stage9_finalise.py` via the local
  Scripts checkout) and/or the targeted `publish_trip_text.py` publish + wire-file recompile
  (`build_locstrings.py`); marks the job done in R2 with a result log.
- `publish_inbox.py` CLI (Session-2 phase 4) remains the scriptable/fallback path over the same
  bus. `completed_trips.json` stays untouched — the bus layers on top; Stage 9's poll keeps
  working until the bus supersedes it.
- **Prod snapshot export:** the publisher instance periodically (and after every publish) exports
  the relevant prod docs to R2; the laptop app diffs staging vs snapshot for the drift indicator
  ("published 2026-06-30 · 2 fields differ"). Snapshot lag is acceptable; no prod key leaves the
  workstation.
- Hard rule carried from the session pack: build + dry-run only; **no real production write until
  Dave clicks one himself.**

## Feature 5 — Staging(+Live) content editor (WS4 phases 1–2 folded in)

- `routes_admin.py`: admin-only staging-wide trip search/open by id — bypass the Trello manifest
  + completed-exclusion (Firefoo replacement). Read/open first, then writes.
- Non-text field editors: categories, videoUrls, images, scene add/remove/reorder — same
  targeted-single-`.update()` discipline as `sessions.py::commit`; later the sceneId single-writer
  (WS4 phase 3, `scene_ids.py` imported from Scripts — never reimplemented).
- Live dimension = drift indicator (Feature 4's snapshot) + the Publish button. Production is
  never edited directly.

## Feature 6 — Stage-4b VR/web bug reports next to the SceneDesc

**What exists (verified 2026-07-08, both repos):**
- **Web** (`library-app`): `BugReportModal.tsx` (checkboxes + 500-char text) →
  `userContentService.submitBugReport()` → callable `submitReport`. Sends **no trip/scene
  context** — the modal doesn't even receive the current trip; `TripViewer.tsx` has
  `tripId/contentId + sceneIndex + videoUrl` in state, just not passed.
- **VR** (`dynamic-languages`): `UserFeedback.cs` + `FeedbackDropdown` (VR-friendly category
  picker: `Issue_With_Audio_Levels`, `Incorrect_Translation`, `Incorrect_Information_Or_
  Mispronunciation`, `Issue_With_Scene_Glitching`, …) → `ReportService.SubmitReport` → the same
  `submitReport` callable. ContentID + scene number + timestamp ARE captured but **embedded as
  text lines in one blob**, not structured fields. Env by compile symbol: default build hits
  **staging**, `APP_ENVIRONMENT_PRODUCTION` hits prod. `FeatureFlagService` is the natural gate
  for reviewer-only UI.
- **Backend** (`dynamic-languages-backend`): `UserReports.ts` `handleReport` → Firestore
  collection **`UserReports`** `{report, rating, reportType(Bug|TripFeedback|InteractionFeedback),
  reporter(email), createdOn}` + Slack webhook. Deployed to both projects. Collection is
  **server-only** (absent from firestore.rules) — read requires admin SDK.
- No sceneId in either client; scene identity = `contentId + sceneIndex`. (Staging Trips already
  carry sceneIds from the WS1 backfill — the clients just don't read them yet.)

**Proposal:**
1. **Structure the payload** (backward-compatible optional fields on `submitReport` /
   `IUserReport`): `context: {contentId, sceneIndex, videoUrl?, timestampSec?, source: 'vr'|'web',
   appVersion?}` + `categories: string[]` (the FeedbackOption picks as discrete values).
   - Web: pass `tripId/contentId + sceneIndex + videoUrl` into `BugReportModal` from
     `TripViewer`/`LessonViewer`; extend `submitBugReport`.
   - VR: `ReportService.SubmitReport` adds the discrete dictionary keys it already has in hand
     (`SelectedContent.name`, `getCurrentScene()`); the blob stays for Slack readability.
     **Chris's repo — coordinate; additive + optional, old clients keep working.**
2. **Ingest into the review app (staging):** the backend already holds the staging admin key —
   new `external_reports.py` reads staging `UserReports` (filter `context.contentId` set),
   resolves `contentId+sceneIndex → sceneId` on ingest (staging docs have sceneIds now — makes
   reports survive future scene reorders), caches into a local `external_reports` table with
   `status open|acknowledged|resolved` + `resolved_by/at`. Status written back to the Firestore
   doc too (review app is the mediating staging writer), so state is visible outside review.db.
   Poll on session open + a periodic refresh; cheap query.
3. **Display:** on `ReviewPage` and `ChangesSummaryPage`, an amber "field reports" chip next to
   the SceneDesc of any scene with open reports — expands to category chips (the FeedbackOption
   values + reportType), free text, reporter, source (VR/web), time; acknowledge/resolve buttons
   (admin; reviewer can see). Trip list + queue rows get an open-report count. Unmapped reports
   (old blob-only ones) land in a per-trip "unassigned" drawer parsed best-effort from the blob.
4. **Prod-app reports** (real customers) land in the prod project's `UserReports` — laptop can't
   read those; include them in the publisher instance's prod snapshot export (Feature 4) or leave
   them on Slack for now. **Stage-4b review happens on the staging build, so the primary flow
   needs no prod access.**
5. Optional: gate a richer reviewer-report UI in VR behind a new `FeatureFlag` rather than a
   separate build.

## Build order & sizing

| # | Block | Size | Repos |
|---|---|---|---|
| 1 | Presence + recall + queue pinning/badge | ~1 session | review-app |
| 2 | Admin inline edit (gate widen + FE composition) | ~1 session | review-app |
| 3 | 4b bug intake: backend fields + web context + review-app ingest/display | ~1 session (VR part = Chris coordination) | dynamic-languages-backend, library-app, review-app (+ dynamic-languages later) |
| 4 | Staging-wide search/open + non-text editors | ~1 session | review-app |
| 5 | R2 job bus + publisher mode + prod snapshot/drift (Session-2 phases 3–4 superset) | 1–2 sessions, off-hours, dry-run-only prod path | review-app + Scripts |

Blocks 1–2 are independent of 3–5. Block 5 keeps the Session-2 hard rule: **no production
Firebase writes during the build.**

## Coordination / cross-repo notes

- VR payload change + FeatureFlag = Chris's repo — additive ask, fold into the existing
  CHRIS_HANDOFF thread.
- `dynamic-languages-backend` is shared with Chris's Belo work — the `UserReports.ts` change is
  small and additive; check he's not mid-release before deploying functions.
- `scene_ids.py` stays the single sceneId ruleset (imported, never copied).
