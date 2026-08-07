# Session log — review-app — 2026-08-07

## CJK voicing parity shipped (BACKLOG 0q + 0n.1)

**Goal:** assess the plan `Scripts/docs/plans/2026-08-07-review-app-cjk-voicing-parity.md`
and fix the issues it raises for ZH and JP.

**Assessment:** plan verified accurate against the code — `sessions.regenerate` routed
zh/jp through `_cjk_spoken` → `plan_whole(raw)` with no `validate_and_clean` call, and
`apply_overrides`' single call site is inside `validate_and_clean`, so a `_ZH` regenerate
dropped both number cleaning and the trip's pronunciation pins. §7's zh double-year repro
confirmed live: 3/3 runs of `mandarin_number_clean.clean_field` emitted
「一九九九年（一九九九年）」.

### What I did

**Scripts repo (`dynamic-content`) — §7, the zh blocker:**
- `gemini_number_clean_prompts.PROMPT_ZH`: added a `1999年 → 一九九九年` example + an
  explicit "never gloss/repeat in parentheses" clause to the year rule.
- `mandarin_number_clean.py`: two deterministic dedup guards applied to every model
  response, because the numeral-stripped similarity guard is structurally blind here
  (both sides strip to nothing): `_PAREN_DUP_RE` collapses 「X（X）」 for numeral runs, and
  `_YEAR_DUP_RE` collapses the sibling raw-output flake 「一九九九年年」 (seen ~1 in 4 runs;
  fires only after a digit-wise year so 年年有余 survives). Verified 5/5 clean live runs.
  This fixes the PIPELINE too, not just the app.

**Review app — plan option B shaped as D:**
- `audio_core`: zh added to `_own_harness` (imports `mandarin_number_clean` — exactly what
  the `_ZH` voice templates call, own overrides, own guard); jp cleans via the generic
  `build_prompt("jp",…)` path (what the six JP templates run), no-op-gated by the
  pipeline's `needs_number_clean` (feature-detected, `_is_convertible` fallback).
  `_NO_CLEAN_LANGS` now empty; block comment rewritten; `CLEANER_VERSION` →
  `3-cjk-cleaned`. New `cjk_convertible()` — deterministic, dependency-free
  digits/symbols/Latin detector.
- `sessions.regenerate` CJK branch: whole-regen and whole-alt now route through
  `validate_and_clean`; `fallback()` already did and now cleans CJK consistently.
  **Deliberate deviation from the plan's §6B "store cleaned baselines" note:**
  `working_text`/`working_hans` STAY RAW (cleaned-space baselines would wedge the
  text-ahead-of-audio bookkeeping that compares them to `current_text`); instead
  `can_surgical` additionally requires `not cjk_convertible(OLD) and not
  cjk_convertible(NEW)` — OLD too, so an edit that deletes a number can't splice at cut
  times read from raw text against cleaned-voiced audio. Highlight on a convertible field
  whole-regenerates (FE gets `cjk_fallback`); alt refuses with a numbers-specific
  `edit_required` reason (never voiced as the whole field).
- `main.py` startup line: drop the now-empty "passthrough" clause.

### Verified
- Full backend suite **145 passed** (17 new in `tests/test_cjk_voicing_parity.py`:
  regenerate wiring via a no-DB/no-network harness, surgical-bail on OLD/NEW convertible,
  surgical preserved on clean fields, alt-refuse reason, outage fallback keeps overrides +
  flags edit_required, no-clean-langs-empty, fallback parity; reworked zh/jp dispatch tests
  in `test_number_clean_language.py`).
- Live against real DeepSeek (workstation): jp 「3ばんめ…634めーとる」 → full kana
  expansion, accepted by the jp inventory guard; zh Taipei101 → 台北一〇一 + 五百零八 +
  一九九九年 (single year); pure-kana/pure-hanzi no-op paths byte-identical with zero API
  calls. All §9 acceptance criteria met (Tokyo_08-shape regression = the no-op test).

### ⚠ Deployment — NOT done, ordering matters
zh cleaning on the laptop is only safe AFTER the laptop's Scripts checkout has the dedup
fix. Order: (1) dave pushes `dynamic-content` (BACKLOG 0m — the pending push now also
carries the zh dedup + prompt fix); (2) laptop `cd ~/Desktop/Server/Scripts && git pull`;
(3) laptop review-app `git pull`; (4) `sudo -n systemctl restart review-app.service` in an
idle window (uvicorn + tunnel both up). Deploying the app first would voice double years
on zh year-bearing fields.

### Open / low-urgency
- BACKLOG 0q moved to Done (with the design deviation recorded); 0n.1 marked done inside 0n
  (0n.2 stripper registration + 0n.3 `{extra}` slots remain).
- Direct char tools (trim-noise/pauses) on convertible CJK fields keep today's behavior:
  aligner mismatch → honest 409. Same as before the change; not a regression.
- Scripts changes are committed locally in `dynamic-content` only if dave's flow allows —
  see deployment note; review-app changes committed on main.

### Next steps
Dave: push dynamic-content, then the 4-step deploy above; optionally trigger a red-team
pass (per standing preference, not auto-run).
