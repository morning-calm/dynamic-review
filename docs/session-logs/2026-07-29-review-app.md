# 2026-07-29 — review-app

## Session: waveform zoom (mobile) + highlight "not locatable" fix

**Goals**
1. Waveform editor needs zoom so refined edits are possible on phones.
2. "Regenerate highlighted" / "Fix pronunciation" often fails with *"Highlighted words
   not locatable in the take's audio"* — reported by the French reviewer, worst after
   editing the first few words of a scene.

**Root cause (item 2)** — three distinct problems found in the highlight path:
- `audio_splice.plan_segment` mapped the highlighted tokens into the working take's
  token space **only through `equal` diff ops** (`_map_new_to_orig`). Highlighting any
  word the reviewer had just retyped (the exact "changed the first few words" case) →
  guaranteed "not locatable", even though the diff knows exactly which orig audio span
  the new words replace.
- `sessions.regenerate` re-cleaned the current text through **Gemini on every call**
  while the baseline clean is cached — two Gemini passes over the *same* text can
  drift, creating phantom diff ops that randomly broke highlights on untouched text
  ("often it cannot locate").
- Combining a highlight while a *separate* text edit was pending elsewhere re-baselined
  `working_text` to the full current text, silently stamping the un-voiced edit as
  spoken (after which "Generate from edit" reports no change — the reviewer's
  "problem with regenerating").

**What I did**
- `backend/app/audio_splice.py`:
  - highlight branch of `plan_segment` now maps through **all** ops — matched words
    map 1:1; words inside a replace/insert fall back to the op's ORIG range, widening
    the highlight to the op's full extent so both seams sit in text the take contains.
    Pure inserts (empty orig span) work through the existing `_silence_cut` machinery.
    A pure insert (empty orig span, `oa == ob`) is planned through the existing
    `_silence_cut` machinery — it works when the surrounding pause is longer than
    ~0.21 s, otherwise it lands on "Degenerate cut span"; that limit is pre-existing
    and shared with "Generate from edit", not new here.
    `_map_new_to_orig` removed (now unused).
  - alt-text mode refuses (with directions) when the highlight only *partially*
    covers an edit — the widened span would make the alt swallow un-highlighted words.
  - new `pending_edit_outside_highlight(working_raw, current_raw, start, end)` —
    raw-token diff, detects un-voiced edits wholly outside the highlight.
- `backend/app/sessions.py`:
  - new `_working_base_raw(frow)` helper (shared with `_cleaned_orig`).
  - `regenerate` reuses the **cached** clean verbatim when the current text equals
    the working baseline (the pure fix-pronunciation flow) — kills the Gemini
    nondeterminism class entirely and saves a Gemini call.
  - highlight/alt with pending edits **outside** the highlight → `409
    unvoiced_edits_outside_highlight` with directions ("Generate from edit first, or
    include those words in the highlight") instead of silently poisoning the baseline.
- `frontend/src/components/RegenerateControls.tsx`: regen 409s now show as a warn
  toast with the server's directions (was a generic "Regenerate failed" error).
- `frontend/src/components/WaveformEditor.tsx`: zoom + pan — view-window state that
  all drawing/pointer math goes through; two-finger **pinch zoom** (audio times
  anchored under the fingers); 🔍+/🔍−/◀/▶/Fit button cluster in the info row
  (buttons work without pinch, so desktop gets it too); envelope drawn as per-bucket
  bars so it stays solid when zoomed; view resets on every reload (edits can change
  clip length). Server still sends 1600 buckets — plenty (≈20 ms/bucket on a 30 s
  clip); min window keeps ≥10 buckets visible.

**Verified**
- Ad-hoc backend test (scratchpad `test_highlight_mapping.py`, synthetic audio +
  monkeypatched TTS): 26/26 pass — edited-first-words highlight now plans span_only
  with tL=0 and voices the new words; unchanged-text highlight regression OK; alt
  partial-overlap refused / full-cover succeeds; insert-word highlight OK; all
  `pending_edit_outside_highlight` cases (inside/outside/deletion/identical/
  whitespace-only); plus the red-team additions below.
- `py -3.12 -m py_compile` on both backend files; `npm run build` (tsc + vite) clean.
- NOT yet deployed to the laptop; not committed (per repo practice, dave triggers
  red-team/deploy).

## Red-team pass (clean-context reviewer)

**Reviewed** the whole diff. The op-mapping loop is sound: `(oa, xlo)` and `(ob, xhi)`
are always MATCHED orig/new positions, because difflib never emits two adjacent
non-equal ops — so the widened edges land on an op boundary and a clipped `equal` op
maps its edge 1:1. That is exactly what the `new_blo`/`new_bhi` 1:1 expansion assumes,
so the widening is safe; a note to that effect now sits next to `blo, bhi = xlo, xhi`.
The `oa is None` guard is now unreachable (every NEW token lies in some op) and is
marked defensive rather than removed. The delete/insert boundary conventions in
`pending_edit_outside_highlight` deliberately MIRROR `plan_segment`'s (delete inclusive
at both edges, insert exclusive), so the guard and the planner agree on what "inside"
means. 409 detail reaches the toast as a plain string (`main.http_exc` flattens
`{error, detail}`; `ApiError.detail = body.detail`).

