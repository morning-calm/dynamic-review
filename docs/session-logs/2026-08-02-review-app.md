# 2026-08-02 — review-app

## 14:00 — Waveform clip-insert + the "hand-edited audio wedges the text tools" bug

### Goals
Three asks from Dave after heavier real use:
1. Admin download available at any review stage.
2. "Create new" takes insertable into the waveform at the cursor.
3. Bug: *Fix pronunciation* / *Regenerate highlighted* stopped working on two scenes of
   Kaohsiung Lotus Pond EN once the waveform had been edited — diagnose from the log.

### What I did

**(1) Admin download — no action.** Asked before building: the per-scene *Download scene
audio* button already renders in `SceneCard` for admins at every session status, and is
outside the `readOnly` inert wrappers. Dave confirmed he'd missed it. Dropped.

**(3) Diagnosed the bug from the live laptop first.** `journalctl -u review-app` for
`sess_bd2bb49fa6e3` (KaohsiungLotusPond_EN), 2026-08-01:

```
10:12:01  wave/delete            200
10:35:08  regenerate             409 Conflict
10:35:43  regenerate             409 Conflict
10:36:34  regenerate             409 Conflict
10:37:47  regenerate             409 Conflict
10:43:49  revert                 200      <- gave up
10:45:00  regenerate             200
```

Field 2847 (scene 4) carried `[auto] Edit removed text only — use whole-regenerate.`, and
its `original_text` has three trailing lines that `current_text` doesn't — the reviewer
deleted them and cut the matching audio out in the waveform editor. Root cause:

