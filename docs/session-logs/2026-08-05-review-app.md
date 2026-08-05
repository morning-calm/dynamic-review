# 2026-08-05 — review-app

## Goals (six asks from Dave)
1. Admin trip-list granularity: language filter + a priority **scoring** system the
   translator works from (beyond pin).
2. Remove **"Generate from edit"** (unreliable one-word candidates, mis-placed combines);
   fold the UI into the highlight section; update help.
3. Investigate: **Revert to original didn't revert** a combined Jedburgh edit.
4. Investigate the **Tokyo_03_Beg_N4_JP AI review**; assess Gate-2 fitness; propose redesign.
5. Per-trip **audio duration** on the task list + a local copy for Dave, wired through the
   Scripts-repo export.
6. (Added mid-session) **Notifier misattribution**: "toshifumi started KochiCity_N3_JP"
   when it was admin.

## What I did

**(3) Revert bug — root-caused from the live journal, then fixed the class.**
Field 4942 (Jedburgh1_TownAbbey_EN scene 7): two 200-OK reverts on 2026-08-04, each
followed seconds later by a `PUT` re-saving the edited text. Revert never quiesces the
autosave pipeline; three compounding FE defects:
- `useDebouncedCallback.cancel()` cleared the timer but **left `lastArgs`**, so a later
  blur-`flush()` resurrected a cancelled save → fixed (cancel drops args).
- The editors' adopt-external-change effects didn't cancel a pending debounce, so a save
  armed just before Revert fired ~1 s after and re-wrote the edit → adopt now cancels
  (EditableField, SourceEditor, LocalizationEditor ScriptRow).
