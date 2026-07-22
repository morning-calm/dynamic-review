# Session log — review-app — 2026-07-23

## Delta reviews: changed-clips re-review of already-approved trips

**Goal.** Implement the delta-review mechanism for trips whose audio/text was
partially regenerated after approval (dave's plan; first real batch = 12 A12
quiz-variety manifests uploaded to R2 `review-audio/_delta/` on 2026-07-22/23).

**Plan assessment (3 corrections, agreed direction otherwise).**
1. Discovery off the R2 `_delta/` listing ∩ `completed_trips`, NOT the Trello
   manifest — the 12 trips are completed and no longer on lanes 6/7. Bonus: "full
   review supersedes delta" (plan rule 4) falls out for free.
2. No parallel correction flow: a delta card opens a **delta session** — a normal
   session seeded with ONLY the manifest's fields — so the existing Review UI *is*
   the per-clip approve/regenerate surface, and submit→approve is unchanged
   (approve's `completed_trips` upsert bumps `completed_at` = the Stage-9
   re-finalise signal, satisfying "log like a lane-7 approve").
3. Delta clip audio seeds FRESH from R2 (`review-audio/<cid>/`), never
   `resolve_audio_dir` — the laptop's `_r2_seed_cache` would hand back
   pre-remediation takes (the known staleness trap). Fetched clips also refresh the
   seed cache in passing.

**What I did.**
- `backend/app/deltas.py` (new): R2 `_delta/` list (60s TTL cache) / fetch / parse /
  validate / delete; clip-stem → field mapping (`6`→SceneDesc, `6_q`→questionKey,
  `6_a1`→option 0; `6_a` answer clips skipped loudly). Verified index base is
  0-based against staging (`Lake_District3_A12_EN` scenes 6/8 text matches exactly).
- `review_audio.download_file()` (new): single-object fetch for the delta seed.
- `db.py`: `sessions.delta_json` column (migration).
- `sessions.py`: `create_or_resume(delta=…)` — inverted completed gate (delta
  REQUIRES completed), disjoint resume paths (`delta_json IS NULL` vs `NOT NULL` —
  shadow-session protection both directions), `_delta_seed_dir` audio source,
  one-guard field filter in `add_field` (trip-level fields drop out naturally),
  `skip_en_questions` forced off for deltas; `get_session` trims scenes to those
  with fields + returns `delta:{created,reason,n_clips}`; `approve()` deletes the
  manifest on success (loud on failure) + comment-only Trello notify (no card
  move); `delta_cards(user)` (ACL-filtered, consumed-but-undeleted guard via
  approved-session-newer-than-manifest); `open_delta()` (resumes an in-flight delta
  session even if the manifest is already gone); `review_queue()` rows carry
  `delta: bool`.
- Routes: `GET /api/deltas`, `POST /api/deltas/{trip_id}/open`.
- FE: `api.ts` types + `listDeltas`/`openDelta` + `Session.delta`;
  TripListPage "Changed after approval" section (N-changed-clips cards, status
  badge, Open/Resume); ReviewPage delta banner; ReviewQueuePage "Delta" badge.
- Docs: `docs/delta-review.md` (contract + **Scripts-side consumption check:
  `head_object` on `_delta/<cid>.json` — object gone = consumed** + edge cases),
  API_CONTRACT.md (Session.delta, 2 endpoints, review-queue row), CLAUDE.md section.

**Verified.**
- `deltas` unit checks (clip_field mappings incl. rejects) — pass.
- Live R2: 12 manifests listed & parsed; `Bath2_A12_EN` fetched (2 scenes, 8 clips).
- End-to-end smoke on the dev DB: delta card listed → `open_delta` seeded a session
  with exactly the 8 changed fields (audio downloaded from R2, `has_audio` true),
  scenes trimmed to 2, `delta` payload present, resume stable, full-review open
  still 409s `completed`, card flips to in-progress; cleaned up after.
- Backend imports clean; `npm run build` (tsc + vite) clean.
- NOT exercised end-to-end: submit→approve of a delta session (would write staging/
  R2) — the path is byte-identical to the normal approve plus the manifest delete.

**Open / low-urgency TODOs.**
- Un-complete while a delta session is `submitted` leaves it in the admin queue
  (documented in docs/delta-review.md — approve or request-changes clears it).
- Consider a nav badge for pending delta cards (currently only visible on the trip
  list page).

**Next steps.** Deploy to the laptop (git pull + restart uvicorn; tunnel stays up),
then confirm the 12 cards appear for the English reviewer ACL.

## Checkpoint 2 — deployed + Scripts-repo handoff

- **Deployed live**: pushed `3c4a857`, laptop pulled, frontend rebuilt, service
  restarted (tunnel stayed up, health ok, no reviewers were live). Verified on the
  laptop: `/api/deltas` route registered (401 unauth as expected) and a real
  `delta_cards` run returns **all 12 cards** (all trips `approved` in
  completed_trips).
- **Scripts-repo handoff** (dynamic-content, per dave): wrote the producer runbook
  `Scripts/docs/plans/2026-07-23-review-app-delta-handoff.md` (contract, checklist
  order R2-audio → staging-text → manifest, consumption polling snippet, the
  round-2 don't-upload-until-consumed warning) and appended a REPLY section to the
  originating prompt `Scripts/docs/plans/2026-07-22-review-app-delta-prompt.md`.
  Left uncommitted — dave commits dynamic-content via GitHub Desktop.
