# Candidate end-cutoff & previous-text leak — analysis + fixes (2026-07-02)

> **STATUS: fixes 1–3 below are IMPLEMENTED (same day).** Fix 1 shipped in a refined
> form: while implementing, the real "shogunate" clip showed the whole unstressed
> final syllable ("-ate", ~0.7 s) sits 26–40 dB below peak — invisible to the −26 dB
> voiced bar, not just a short burst — so `trim_trailing_breath` now extends the end
> over any content above a LOW bar (peak−40 dB) that follows within 150 ms chained
> (a quiet word tail is contiguous with its vowel; a breath comes after a real pause,
> so it is still trimmed). Validated: synthetic burst/breath/quiet-tail set + 3 real
> EL takes Whisper-verified ("gate"/"shogunate" intact post-trim, tails still cut).
> Fix 2 = `_CAND_LEAD_MAX_S`/`_CAND_LEAD_KEEP_S` in `sessions.regenerate` (cand_words
> shifted; `trim_candidate` re-applies `cand_front_trim_s` from the pristine copy).
> Fix 3 = the one-shot no-context retry in `audio_core.generate_with_timestamps`
> (`_LEAK_RETRY_LEAD_S`). Fixes 4–5 remain open (4's risk is reduced by the EL end
> times we observed running long, not short).
>
> **Follow-up (same day):** report 2's "extra words before the phrase" turned out to be
> mostly plan-time LEFT-EXPANSION, not the leak: on the real Tokyo_08 take the pause
> between "residence | and" starts 226 ms AFTER Whisper's reported start of "and"
> (Whisper stretched "and" over 630 ms of the pause), and `_silence_cut`'s forward
> reach `_FWD=0.22` missed it by 6 ms → the span expanded 4 words back to "as".
> `_FWD` is now 0.45 (symmetric with `_LOOK`; `silence_run_nearest` still prefers the
> nearest run, so genuine backward pauses keep winning). Verified on the live session
> audio: the highlight now plans exactly "and the headquarters of the shogunate."
> (tL inside the real pause), and the Seimon sentence plan is unchanged.

Two field reports from English review (Tokyo_08_ImperialPalace_EN), both on
SceneDesc highlight/alt regenerates:

1. **Alt-text regen cuts off the final word.** "During these times, the public usually
   pass over the Seimon Tetsubashi to reach the main gate." regenerated via *…with alt
   text* (only "Seimon" respelled). Pronunciation fixed, but the last word is clipped —
   and **every re-roll of the candidate has the same clip**.
2. **Candidate contains the PREVIOUS sentence's audio + final word cut.** Regenerating
   "and the headquarters of the shogunate." produced a candidate that also voices the
   preceding "as the shogun's residence", and "shogunate" stops halfway.

## Root causes (code-level)

### A. `audio_io.trim_trailing_breath` chops final plosives — deterministic
After every regenerate, `sessions.regenerate` runs `trim_trailing_breath` on the
candidate (to drop the TTS breath / next-sound bleed that `next_text` prosody context
encourages). Its cut point is *end of the last **sustained** voiced run
(≥ 80 ms above peak−26 dB) + 90 ms release*.

Both reported words end in **/t/** ("gate.", "shogunate."). A final stop is:
vowel → **closure silence (~50–120 ms)** → **burst (~30–50 ms)**. The burst is loud
enough to count as voiced but far too short to be "sustained", so the last sustained
run ends at the vowel; the cut lands at *vowel end + 90 ms* — usually inside the
closure, **before the burst**. The burst is deleted → "gate" → "gay-". This is pure
DSP, so re-rolling the candidate reproduces it every time — exactly what was reported.

The same word-end risk exists at combine time: `_splice_span_only` trims the candidate
at `cand_words[-1].end + 18 ms`, and ElevenLabs character end-times for a final stop
often sit at the burst *start*.

### B. ElevenLabs `previous_text` leak — the model sometimes voices part of the context
`plan_segment`/`plan_cjk*` pass `previous_text`/`next_text` (≤40 tokens each) for
prosody. On `eleven_multilingual_v2` the model occasionally **speaks a tail of
`previous_text`** before the requested phrase (report 2: "as the shogun's residence"
precedes the requested span in the sentence). Consequences today:

- The audition candidate is only ever **trailing**-trimmed, so the leak is fully
  audible when the reviewer plays the candidate.
- The EL char alignment covers only the requested `text`, so `cand_words[0].start`
  sits *after* the leak. `_splice_span_only` would cut the leak off at combine — but
  the reviewer (rightly) never gets that far, and `trim_trailing_breath`'s
  peak-relative threshold is skewed by the extra speech, compounding the end clip.

## Proposed fixes (in order of value : risk)

1. **Make `trim_trailing_breath` plosive-safe** (fixes report 1, benefits all langs):
   after finding the last sustained run, extend the cut over any *short voiced blip*
   that starts within ~250 ms of it (the burst), then apply the release. Equivalently:
   cut at the last voiced frame within (sustained_end + 250 ms), + release. Keep the
   existing "no-op when tail < 40 ms" guard. Cheap, deterministic, testable on the
   two reported clips.
2. **Front-trim the candidate at regenerate time using `cand_words[0].start`**
   (fixes the audible leak of report 2): when `cand_words[0].start > ~0.25 s`, trim
   the lead and **shift every `cand_words` time (and store `cand_front_trim_s` in the
   splice meta)** so the span-splice stays aligned. The trailing-only trim invariant
   ("cand_words indexed from the start stays valid") must be preserved by the shift.
3. **Leak detector + context retry**: if `cand_words[0].start` exceeds ~0.4 s (or
   audio duration ≫ aligned char span), re-request **without `previous_text`** —
   a clause-length phrase has acceptable standalone prosody (the JP/v3 path already
   omits context entirely and sounds fine).
4. **Combine-side end margin**: raise `_splice_span_only`'s +18 ms end margin to
   ~120 ms *or* snap the end cut to `first_silence_after(cand, cw[-1].end …)` so a
   late burst is never sliced. (The margin is inside the pause anyway; the splice's
   `set_trailing_silence`/crossfade handles the rest.)
5. *(Heavier, later)* **Verify the candidate with Whisper** before offering it:
   transcribe the candidate (the model is already resident), align to the requested
   phrase; flag/auto-re-roll when the head has extra words or the final word is
   missing. This converts both failure modes into a visible, automatic retry.

## Do the other languages share this?

- **Mandarin (`_ZH`)** uses the same `generate_with_timestamps` **with**
  `previous_text`/`next_text` on v2 → the **leak (B) can happen**. `trim_trailing_breath`
  also runs on ZH candidates; Mandarin syllables end in vowels/-n/-ng (no released
  final stops), so the plosive clip (A) is unlikely — but a soft final syllable below
  the "sustained" bar could still be shaved. Fix 1 & 3 apply as-is.
- **Japanese (`_JP`)** uses `eleven_v3` where context is **omitted** ("unsupported_model")
  → no leak path. Kana end in vowels/ん → clip risk low, but fix 1 is still safe.
  Note `_chars_to_words` makes ONE "word" of a spaceless CJK phrase, so `cw[-1].end`
  = last char end — the combine-side margin (fix 4) protects CJK too.

Nothing is implemented yet — this is the agreed analysis for dave to pick from.
