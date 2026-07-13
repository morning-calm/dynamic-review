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
- **The full narration toolset works in every language** on the SceneDesc:
  - **Generate from edit** — re-record just the changed words. If the *voiced* line is
    unchanged (JP: kanji-only edit; ZH: a non-Simplified script), the app refuses with a
    message saying which line drives the audio.
  - **Regenerate highlighted** / **Fix pronunciation…** — select words in the narration and re-record
    them (alt text lets you spell a tricky pronunciation phonetically; on screen the text is
    unchanged).
  - **Trim highlighted noise** — highlight where an unwanted blip/breath is and trim it.
    Also available on **question and answer-option audio** (highlight in that field's own
    text box), in every language.
  - **Insert 1s / Insert 0.5s** — lengthen a pause after a full stop by 1 s or 0.5 s
    (0.5 s is the lighter touch for non-beginner audio).
  - **Remove 1s / Remove 0.5s** — shorten an over-long pause (≥0.25 s of natural pause is
    always kept; it never cuts speech).
  - **Trim end silence** — normalise the trailing pause for the level.
- **Where to highlight per language:** English — the narration textarea; **Japanese** — the
  **kana** (last) line of the narration (kanji-line selections get a hint); **Mandarin** — the
  **Simplified (Hans)** box of the 4-script block.
- **CJK safety behaviour:** the splice engine is deliberately conservative. When it can't cut
  at the exact spot safely, *Regenerate highlighted / Generate from edit* fall back to
  re-recording the whole narration (a toast tells you — re-listen to the full clip);
  *Fix pronunciation…* instead flags the part **edit-required** (the alt text is never silently voiced
  as the whole field); the trim/pause tools refuse with a clear message rather than guess.
- **Mandarin (`_ZH`)** trips use the 4-script block; the **Simplified (Hans)** line is what the
  voice speaks. Mandarin is **V3-only** (decided 2026-07-02) — the old V2/V3 side-by-side
  audition and per-trip pick are retired, and a `_ZH` trip now seeds a single working take
  like any other language.
- **Japanese (`_JP`)** trips use the kanji/kana narration; the voice speaks the kana line
  (see Toshifumi's guide).
- **Revert to original** (bottom right of each part) restores that part completely: the text,
  the editable English sibling, the `_ZH` 4-script block, **and** the pristine master audio.
  After a revert the part no longer counts as an edit and won't be written to staging.

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

## 5. The AI review (Gate 2) — it goes to the REVIEWER first
After a reviewer submits, an automated check (Claude) reads their edits for **meaning**,
**wording** and **Q&A logic**. It judges the target language — **not vocabulary level** (that's a
separate deterministic check; the LLM's level calls were wrong as often as right and were
removed). Nothing is ever applied automatically.

- **A clean report goes straight to your approve queue.** Only if it flags something does the
  trip bounce **back to the reviewer** (status *AI review*) — out of your queue until they've
  answered. They're told by a nav badge and an email.
- The reviewer answers each item: **resolved** (they actioned it) / **rejected** (they keep their
  version — **a note is required**, and you read that note instead of a change) / **deferred**
  (it's about the **English**, so it's your call, not theirs).
- You see every answer and note on the **Changes & submit** page.
- **"Take it back now"** (in the purple *AI review* box on that page — it reads *"Take back
  without triage"* on the trip's own review page) reclaims a trip immediately, marking any
  unanswered items **deferred to you** — the escape hatch so the gate can never wedge a trip
  (e.g. the reviewer is away).
- Suggested fixes are shown read-only unless machine-verified; a verified one gets an **Apply
  suggested fix** button.

## 6. Importing a hand-edited audio file
- On any audio part you can **import an mp3** to replace the working take (for a manual edit
  done outside the app). The previous take is archived so you can undo.

## 7. Marking a trip complete (bypass)
- For work already finished in the old system, **Mark complete** records the trip as done
  **without** writing anything to staging or masters — it's just a bookkeeping marker.
- **Un-complete** returns a trip to the active queue.

## 8. Accounts
- Reviewer/admin accounts are created with `backend/manage.py` (there is no sign-up). Reviewers
  are scoped to their language(s): Ted → Mandarin, Toshifumi → Japanese.

## 9. Bug reports
- Reviewers (and you) file a **"Report a problem"** on any audio part — in any language. The
  exact text + working/candidate audio are snapshotted with the report, so you see and hear
  precisely what the reporter saw.
- **"Bug reports"** in the top bar opens the inbox; the badge shows open reports (for you) /
  unread replies (for reviewers). Open a report to play the snapshots, **reply in a thread**,
  and set its status (**open → investigating → resolved**) — statuses are admin-only.
- `scripts/check_bug_reports.py` prints open reports from the terminal if you want a
  scriptable check.

## 10. Help button
- The **?** in the top bar opens the user guides in a new tab: the one-page **quick
  reference** and the **written guide** matched to the signed-in user (reviewers also get
  their native-language version). They're served straight from `docs/user-guides/`, so
  editing those files updates what everyone sees.

## Running the app (quick reference)
- Backend: `REVIEW_APP_SERVE_FRONTEND=1 REVIEW_APP_COOKIE_SECURE=1 py -3.12 -m uvicorn --app-dir backend backend.app.main:app --host 127.0.0.1 --port 8000`
- Rebuild the UI it serves: `cd frontend && npm run build`
- Public URL for reviewers: `cloudflared tunnel run review-app` (keep this machine + the backend on).
