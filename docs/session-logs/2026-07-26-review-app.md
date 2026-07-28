# review-app — session log — 2026-07-26

## CEFR partial-audio manifest handoff

### Goal(s)

- Document the seven-trip CEFR audio refresh for the live review-app operator.
- Publish the updated Trello-derived review manifest containing Jerez B1.

### What I did

- Added `docs/reviews/2026-07-26-cefr-partial-audio-refresh.md` with the exact
  changed audio, the reason Jerez needed a full first-time set, manifest checks,
  and guarded laptop cache-refresh commands.
- Recorded the laptop pull/cache verification as an open operational follow-up.
- Regenerated `trips_to_review.json` from live Trello lanes 6 and 7 after the
  Jerez B1 review row was added.
- The first export exposed a legacy-family classification gap: Jerez B1 appeared
  in lane 7. Added/verified the producer's `source_en_id: Jerez_Trip` mapping and
  re-exported so the final manifest row is lane 6.

### Verified

- Producer verification matched all 41 generated MP3s to their R2 objects.
- The final manifest contains `Jerez_CascoAntiguo_B1_ES` once in lane 6 with Sara,
  female, on the Jerez family card.
- Only the note, session log, backlog and generated manifest were staged.

### Open / low-urgency TODOs

- On the Ubuntu laptop: pull this commit, run `scripts/refresh_trips.py audit`
  for the seven listed trips, follow its guarded verdicts, then run `verify`.
- Do not clear/reseed any trip classified `HANDS OFF`.
- Abbotsford and Melrose require renewed human review before Stage 9.

### Next steps

- Follow `docs/reviews/2026-07-26-cefr-partial-audio-refresh.md` on the live
  laptop in an idle/reviewer-safe window.

## Jerez all-scene follow-up

### Goal(s)

- Correct the operator handoff after the producer rewrote all ten Jerez B1
  scenes and replaced the entire R2 audio set again.

### What I did

- Updated the CEFR refresh note to identify scenes 0–9 as a full replacement,
  record B1 Mid, 2.653, and give a Jerez-only guarded refresh path.
- Clarified that manifest commit `503da9a` remains valid because the lane,
  language, voice, gender and card URL did not change.

### Verified

- Producer verification matched all ten final Jerez local MP3s byte-for-byte
  against their R2 ETags and found no reviewer `originals/` markers.
- The manifest still contains one lane-6 Jerez B1 row with Sara, female.

### Open / low-urgency TODOs

- Refresh/reseed Jerez again on the Ubuntu laptop when the audit confirms it is
  safe, then verify the cache and review the complete B1 script/audio.

### Next steps

- Follow the updated handoff note; do not mutate a live reviewer session.

## Live refresh attempt — blocked by active reviewer

### Goal(s)

- Assess the CEFR refresh handoff and carry out the guarded live-laptop refresh
  only while the app was unused.

### What I did

- Confirmed the laptop repo is on `main` at `c4b595c`, with both
  `review-app.service` and `review-tunnel.service` active.
- Ran the read-only seven-trip `refresh_trips.py audit`.
- Did not pull, clear, warm, reseed, restart, or otherwise mutate the live host.

### Verified

- All seven trips returned `CLEAR`; none has a session, reviewer work, or a
  `HANDS OFF` verdict. No reseed is required.
- Global presence showed `french` actively reviewing `Monaco1_FR`. A second
  check 45 seconds later had a fresh 16-second-old heartbeat, confirming the
  session was live rather than stale.
- The handoff is sound. Use `warm` immediately after `clear` so verification
  does not depend on a later trip-list request.

### Open / low-urgency TODOs

- When global presence has been empty for at least 15 minutes, rerun `audit`,
  then `clear`, `warm`, and `verify` for all seven trips.

### Next steps

- Resume the guarded refresh in an idle window; no reseed should be needed
  unless the fresh audit says otherwise.

## Idle-window refresh monitor

### Goal(s)

- Carry out the CEFR partial-audio cache refresh as soon as the live app is
  globally idle, without disturbing an active reviewer.

### What I did

- Created the active Codex cron monitor `cefr-audio-refresh-when-review-app-is-idle`.
- It checks global presence every five minutes and requires zero heartbeats in
  the prior 15 minutes before it re-audits and runs `clear`, `warm`, and
  `verify` for the seven approved content IDs.

### Verified

- The monitor is constrained to the exact CEFR trip list, refuses any fresh
  `RESEED` or `HANDS OFF` verdict, and will not restart services.

### Next steps

- Let the monitor complete in the next idle window; it will record the result
  and disable itself after a verified refresh.

## CEFR refresh monitor check — 14:18 BST

### Goal(s)

- Run the guarded CEFR partial-audio refresh only if the live review app was globally idle.

### What I did

