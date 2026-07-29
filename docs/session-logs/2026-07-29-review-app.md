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

**Next steps** — dave to review, run red-team if wanted, deploy to the laptop, and
reply to the French reviewer that highlight-after-editing now works (plus: if you get
the new 409 message, "Generate from edit" first).
