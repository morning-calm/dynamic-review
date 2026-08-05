# Gate-2 (AI review) — incident report & redesign proposal (2026-08-05)

## What happened on Tokyo_03_Beg_N4_JP

`sess_928f51fd60fd` was submitted 2026-08-04. The cron (`scripts/claude_review.py`,
every 5 min) picked it up and failed **five times over two days** (`auto_reviews` rows
22–26, all `status='error'`, retried every ~6 h per `ERROR_RETRY_S`), so the trip sat in
David's approve queue with no Gate-2 report and nobody was told.

**Root cause:** the `claude` CLI on the laptop is **logged out** — a manual run returns
`"Not logged in · Please run /login"`. The runner shells out to the interactive-login CLI,
so any auth lapse (token expiry, an update, a `/logout`) silently kills Gate 2 for every
language. Two bugs made it invisible:

1. The failure detail rides in the JSON envelope on **stdout**, but the error path logged
   only stderr — every report said `auto-review FAILED: claude exited 1: ` (empty).
   **Fixed today** (`call_claude` now surfaces the envelope text and appends the
   `claude /login` remedy when it sees "Not logged in").
2. Errors retry silently forever. No email, no nav badge, nothing in the admin UI says
   "Gate 2 has been down since Tuesday".

**To restore service now:** `ssh review-laptop`, run `claude` and `/login` interactively
(the cron user's home). Nothing else is wrong — the next 5-min tick will review Tokyo_03.

## Why the feature is under-delivering (fitness review)

The trip that finally exercised Gate 2 on Japanese exposed structural gaps beyond the
outage:

- **Japanese is reviewed with English-shaped tooling.** ZH fields send the structured
  4-script block and the prompt explains "Simplified is the spoken line". JP fields fall
  into the generic `else` of `session_diff()`: Sonnet gets a raw two-line text blob with
  **no hint that only the kana (last) line is voiced** — the exact trap the rest of the
  app treats as a first-class hazard (409 guards, UI hints, `_cjk_spoken`).
- **JP suggested fixes are never machine-checked.** `verify_fixes()` gates on `_ZH`; for
  ZH a fix is Hant↔Hans + zhuyin verified, for JP `suggested_fix_verified` is always
  `null`. A Sonnet fix that edits the kanji and leaves the kana stale (or vice versa)
  renders as a plausible suggestion with no warning — and the one mismatch that matters
  most (kana ≠ what the audio says) is precisely the un-flaggable one.
- **Cadence/architecture is fragile.** A 5-minute cron shelling to an
  interactively-authenticated CLI is the only trigger; the review-app itself never knows
  Gate 2 exists until a findings row appears. Silent error-retry + no alerting means an
  outage is only found by a human wondering where the report went (this incident).
- **Signal per run is modest.** Since going live (07-13) the whole table holds ~26 runs,
  almost all ZH; warn/flag counts are low single digits. That's partly *good* (the level
  check was removed for being wrong), but it means the pipeline's complexity —
  cron + CLI + ingest + status bounce — is a lot of moving parts for the value produced.

## Proposed redesign (in order of leverage)

1. **Replace the CLI subprocess with a direct Anthropic API call.** The Scripts `.env`
   already holds service keys for EL/Gemini; add an `ANTHROPIC_API_KEY` and call the
   Messages API (`claude-sonnet-5`) from `claude_review.py`. This removes the login
   state, the envelope-parsing, and the usage-limit heuristics in one move, and makes the
   runner deployable anywhere. (~40 lines; the prompt and report contract are unchanged.)
2. **Alert on failure.** `activity_notifier` already emails David within 15 min for
   logins; give it one more query — latest `auto_reviews.status='error'` older than the
   newest success — and Gate-2 outages surface the same day, not on discovery.
3. **Teach the prompt Japanese.** Mirror the ZH clause: "JP narration fields hold a kanji
   line and a kana line; ONLY the kana line is voiced; a meaning judgment must read both,
   and any suggested fix must update both consistently." Send JP fixes structured
   (`{kanji, kana}`) instead of free `text`.
4. **Mechanically verify JP fixes** (the JP analogue of the zhuyin check): a fix that
   changes the kanji line but not the kana line (or supplies kana containing kanji
   characters) is marked `suggested_fix_verified: false`. Cheap, deterministic, and
   catches exactly the audio-desync class.
5. **Trigger on submit, not cron.** `sessions.submit()` can enqueue the review (a
   background thread or a one-shot systemd unit) so feedback lands minutes after
   submission while the reviewer is still warm; keep a slow cron only as the retry
   sweeper. The current 5-min cron mostly prints "nothing to review" (300+ lines/day).
6. **Consider narrowing scope to where it earns its keep.** Meaning-equivalence and Q&A
   logic on *changed* fields is the demonstrated value (Ted's Taipei findings). Wording
   nits have been noise. If after the JP fixes the reject rate stays high, drop item 2
   (LANGUAGE QUALITY) from the prompt rather than adding more triage burden — the human
   translator is the language authority, and the triage loop costs them a re-submit.

Items 1–2 are pure reliability and need no product decision. Items 3–4 are small and make
JP a first-class citizen before Toshifumi's next submits. Items 5–6 are David's call.