**Fixed** — one genuine bug, in `WaveformEditor`: a pointer-id could be orphaned in
`pointers` if the canvas unmounted mid-gesture (a failed `api.waveform` swaps it for
the "Loading…" box, so the `pointerup` never arrives). The ghost id made the NEXT
single touch look like a second finger and start a bogus pinch — permanently, since
nothing ever removed it. `onDown` now clears the maps on a PRIMARY pointer down (which
by definition starts a gesture with no other finger down).

**Also** — DRY'd the raw-token-range loop shared by `highlight_span_in_cleaned` and
`pending_edit_outside_highlight` into `_raw_token_span`; the draw effect now uses the
same `v0/v1` window as the pointer maths instead of recomputing it (a zoomed click must
land where it was drawn); hoisted the repeated `_working_base_raw(frow)` call in
`regenerate`; `aria-label`s on the icon-only zoom/pan buttons.

**Left alone (for dave)** — English `hasTextChange` in `SceneCard.tsx` compares against
`original_text`, so "Generate from edit" stays lit after a combine; clicking it now
deterministically returns "No text change detected" and FLAGS the field edit_required
(before, Gemini drift sometimes manufactured a phantom splice instead — a worse
outcome, but the button state is still misleading). The JP branch already compares
against `working_text ?? original_text`; making English match would fix it, but it is
a behaviour change outside this session's scope.

**Open / low-urgency TODOs**
- Deploy: commit + push, then on the laptop `git pull`, `npm run build` in frontend/,
  restart uvicorn AND check cloudflared (`review-tunnel.service`).
- Pre-existing quirk noticed, not fixed: highlight char offsets index the raw
  textarea text but the backend maps them against URL-line-stripped text — only
  matters if a SceneDesc has a URL line *above* the narration (believed rare).
- Waveform zoom could later re-fetch a windowed, higher-density envelope for very
  long clips; not needed at current clip lengths.

## Deployed (2026-07-29 ~10:15 BST)

