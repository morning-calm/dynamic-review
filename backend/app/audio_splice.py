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
               voice_settings: dict) -> RegenPlan:
    """Whole-field regenerate (SceneDesc 'whole' mode + every Q&A field).

    S2: when the Gemini number-cleaner fell back to uncleaned text, the clip is voiced
    from raw numerals/abbreviations — still return the candidate so it can be auditioned,
    but flag edit_required so a human MUST listen before it is accepted."""
    mp3 = audio_core.generate_audio(cleaned_new, voice_id, voice_settings)
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


def _whisper_index_map(orig_toks: list[str], words: list[dict]) -> dict[int, int]:
    """Align cleaned-orig tokens ↔ Whisper words; map orig_token_idx → word_idx for
    matching blocks only (so we read RAW word times for verified anchors)."""
    sm = difflib.SequenceMatcher(
        a=[_norm(t) for t in orig_toks],
        b=[_norm(w["word"]) for w in words], autojunk=False)
    out: dict[int, int] = {}
    for i, j, n in sm.get_matching_blocks():
        for k in range(n):
            out[i + k] = j + k
    return out


def plan_segment(doc_id: str, cleaned_orig: str, cleaned_new: str,
                 used_fallback: bool, whisper_words: list[dict],
                 voice_id: str, voice_settings: dict,
                 highlight_orig_span: tuple[int, int] | None = None) -> RegenPlan:
    """Anchor-context regenerate + splice plan. ``highlight_orig_span`` (token range
    in cleaned_new) forces the changed span for highlight mode."""
    if used_fallback:
        return RegenPlan(edit_required=True,
                         reason="Gemini cleaner fell back to uncleaned text — "
                                "diff unreliable; whole-regenerate advised.")

    orig_toks, new_toks = tokens(cleaned_orig), tokens(cleaned_new)

    if highlight_orig_span is not None:
        blo, bhi = highlight_orig_span
        _, _, ops = _span_segment(orig_toks, new_toks)
    else:
        blo, bhi, ops = _span_segment(orig_toks, new_toks)
        if blo is None:
            return RegenPlan(edit_required=True,
                             reason="No text change detected for a segment regen.")

    blo = max(0, min(blo, len(new_toks)))
    bhi = max(blo, min(bhi, len(new_toks)))

    # K3: hanzi/kana anywhere in the changed span → whole-regen.
    for t in new_toks[blo:bhi]:
        if audio_core.has_non_latin(t):
            return RegenPlan(edit_required=True,
                             reason="Non-Latin text in the edited span — "
                                    "whole-regenerate advised (no surgical splice).")

    overrides = set(audio_core.override_phrases(doc_id))

    # Left anchor: nearest eligible token before the span (else start-of-clip)…
    la = blo - 1
    while la >= 0 and not _eligible_anchor(new_toks[la], doc_id, overrides):
        la -= 1
    # …then expand it back to a sentence/clause boundary so the cut sits in a pause.
    left_pause = False
    if la >= 0:
        la, left_pause = _expand_left_to_boundary(
            new_toks, la, doc_id, overrides, ANCHOR_EXPAND_CAP)

    # Right anchor: nearest eligible token at/after the span end (else end-of-clip)…
    ra = bhi
    while ra < len(new_toks) and not _eligible_anchor(new_toks[ra], doc_id, overrides):
        ra += 1
    # …then expand it forward to a sentence/clause start.
    right_pause = False
    if ra < len(new_toks):
        ra, right_pause = _expand_right_to_boundary(
            new_toks, ra, doc_id, overrides, ANCHOR_EXPAND_CAP)

    wmap = _whisper_index_map(orig_toks, whisper_words)
    dur = whisper_words[-1]["end"] if whisper_words else 0.0

    # ---- left cut time from RAW Whisper word.end (verify anchor) ----
    if la < 0:
        tL, left_anchor = 0.0, None
    else:
        oi = _map_new_to_orig(ops, la)
        if oi is None or oi not in wmap:
            return RegenPlan(edit_required=True,
                             reason="Left anchor not locatable in the take's audio.")
        w = whisper_words[wmap[oi]]
        if _norm(w["word"]) != _norm(new_toks[la]):
            return RegenPlan(edit_required=True,
                             reason="Left anchor mismatch vs spoken audio.")
        tL, left_anchor = float(w["end"]), _norm(new_toks[la])

    # ---- right cut time from RAW Whisper word.start (verify anchor) ----
    if ra >= len(new_toks):
        tR, right_anchor = None, None        # → end of clip (resolved at splice)
    else:
        oi = _map_new_to_orig(ops, ra)
        if oi is None or oi not in wmap:
            return RegenPlan(edit_required=True,
                             reason="Right anchor not locatable in the take's audio.")
        w = whisper_words[wmap[oi]]
        if _norm(w["word"]) != _norm(new_toks[ra]):
            return RegenPlan(edit_required=True,
                             reason="Right anchor mismatch vs spoken audio.")
        tR, right_anchor = float(w["start"]), _norm(new_toks[ra])

    if tR is not None and tR <= tL:
        return RegenPlan(edit_required=True,
                         reason="Degenerate cut (right anchor not after left).")

    # ---- phrase = [left_anchor … changed … right_anchor] + prosodic context ----
    plo = la if la >= 0 else 0
    phi = ra if ra < len(new_toks) else len(new_toks) - 1
    phrase = " ".join(new_toks[plo:phi + 1]).strip()
    previous_text = " ".join(new_toks[max(0, plo - 40):plo]).strip() or None
    next_text = " ".join(new_toks[phi + 1:phi + 41]).strip() or None
    if not phrase:
        return RegenPlan(edit_required=True, reason="Empty regeneration phrase.")

    mp3, cand_words = audio_core.generate_with_timestamps(
        phrase, voice_id, voice_settings, previous_text, next_text)

    meta = {
        "mode": "segment",
        "tL": tL, "tR": tR, "orig_duration": dur,
        "left_anchor": left_anchor, "right_anchor": right_anchor,
        "left_pause": left_pause, "right_pause": right_pause,
        "changed_tokens": max(0, bhi - blo),
        "cand_words": cand_words,
        "phrase": phrase,
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


def do_splice(orig: np.ndarray, cand: np.ndarray, meta: dict,
              sr: int = audio_io.SR) -> SpliceResult:
    od = audio_io.duration_seconds(orig, sr)
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
