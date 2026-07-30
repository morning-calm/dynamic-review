# review-app — Session log — 2026-07-30

## Taipei101 HSK12 approval-block investigation

**Goal**
- Explain why the submitted `Taipei101_HSK12_ZH` review cannot be approved, without
  changing live data.

**What I did**
- Inspected the live Ubuntu laptop's `review.db` read-only and ran the current Gate-1
  deterministic checks over `sess_67d43aae2c03`.
- Compared the affected localization blocks before/after and checked the latest Gate-2
  report/finding state.

**Verified**
- Approval has four hard Gate-1 blockers:
  - scene 6 option 0 added `这颗/這顆` but retained the old zhuyin, which omits the two
    added syllables;
  - scene 6 option 1 added `这颗` only in Hans, leaving Hant and zhuyin unchanged, causing
    both Hant↔Hans and zhuyin-alignment failures;
  - scene 8 SceneDesc has concatenated zhuyin syllables (`ㄧㄡˇㄧˊ`,
    `ㄓㄢˋㄉㄜ˙ㄉㄧˋㄈㄤ`) rather than one spaced syllable per hanzi.
- Gate 2 independently found the scene 6 option 1 Hant mismatch and returned the session
  from `submitted` to `ai_review`; finding 16 is open. Current live status is therefore
  `ai_review`, not approvable `submitted`.
- These are mechanical multi-script consistency failures, not a rejection of the native
  reviewer's Mandarin judgment. No live writes or approval attempts were made.

**Open / low-urgency TODOs**
- Existing backlog item 0a covers the wider `这颗` edits and related TTS reports.

**Next steps**
- Reviewer should synchronize Hant and zhuyin for the two scene-6 options, normalize the
  scene-8 zhuyin spacing/neutral-tone placement, resolve the open Gate-2 finding, and
  re-submit. Re-run approval validation afterward.

## Follow-up — live repair after reviewer retry

**Goal**
- Apply the two remaining corrections after dave's retry still would not approve.

**What I did**
- Re-read the live session and active presence. The reviewer had fixed/reverted the scene 6
  option-1 mismatch and resubmitted; only two zhuyin alignment blocks remained.
- Dry-ran exact replacements through the live `hsk_lib.zhuyin_to_pinyin` aligner (zero
  warnings), then applied a guarded two-row SQLite transaction:
  - field 1716: inserted the missing space in `ㄎㄜ ㄑㄧㄡˊ`;
  - field 1723: normalized all missing syllable spaces and placed the neutral-tone marker
    before `ㄉㄜ`.
- Preserved both fields' `done` flags because these were display-script-only corrections;
  Mandarin wording and audio were unchanged. Session remained `submitted`.

**Verified**
- Guarded write committed successfully; concurrent-value and submitted-status assertions
  passed.
- Gate 1 after the write: zero hard blockers.
- Full `sessions.validate(sid, mode="approve")`, including fresh staging drift checks:
  zero hard blockers. Eight remaining items are warnings/notes only and do not block approval.
- No approval or staging write was performed.

**Open / low-urgency TODOs**
- The existing zhuyin save-time normalizer backlog item would prevent this spacing class.

**Next steps**
- Dave can refresh the Changes page and approve the still-submitted session.

## Zhuyin normalizer backlog follow-up

**Goal**
- Locate the previously deferred Zhuyin spacing item and assess it against today's incident.

**What I did**
- Located BACKLOG item 0g, “Zhuyin auto-space normalizer on save.”
- Confirmed the shared `hsk_lib` canonical format: one space-separated syllable per hanzi
  and a leading neutral-tone dot. It has conversion/alignment helpers but no authored-text
  normalizer.
- Added today's Taipei101 recurrence to the backlog item.

**Verified**
- The proposed feature would have prevented both remaining Taipei101 blockers: joined
  `ㄎㄜㄑㄧㄡˊ`, joined `ㄧㄡˇㄧˊ`, and the longer joined run with a trailing/misplaced dot.
- This is the second separate approval incident after Kaohsiung, so the original “revisit
  after recurrence” condition is met.

**Open / low-urgency TODOs**
- Implement conservatively at zhuyin save time: canonicalize only unambiguous syllable
  parses and neutral-dot placement; retain Gate 1 as the hard backstop.

**Next steps**
- Proceed with implementation and tests when authorized; deployment needs the usual laptop
  idle-window pull/restart checks.

## Zhuyin autosave normalizer implementation

