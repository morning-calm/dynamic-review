# Review App — Admin Guide

*For David (admin). Covers the whole workflow: reviewing any language yourself, plus the
admin-only steps (approving reviewers' work, importing audio, settings, completion).*

The admin can do everything a reviewer does **and** approve/finalise. English trips have no
separate reviewer — you review and approve them in one pass.

## 1. The trip list
- Shows **all** trips (across languages). Completed trips move to a separate **Completed** list.
- **Pin** a trip to push it to the top of the reviewers' lists (for urgent work). Un-pin to
  restore normal order.
- The order is: pinned first (newest pin first), then Trello card order.

## 2. Reviewing a trip (same as the reviewer guides)
- Open a trip, correct each part's text, regenerate/combine the audio, listen fully, mark Done.
- **English trips** give you the full narration toolset on the SceneDesc:
  - **Generate from edit** — re-record just the changed words.
  - **Regenerate highlighted** / **…with alt text** — select words in the narration and re-record
    them (alt text lets you spell a tricky pronunciation phonetically).
  - **Trim highlighted noise** — highlight where an unwanted blip/breath is and trim it.
  - **Insert 1s pause at cursor** — lengthen a pause after a full stop.
  - **Trim end silence** — normalise the trailing pause for the level.
- **Mandarin (`_ZH`)** trips use the 4-script block + V2/V3 voice pick (see Ted's guide).
- **Japanese (`_JP`)** trips use the kanji/kana narration; the voice speaks the kana line
  (see Toshifumi's guide). The English-only selection tools are hidden for JP/ZH.

## 3. Narration settings (voice / speed / model)
- Per trip you can correct the **voice**, **speed**, or **ElevenLabs model** if the auto-guess
  is wrong. Any take that was regenerated under the old setting is reset to the master (your
  text edits are kept). Untouched master audio is preserved.

## 4. Approving a reviewer's submission
- The **Review queue** lists trips reviewers have submitted.
- Open one and check the diff (the "Changes & submit" view shows every changed field + audio).
- Then either:
  - **Approve** — writes the corrected **text** to staging (the Trip doc + TripGroup
    description/categories; for Mandarin, TripLocalizations too) and promotes the corrected
    `.mp3` masters. The trip is then finalised later by Stage 9 (ogg + subtitles + S3).
  - **Request changes** — send it back to the reviewer with a note; they can edit again.
- Approval is safe: it re-checks against **live** staging first, and if anything drifted (e.g.
  a scene was removed in staging since the review started) it blocks rather than writing.

## 5. Importing a hand-edited audio file
- On any audio part you can **import an mp3** to replace the working take (for a manual edit
  done outside the app). The previous take is archived so you can undo.

## 6. Marking a trip complete (bypass)
- For work already finished in the old system, **Mark complete** records the trip as done
  **without** writing anything to staging or masters — it's just a bookkeeping marker.
- **Un-complete** returns a trip to the active queue.

## 7. Accounts
- Reviewer/admin accounts are created with `backend/manage.py` (there is no sign-up). Reviewers
  are scoped to their language(s): Ted → Mandarin, Toshifumi → Japanese.

## 8. Bug reports *(feature being added)*
- Reviewers will be able to file a **"Report a problem"** report on any part (in their own
  language), with the exact text + audio attached. You'll be notified, and the reviewer gets a
  reply in the app once it's resolved. (Implementation options are in
  `docs/bug-reports-proposal.md`.)

## Running the app (quick reference)
- Backend: `REVIEW_APP_SERVE_FRONTEND=1 REVIEW_APP_COOKIE_SECURE=1 py -3.12 -m uvicorn --app-dir backend backend.app.main:app --host 127.0.0.1 --port 8000`
- Rebuild the UI it serves: `cd frontend && npm run build`
- Public URL for reviewers: `cloudflared tunnel run review-app` (keep this machine + the backend on).