- `working_text` (what the take says — the splice engine's diff baseline) is re-baselined
  **only** at combine / import / manual-promote. A hand audio edit never updates it.
- So `audio_splice.pending_edit_outside_highlight` saw a pending deletion outside the
  highlight → **409 `unvoiced_edits_outside_highlight`**, whose advice is "use Generate
  from edit first" — and Generate from edit hits `plan_segment`'s delete-only guard,
  "Edit removed text only — use whole-regenerate".
- Closed loop. Both exits (whole-regenerate, Revert) discard the audio work. Revert is
  what fixed it at 10:43, which confirms the diagnosis — revert re-baselines `working_text`.

Scenes 5/7 escaped it only because they combined *before* their `wave_delete`.

**Fix — `sessions.accept_text_as_voiced`** (`POST …/text-matches-audio`, FE **"Audio
already matches"**). Sets `working_text` (+ the `_ZH` `working_hans` sibling) to the
current text. Generates nothing, touches no audio, leaves coverage alone (the audio didn't
change; the wave edit already reset it).

It is an unverifiable **claim** — ASR can't check it (Whisper hallucinates truncated words
from context, `docs/splice-end-cutoff-analysis.md`), so:
- Offered only when `_can_accept_text_as_voiced` = text ahead of the take **AND** the
  current take's version `kind` is hand-made (`not in {v0_original, splice}`, expressed as
  the complement so the growing `wave_*` family can't be forgotten). My first cut gated on
  the text mismatch alone — which lit the button up during *ordinary* edit-then-regenerate,
  where the honest answer is "no, the audio doesn't say that yet". Caught and narrowed
  before finishing.
- The FE confirms with a **take-says → field-says** diff and a warning that green text not
  actually spoken will be submitted over audio that doesn't say it.
- Both dead-end messages now name the real remedy (`audio_splice._REMOVED_ONLY`, and the
  409 detail).

**(2) Create new → insert into the waveform.**
- `sessions.wave_insert_clip` + `POST …/wave/insert-clip` — drops a `manual_clips` take in
  at the cursor, level-matched to the surrounding audio (gated RMS ±12 dB, then peak
  limited, same treatment the splice engine gives a candidate) with 8 ms equal-power seams.
  Speech-altering, so no `silence_edit` → the word cache re-transcribes honestly.
- `WaveformEditor` gets **Insert new**: one saved take → straight in; several → a picker
  with an audio preview per take (choose by ear, not by id).
- `ManualEditModal` gets **Save & insert…** — commits the draft with **no note**, so it
  does *not* raise `edit_required` or land in the admin queue, and hands the reviewer to
  the waveform. Requiring an "instruction for the admin" when the reviewer is doing the
  edit themselves was pure friction (and the flag would be a lie). The saved list now shows
  every non-draft clip, chipped *for the admin* vs *to insert yourself*.

### Verified
- `backend/tests/test_text_ahead_of_audio.py` — 12 new tests pinning both jaws of the
  deadlock, the JP kana-line nuance, and exactly when the escape hatch is offered.
  Full suite **21 passed**.
- Integration smoke against **copies** of the dev `review.db` + work dir (R2 upload stubbed
  — a real upload would overwrite a live trip's corrected audio):
  insert grew a 7.86 s take to 8.86 s for a 1.04 s clip (the ~50 ms is the two crossfade
  seams + mp3 frame rounding), archived `wave_insert_clip`, re-locked the Done gate, undo
  restored 7.86 s; wedged field → hatch offered → re-baselined → hatch withdrawn → repeat
  call `409 already_matches`; ordinary edit on a pristine take → hatch correctly hidden.
  Dev `review.db` confirmed untouched (mtime still 2026-07-23).
- `tsc --noEmit` + `eslint src` clean.
- **Not** verified in a browser, and **not deployed** — see Next steps.

### Open / low-urgency TODOs
- Scene 4 of KaohsiungLotusPond_EN is still `edit_required` with two `[auto]` notes and two
  attached takes on the laptop — the reviewer can now finish it with the new tools.

### Next steps
1. Deploy to the laptop: pull, rebuild `frontend/dist`, restart `review-app.service` —
   and check **both** uvicorn and `review-tunnel.service`. The `audio_versions` migration
   runs automatically on first connect.
2. Tell the reviewer the Create new → Insert new flow exists (it's in the admin guide,
   which the in-app **?** serves live with no rebuild).


## 17:30 — undo/redo text baseline + browser verification

### Goals
Dave: test the UI in a browser, fix the undo/redo bug found earlier, test the bug fix
completely, then red-team.

### What I did

**Fixed the undo/redo bug.** `_restore_audio_version` moved the audio cursor but left
`working_text` on whatever the newest take set — so undoing a combine left the splice
engine diffing against text the restored audio doesn't say, and "Generate from edit" saw
nothing to voice (the edit could not be re-voiced at all). Same wedge class as the
waveform deadlock.

Each `audio_versions` row now snapshots **what that take says** — `working_text` +
the `_ZH` `working_hans` sibling — and undo/redo restores it with the audio:
- `_set_working(new_text=…, new_hans=…)`: passed by the four ops that re-voice FROM text
  (combine ×2, import-mp3, promote-a-manual-take). Everything else — waveform tools,
  trim-noise, pauses — inherits, because it changes the audio without changing what it
  says. Getting this backwards would have made a waveform cut silently claim the
  reviewer's un-voiced text edit.
- `''` = unset (→ `original_text` / `orig.Hans`); **NULL = pre-migration → leave the
  baseline alone**, since it's genuinely unknowable. The migration backfills v0 from
  `original_text`, the one legacy case that IS knowable.
- `accept_text_as_voiced` re-stamps the version it sits on — otherwise an undo/redo round
  trip would restore the pre-claim baseline and re-wedge the field.
- Refactored `_zh_working_hans_patch` onto a general `_working_hans_patch(frow, hans)` that
  can also CLEAR the baseline (stepping back to a take that predates any combine).

**Found and fixed a `??` vs `||` bug this exposed.** `working_text` can legitimately be
`''` (meaning unset), but `SceneCard` and my new `RegenerateControls` code used
`working_text ?? original_text`, which keeps the empty string. For JP that made
"Generate from edit" light up on every field. Both now use `||`, matching the backend's
`_working_base_raw` / `_cjk_spoken`.

**Browser pass** (local uvicorn + vite on :5199 — a pre-existing library-app dev server
held IPv4 :5173, so review-app's vite had bound IPv6-only and I was initially looking at
the wrong app). R2 creds blanked in the backend's env so no local edit could touch the
live bucket (verified `_r2()` returns None with empty creds).

### Verified
Backend suite **31 passed** (10 new tests for the version snapshot: what each op records,
inherit-vs-record, the ZH set/clear patch, corrupt-blob tolerance). `tsc` + `eslint` clean.

Integration smokes against **copies** of the dev DB/work dir (R2 stubbed):
- `smoke_undo_text.py` — v0 backfilled from `original_text`; combine → undo restores the
  seed baseline and the edit becomes un-voiced again; redo restores the combined baseline;
  an "Audio already matches" claim survives an undo/redo round trip; a NULL legacy row
  leaves the baseline untouched.
- `smoke_deadlock.py` — reproduces the reported bug through the REAL `sessions.regenerate`
  (only Gemini/Whisper/ElevenLabs stubbed): highlight → `409
  unvoiced_edits_outside_highlight`, Generate-from-edit → "only removed text, nothing new
  to voice", then after `accept_text_as_voiced` the identical highlight call plans a
  candidate with no 409.

Browser, on `A._A. Milne and Rudyard Kipling_A12_EN` scene 1:
- Toolbar renders; "Audio already matches" correctly **absent** on the pristine take.
- Waveform opens with **Insert new**; with no takes it toasts and auto-opens Create new.
- Import → **Save & insert…** → modal closes, waveform stays open, Insert new goes amber;
  the take is listed as *to insert yourself* (no note, so no admin queue, no
  edit-required) — the "Create new" button stayed un-highlighted, as intended.
- Insert at cursor 0:03.07: 7.82 s → **18.19 s** (clip 10.42 s minus two 8 ms seams);
  version archived, Undo enabled, Done gate re-locked, waveform redrawn.
- Second take → **picker** ("Which take goes in at 0:03.07?") with per-take audio preview;
  inserting the 2.09 s take → **20.22 s**.
- Deleted a sentence from the narration → **"Audio already matches" appears**; the confirm
  modal shows the *take says → field says* diff with the deletion struck through; confirming
  toasts, re-baselines, hides the button, and leaves the audio and coverage untouched.
- **Undo** → audio back to 18.19 s **and the button reappears**, i.e. the text baseline came
  back with the audio (the fix, visible in the UI).
- Phone layout: the "Edit the waveform" disclosure contains both buttons and auto-opens.
- No console errors; no 4xx/5xx or tracebacks in the backend log.

Dev checkout restored afterwards (DB, working mp3, versions, clips) — verified back to
one v0, no clips, 7.86 s; the throwaway admin password went with it.

### Next steps
Red-team pass, then deploy to the laptop.


## 19:00 — red-opus pass + my verification of it

### What the reviewer got right
- **Real latent bug in `accept_text_as_voiced`**, confirmed by hand-trace: the field patch
  `_zh_working_hans_patch` is a **no-op when `cur.Hans` is empty**, but the version re-stamp
  wrote `_zh_current_hans(frow)` unconditionally, i.e. a literal `''`. On a `_ZH` field with
  a combined `working_hans` and a blank `cur.Hans`, a later undo→redo would then CLEAR a
  baseline the field still held, sending the next surgical splice back to `orig.Hans`. Fixed
  by routing the stamp through `_version_text_snapshot` with the same `… or None` inherit
  signal the combine sites use, so field and version can't diverge by construction.
- Dropped a redundant `SELECT n FROM audio_versions` in `serialize_field` (`kind_by_n`
  already holds every `n`) — verified equivalent, and it runs once per field.
- Caught a wrong field name in my `textMatchesAudio` JSDoc (`text_ahead_of_audio`, which
  doesn't exist) and a migration comment that described `''` where the SQL copies
  `original_text`.
- Verified all six things I flagged, including that `trim_noise`'s English branch calls
  `_set_working` directly rather than via `_commit_working_edit` — I had not checked that
  one; it inherits, which is correct.

### ⚠️ What the reviewer BROKE — caught by verifying rather than trusting
It extracted a `_loc_block(frow)` helper **whose name was already taken**: `_loc_block(node,
scripts=…)` exists at module scope (line 378) and flattens a TripLocalizations *node*. The
new definition **shadowed** it, so `_index_localization` — used when seeding **every
Mandarin trip** — raised `TypeError: 'NoneType' object is not subscriptable`.

All four gates passed anyway: the import check can't see a redefinition, and nothing in the
suite exercised `_index_localization`. Renamed to `_zh_localization` and added three
regression tests (`_loc_block` node flattening, `_index_localization` end-to-end, and an
explicit "these are two different functions" assertion) so the collision can't recur
silently. This is the textbook red-team failure mode — an edit made to justify the run,
landing on working code.

### Findings I actioned beyond its edits
- **Its finding A — the confirm modal could under-report the claim.** `acceptTextAsVoiced`
  flushed the pending text save *before POSTing*, but the modal diffs the SAVED text, so
  editing and clicking inside the 1 s debounce showed one diff and baselined a newer one —
  defeating the modal's only job. Moved the flush to modal-OPEN (`openTextMatches`), and it
  now refuses to open if the flush fails rather than showing a stale diff.
  **Re-verified in the browser by staging the exact race:** saved "EDIT ONE saved.", then
  typed "EDIT TWO typed just now." and clicked with no pause — the modal showed EDIT TWO,
  and the DB baselined EDIT TWO on both the field and the version row.
- **Its finding B — I had widened the blast radius of a corrupt `localization_json`.**
  `serialize_field` now reaches `_cjk_spoken`, so an unparseable blob would 500 the whole
  session GET instead of one op. Guarded in `_text_ahead_of_audio` only (that field loses
  its button, the trip still opens) — deliberately NOT in `_zh_hans_for_tts`, because
  `regenerate` should still fail loudly there rather than quietly route Chinese through the
  English splice engine.
- **Its finding D — `ZhFieldBlock.tsx` used `??` on `working_hans`.** Behaviour-identical
  today (only because `_working_hans_patch` *pops* the key instead of writing `''`), but the
  same trap the `SceneCard`/`working_text` fix above closed. Changed to `||` with the reason
  in a comment, so the two `_ZH`/JP gates now resolve their baseline exactly as the backend's
  `_cjk_spoken` / `_working_base_raw` do.
- Its finding E: refreshed the stale `AudioVersion.kind` comment in `api.ts`.
- Added the missing `400 no_audio` to the API_CONTRACT row for `text-matches-audio`.

### Findings I triaged and left
- **C — `_zh_hans_for_tts` is now literally `_zh_current_hans(frow) or None`.** Collapsing
  them is tempting but changes `regenerate`'s CJK error semantics; left for a deliberate
  decision.
- **F/G** — abandoned drafts listed in the modal (intended, and chipped honestly) and no
  toast on "Save & insert" (the modal closing + the amber "Insert new (N)" reads clearly —
  I watched it).

### Verified
34 tests pass (3 new), backend import ok, `npm run build` ok, `eslint src` clean — run by
me, not taken from the report. Dev checkout restored again afterwards (one v0, 7.86 s
working take, throwaway password gone).
