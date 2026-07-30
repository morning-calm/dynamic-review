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

---

## 23:15 — Jedburgh pronunciation reseed + Scripts-driven refresh WOW

**Goal**
- Execute the 8-trip Jedburgh/Melrose seed-cache refresh (audio-only pronunciation fix,
  25 clips re-uploaded to R2 earlier today by the Scripts side).
- Answer "can the Scripts repo drive this directly next time?" — build and document that
  way of working.

**What I did**
- On the laptop (over ssh): backed up review.db to R2, then `refresh_trips.py`
  audit → clear → warm → verify on 7 of the 8 cids. All 245 refilled cached mp3s
  byte-match R2. Reseeded `sess_49e374536b68` (`Jedburgh1_TownAbbey_EN`, zero
  edits/flags/takes — all guards passed; 70 sessions remain, 0 orphans).
- `Melrose_EN` audited **HANDS OFF — completed trip**, so it was excluded from the
  clear/reseed and routed through the delta-review flow instead: uploaded
  `review-audio/_delta/Melrose_EN.json` (scene 7, clip `7`, reason "Jedburgh
  pronunciation fix"). The delta approve will bump `completed_at`, which is exactly the
  Stage-9 re-finalise signal this already-published trip needs.
- Gotchas hit: non-interactive ssh shells need `REVIEW_APP_SCRIPTS_ROOT` exported or the
  backup aborts on missing R2 creds; `refresh_trips.py verify --changed` asserts
  quiz-newer-than-narration (quiz-variety semantics) and false-alarms on a narration
  batch — relied on the byte-match verify + R2 LastModified instead.
- **New WOW: `Scripts/refresh_review_app.py`** (dynamic-content repo) — one command from
  the workstation runs the whole § 5 procedure: freshness-gates every changed clip on R2
  (48 h default), backs up review.db, audits over ssh, auto-generates + uploads
  `_delta/<cid>.json` for completed trips, clear+warm+verifies the rest, and runs the
  guarded reseed only with `--reseed`. Replaces the hand-written reseed-prompt handoff.
- Docs updated: `Scripts/Trello/REVIEW_QUEUE_HANDOFF.md` § 5 (driver is now the primary
  path; manual laptop steps kept as a fallback detail), Scripts `CLAUDE.md` pointer,
  this repo's `docs/adding-trips-to-review.md` § 5b and `CLAUDE.md` (Pipeline scripts).

**Verified**
- `verify`: 7/7 trips, every cached file matches R2 (ETag MD5); all 25 changed clips
  show R2 LastModified 2026-07-30 21:43–21:44.
- End-to-end test of the new driver on `Jedburgh1_TownAbbey_A12_EN` + `Melrose_EN`:
  correct routing (refresh vs delta), clean verify.
- reseed dry-run before the real run; guards all green.

**Open / low-urgency TODOs**
- `Jedburgh1_TownAbbey_EN` scene 12 staging text still carries the literal `REMOVED`
  marker + two orphaned paragraphs (only copy of that prose — writer's call, Dave). The
  new clip correctly stops at "See you then."; the reviewer will see junk text under
  audio that doesn't say it — intended.
- `ValidateTripSceneDesc.py` (Scripts) doesn't catch a `REMOVED` editorial marker —
  worth a rule.
- dynamic-content changes (refresh_review_app.py, HANDOFF § 5, CLAUDE.md) are
  uncommitted — Dave commits that repo via GitHub Desktop. review-app doc changes also
  left uncommitted.

**Next steps**
- Reviewer re-confirms the Jedburgh trips normally; Melrose_EN shows a "1 changed clip"
  delta card.

### 23:55 addendum — bus cross-check + red-opus pass on refresh_review_app.py

- Added the independent completed-trip check Dave asked for: the driver now
  cross-references `stage9.completed.load_completed()` (R2 `_bus/completed_trips.json`)
  against the audit verdict; disagreement warns loudly and the trip is blocked, never
  cleared/reseeded on conflicting evidence. Re-tested live: Melrose_EN routes to delta
  with both signals agreeing.
- **red-opus found one real bug** (confirmed by hand-trace): the driver's verify gate
  matched only the string "MISMATCH", but `refresh_trips.cmd_verify` also fails via the
  "not on R2" branch (and `check=False` swallowed ssh rc 255) — a failed refresh could
  have marched on to `--reseed`. Fixed by gating on verify's exit code via a new
  `ssh_rc()`; corrections/multipart are excluded from `bad` so no false aborts.
- Its other edits (all verified): UTF-8 decode with errors=replace on ssh output;
  `parse_audit` handles cids with spaces; `scene_index` rejects malformed stems with a
  message; dropped a duplicate `head_object` loop (freshness gate already proves the
  stems exist); warning when a delta stem names no reviewable field (e.g. `<i>_a`);
  doc/docstring accuracy fixes incl. § 5b "warm" wording.
- Verified: ruff clean; Scripts fast suite green (827 passed — 19 new tests are a
  parallel HSK/Korea session's, unrelated); backend import ok; read-only live test of
  ssh_rc/parse_audit/verify-rc against the laptop (a third full driver run was blocked
  by the permission classifier — mutating steps were already proven live twice).
- Deferred reviewer findings (judgment calls, not defects): stale local
  completed_trips.json fallback if R2 is down (fails safe — refusal + warn); a
  bus/audit disagreement can upload a delta manifest the app ignores until the trip is
  (re-)completed (warned, harmless dangling object); zero-reviewable-stem manifests
  upload with a warning; fixed 600 s ssh timeout could abort a very large `warm`.

### Close-out (23:45)

- Belt-and-braces full live run of the red-teamed `refresh_review_app.py` on the test
  pair (Jedburgh1_TownAbbey_A12_EN + Melrose_EN): green end-to-end — freshness gate,
  backup, audit + bus agreement, delta manifest (idempotent re-upload), clear/warm,
  verify rc=0.
- Committed this repo's doc changes (CLAUDE.md, adding-trips-to-review.md § 5b,
  session log, BACKLOG 0k/0l) and pushed main.
- dynamic-content changes (refresh_review_app.py + REVIEW_QUEUE_HANDOFF.md § 5 +
  CLAUDE.md pointer) remain for Dave to commit via GitHub Desktop.
