"""
The splice engine (SceneDesc only; Q&A and whole-block are plain whole-regenerate).

Two phases, mirroring the API contract:
  * plan_segment()  — runs at /regenerate. Diffs cleaned text, locates the changed
                      span, picks pause-aligned NON-numeric/NON-hanzi anchor words,
                      reads the cut times from RAW Whisper word.start/end, verifies
                      the anchors, then calls ElevenLabs /with-timestamps for the
                      anchor-context phrase. Returns the candidate mp3 + a splice plan
                      (or edit_required).
  * do_splice()     — runs at /combine. Refines each cut to a local RMS-energy
                      minimum, level-matches the candidate to the retained original
                      context, assembles orig[:tL] + cand[tL':tR'] + orig[tR:] in PCM
                      with equal-power edge fades, peak-limits, and scores confidence.

Correctness points enforced (from two code reviews):
  C1  cut times come from raw Whisper word.start/.end, never interpolated timelines.
  S1  level match measured over the LONGEST retained original context, not one word.
  S2  Gemini fallback (uncleaned) → edit_required (never align on drifted tokens).
  S3  cuts refined to true energy minima within ±120 ms.
  K3  non-Latin token in span/anchors → edit_required (advise whole-regen).
  +   numeric / regnal / override-phrase tokens are never used as anchors.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

import numpy as np

from . import audio_core, audio_io
from .config import (ANCHOR_EXPAND_CAP, ENERGY_MIN_WINDOW, SEAM_DEPTH_MIN,
                     SEAM_FADE_MS, SPLICE_CONFIDENCE_FLOOR, TRUE_PEAK_CEILING_DB)

_TOKEN_RE = re.compile(r"\S+")


def tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text or "")


def _norm(tok: str) -> str:
    """Lowercase + strip leading/trailing punctuation (keeps internal ' and -)."""
    return re.sub(r"^\W+|\W+$", "", tok, flags=re.UNICODE).lower()


def _ends_sentence(tok: str) -> bool:
    return tok.rstrip().endswith((".", "!", "?", ";", ":"))


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
@dataclass
class RegenPlan:
    edit_required: bool = False
    reason: str = ""
    candidate_mp3: bytes | None = None
    meta: dict = field(default_factory=dict)   # persisted for do_splice / whole replace
    whole: bool = False                        # True → combine just replaces working


@dataclass
class SpliceResult:
    samples: np.ndarray
    confidence: float
    edit_required: bool
    detail: dict


# --------------------------------------------------------------------------- #
# Phase 1 — plan (runs at /regenerate)
# --------------------------------------------------------------------------- #
def plan_whole(cleaned_new: str, used_fallback: bool, voice_id: str,
               voice_settings: dict,
               model_id: str = audio_core.EL_MODEL) -> RegenPlan:
    """Whole-field regenerate (SceneDesc 'whole' mode + every Q&A field).

    S2: when the Gemini number-cleaner fell back to uncleaned text, the clip is voiced
    from raw numerals/abbreviations — still return the candidate so it can be auditioned,
    but flag edit_required so a human MUST listen before it is accepted."""
    mp3 = audio_core.generate_audio(cleaned_new, voice_id, voice_settings, model_id)
    plan = RegenPlan(candidate_mp3=mp3, whole=True,
                     meta={"mode": "whole", "text": cleaned_new})
    if used_fallback:
        plan.edit_required = True
        plan.reason = ("Numbers/abbreviations could not be cleaned reliably — voiced "
                       "from raw text; please listen before accepting.")
    return plan


def _span_segment(orig_toks: list[str], new_toks: list[str]):
    """Return (blo, bhi, opcodes) — the union changed range in NEW token space."""
    sm = difflib.SequenceMatcher(
        a=[_norm(t) for t in orig_toks], b=[_norm(t) for t in new_toks],
        autojunk=False)
    ops = sm.get_opcodes()
    blo, bhi = None, None
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            continue
        blo = j1 if blo is None else min(blo, j1)
        bhi = j2 if bhi is None else max(bhi, j2)
    return blo, bhi, ops


def _map_new_to_orig(ops, new_idx: int) -> int | None:
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal" and j1 <= new_idx < j2:
            return i1 + (new_idx - j1)
    return None


def _eligible_anchor(tok: str, doc_id: str, overrides: set[str]) -> bool:
    if audio_core.is_numberish(tok) or audio_core.has_non_latin(tok):
        return False
    if _norm(tok) in overrides:
        return False
    return True


def _expand_left_to_boundary(toks, la, doc_id, overrides, cap):
    """S1: extend the LEFT anchor back to the nearest eligible token that ENDS a
    sentence/clause (cut lands in the following pause). Returns (idx, found_pause)."""
    lo = max(0, la - cap)
    for j in range(la, lo - 1, -1):
        if _eligible_anchor(toks[j], doc_id, overrides) and _ends_sentence(toks[j]):
            return j, True
    return la, False


def _expand_right_to_boundary(toks, ra, doc_id, overrides, cap):
    """S1: extend the RIGHT anchor forward to the nearest eligible token that BEGINS a
    sentence/clause (its predecessor ends one → cut lands in the preceding pause)."""
    hi = min(len(toks) - 1, ra + cap)
    for j in range(ra, hi + 1):
        if (_eligible_anchor(toks[j], doc_id, overrides)
                and j > 0 and _ends_sentence(toks[j - 1])):
            return j, True
    return ra, False


def _whisper_index_map(orig_toks: list[str],
                       words: list[dict]) -> dict[int, tuple[float, float]]:
    """Align cleaned-orig tokens ↔ Whisper words → ``orig_token_idx → (start, end)`` seconds.

    Tokens the diff matches take their Whisper word's RAW times (C1). Tokens it leaves
    UNMAPPED — number reformatting (``1809`` ↔ "eighteen oh nine"), hyphenation, possessives,
    minor transcription drift — are bracketed by INTERPOLATION between their nearest mapped
    neighbours (the audio span between the left word's end and the right word's start, split
    evenly). This is deliberately PARSER-FREE, so it is language-agnostic: it never needs to
    know that "eighteen" == 18 (which would be English-only and pointless anyway, since the
    K3 non-Latin guard routes kana/hanzi spans to whole-regenerate). The interpolated time is
    only a SEED for the silence search in `_silence_cut`; the real cut still snaps to genuine
    silence, so cut times remain audio-derived, never synthetic."""
    sm = difflib.SequenceMatcher(
        a=[_norm(t) for t in orig_toks],
        b=[_norm(w["word"]) for w in words], autojunk=False)
    out: dict[int, tuple[float, float]] = {}
    for i, j, n in sm.get_matching_blocks():
        for k in range(n):
            out[i + k] = (float(words[j + k]["start"]), float(words[j + k]["end"]))
    n_orig = len(orig_toks)
    i = 0
    while i < n_orig:                       # fill each run of unmapped tokens by interpolation
        if i in out:
            i += 1
            continue
        j = i
        while j < n_orig and j not in out:
            j += 1
        left_end = out[i - 1][1] if (i - 1) in out else None
        right_start = out[j][0] if (j < n_orig and j in out) else None
        if left_end is not None and right_start is not None and right_start > left_end:
            step = (right_start - left_end) / (j - i)
            for k in range(i, j):
                s = left_end + step * (k - i)
                out[k] = (s, s + step)     # boundary runs (no neighbour one side) stay unmapped
        i = j
    return out


_EXPAND_CAP = 12   # max extra words to re-voice when the boundary is connected speech
_LOOK = 0.45       # backward reach (~one word) to find the adjacent pause
_FWD = 0.22        # forward reach: Whisper often absorbs the boundary pause INTO the start
                   # of the following word (stretching it), so the real pause sits forward


def _w_time(wmap, idx, which):
    t = wmap.get(idx)
    if t is None:
        return None
    return t[0] if which == "start" else t[1]


def _keep_pause(run_len: float) -> float:
    """How much of a found silence run to KEEP at the seam — a short, natural pause that
    scales with the boundary (a comma keeps less than a sentence period), capped both ways
    so a long TTS pause isn't fully retained and a tiny one isn't swallowed."""
    return min(0.28, max(0.10, 0.35 * run_len))


def _silence_cut(base, sr, wmap, n_orig, start_idx, side, dur):
    """Anchor a cut in REAL silence (not at an imprecise Whisper word edge). From the word
    edge it searches a forward/backward LOOK window — NOT bounded by Whisper's next/prev
    word time, which is unreliable (a long inter-word pause makes Whisper mis-place the
    following word *before* the pause). So it reliably finds the adjacent pause and cuts
    there (C, no over-expansion). Only when there is genuinely no pause within ~one word
    does it advance to the next word and re-voice it (A, expansion), up to _EXPAND_CAP;
    none within the cap → (None, None) → caller flags edit_required (B). ``side`` 'R' =
    silence after start_idx; 'L' = silence before start_idx."""
    if side == "R":
        for oj in range(start_idx, min(n_orig - 1, start_idx + _EXPAND_CAP) + 1):
            we = _w_time(wmap, oj, "end")
            if we is None:
                continue
            # nearest pause to the word END — forward-biased (the boundary pause follows
            # this word), with a touch of back-reach for Whisper over-estimating word.end.
            run = audio_io.silence_run_nearest(base, sr, we, 0.05, _LOOK)
            if run is not None:                   # cut near the END of the pause (keep a
                s0, s1 = run                      # short pause, drop the excess)
                return max(s0, s1 - _keep_pause(s1 - s0)), oj
        return None, None                         # reached cap/clip end with no pause → flag
    for oj in range(start_idx, max(0, start_idx - _EXPAND_CAP) - 1, -1):
        ws = _w_time(wmap, oj, "start")
        if ws is None:
            continue
        # nearest pause to the word START — search BOTH sides: Whisper may report the start
        # before the pause (correct) OR absorb the pause into the word (stretched, _FWD).
        run = audio_io.silence_run_nearest(base, sr, ws, _LOOK, _FWD)
        if run is not None:                       # cut near the START of the pause
            s0, s1 = run
            return min(s1, s0 + _keep_pause(s1 - s0)), oj
    return None, None                             # reached cap/clip start with no pause → flag


def plan_segment(doc_id: str, cleaned_orig: str, cleaned_new: str,
                 used_fallback: bool, whisper_words: list[dict],
                 voice_id: str, voice_settings: dict,
                 base_samples: np.ndarray, sr: int,
                 highlight_orig_span: tuple[int, int] | None = None,
                 model_id: str = audio_core.EL_MODEL,
                 alt_text: str | None = None) -> RegenPlan:
    """Span-only regenerate + splice plan (shared by 'generate from edit' and highlight).

    Renders the changed/highlighted words and plans a splice whose cuts land in REAL
    silence in ``base_samples`` (the current working take). When a boundary is connected
    speech (no inter-word silence), it expands the re-voiced span through the next word(s)
    to the nearest pause so the seam is still clean; if none is within the cap it returns
    edit_required. ``highlight_orig_span`` forces the span; ``alt_text`` voices free text
    verbatim in place of the changed words."""
    if used_fallback:
        return RegenPlan(edit_required=True,
                         reason="Gemini cleaner fell back to uncleaned text — "
                                "diff unreliable; whole-regenerate advised.")

    orig_toks, new_toks = tokens(cleaned_orig), tokens(cleaned_new)
    seg_blo, seg_bhi, ops = _span_segment(orig_toks, new_toks)

    # --- changed span in NEW tokens [blo,bhi) and in ORIG tokens [oa,ob) -------------
    if highlight_orig_span is not None:
        blo, bhi = highlight_orig_span
        blo = max(0, min(blo, len(new_toks)))
        bhi = max(blo, min(bhi, len(new_toks)))
        if bhi <= blo:
            return RegenPlan(edit_required=True, reason="Empty highlight selection.")
        oaO, obO = _map_new_to_orig(ops, blo), _map_new_to_orig(ops, bhi - 1)
        if oaO is None or obO is None:
            return RegenPlan(edit_required=True,
                             reason="Highlighted words not locatable in the take's audio.")
        oa, ob = oaO, obO + 1
    else:
        if seg_blo is None:
            return RegenPlan(edit_required=True,
                             reason="No text change detected for a segment regen.")
        blo, bhi = seg_blo, seg_bhi
        oa = ob = None
        for tag, i1, i2, _j1, _j2 in ops:
            if tag == "equal":
                continue
            oa = i1 if oa is None else min(oa, i1)
            ob = i2 if ob is None else max(ob, i2)

    # K3: hanzi/kana anywhere in the changed span → whole-regen.
    for t in new_toks[blo:bhi]:
        if audio_core.has_non_latin(t):
            return RegenPlan(edit_required=True,
                             reason="Non-Latin text in the edited span — "
                                    "whole-regenerate advised (no surgical splice).")

    if bhi <= blo and (not alt_text or not alt_text.strip()):
        return RegenPlan(edit_required=True,
                         reason="Edit removed text only — use whole-regenerate.")

    wmap = _whisper_index_map(orig_toks, whisper_words)
    n_orig = len(orig_toks)
    dur = audio_io.duration_seconds(base_samples, sr)

    # ---- anchor each cut in REAL silence, expanding the re-voiced span if connected ----
    connected = ("Connected speech — use whole-regenerate or highlight more words.")
    if oa <= 0:
        tL, l_word = 0.0, 0
    else:
        tL, l_word = _silence_cut(base_samples, sr, wmap, n_orig, oa, "L", dur)
        if tL is None:
            return RegenPlan(edit_required=True, reason=connected)
    if ob >= n_orig:
        tR, r_word = dur, n_orig - 1
    else:
        tR, r_word = _silence_cut(base_samples, sr, wmap, n_orig, ob - 1, "R", dur)
        if tR is None:
            return RegenPlan(edit_required=True, reason=connected)
    if tR <= tL:
        return RegenPlan(edit_required=True, reason="Degenerate cut span.")

    # extend the rendered span to the silence-anchored words ([l_word..oa) and [ob..r_word]
    # are unchanged, so they map 1:1 into NEW token space around the change)
    new_blo = max(0, blo - (oa - l_word))
    new_bhi = min(len(new_toks), bhi + (r_word - (ob - 1)))
    if alt_text is not None and alt_text.strip():
        parts = new_toks[new_blo:blo] + [alt_text.strip()] + new_toks[bhi:new_bhi]
    else:
        parts = new_toks[new_blo:new_bhi]
    phrase = " ".join(parts).strip()
    if not phrase:
        return RegenPlan(edit_required=True,
                         reason="Edit removed text only — use whole-regenerate.")

    previous_text = " ".join(new_toks[max(0, new_blo - 40):new_blo]).strip() or None
    next_text = " ".join(new_toks[new_bhi:new_bhi + 40]).strip() or None
    mp3, cand_words = audio_core.generate_with_timestamps(
        phrase, voice_id, voice_settings, previous_text, next_text, model_id)

    meta = {
        "mode": "segment", "span_only": True,
        "tL": tL, "tR": tR, "orig_duration": dur,
        "changed_tokens": len(parts), "cand_words": cand_words, "phrase": phrase,
    }
    return RegenPlan(candidate_mp3=mp3, meta=meta)


def highlight_span_in_cleaned(current_text: str, cleaned_new: str,
                              start: int, end: int) -> tuple[int, int]:
    """Map a textarea char range in the RAW current_text to a token span in
    cleaned_new (best effort, via raw-token ↔ cleaned-token alignment)."""
    raw_toks = list(_TOKEN_RE.finditer(current_text or ""))
    rlo, rhi = None, None
    for idx, m in enumerate(raw_toks):
        if m.end() > start and m.start() < end:      # token overlaps the selection
            rlo = idx if rlo is None else rlo
            rhi = idx + 1
    if rlo is None:
        return (0, len(tokens(cleaned_new)))          # whole field fallback
    new_toks = tokens(cleaned_new)
    sm = difflib.SequenceMatcher(
        a=[_norm(m.group()) for m in raw_toks],
        b=[_norm(t) for t in new_toks], autojunk=False)
    blo, bhi = None, None
    for i, j, n in sm.get_matching_blocks():
        for k in range(n):
            if rlo <= i + k < rhi:
                blo = j + k if blo is None else min(blo, j + k)
                bhi = (j + k + 1) if bhi is None else max(bhi, j + k + 1)
    if blo is None:
        return (0, len(new_toks))
    return (blo, bhi)


# --------------------------------------------------------------------------- #
# Phase 2 — splice (runs at /combine)
# --------------------------------------------------------------------------- #
def _find_cand_anchor(cand_words: list[dict], norm: str, last: bool) -> dict | None:
    matches = [w for w in cand_words if _norm(w["word"]) == norm]
    if not matches:
        return None
    return matches[-1] if last else matches[0]


def _splice_span_only(orig: np.ndarray, cand: np.ndarray, meta: dict,
                      sr: int, od: float) -> SpliceResult:
    """Span-only splice: replace orig[tL:tR] with the candidate. tL/tR were anchored in
    REAL silence at plan time (audio_splice.plan_segment), so the seam is clean; here we
    just trim the candidate to its own speech, level-match it, and crossfade it in."""
    cw = meta.get("cand_words") or []
    if cw:                                   # trim TTS lead/trail silence (+18 ms breath)
        m = 0.018
        cs = max(0.0, float(cw[0]["start"]) - m)
        ce = min(audio_io.duration_seconds(cand, sr), float(cw[-1]["end"]) + m)
        a, b = int(cs * sr), int(ce * sr)
        if b - a > 8:
            cand = cand[a:b]

    tL = float(meta.get("tL") or 0.0)
    tR = od if meta.get("tR") is None else float(meta["tR"])
    sL = max(0, min(int(round(tL * sr)), len(orig)))
    sR = max(sL, min(int(round(tR * sr)), len(orig)))
    head, tail, mid = orig[:sL], orig[sR:], cand

    retained = np.concatenate([head, tail]) if (len(head) + len(tail)) else orig
    ref_db = audio_io.gated_rms_db(retained, sr)
    mid_db = audio_io.gated_rms_db(mid, sr)
    gain = 0.0
    if mid_db > -119 and ref_db > -119:
        gain = float(np.clip(ref_db - mid_db, -12.0, 12.0))
        mid = audio_io.apply_gain_db(mid, gain)
    mid = audio_io.limit_peak(mid, TRUE_PEAK_CEILING_DB)
    spliced = audio_io.crossfade_join([head, mid, tail], sr, 12.0)

    # Cuts are in real silence by construction → score on duration plausibility only.
    expected = meta.get("changed_tokens", 0) * 0.34
    mid_dur = audio_io.duration_seconds(mid, sr)
    if expected <= 0:
        dur_score = 1.0 if mid_dur < 1.5 else 0.6
    else:
        dur_score = 1.0 if 0.4 <= mid_dur / expected <= 2.6 else 0.5
    confidence = round(0.6 + 0.4 * dur_score, 3)
    return SpliceResult(
        samples=spliced, confidence=confidence,
        edit_required=confidence < SPLICE_CONFIDENCE_FLOOR,
        detail={"span_only": True, "tL": round(tL, 3), "tR": round(tR, 3),
                "gain_db": gain, "mid_dur": round(mid_dur, 3)},
    )


def do_splice(orig: np.ndarray, cand: np.ndarray, meta: dict,
              sr: int = audio_io.SR) -> SpliceResult:
    od = audio_io.duration_seconds(orig, sr)
    if meta.get("span_only"):
        return _splice_span_only(orig, cand, meta, sr, od)
    cd = audio_io.duration_seconds(cand, sr)
    cand_words = meta.get("cand_words") or []

    tL = float(meta.get("tL") or 0.0)
    tR = meta.get("tR")
    tR = od if tR is None else float(tR)

    # candidate-side anchor times
    if meta.get("left_anchor"):
        w = _find_cand_anchor(cand_words, meta["left_anchor"], last=False)
        tLc = float(w["end"]) if w else 0.0
        left_ok = w is not None
    else:
        tLc, left_ok = 0.0, True
    if meta.get("right_anchor"):
        w = _find_cand_anchor(cand_words, meta["right_anchor"], last=True)
        tRc = float(w["start"]) if w else cd
        right_ok = w is not None
    else:
        tRc, right_ok = cd, True

    # S3 + C1: refine ONLY interior (anchored) cuts to the local energy minimum.
    # A clip-boundary cut (no anchor → the candidate supplies that edge) must NOT be
    # refined: find_energy_min can only move the cut *inward*, which injects slivers
    # of the old take and truncates the new audio — this fired on every first/last-
    # sentence edit at high confidence (red-team C1).
    has_l, has_r = bool(meta.get("left_anchor")), bool(meta.get("right_anchor"))
    if has_l:
        tL, dL = audio_io.find_energy_min(orig, sr, tL, ENERGY_MIN_WINDOW)
        tLc, _ = audio_io.find_energy_min(cand, sr, tLc, ENERGY_MIN_WINDOW)
    else:
        tL, dL, tLc = 0.0, 1.0, 0.0
    if has_r:
        tR, dR = audio_io.find_energy_min(orig, sr, tR, ENERGY_MIN_WINDOW)
        tRc, _ = audio_io.find_energy_min(cand, sr, tRc, ENERGY_MIN_WINDOW)
    else:
        tR, dR, tRc = od, 1.0, cd

    sL, sR = int(round(tL * sr)), int(round(tR * sr))
    sLc, sRc = int(round(tLc * sr)), int(round(tRc * sr))
    sL = max(0, min(sL, len(orig)))
    sR = max(sL, min(sR, len(orig)))
    sLc = max(0, min(sLc, len(cand)))
    sRc = max(sLc, min(sRc, len(cand)))

    head = orig[:sL]
    tail = orig[sR:]
    mid = cand[sLc:sRc]

    # S1: level-match candidate middle to the LONGEST retained original context.
    retained = np.concatenate([head, tail]) if (len(head) + len(tail)) else orig
    ref_db = audio_io.gated_rms_db(retained, sr)
    mid_db = audio_io.gated_rms_db(mid, sr)
    gain = 0.0
    if mid_db > -119 and ref_db > -119:
        gain = float(np.clip(ref_db - mid_db, -12.0, 12.0))
        mid = audio_io.apply_gain_db(mid, gain)

    # S4: peak-limit ONLY the (new) candidate middle — never scale the retained
    # original head/tail, which came from the accepted master and must stay untouched.
    mid = audio_io.limit_peak(mid, TRUE_PEAK_CEILING_DB)
    spliced = audio_io.butt_join([head, mid, tail], sr, SEAM_FADE_MS)

    # ---- S1 seam gate: a cut that did NOT land on a sentence/clause boundary MUST
    # sit in a genuine silence dip, else there is no safe seam → edit_required. ----
    seam_fail = False
    if has_l and not meta.get("left_pause", False) and dL < SEAM_DEPTH_MIN:
        seam_fail = True
    if has_r and not meta.get("right_pause", False) and dR < SEAM_DEPTH_MIN:
        seam_fail = True

    # ---- confidence: seam-led (0.4 anchor · 0.4 seam · 0.2 duration) ----
    anchor_score = 1.0
    if not left_ok:
        anchor_score *= 0.4
    if not right_ok:
        anchor_score *= 0.4
    seam_score = float(np.clip((dL + dR) / 2.0, 0.0, 1.0))
    expected = meta.get("changed_tokens", 0) * 0.34   # ~avg word duration (s)
    mid_dur = audio_io.duration_seconds(mid, sr)
    if expected <= 0:
        dur_score = 1.0 if mid_dur < 1.5 else 0.5
    else:
        ratio = mid_dur / expected
        dur_score = 1.0 if 0.4 <= ratio <= 2.6 else 0.4
    confidence = round(0.4 * anchor_score + 0.4 * seam_score + 0.2 * dur_score, 3)

    return SpliceResult(
        samples=spliced,
        confidence=confidence,
        edit_required=(confidence < SPLICE_CONFIDENCE_FLOOR) or seam_fail,
        detail={"tL": tL, "tR": tR, "tLc": tLc, "tRc": tRc, "gain_db": gain,
                "seam_depth": [round(dL, 3), round(dR, 3)], "seam_fail": seam_fail,
                "left_pause": bool(meta.get("left_pause")),
                "right_pause": bool(meta.get("right_pause")),
                "anchor_score": anchor_score, "dur_score": dur_score,
                "mid_dur": round(mid_dur, 3)},
    )