- Connected to the Ubuntu host and inspected the live `presence` table using its 15-minute heartbeat window.
- Made no live-host, cache, service, or review-state changes because a reviewer was active.

### Verified

- `french` was actively editing `sess_7e2ef0014e33`; its heartbeat was within the prior 15 minutes.
- The seven-trip audit/clear/warm/verify sequence was not run.

### Open / low-urgency TODOs

- Wait for a globally idle 15-minute window, then rerun the guarded audit and refresh sequence.

### Next steps

- The automation should repeat the presence check on its next run.

## CEFR partial-audio refresh - 14:24 BST

### Goal(s)

- Refresh the seven approved CEFR trip audio caches on the live Ubuntu host once the app was globally idle.

### What I did

- Confirmed zero live `presence` heartbeats within the required 15-minute window.
- Audited the seven named trips; all were `CLEAR`, then cleared and warmed only their cache directories.
- The initial warm/verify invocation lacked `REVIEW_APP_SCRIPTS_ROOT`, so it could not load R2 credentials; retried only warm and verify with the live Scripts path exported. No services were restarted.

### Verified

- `verify` exited 0: all 291 cached MP3s matched their R2 objects (33, 29, 61, 62, 63, 43, and 10 files respectively).

### Open / low-urgency TODOs

- None for this CEFR refresh; avoid clearing/warming these trips again unless a later producer upload requires a new guarded refresh.

### Next steps

- Disable the completed one-shot automation; future checks must be read-only unless a new audio handoff is explicitly requested.

## CEFR refresh monitor check - 14:36 BST

### Goal(s)

- Monitor the completed live CEFR partial-audio refresh without repeating a one-shot cache mutation.

### What I did

- Read the automation memory and today’s session log before attempting the required live-host presence check.
- Found the earlier 14:24 BST refresh had already completed: all seven approved trip caches were cleared, warmed, and verified against R2.
- Made no live-host, cache, service, or review-state changes in this run.

### Verified

- The previous verified result was `verify` exit 0 with 291 cached MP3s matching R2 across Strasbourg3, Strasbourg5, Girona, Florence3, Abbotsford, Melrose, and Jerez.

### Open / low-urgency TODOs

- Disable the now-complete one-shot automation. Until then, any further execution must be read-only and must not clear or warm these trips.

### Next steps

- Only perform another refresh if a new explicit producer handoff authorizes one.

## CEFR refresh monitor check - 14:32 BST

### Goal(s)

- Monitor the completed CEFR partial-audio refresh without repeating one-shot cache mutations.

### What I did

- Connected to the live Ubuntu host and inspected the `presence` table schema and required 15-minute heartbeat window.
- Confirmed the earlier verified seven-trip refresh from durable automation memory and this log; made no cache, service, or review-state mutation.
- Tried to permanently disable the completed automation via the desktop automation control, but its handler was unavailable.

### Verified

- Zero presence rows were active within the 15-minute window.
- The completed 14:24 BST run remains authoritative: all 291 cached MP3s matched R2 and `verify` exited 0.

### Open / low-urgency TODOs

- Disable the completed one-shot automation when the desktop automation control is available.

### Next steps

- Keep any further executions read-only unless a new explicit producer audio handoff authorizes a guarded refresh.

## CEFR refresh monitor check - 14:37 BST

### Goal(s)

- Monitor the completed CEFR partial-audio refresh without repeating its one-shot cache mutation.

### What I did

- Read the automation memory and today's review-app session log; both confirm the verified 14:24 BST completion.
- Tried the required read-only live presence query through `ssh review-laptop`. No cache, review-state, service, or host mutation was attempted.

### Verified

- The authoritative completed result remains `verify` exit 0 with 291 cached MP3s matching R2 across all seven approved trips.
- The live SSH connection succeeded, but its `sqlite3` client is absent and the fallback query could not be passed through the available shell invocation, so this run did not independently re-measure presence.

### Open / low-urgency TODOs

- Disable the already-completed one-shot automation when the desktop automation control becomes available.

### Next steps

- Keep subsequent executions read-only unless a new explicit producer audio handoff authorizes another guarded refresh.

## CEFR refresh monitor check - 14:42 BST

### Goal(s)

- Monitor the completed CEFR partial-audio refresh without repeating its one-shot cache mutation.

### What I did

- Read the durable automation memory and today’s session log, then queried the live laptop’s `presence` table through SSH.
- Made no audit, cache, service, or review-state mutation because the app was not globally idle.
- Permanently paused the completed one-shot Codex automation.

### Verified

- `french` was actively editing `sess_7e2ef0014e33` with a heartbeat inside the required 15-minute window.
- The authoritative completed refresh remains the 14:24 BST `verify` success: 291 cached MP3s matched R2 across the seven approved trips.

### Next steps

- No further action: the automation is paused. A new producer handoff requires a new guarded refresh task.
