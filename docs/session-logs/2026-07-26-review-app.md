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
