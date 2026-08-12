# review-app — 2026-08-12

## Stuck Castello delta card + re-issued-manifest guards

**Goal:** unblock `CastellodiBrolio_A12_IT` (per Scripts prompt
`docs/plans/2026-08-12-review-app-delta-session-stuck-PROMPT.md`) and close the three
gaps it exposed: no discard for delta cards, frozen `delta_json` never re-read,
approve deletes the manifest wholesale.

**What I did**
- **Unblock:** backed up review.db to R2 (`review-20260812-092940.db`), then dropped
  the zero-work delta session `sess_76bfc965b697` on the laptop — via the NEW
  `sessions.discard_delta` (deployed first), not raw SQL. The approved
  `sess_1035f8aa8b4d` (38 coverage marks + 4 admin comments) untouched. The 4-scene
  manifest `[1,2,8,9]` verified still pending on R2, so the next open seeds all four
  scenes for the Italian reviewer. Note: the prompt said "takes 0" but the session had
  3 `audio_versions` rows — all `v0_original` seed archives, which is why the work
  counters exclude that kind.
- **Guards (commit 017eac2, deployed):**
  - `deltas.delete_object(expect_doc=)` — compare-and-delete; a re-issued manifest is
    left pending, loudly.
  - `sessions.approve` — `409 delta_manifest_changed` while live manifest field set ≠
    seeded `delta_json` (invariant: a card can never approve away clips it never showed).
  - `sessions.open_delta` — reconciles: zero-work stale session is auto-discarded and
    re-seeded from the live manifest; a session with work resumes (work wins) + WARN.
  - `sessions.discard_delta` + `POST /api/deltas/{tid}/discard` + admin **Discard**
    button on the trip-list delta card — the non-destructive exit; 409 `delta_has_work`
    if any edits/flags/takes/clips/candidates exist.
- Docs: `docs/delta-review.md` edge-case section + CLAUDE.md delta paragraph updated.
  Scripts-side amend guard (`--amend-open-delta`) was already done per the prompt;
  manifest schema/consumption signal unchanged, so nothing to tell the Scripts side.

**Verified**
- `backend/tests/test_delta_reissue.py` (5 new) + full suite: **170 passed**; `tsc --noEmit` clean.
- Laptop: git pull + `npm run build` + `sudo -n systemctl restart review-app.service`;
  uvicorn AND review-tunnel active, `/api/health` 200.
- Post-discard DB: only `sess_1035f8aa8b4d|approved` remains for the trip; live
  manifest scenes `[1, 2, 8, 9]`.

**Open / low-urgency**
- Permission classifier blocked raw remote SQL deletes over ssh (fine — the app-level
  discard is the better path and now exists).
- Prompt's §4 second check (`refresh_review_app.py --clips clips4.txt` reporting
  "identical manifest already pending") is Scripts-side; not run here — the manifest
  was verified directly instead.

**Next steps:** Italian reviewer opens the Castello card → should see scenes 1, 2, 8, 9.
