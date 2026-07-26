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

### Verified

- Producer verification matched all 41 generated MP3s to their R2 objects.
- The manifest contains `Jerez_CascoAntiguo_B1_ES` once in lane 6 with Sara,
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

