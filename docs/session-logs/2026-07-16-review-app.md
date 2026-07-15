# review-app — 2026-07-16

## Finalised-bus consumer (Stage-9 → app reverse handshake)

**Goal:** implement `D:\Dynamic Languages\Scripts\REVIEW_APP_FINALISED_BUS_PROMPT.md` —
consume the new read-only `review-audio/_bus/finalised_trips.json` that Stage 9 will
publish, so shipped trips are marked **Published** and sink to the bottom of the
Completed list, and re-approved-since-shipped trips show **Re-finalise pending**.

**What I did**
- `backend/app/review_bus.py`: `FINALISED_KEY` + `get_finalised_snapshot()` — best-effort
  GET keyed by trip_id; any failure (no creds / NoSuchKey pre-first-publish / network)
  → `{}` = "nothing finalised"; only unexpected errors log. App is strictly read-only
  on the key (mirror of the Scripts-never-writes-completed_trips rule).
- `backend/app/sessions.py`: `_parse_bus_ts` (tolerant ISO→epoch, naive=UTC),
  `_finalised_state` (join on the entry's `completed_at` with 1 s ISO-rounding slop:
  current approval newer → `restale`; else `finalised_at >= completed_at` → `shipped`).
  `completed()` fetches the bus fresh per load (no caching), adds `finalised` /
  `finalised_at` to each item, and stable-sorts shipped rows to the bottom.
- Frontend: `CompletedItem` gains the two fields; `CompletedPage` shows a **Published**
  (sky) / **Re-finalise pending** (amber) badge, greys shipped rows, prints the
  published timestamp, and labels the shipped section with a one-line divider.

**Verified:** `tsc -b` clean; backend syntax + live import ok; `_finalised_state`
exercised (shipped / restale-on-re-approval / absent / junk timestamps) — all correct;
`get_finalised_snapshot()` with no creds degraded silently to `{}`.

## Red-team + deploy (same session)

- **/red-opus** (clean-context Opus): no correctness bugs, no edits. Confirmed the R2
  read can't raise into `/api/completed`, restale-before-shipped ordering is right,
  JSX balances, gates pass. Its one cosmetic note (li-level `opacity-60` dimmed the
  Published divider) was applied — opacity moved to the inner row div.
- Committed **f9fad71**, pushed; **deployed to the Ubuntu laptop**: journal showed no
  reviewer traffic for 30 min → pull, `npm run build`, NOPASSWD
  `systemctl restart review-app.service`. Verified: review-app + review-tunnel active,
  tunnel 200, no R2-creds warning at service startup, `get_finalised_snapshot()` = 0
  trips (expected — Scripts hasn't published the key yet).

**Next steps**
- Scripts side (`stage9/completed.py` publisher) lands separately in dynamic-content;
  once it first publishes, shipped trips flip to Published on the next Completed load.
- Backlog updated (Done entry 2026-07-16).
