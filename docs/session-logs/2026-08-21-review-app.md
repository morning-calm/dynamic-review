# review-app — 2026-08-21

## Playback-coverage loss fix ("listened whole clip, app didn't register")

**Goal:** dave (reviewing English A1-2 trips) reported clips occasionally finishing
without being recognised as played — only fix was re-listening to the whole clip.
Often correlated with starting a second clip during the trailing silence.

**Root cause:** coverage posting in `AudioReview.tsx` used a 700 ms *trailing*
debounce that every `timeupdate` (~250 ms apart) reset — so an entire listen was
sent to the server as EXACTLY ONE POST, ~700 ms after playback stopped. Any loss of
that single request lost the whole listen: (a) a failed POST was only console-warned,
never retried; (b) unmount `cancel()`s the debounce; (c) the working-URL reset effect
cancels it. Starting a second clip's audio fetches through the tunnel at exactly that
moment is a plausible trigger for (a).

**What I did (fixes 1–3 of the proposal):**
- `hooks.ts`: `useDebouncedCallback` gained an optional `maxWaitMs` — the oldest
  un-fired call can't be starved past that bound (fires immediately with freshest args).
- `AudioReview.tsx`: both coverage posters (working + original) now use
  `maxWaitMs=5000` → server receives coverage every ≤5 s of continuous play
  (merge is idempotent); `pause`/`ended` handlers flush the debounce immediately;
  a failed POST is retried up to 3 times from the client-side ranges, with a
  `workingGen` generation guard so a retry can't post old-take ranges under a
  new take's hash (the URL-reset effect bumps the gen).

**Verified:** `npx tsc -b` clean. No backend change (server merge already idempotent).

**Fix 4 (added same session):** unmount cleanup in `AudioReview` now flushes
still-dirty coverage via the existing `flushPlayedBeacon` (fire-and-forget survives
unmount; server merge idempotent). The Revert/URL-change path remains a `cancel()` —
it clears the dirty flags first, so the unmount flush can't resurrect stale ranges.
`tsc -b` clean again.

**Open / deferred:** deploy to the laptop still pending (this is the dev checkout) —
rebuild `frontend/dist` there.
