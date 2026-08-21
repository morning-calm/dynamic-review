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

## Deploy (same session, ~10:23 laptop time)

- Committed `6763269` on main, pushed to `morning-calm/dynamic-review`.
- Laptop: `git pull` (fast-forward to 6763269) → `npm run build` (fresh
  `dist/assets/index-DHpb5feh.js`, new max-wait constant confirmed in bundle) →
  `sudo -n /usr/bin/systemctl restart review-app.service`.
- **Verified:** `review-app.service` AND `review-tunnel.service` both active;
  local `127.0.0.1:8000` → 200; `https://review.dynamiclanguages.org/` → 200 and
  serving the NEW bundle (`index-DHpb5feh.js`).
- Not verified end-to-end by ear: an actual listen-and-watch-coverage-post test
  needs a logged-in browser session — dave's next review pass is the real test.
  If a clip still fails to register, check the browser console for
  `played POST failed` (now retried 3×) and the network tab for `/played` posts
  every ≤5 s during playback.

**Open / deferred:** none new. Watch for reviewer reports confirming the
"listened but not recognised" symptom is gone.
