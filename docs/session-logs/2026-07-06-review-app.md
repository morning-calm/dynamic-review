# review-app — session log 2026-07-06

## Investigation: reviewer-queue inconsistency for the new HSK1-2 Mandarin trips

**Goal:** Explain why Ted saw Taichung + KaohsiungLotusPond but not Taipei101, why they
read as "HSK3", why completed trips vanished, and Ted's content looked "unassigned" —
without losing any of Ted's in-progress edits.

**Root cause (single):** the **live laptop (`dynamic-languages-Lenovo-Z580`) was 3 commits
behind on a STALE manifest.** Its `trips_to_review.json` was `generated_at 2026-07-02` with
**zero ZH trips**; the 2026-07-06 workstation push adding `Taichung_HSK12_ZH`,
`Taipei101_HSK12_ZH`, `KaohsiungLotusPond_HSK12_ZH` was never `git pull`ed. The 3 unpulled
commits (`292e5b6..ec80524`) touch **only** the manifest.

**Diagnosis (verified live on laptop):**
- `list_trips(None)` returned 0 ZH trips (stale manifest + no `_voice_test` dir → no
  presence injection). So Ted's "queue" was the **admin review-queue** (`status='submitted'`).
- DB `sessions` held three **HSK3** (retired) rows — Ted's real work:
  `Taichung_HSK3_ZH` submitted, `KaohsiungLotusPond_HSK3_ZH` submitted,
  `Taipei101_HSK3_ZH` **in_review**. `review_queue()` shows only `submitted` → the two show,
  Taipei101 (in_review / sent-back) doesn't. **No stale status/assignment row filtered it.**
- **No assignment/ownership table exists** — "assignment" is emergent (manifest ∖ completed,
  language-scoped). "Unassigned" isn't a real state.
- **Level label is correct**: `_level_family(_HSK12_ZH)→HSK1-2`; manifest `level` preferred.
  `ZH-HSK3` lives only in the Trello `labels` array (never surfaced). Ted saw "HSK3" because
  his actual sessions ARE the retired `_HSK3_ZH` ids; the new `_HSK12_ZH` weren't pulled.
- **Completed trips hidden by design**: `list_trips` drops all `completed_trips` rows for all
  roles → they live in the view-only `/completed` tab (13 rows: 12 manual JP-EN + Tokyo_08
  approved). Not lost.

**Fix applied:** `git pull --ff-only` on the laptop (`292e5b6 → ec80524`), manifest now
`2026-07-06`. Verified: the three HSK1-2 trips now surface (level=HSK1-2, reviewable=True,
status=None). Touched only `trips_to_review.json` — `review.db` and `work/` untouched.

**Verified — Ted's edits preserved:** all three HSK3 sessions intact with field_edits
KaohsiungLotusPond 91 / Taichung 70 / Taipei101 47. Protected ZH demo `sess_5bc56203b40a`
still present.

**Open / caveat carried forward:** the new HSK1-2 trips are DISTINCT content-ids from the
retired `_HSK3_ZH` Ted worked on — the manifest does NOT migrate his HSK3 work onto them.
After the pull he'll see blank HSK1-2 trips alongside his existing HSK3 sessions. Decision
needed (with Ted): finish the HSK3 work as-is vs. re-do on HSK1-2. Nothing deleted either way.

**Next steps:** none required on the app. Coordinate with Ted on the HSK3-vs-HSK1-2 content
question above.

## Follow-up (same day): HSK3 scripts invisible + auto-update + dynamic-content root cause

**Correction:** HSK3 is NOT retired — it's a higher-level Mandarin script, a sibling of HSK1-2
in the same TripGroup/family (each Taiwan Trello card has BOTH). The CLAUDE.md "HSK3 retired"
line is about the old v2@0.85 *audio* being replaced by V3, not the HSK3 *content*.

**Why Ted couldn't see Taipei101_HSK3:** the Trello card `[review]` blocks list only
`<family>_HSK12_ZH` + `<family>_EN` — the `_HSK3_ZH` row is missing from all three. Root cause
in dynamic-content: `Trello/review_block.audio_present()` checks LOCAL `Audio Generation/`
masters only; `sync_review_variant.py` (5c hook) + backfill rebuild the WHOLE block via
`build_rows` (audio_present filter) + `splice_block` (full replace). HSK3 masters live on R2 /
`_voice_test`, not local → `audio_present=False` → HSK3 silently dropped, and re-dropped on
every sync. The export's `audit_unlisted` shares the same blind spot.

**Fixes shipped:**
- **review-app (`4f1f368`, pushed to main, pulled on laptop):** `list_trips` now injects any
  trip with a non-completed session that the manifest omits — in-progress work can never be
  hidden by a manifest/Trello-block change. Surfaces all 3 HSK3 sessions incl. `in_review`
  Taipei101. **Needs a backend restart to take effect (pending — see below).**
