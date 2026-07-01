# CJK forced-alignment R&D (Path B: surgical splice for Mandarin + Japanese)

Spike + prototype that de-risk **Path B** — true character-level surgical audio splicing for
non-Latin narration (`_ZH`, `_JP`). Today the review-app splice engine (`backend/app/audio_splice.py`)
routes any hanzi/kana edit to whole-regeneration (the K3 guard); its timing pass runs Whisper in
English and aligns Latin tokens, which CJK (no word spaces) can't use. Path B replaces the timing
backend for CJK with a **forced aligner** (we already know the text, so it's alignment, not ASR).

## Why an aligner, and which one
Whisper/faster-whisper word timestamps are cross-attention-derived and unreliable for CJK. A forced
aligner aligns *known* text → audio at character precision. We picked **torchaudio MMS forced
alignment** (`MMS_FA`): char-level for CJK, per-token `.score` confidence, `uroman` romanization,
pip-installable. (Alternatives: MFA — ~<15 ms but heavyweight conda/Kaldi; NeMo NFA — CTC, big dep.)

Two things lower the bar: our splice already snaps cuts to a local energy minimum within **±120 ms**
(invariant S3), so the aligner only needs to land within a syllable; and the *candidate* side already
has ElevenLabs `/with-timestamps` char alignment, so the aligner is needed only for the master side.

## Files
- `gather_clips.py` — pull real `_ZH` SceneDesc masters + their Simplified hanzi from a live review
  session (needs the backend up + a seeded ZH session). Writes `manifest.json` + `clips/*.mp3`.
- `mms_align.py` — alignment **spike**: char-align each clip, report per-char confidence and whether
  clause/sentence boundaries land within ±120 ms of a real silence. → `results.txt` / `results.json`.
- `cjk_splice_proto.py` — surgical-splice **prototype**: derive silence-anchored cut points from the
  alignment, splice at the master's native sample rate with an equal-power seam crossfade, output a
  WAV to listen to. → `out/splice_demo.wav`, `out/splice_report.txt`.

## Results so far (2026-07-01, 3 real HSK3 masters)
- Alignment: mean per-char confidence **0.96–0.98**; **13/15** clause boundaries within ±120 ms of a
  real silence (2 misses at 130/140 ms). See `results.txt`.
- Splice proto (clip 1, removed the middle sentence): both cuts refined **into genuine silence**
  (energy 0.0007 / 0.0012 vs 0.016 threshold), guard PASS, clean 10 ms seam. See `splice_report.txt`.

## Run (isolated env — keep torch OUT of the backend env)
```bash
python -m venv venv
venv/Scripts/python -m pip install --index-url https://download.pytorch.org/whl/cpu torch torchaudio
venv/Scripts/python -m pip install soundfile numpy uroman
# with the backend up + a ZH session seeded:
../../backend/... # (run gather_clips.py with the main interpreter that has `requests`)
PYTHONIOENCODING=utf-8 venv/Scripts/python mms_align.py
PYTHONIOENCODING=utf-8 venv/Scripts/python cjk_splice_proto.py
```

## Path B roadmap (remaining)
1. **Anchor eligibility on `.score`** — never choose a low-confidence char as a cut anchor (the CJK
   analogue of the numberish/non-Latin anchor bar). Some chars (proper nouns) align at score 0.
2. **Japanese + edited-hanzi validation** — repeat the spike on `_JP` (kana) and on regenerated
   (edited) text, not just the original.
3. **The REPLACE case** — generate the changed span via ElevenLabs `/with-timestamps` and splice it
   between the two silence-anchored cuts (proto currently proves delete+rejoin).
4. **Char-level diff/anchor planner** — the `audio_splice.py` CJK counterpart: hanzi diff →
   changed char span → cut points → candidate → assemble; keep it an ISOLATED path (English untouched).
5. **Backend integration** — decision pending: run the aligner as an isolated subprocess/service the
   backend shells out to (keeps the backend torch-free, mirrors the ffmpeg shell-out) vs. adding torch
   to the backend env (risks the numpy/numba conflict the pipeline avoids).
6. **Recalibrate + red-team** — seam-depth gate, confidence floor, level-match, on real ZH+JP; the
   feared failure is a *silent* mis-cut.
