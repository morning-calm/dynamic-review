# Handoff prompt — add the English selection tools to Japanese & Mandarin

Copy the block below into a fresh Claude Code session started in `D:\Projects\WebApp\review-app`.
It is written to be self-contained for a cold start.

---

We're adding four editing tools to **Japanese (`_JP`)** and **Mandarin (`_ZH`)** SceneDesc that
today only work for English: **Regenerate highlighted**, **…with alt text**, **Trim highlighted
noise**, and **Insert 1s pause at cursor**. Before writing code, read `CLAUDE.md`, `API_CONTRACT.md`,
and the memories `cjk-audio-editing-plan` and `bug-reports-feature`.

**Why they're currently hidden for CJK (the crux):** all four map a textarea char-range → an audio
time using the **English** pipeline — `audio_splice.highlight_span_in_cleaned` +
`audio_splice._whisper_index_map` + faster-whisper **English** word timings. That mapping is useless
for spaceless CJK, so I deliberately hid the buttons (`hasSelection={!isJp}` in
`frontend/src/components/SceneCard.tsx`; `hasSelection={false}` in `ZhFieldBlock.tsx`) and the backend
CJK branch in `sessions.regenerate` ignores `range`. Un-hiding alone would misbehave — the real work is
building the CJK char→time mapping.

**The approach (keep it ISOLATED from the English path, like `cjk_splice.py`):**
- We already have a working CJK forced aligner: `cjk_align.align(audio_path, text)` returns per-char
  `{pos, start, end, score}` for hanzi (ZH) / the kana line (JP). `cjk_splice.py` already uses it to turn
  a char span into silence-anchored cut points (`plan_cuts`, `gap_cut`, the energy-min refine).
- **Regenerate highlighted / alt-text:** map the highlighted char range in the spoken text (ZH: the
  Simplified `cur.Hans`; JP: the kana line) → char indices → aligner timings → cut points (reuse
  `cjk_splice`'s silence/`gap_cut` logic) → EL-generate the highlighted span (or the alt text) →
  assemble with `audio_splice.do_splice` (`span_only`). This is a more granular sibling of the existing
  CJK "Generate from edit"; factor the shared cut-planning so both use it.
- **Trim highlighted noise / Insert pause:** these only need char-range → audio time. Swap the English
  `_whisper_index_map` mapping for the aligner's per-char times, then reuse the existing
  `audio_io.trim_slivers` / silence-insert logic unchanged.

**UX to resolve:**
- **JP** narration is a single `kanji⏎kana` textarea; the reviewer highlights in the **kana** line
  (that's what's voiced). Un-hide the tools for JP and route them to the CJK handlers.
- **ZH** hanzi is edited in the **4-script block** (`LocalizationEditor`), not one narration textarea —
  so decide where the reviewer highlights (likely the Simplified script field) and plumb a selection
  range from there. This is the harder half; scope it explicitly.

**Constraints (do not regress — this path was twice red-teamed):** additive; leave the English engine
and `audio_splice.py` untouched where possible; per-language confidence floors as in `cjk_splice`
(anchor ZH 0.90 / JP 0.50; mean ZH 0.50 / JP 0.30); the **human seam-listen is the backstop**; bail to
whole-regen on any uncertainty.

**Environment:** the forced-aligner venv is at `research/cjk-aligner/venv` (py3.12, torch/torchaudio/
uroman). Test sessions: `sess_5bc56203b40a` (ZH, voice now = **annasu**, female) and `sess_928f51fd60fd`
(JP). Reuse the harness patterns in `research/cjk-aligner/redteam_cjk.py` (no EL) and `validate_cjk.py`
(EL — credits are available; **ZH must use the `annasu` voice** to match the masters, `yu`/`jason` are
male). Start the app with `REVIEW_APP_SERVE_FRONTEND=1 REVIEW_APP_COOKIE_SECURE=1 py -3.12 -m uvicorn
--app-dir backend backend.app.main:app --host 127.0.0.1 --port 8000` and `cd frontend && npm run build`;
restart uvicorn manually via the Bash tool after backend edits (PowerShell bg launch dies).

**Git:** current work is committed on `feat/cjk-surgical-splice` (latest `beb4f98`), not pushed. Branch a
new one off it for this feature. Deliver: red-team + a human seam-listen (send audio), then commit.

Plan it first (which tool to land first — Insert-pause/Trim-noise are the easiest since they're
pure char→time; Regenerate-highlighted/alt-text are the splice-bearing ones), and confirm the ZH
highlight-source UX with me before building that half.