Red-team verified (gates re-run independently; reviewer's one behavior edit — the
WaveformEditor orphaned-pointer self-heal — hand-traced and confirmed). Committed as
`d540ef3`, pushed. On the laptop: `git pull` (ff), `npm run build` in frontend/,
`sudo -n /usr/bin/systemctl restart review-app.service`. Presence checked read-only
first — no reviewer active (latest heartbeat >15 min old, admin's own). After restart:
`review-app.service` AND `review-tunnel.service` both active, clean uvicorn startup in
journal, local `/api/trips` answers 401 (auth middleware) as expected. The
`hasTextChange`-vs-`original_text` finding went to BACKLOG § 3b.

**Next steps** — reply to the French reviewer that highlight-after-editing now works
(plus: if you get the new 409 message, "Generate from edit" first).

## Kaohsiung HSK12 approve block + Taiwan approval verification (afternoon)

**Goals**
1. Explain why `KaohsiungLotusPond_HSK12_ZH` won't approve ("Approved with validation
   issues" toast).
2. Verify today's Taiwan approvals (Taipei101/Taichung `_HSK3_ZH`) copied correctly to
   the Trips doc AND `TripLocalizations` (incl. pinyin/Hant), and confirm pinyin is
   regenerated from zhuyin at approve.

**Findings**
- The toast is MISLEADING: `ok:false` from approve means it was **NOT** approved —
  status reverts to `submitted`, nothing written. (FE fix candidate: the toast should
  say "Approve blocked by validation".)
- `sess_4243b6da1f26` is blocked by 6 hard Gate-1 issues: reviewer's hand-edited zhuyin
  on scenes 2/5/10/16/24 is missing inter-syllable spaces (scene 10 fully unspaced) and
  uses dot-AFTER neutral tone (`ㄉㄜ˙` instead of `˙ㄉㄜ`); scene 5 Hans also contains a
  traditional `隻`. The aligner (`hsk_lib.zhuyin_to_pinyin`) needs one space-separated
  syllable per hanzi.
- Remedy staged: `/tmp/fix_kaohsiung_zhuyin.py` on the laptop — mechanical re-space of
  the 6 fields + `隻→只` (homophone, audio untouched), each fix pre-verified against the
  aligner (dry run 6/6 OK). **Dave to run with `--apply`** (classifier blocks live-DB
  writes from Claude), then re-approve in the UI.
- Taiwan approvals verified field-by-field against live Firebase (read-only script,
  19/19 changed fields OK): TripLocalizations `target.{Hans,Hant,zhuyin,pinyin}` +
  `home.en` all match the reviewed text, Trip `quickTrips` lines are exact
  `"Hans\npinyin"`, `status: reviewed`, staged pinyin == fresh regen from the confirmed
  zhuyin. **Yes — pinyin is regenerated from zhuyin at approve** (`_zh_regen_pinyin` in
  `zh_writeback`; never authored, hard 409 if regen comes back empty).

**Open / carried forward**
- ⚠️ Content nit found while verifying: Taichung scenes 14 & 18 staged zhuyin/pinyin
  read 长 as `ㄓㄤˇ/zhǎng` in 有很长的历史 — should be `ㄔㄤˊ/cháng`. Faithful copy of the
  reviewer's confirmed zhuyin (aligner accepts either reading of the polyphone), display
  scripts only. Needs a small staging touch-up.
- FE toast wording on `ok:false` approve ("Approved with validation issues") implies
  success — reword.

## Follow-up (same afternoon): Kaohsiung approved + Taichung 长 fix + toast bug
- Kaohsiung zhuyin fix applied on the laptop (6/6, post-fix validate hard=0) — dave
  approved KaohsiungLotusPond_HSK12_ZH in the UI.
- Taichung_HSK3_ZH scenes 14 & 18: staging corrected ㄓㄤˇ/zhǎng → ㄔㄤˊ/cháng in
  TripLocalizations target.zhuyin, pinyin regenerated from the corrected zhuyin, Trip
  quickTrips line-2 pinyin replaced with the regen (guarded one-off, dry-run first,
  verified by read-back on both docs).
- FE toast bug fixed (ChangesSummaryPage.approve): ok:false used to toast "Approved
  with validation issues" although NOTHING is approved/written (status reverts to
  submitted). Now toast.error "NOT approved — blocked by validation, nothing written."
  (The ResultPanel wording was already correct.)

## Wrap-up (afternoon session)
- New reviewer login created on the laptop: **john** (role reviewer, languages
  ["Mandarin"]) — password generated by manage.py and handed to dave in-session.
  No email set yet (`manage.py set-email` once John's address is known — the
  activity notifier and findings emails need it). Mandarin review is moving from
  Ted to John.
- Zhuyin auto-space normalizer (would prevent the Kaohsiung-class Gate-1 blocks at
  the source): proposed, dave deferred — "not yet, we're going to change reviewer".
  Backlogged.
- Verified at wrap: uvicorn + cloudflared both active; live bundle index-ef4_H2sm.js
  (toast fix) serving through the backend.

## Evening: Hant↔Hans false positive (reported from the Scripts repo)
**Goal:** the Scripts repo flagged that its own Hant↔Hans check false-positived when
compared via `to_simplified`, and warned that `auto_checks.py` looked to have the same
bug. Verify and fix.

**Confirmed — the review app had it too.** `_zh_field_issues` compared
`to_simplified(Hant)` vs `to_simplified(Hans)`. OpenCC `s2tw` correctly writes the
durative 着 as 著 for Taiwan, but 著 is *also* valid Simplified (著名), so `t2s` leaves it
alone and the two sides never meet. Result: a **hard `block`** ("Traditional text doesn't
correspond to the Simplified text") on correct, pipeline-derived text, with nothing the
reviewer could change to clear it. Per `sessions.validate`'s mode split that is hard at
**approve** (the trip wedges with the admin) and a loud `[will block approval]` warning at
**submit**. 6 of 13 ordinary test sentences tripped it (看着/坐着/沿着/跟着/吃着/显着).

**What I did** — `backend/app/auto_checks.py`:
- New `_hant_correspondence(hsk, hans, hant)` → `ok` | `punct` | `bad` | `unknown`.
  Compares **forward** first (`to_traditional(hans) == hant`, exactly how the pipeline
  derives Hant, so a fresh pair matches char-for-char), with the **reverse** kept as a
  fallback so a reviewer's own legitimate variant still passes (裏面 for 裡面, 臺北 for
  台北). Blocks only when *both* fail; `unknown` (no opencc) says nothing rather than
  blocking on a check it couldn't run. **Red-team follow-up:** `unknown` now triggers
  whenever the *reverse* comparison is unavailable, not only when both are — with the
  variant-forgiving half missing, a forward mismatch alone can't tell a stale Hant from
  a legitimate 裏面/臺北, and the old code was silent on that path.
