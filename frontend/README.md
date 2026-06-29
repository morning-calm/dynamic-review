# review-app · frontend

React 19 + Vite 7 + TypeScript (strict) + Tailwind 3.4 frontend for the internal
content-review tool. It mirrors the `library-app` build tooling and green theme,
but talks **only** to our FastAPI backend (no Firebase/Dexie/Stripe).

The integration truth is [`../API_CONTRACT.md`](../API_CONTRACT.md). Every shape and
URL in `src/api.ts` comes from there.

## Stack

React 19 · Vite 7 · TypeScript ~5.9 (strict, no `any`) · Tailwind 3.4 ·
react-router-dom 7 · react-modal · react-toastify · @vimeo/player ·
@headlessui/react · diff-match-patch.

## Run

```bash
npm install
npm run dev      # http://localhost:5173  (proxies /api + /audio → 127.0.0.1:8000)
npm run build    # tsc -b && vite build  — must pass clean
npm run preview  # serve the production build
```

The backend (`../backend`) should be running on `127.0.0.1:8000`. In dev, Vite
proxies `/api` and `/audio` to it, so the browser stays single-origin.

### Auth token

Every request carries `X-Review-Token`. It comes from `VITE_REVIEW_TOKEN`
(default `dev-token`). Copy `.env.example` → `.env` and set it to match the
backend's `REVIEW_APP_TOKEN`.

## Structure

```
src/
  api.ts            Typed client for every endpoint + Field/Scene/Session types
  hooks.ts          useDebouncedCallback (autosave debounce + flush/cancel)
  saveStatus.tsx    Context coordinating the global "saving…/saved" indicator
  App.tsx           Router: / → /review/:sid → /admin/:sid
  pages/
    TripListPage        GET /api/trips; open/resume a session
    ReviewPage          GDoc-style scroll; trip header + memoised SceneCards
    ChangesSummaryPage  Admin: diffs, manual-edit queue, import-mp3, download, submit
  components/
    SceneCard         memo'd per-scene card (one keystroke ≠ 20 re-renders)
    VimeoPlayer       lazy-mounts the Vimeo iframe via IntersectionObserver
    EditableField     textarea + inline diff + autosave (1 s idle / on blur)
    InlineDiff        diff-match-patch render (memoised)
    AudioReview       original/working/candidate/fallback players + coverage gate
    RegenerateControls whole/segment/highlight + combine + fallback modal
    FlagControl       done (gated) / edit-required / clear + revert
    CommentBox        autosaved per-field comment
    SaveStatus, NavBar
```

## How the tricky bits work

- **Autosave + resume.** Text edits debounce ~1 s and flush on blur. On
  `visibilitychange`/`beforeunload` a pending edit is flushed with
  `fetch(..., { keepalive: true })` (not `navigator.sendBeacon`, which cannot set
  the required `X-Review-Token` header). Reopening a trip restores full state from
  `GET /api/sessions/{sid}`.
- **Played-coverage gate.** `AudioReview` tracks contiguous play ranges of the
  *working* take from `timeupdate`, ignoring `seeking`/`seeked` jumps, and POSTs
  them to `/played`. The Done button is disabled until the server returns
  `can_mark_done` (server-authoritative; it re-checks on `/flag`).
- **Working-audio swaps.** The backend stamps `Field.audio.working/candidate/fallback`
  with a `?v=<hash>` that changes whenever the take's content changes OR coverage is
  reset server-side. The client keys its reset+`.load()` **solely on that URL
  changing** (cancelling any in-flight `/played` first), and reloads candidate /
  fallback rows the same way — so Done can never unlock against audio never heard,
  and a second take is never auditioned from cache.
- **Highlight-to-regenerate.** `selectionStart`/`selectionEnd` are read straight
  off the SceneDesc `<textarea>` and passed as `range` to `/regenerate` mode
  `highlight`.
- **Performance.** Each `SceneCard` is `React.memo`'d with local field state, the
  `ReviewPage` updater replaces only the changed scene's field array (others keep
  their reference), the Vimeo iframe lazy-mounts on scroll, and the diff is
  memoised.
- **Edge cases.** `is_static_image` shows `image_url` (audio still rendered if
  `has_audio`); `has_audio:false` is text-only with no audio gate; question
  option 0 carries a non-reorderable "✓ Correct answer (option 1)" marker; Q&A
  fields are whole-regenerate only.

## Assumptions / notes for the integrator

- **`sendBeacon` → keepalive fetch.** The contract requires the token header on
  every request; `sendBeacon` can't add headers, so unload-flush (text + comment)
  uses a keepalive `fetch`. The body is identical to a normal request.
- **Download is an authenticated blob.** A plain `<a href>` can't send
  `X-Review-Token` (→ 401), so "Download all" fetches the zip with the header and
  triggers a download from an object URL.
- **Autosave retries.** On a failed `PUT`, the client rolls its saved marker back
  (so the delta is re-sent by the next edit / unload) and schedules one real retry
  after 3 s; the save indicator's "retrying…" is therefore truthful. A
  regenerate first awaits the in-flight save so the server diffs the intended text.
- **Fallback description.** The reviewer's manual-edit instruction is sent to
  `POST …/fallback { description }`, but the `Field` shape doesn't echo it back, so
  the admin queue shows the field's `comment` + the standalone clip. Surface the
  stored description in the queue too if it's added to the `Field` payload.
- **`played` response merges into the field.** `/played` returns
  `{ played_coverage, can_mark_done }`; the client merges those onto the existing
  `Field`. If the server also mutates other field state on that call, return a full
  `Field` instead and we'll consume it directly.
- **Audio/diff degrade gracefully when the backend is down** — the Trip list shows
  an error/empty state rather than crashing. No mock data is injected into
  components.
- Build verified with `npm run build` (tsc project references + vite). No `any`,
  no `@ts-ignore`.
```