- **Auto-update:** laptop crontab now runs `*/10 * * * * git pull --ff-only` (tested clean
  against the dirty package-lock). Manifest/data changes auto-deploy; CODE changes still need a
  manual `systemctl restart review-app.service`.
- **dynamic-content (`D:\Dynamic Languages\Scripts`, working tree — user to commit via GitHub
  Desktop):** `Trello/review_block.py` — `audio_present()` now falls back to R2
  `review-audio/<cid>/` (cached client, best-effort → False on no creds); added
  `preserve_existing_rows()` guard so a rebuild never DROPS an already-listed family variant
  (protects against a transient R2/creds false-negative wiping rows). Wired into both write
  sites in `Trello/sync_review_variant.py`. Verified: `build_rows` now emits HSK3 for Taichung +
  Kaohsiung (audio on R2); guard smoke-tested.

**Operational finding:** `Taipei101_HSK3_ZH` audio is NOT on R2 (only in `_voice_test/
Taipei101_HSK3_ZH__Jason_Beijing__V3_1x`) — its 5c audio→R2 upload never completed (it's the
`in_review` one). So it stays out of the block/manifest until it progresses (submit/approve
mirrors to R2) — but Ted sees it NOW via the session-injection. Re-running its 5c audio→R2
step would also surface it on the card.

**Pending user actions:**
1. Restart the live backend to activate the injection: `sudo systemctl restart review-app.service`
   on the laptop (needs interactive sudo — I can't do it over SSH). Tunnel is a separate service;
   ~2-3s blip; autosave protects edits.
2. Commit + push the dynamic-content `review_block.py` / `sync_review_variant.py` changes, then
   re-run `sync_review_variant.py --apply` (or `backfill_review_blocks.py --apply`) + the export
   to restore HSK3 on the Taichung/Kaohsiung cards and into the manifest.

## Resolution (same day) — HSK3 fully surfaced

- User uploaded `Taipei101_HSK3_ZH` audio to R2; all 3 HSK3 now `audio_present=True`.
- User committed the dynamic-content `review_block.py` + `sync_review_variant.py` changes.
- **Backfill dry-run caught a voice bug**: `resolve_voice_gender` mis-defaulted the new HSK3
  rows to `voice=yu`. Confirmed true family voices from the generated audio (`_voice_test`
  dir names): Taichung=**Yu**, Kaohsiung=**AnnaSu**, Taipei101=**Jason**. So ran the SURGICAL
  per-variant hook `sync_review_variant.py --variant <cid> --voice <v> --apply` for each (not
  the board-wide backfill, which would mis-voice 2 cards + sweep unrelated ones). All 3 Taiwan
  cards now `[HSK12, HSK3, EN]` with correct narrators + TripGroup memberships added.
- Ran `export_review_trips.py` → manifest regenerated/committed/pushed (`3710ed4`). Laptop
  pulled. **All 6 ZH trips (3 HSK1-2 + 3 HSK3) now live in Ted's list**, HSK3 resuming his
  sessions (Kaohsiung/Taichung submitted, Taipei101 in_review) with edits intact. Delivered
  via the MANIFEST route — no backend restart required.
- **Restart-permission durable fix identified:** `review-app.service` is a system service →
  restart needs root; non-interactive SSH sudo fails. Fix = scoped NOPASSWD sudoers drop-in
  (`/etc/sudoers.d/review-app-restart`) for `systemctl restart review-app.service` +
  `review-tunnel.service`. User to add it; then Claude restarts on request. Only CODE deploys
  need a restart (manifest/data auto-deploy via the pull cron). Decision: restarts stay
  explicit (on request), not auto-in-cron.
- **Sudoers rule added by user** (`/etc/sudoers.d/review-app-restart`) — passwordless
  `systemctl restart` for review-app + review-tunnel now works over SSH. Verified: Claude
  restarted `review-app.service` non-interactively; both services active; health HTTP 200;
  **session-injection net (`4f1f368`) now ACTIVE** in the running backend. Policy: restarts
  stay explicit (Claude restarts on request). Only code deploys need it; manifest/data
  auto-deploy via the pull cron.
- **Minor open:** systemd warns the review-app unit changed on disk → run
  `sudo systemctl daemon-reload` on the laptop when convenient (outside the restart-only
  sudoers scope; harmless to defer). HSK3 TripGroup button/level wording is a default;
  duration unknown (R2-only audio) — cosmetic. Follow-up idea for dynamic-content:
  `resolve_voice_gender` should inherit a family's HSK12 voice for a sibling HSK3 lacking
  stored voice data, so a board-wide backfill can't re-mis-default it to `yu`.