**Goal**
- Implement backlog item 0g after the Taipei101 recurrence.

**What I did**
- Added `backend/app/zhuyin_normalize.py`. It loads the exact committed syllable map used by
  `hsk_lib`, accepts trailing-dot neutral tones as aliases, and parses the whole field using
  the Hans character count. It normalizes only a unique one-syllable-per-hanzi solution;
  ambiguity, invalid input, or a missing map is a no-op so Gate 1 remains authoritative.
- Wired normalization into `sessions.update_localization` for `script == "zhuyin"`.
- Updated `LocalizationEditor` to adopt the normalized server response without clobbering
  keystrokes entered while the save request was in flight.
- Added `backend/tests/test_zhuyin_normalize.py` with fixture-level safety cases and the two
  real Taipei101 regressions against the full shared map.
- Red-team hardened the recursive parser so a pathologically long pasted run degrades to
  an unchanged value for Gate 1 instead of raising `RecursionError` from autosave.

**Verified**
- Backend tests: 9/9 pass, including the live 1,674-spelling map and a fail-closed
  overlong-input regression.
- `ruff check` passes for both new Python files; `compileall` passes. Full `sessions.py`
  Ruff still has three unrelated pre-existing unused-import/local findings.
- `npm.cmd run build`: TypeScript + Vite production build passed (126 modules).
- `git diff --check` passed. No live deployment or service restart was performed.

**Open / low-urgency TODOs**
- None in the implementation. Backlog item remains outside Done until deployed and smoke-tested.

**Next steps**
- Commit/push, then on an idle laptop: pull, build frontend, restart `review-app.service`,
  verify `review-app.service` and `review-tunnel.service`, and smoke-test one `_ZH` autosave.

## Clean-context red-team

**Goal**
- Independently red-team all Zhuyin normalizer code and tests after implementation.

**What I did**
- Ran the `red-opus` workflow with a fresh clean-context reviewer. Opus was unavailable in
  this workspace's agent tool, so the strongest available clean coding model was used at
  maximum reasoning with the same bounded scope and production-safety restrictions.
- The reviewer found one correctness edge case: a pathologically long joined Bopomofo run
  could exceed Python's recursion limit and make autosave return 500. It added a narrow
  `RecursionError` fail-closed path that preserves the input for Gate 1, plus a 2,000-character
  regression test. No other code behavior was changed.
- Independently read the review hunk and confirmed the unchanged value is safe: Gate 1's
  space-token alignment will report the malformed run without using this recursive parser.

**Verified**
- Independently re-ran all gates after the reviewer: 9/9 focused tests, compileall, Ruff on
  the new Python/test files, `git diff --check`, and the production frontend build all pass.
- The exact full-backend import gate remains unavailable locally: the launcher has no Python
  3.12 registration and the Scripts venv lacks FastAPI. No dependencies were installed.
- The reviewer touched only the normalizer, its regression test, and the matching log counts;
  sessions integration and frontend concurrency code were unchanged.

**Open / low-urgency TODOs**
- None from the red-team. Deployment remains pending.

**Next steps**
- Commit/push and follow the guarded laptop deployment/smoke-test sequence above.

## Commit and Ubuntu deployment

**Goal**
- Commit, deploy, and restart the live Ubuntu-hosted app after the clean-agent review.

**What I did**
- Committed all scoped implementation/test/docs changes as `a06c02f`
  (`Normalize Zhuyin formatting on save`) and pushed `main`.
- Checked the live presence table first: no reviewer or admin heartbeat in the 15-minute
  safety window.
- On `review-laptop`: fast-forward pulled `ad3fb3c → a06c02f`, built the production frontend,
  and restarted `review-app.service`. The laptop's pre-existing untracked
  `backend/review.db.bak-resync-1784471971` was observed and left untouched.
- Moved backlog item 0g to Done after live verification.

**Verified**
- Ubuntu checkout is `a06c02f`; frontend build passed (126 modules).
- `review-app.service` and `review-tunnel.service` are both `active`.
- Clean shutdown/startup in journal; uvicorn reports application startup complete with no
  errors.
- Local `/` HTTP 200; unauthenticated `/api/trips` HTTP 401 as expected; public
  `https://review.dynamiclanguages.org/` HTTP 200.
- The deployed normalizer produced the exact canonical outputs for both Taipei regressions
  using the laptop's real syllable map.

**Open / low-urgency TODOs**
- None.

**Next steps**
- No further restart is needed for the documentation-only deployment record.