- The punctuation-only → `warn` demotion now applies to whichever comparison ran (the old
  code hard-blocked a punctuation nit whenever a 着 was also present).

**Verified**
- 14-case table (durative 着 ×6, 著名, reviewer variants, punct-only, 3 genuinely stale
  Hant): 14/14 as expected — every 着 case flips `bad`→`ok`, every stale case still
  blocks. End-to-end through `run_checks` with pipeline-derived Hant+zhuyin: hard=0 soft=0.
- The change is **provably more permissive** (pass if either direction matches; `punct`
  strictly demotes) so it cannot newly block anything that passed before.
- Live-DB scan (read-only, laptop): 2 of 458 four-script rows carry the pattern —
  `KaohsiungLotusPond_HSK12_ZH` sc.6 (穿着/穿著) and `_HSK3_ZH` sc.15 (连着/連著). Both
  verified `BLOCK`→`ok` under the fix. They only escaped because those rows were
  unedited (`cur == orig`, so `run_checks` skips them) — **one reviewer edit anywhere in
  those blocks would have wedged the trip.** Both trips are already `approved`, so nobody
  is stuck right now.
- Also checked: no frontend or other **backend** copy of this comparison; the zhuyin aligner
  handles the durative fine (neutral tone is leading `˙ㄓㄜ`, `hsk_lib._lead_neutral_zhuyin`).
  There IS one more copy in this repo though — `scripts/claude_review.py:195` (Gate-2's
  post-verification of a suggested fix), found by the red-team pass. **Fixed too** (below);
  it is NOT cosmetic, as first assessed.

**Second instance — `scripts/claude_review.py` (fixed in the same pass)**
`verify_fixes` re-rolled the identical reverse-only comparison, so a *correct* Gate-2
suggestion containing a durative 着 got `suggested_fix_verified: false`. That is not
cosmetic — tracing the consumers: `AutoReviewPanel.tsx:140` shows a red **"failed machine
check — don't paste as-is"** badge, `canApply` (line 110) hides the Apply button, and
`sessions.apply_suggested_fix` (line 1647) **409s `fix_unverified`**. So the reviewer is
told a good fix is broken and the one-click apply is refused.
- Now calls `auto_checks._hant_correspondence` (added `auto_checks` to the existing
  `from app import …`; the script already imports from `backend/app`).
- Kept **fail-CLOSED**: anything but a clean `ok` still fails, matching the old
  `except → ok = False`. Only the 着 case changes. (`!= "ok"`, not `== "bad"` — the latter
  would also have quietly started passing punct-only and no-opencc cases. Withholding a
  badge is the safe side; unlike Gate 1 it never blocks, so there's no reason to loosen it.)
- Verified 5-case table: durative `FAIL`→`PASS`; variant/punct-only/stale/clean all
  preserved. `py -3.12 scripts/claude_review.py --help` exercises the new import chain.

**Not done / next**
- **Not deployed to the laptop and not committed** — awaiting dave's go-ahead.
- Known remaining gap (pre-existing, not touched): a durative typed as 著 *in the Hans
  box* is not flagged, because 著 is legitimately Simplified in 著名. Can't be caught by
  character purity alone; left to Gate 2 / the human read.
- Remaining gap the fix does NOT cover: a reviewer variant AND a durative in the same
  field still blocks — e.g. Hans `里面坐着人` / Hant `裏面坐著人`. Forward gives 裡面 (not
  裏面) so it misses; reverse still trips on 着/著. Rarer than either alone; a fix would
  mean normalising variants before comparing (a real design call, not a patch).
- Red-team pass (`/red-opus`, clean-context Opus) found **one real correctness hole** in
  the first cut of `_hant_correspondence` and closed it (the asymmetric-opencc `unknown`
  guard, above) — independently re-traced here with a stub across the 2×2 converter-failure
  matrix: `t2s broken` now emits nothing on all five probe pairs, `s2tw broken` degrades
  exactly to old behaviour. It also caught the two log inaccuracies (submit-vs-approve
  severity; the `claude_review.py` copy). Its "cosmetic" call on that second copy was the
  one judgment I overrode — see above.