- Nothing drained in-flight saves before revert (unlike regenerate's flush) → FlagControl
  gained `beforeRevert`, wired to the existing flushRefs in AudioFieldBlock, SceneCard,
  ZhFieldBlock, AdminInlineEdit. The failure-retry in persist() now also aborts if the
  textarea no longer holds the failed text (a revert/undo replaced it).
`persist`'s optimistic `savedRef` then suppressed correction, so UI and server silently
diverged — exactly "revert didn't revert".

**(2) Generate from edit removed end-to-end.** FE: button + `hasTextChange` prop +
`spokenLine`/`hanziChanged` gates deleted; phone "Generate" group merged into a single
"Re-record & fix" group (Regenerate All + highlight tools); `'segment'` dropped from
`RegenerateMode`; "Audio already matches" modal reworded. BE: `models.Regenerate` Literal
narrowed; the CJK full-diff branch (with its `spoken_line_unchanged` 409) and the English
no-highlight path removed from `sessions.regenerate`; SceneDesc highlight/alt without a
selection → `400 selection_required`; the `unvoiced_edits_outside_highlight` 409 reworded
(highlight the words / Regenerate All / Audio already matches). `plan_segment` kept (span
math shared with highlight). Help updated: admin guide, Toshifumi en+ja, Ted en+zh,
quick-reference (① renumbered to Regenerate All), API_CONTRACT, CLAUDE.md.

**(1)+(5) Priority scoring, language filter, durations.**
- `trip_priority` gains nullable `score` (migration; `pinned_at=0` = not-pinned sentinel —
  old DBs have NOT NULL). `sessions.set_trip_priority` + `POST /api/trips/{id}/priority`
  (admin). Ordering extracted to `_apply_priority_order`: scored (desc) → pinned (newest)
  → Trello order, stable.
- `list_trips` now stamps `language` (suffix-derived) per trip; FE admin-only language
  `<select>` shows exactly one translator's view.
- Duration: manifest `duration_sec` (new) wins; else measured host-side from the resolved
  clip dir (`_CLIP_MP3_RE`: `{i}.mp3`/`{i}_q.mp3`/`{i}_a{n}.mp3` — excludes the answer
  clip, `{i}v{n}` corrections, fallbacks) and cached in new `trip_durations` table. FE
  shows a 🎧 mm:ss chip per trip.
- Scripts repo (`dynamic-content`): `review_block.audio_dir_of` (refactor) +
  `audio_duration_of` (ffprobe, cached in gitignored `Trello/.audio_duration_cache.json`);
  `export_review_trips.py` stamps `duration_sec` per entry and writes
  **`trip_durations.json`** (repo root, committed with the manifest) — Dave's local copy,
  with totals + the unmeasured (R2-only) list.

**(4) AI review — root cause found: the `claude` CLI on the laptop is LOGGED OUT.**
Five `status='error'` rows (6-hourly retries) since 2026-08-04; manual run returns
"Not logged in · Please run /login"; the error path only printed stderr (empty — the
reason is in the JSON envelope on stdout). Fixed `call_claude` to surface the envelope
text + the `/login` remedy. **Dave must run `claude` → `/login` on the laptop (interactive
— I can't).** Fitness review + redesign proposal written to
`docs/auto-review-redesign.md`: direct-API call instead of the CLI, failure alerting via
the notifier, JP-aware prompt (kana line is voiced), deterministic JP fix verification,
submit-triggered runs, possible scope narrowing.

**(6) Notifier misattribution — presence-first attribution.** Evidence: session
`sess_c1d63c6153cf` seeded by admin 11:00:11 (51 field rows, no `edited_by` — opening a
trip stamps nothing), presence row `admin / editing / that sid` at 11:00:23, toshifumi
holding a live 14-day token → the specialist-token heuristic reported him. Fix:
`actor_for` now consults the **presence table first** (`presence_by_session`; rows on the
session at/after the activity, 60 s slack, most recent wins), then `edited_by`, then the
old heuristic. Third misattribution of this class — presence is the first *exact* signal.

## Verified
- Backend suite **47 passed** (6 new priority/ordering/clip-filter tests, 6 new notifier
  attribution tests replaying the KochiCity incident; the generate-from-edit tests
  retargeted). Backend import ok; `py_compile` on both scripts.
- `tsc --noEmit`, `eslint src` (clean), `npm run build` green.
- API smoke against a local uvicorn (R2 creds blanked, throwaway admin, dev DB restored
  from backup afterwards): `/api/trips` returns 319 trips with
  `language`/`priority`/`duration_sec` (279 measured on first listing, cached in
  `trip_durations` — 292 rows); score set/clear + pin/unpin round-trip and re-order
  correctly; base order restores.
- Scripts: `audio_duration_of` measured Jedburgh1 at 424.9 s (2.3 s cold, 0.15 s cached);
  full `export_review_trips.py --dry-run` exit 0.
- **No browser testing** (per Dave — verified at API + build level instead).
- NOT yet deployed; laptop still needs `claude /login`.

## Open / low-urgency TODOs
- `claude /login` on the laptop (interactive) — until then Gate 2 stays down; next cron
  tick after login reviews Tokyo_03 automatically.
- Redesign decisions in `docs/auto-review-redesign.md` (items 5/6 are Dave's call).
- BACKLOG P2 #3 (`user_id` on `field_edits`) remains the durable attribution fix;
  presence-first covers the observed failure modes.
- First laptop `/api/trips` after deploy will ffprobe-measure the R2-only trips once
  (seed cache is local there) — slightly slow single listing, then cached.

## Next steps
1. ~~Commit + push both repos~~ — DONE (`d4c5d62` review-app, `8edd03f4` dynamic-content).
2. ~~Run the real export~~ — DONE (26h 59m across 291 measured trips; 37 R2-only).
3. ~~Deploy to the laptop~~ — DONE (idle window; both services active, public 200,
   migrations applied, journal clean).
4. Dave: laptop `claude` login — first attempt didn't persist for `dynamic-languages`
   (credentials file unchanged, token `expiresAt=0`); dave then minted a long-lived
   `setup-token`, which the CLI does NOT read from disk — being wired as
   `CLAUDE_CODE_OAUTH_TOKEN` in the Gate-2 cron (dave saves the token to
   `~/.claude/cron-oauth-token`, chmod 600; I update the crontab + verify).

## 17:00 — red-opus pass + my verification

**Its real find: the revert-flush fix missed `pages/ReviewPage.tsx`** — the reviewer-facing
trip-header `contentTitleKey`/`tripgroup_description` FlagControls had no
`flushRef`/`beforeRevert` (every other mount was wired; my call-site list came from an
explorer sweep of `components/` and I never re-checked `pages/`). Its fix mirrors
AdminInlineEdit exactly (refs in the top hook block, `_ZH` branch already covered via
ZhFieldBlock) — hand-verified. Same failure scenario as Jedburgh, on the header surface.

**Its other edits, all verified:** API_CONTRACT regenerate row scoped correctly (the
`400 selection_required` is ENGLISH-only — the CJK branch whole-regenerates instead; I
applied the same scoping to CLAUDE.md, which had my imprecise wording); `import
subprocess` hoisted in review_block.py; a NaN-comparator "why" comment in TripListPage.

**Findings triaged:** (1) CJK alt-no-range whole-voices the alt — pre-existing,
FE-unreachable → BACKLOG 3c. (2) `selection_required` raised late (after clean +
transcription) — left, changing it reorders the fb_orig outcome. (3) `trip_durations`
never invalidates → BACKLOG 3d (cosmetic; manifest value papers over it).
(4) `TripPriority.score` accepted Infinity/NaN via Python's json parser — applied
`Field(allow_inf_nan=False)` + verified rejection.

Gates re-run by me after all of the above: backend import ok, **47 passed**, tsc/eslint
clean, `npm run build` green; Scripts ruff clean. Deployed the addendum to the laptop
(`28a48a9`; pull → build → restart in an idle window — both services active, public 200,
zero journal errors).

## 17:45 — Gate 2 RESTORED (long-lived token wired)

Dave's interactive `/login` didn't persist for `dynamic-languages`, so he minted a
long-lived `claude setup-token`. The CLI does not read that from disk — it must arrive as
`CLAUDE_CODE_OAUTH_TOKEN`. Wiring (all verified live):
- Token moved to **`~/.claude/cron-oauth-token`** (chmod 600; originally dropped in
  ~/Documents world-readable — moved, not copied).
- The Gate-2 crontab line now prefixes
  `CLAUDE_CODE_OAUTH_TOKEN=$(cat ~/.claude/cron-oauth-token | tr -d "[:space:]")`
  (backup of the old crontab at /tmp/cron.bak).
- Headless smoke call answered; then a forced `claude_review.py --sid sess_928f51fd60fd`
  reviewed **Tokyo_03_Beg_N4_JP in 62 s: 7 ok, 2 warnings, 0 needs_human** — the two
  warnings are titleKey edits giving a more specific JP building name without the EN
  caption updated. 2 findings ingested; session bounced to `ai_review` for Toshifumi
  (nav badge only — he still has no email set, BACKLOG 0h/0c class).
- Subsequent cron ticks clean ("nothing to review" — correct, the session left
  `submitted`).
⚠️ When the token is rotated/revoked, update `~/.claude/cron-oauth-token` — the cron
env var is the ONLY auth the headless CLI sees.
