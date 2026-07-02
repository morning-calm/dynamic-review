"""CJK surgical splice (Path B) — the character-level counterpart of
``audio_splice.plan_segment``, for Mandarin (``_ZH``, hanzi) and Japanese (``_JP``, the
KANA line).

This path is **ADDITIVE and SEPARATE** from the English token engine (faster-whisper-en +
SequenceMatcher over space-delimited words), which CJK — no word spaces — cannot use. It:

  1. char-diffs OLD vs NEW spoken text and finds the changed span;
  2. EXPANDS that span outward to the enclosing clause/sentence PUNCTUATION on each side, so
     cuts land in real inter-clause silence, never mid-syllable;
  3. reads cut times from the MMS forced-aligner (``cjk_align``, an isolated torch
     subprocess) and refines each to a local energy minimum within ±120 ms (the same S3
     window the English engine uses — the refiner does the heavy lifting, the aligner only
     needs to land within a syllable);
  4. gates on real silence at each cut AND the anchor chars' alignment confidence AND the
     GLOBAL alignment quality (mean char score — a near-zero mean means the audio does not
     say ``old_text`` at all, e.g. stale text↔audio, so we must NOT splice).

On ``splice`` it EL-generates the expanded NEW clause and returns a ``RegenPlan`` whose meta
is ``span_only`` — so the REPLACE assembly reuses the validated, language-agnostic
``audio_splice.do_splice`` / ``_splice_span_only`` (trim candidate → level-match to the
LONGEST retained context (S1) → peak-limit ONLY the insert (S4) → equal-power crossfade).
Nothing in ``audio_splice.py`` is modified.

When ANY guard is uncertain → return ``None`` → the caller WHOLE-REGENERATES (the safe
floor, Path A). The feared failure is a *silent* mis-cut (plausible audio, wrong words);
every guard biases to whole-regen and the human listen is the load-bearing backstop.

Two entry points share the machinery: ``plan_cuts``/``plan_cjk`` start from a TEXT DIFF
("Generate from edit"); ``plan_span_cuts``/``plan_cjk_span`` start from a reviewer
SELECTION (highlight / alt-text — old==new allowed) — same clause expansion, gates and
assembly. ``char_times`` exposes the gated per-char timings for the direct working-take
tools (trim-noise / insert- & remove-pause), which never fall back silently (they 409).
"""

from __future__ import annotations

import difflib

import numpy as np

from . import audio_core, audio_io, cjk_align
from .audio_splice import RegenPlan   # reuse the plan dataclass (candidate + span_only meta)

# Clause/sentence punctuation (CJK + ASCII) + spaces — dropped from alignment, used as the
# expansion boundaries. Mirrors align_service.PUNCT so `pos` indices line up.
PUNCT = set("。，、！？；：…—～「」『』（）()，.!?;:　 ")

# ---- per-language gates (tunable; validated ZH mean≈0.96, JP kana mean≈0.60) --------------
# Anchor-char alignment confidence: the boundary char whose timing defines a cut must be
# this trustworthy. ZH aligns tightly (~0.96); JP kana looser (~0.60) so a lower bar.
_ANCHOR_FLOOR = {"zh": 0.90, "jp": 0.50}
# Global alignment quality: mean char score over the whole OLD text. A near-zero mean (the
# aligner returned ~0.009 when text≠audio in testing) means the base take does NOT say
# old_text → a splice would be a SILENT mis-cut. Bail well above that noise floor.
_MEAN_FLOOR = {"zh": 0.50, "jp": 0.30}
# Revoicing ~all of the clip is not a "surgical" splice — cleaner (and safer) to whole-regen.
_MAX_REVOICE_FRAC = 0.85
# Silence-gap search (seconds): the cut is the deepest energy min in the pause between a
# clause ender and the next clause's onset. _GAP_MAX caps the span so a mis-placed onset
# can't drag the search into the next clause; _GAP_PAD softens the ender/onset edges.
_GAP_MAX, _GAP_MIN, _GAP_PAD = 0.60, 0.06, 0.02


def _spoken(text: str) -> list[str]:
    return [c for c in text if c not in PUNCT]


def _char_pos(chars: list[dict]) -> tuple[dict[int, tuple[float, float, float]], float]:
    """``cjk_align`` char list → ({orig_pos: (start, end, score)} over PLACED chars,
    mean score over placed chars). Unplaced chars (start=None) are dropped."""
    pos: dict[int, tuple[float, float, float]] = {}
    scores: list[float] = []
    for c in chars:
        if c.get("start") is not None:
            pos[c["pos"]] = (c["start"], c["end"], c["score"])
            scores.append(c["score"])
    return pos, (float(np.mean(scores)) if scores else 0.0)


def _energy_env(samples: np.ndarray, sr: int) -> tuple[np.ndarray, float, float]:
    """RMS envelope (5 ms hop, 20 ms window) + seconds-per-frame + silence threshold
    (0.35 × median), matching the validated spike (``mms_align`` / ``cjk_planner``)."""
    hop = max(1, int(0.005 * sr))
    win = max(hop, int(0.020 * sr))
    n = max(1, (len(samples) - win) // hop + 1)
    env = np.array([np.sqrt(np.mean(samples[i * hop:i * hop + win] ** 2) + 1e-12)
                    for i in range(n)])
    return env, hop / sr, float(np.median(env)) * 0.35


def plan_cjk(audio_path: str, old_text: str, new_text: str,
             voice_id: str, voice_settings: dict, model_id: str,
             lang: str) -> RegenPlan | None:
    """Attempt a surgical CJK splice of ``old_text`` → ``new_text`` on ``audio_path`` (the
    current working take, which SAYS ``old_text``). Returns a ``RegenPlan`` (candidate mp3 +
    ``span_only`` meta for ``audio_splice.do_splice``) on success, or ``None`` → the caller
    must whole-regenerate. NEVER raises (aligner down, API error, odd text → ``None``)."""
    try:
        cuts = plan_cuts(audio_path, old_text, new_text, lang)
        if cuts is None:
            return None
        # EL-generate the expanded NEW clause, with the surrounding NEW text as prosody
        # context (previous/next) so the re-voiced clause blends at the seams. eleven_v3
        # (Japanese) rejects previous_text/next_text on /with-timestamps ("unsupported_model")
        # → omit it there; a full clause has natural standalone prosody anyway.
        ctx = model_id != "eleven_v3"
        mp3, cand_words = audio_core.generate_with_timestamps(
            cuts["revoiced_new"], voice_id, voice_settings,
            cuts["prev_text"] if ctx else None, cuts["next_text"] if ctx else None, model_id)
        # span_only meta → the REPLACE assembly is audio_splice.do_splice/_splice_span_only,
        # which trims the candidate, level-matches it to the LONGEST retained context (S1),
        # peak-limits ONLY the insert (S4), and equal-power crossfades it between tL/tR.
        # changed_tokens (spoken char count) seeds its duration-plausibility confidence.
        meta = {
            "mode": "segment", "span_only": True, "cjk": True, "cjk_lang": lang,
            "tL": cuts["tL"], "tR": cuts["tR"], "orig_duration": cuts["dur"],
            "changed_tokens": max(1, len(_spoken(cuts["revoiced_new"]))),
            "cand_words": cand_words, "phrase": cuts["revoiced_new"],
            "cjk_detail": cuts["detail"],
        }
        return RegenPlan(candidate_mp3=mp3, meta=meta)
    except cjk_align.AlignerError:
        return None
    except Exception:  # noqa: BLE001 — any failure must fall back to whole-regen, never 500
        return None


def _old2new(ops) -> dict[int, int]:
    """OLD→NEW position map over the diff's equal runs (chars present in both texts)."""
    m: dict[int, int] = {}
    for tag, i1, i2, j1, _j2 in ops:
        if tag == "equal":
            for d in range(i2 - i1):
                m[i1 + d] = j1 + d
    return m


def plan_cuts(audio_path: str, old_text: str, new_text: str,
              lang: str) -> dict | None:
    """Pure cut-planning (NO ElevenLabs, NO network): char-diff → expand-to-clause → aligner
    timings → energy-refined, silence+confidence-gated cut points. Returns a dict
    ``{tL, tR, dur, revoiced_new, prev_text, next_text, detail}`` when a safe splice exists,
    else ``None`` (→ whole-regen). Separated from ``plan_cjk`` so cut geometry is testable
    without spending EL credits. May raise ``AlignerError`` (caller treats as ``None``)."""
    old_text = (old_text or "").strip()
    new_text = (new_text or "").strip()
    if not old_text or not new_text or old_text == new_text:
        return None
    if not cjk_align.available():
        return None

    # 1) char-level diff → changed span [oa, ob) in OLD; map OLD→NEW positions for the
    #    equal (unchanged) runs so we can locate the NEW clause to re-voice.
    ops = difflib.SequenceMatcher(None, old_text, new_text, autojunk=False).get_opcodes()
    changed = [(i1, i2) for tag, i1, i2, _j1, _j2 in ops if tag != "equal"]
    if not changed:
        return None
    oa = min(i1 for i1, _ in changed)
    ob = max(i2 for _, i2 in changed)
    return _cuts_for_old_span(audio_path, old_text, new_text, lang, oa, ob, _old2new(ops))


def _cuts_for_old_span(audio_path: str, old_text: str, new_text: str, lang: str,
                       oa: int, ob: int, old2new: dict[int, int]) -> dict | None:
    """Steps 2–6 of the cut plan, for a target span ``[oa, ob)`` in OLD coordinates —
    shared by the diff entry (``plan_cuts``) and the highlight entry (``plan_span_cuts``).
    ``old_text`` must be what the audio SAYS; ``old2new`` maps the diff's equal runs."""
    # A whole-sentence highlight naturally INCLUDES the trailing 。/、 — but punctuation IS
    # the clause boundary, so leaving it inside the span makes the pR search below start
    # PAST it and re-voice one clause too many (e.g. highlighting "たかさは…あります。"
    # regenerated on into "この…すうじは、"). Shrink the span to its spoken chars first;
    # a span that is ONLY punctuation collapses to its left edge (the boundary itself).
    while ob > oa and old_text[ob - 1] in PUNCT:
        ob -= 1
    while oa < ob and old_text[oa] in PUNCT:
        oa += 1

    # 2) expand to the nearest clause/sentence PUNCTUATION on each side (= the pauses).
    pL = next((p for p in range(oa - 1, -1, -1) if old_text[p] in PUNCT), -1)
    pR = next((p for p in range(ob, len(old_text)) if old_text[p] in PUNCT), len(old_text))
    jL = (old2new.get(pL, -1) + 1) if pL >= 0 else 0
    jR = old2new.get(pR, len(new_text)) if pR < len(old_text) else len(new_text)
    revoiced_new = new_text[jL:jR].strip()
    revoiced_old = old_text[pL + 1:pR]
    if not revoiced_new:
        return None   # deletion-only clause → nothing to voice → whole-regen

    # Revoicing essentially the whole clip is not surgical → whole-regen is cleaner/safer.
    spoken_all = _spoken(old_text)
    if not spoken_all or len(_spoken(revoiced_old)) / len(spoken_all) > _MAX_REVOICE_FRAC:
        return None

    # 3) align OLD text against the base audio (which says OLD). A near-zero mean score means
    #    the audio does NOT say old_text (stale/misaligned) → a splice would be a silent
    #    mis-cut → bail.
    chars = cjk_align.align(audio_path, old_text)
    pos, mean_conf = _char_pos(chars)
    if mean_conf < _MEAN_FLOOR.get(lang, 0.40):
        return None

    base = audio_io.mp3_to_samples(audio_path)
    sr = audio_io.SR
    dur = audio_io.duration_seconds(base, sr)
    env, spf, thr = _energy_env(base, sr)

    def emin(t0: float, t1: float) -> tuple[float, float]:
        """(time, energy) of the lowest-energy frame in [t0, t1] seconds."""
        a = max(0, int(t0 / spf))
        b = min(len(env), int(t1 / spf) + 1)
        if b <= a:
            k = min(len(env) - 1, max(0, a))
        else:
            k = a + int(np.argmin(env[a:b]))
        return k * spf, float(env[k])

    def ender_before(p: int) -> int | None:
        """Last placed, non-numeric char before position ``p`` WITHOUT crossing an earlier
        clause boundary (stop at the previous punctuation → don't jump to a distant clause).
        Both cuts anchor on clause-ENDERS: a char right before a pause is placed confidently
        (JP enders 0.77–1.0), whereas the NEXT clause's first char is pause-blurred and
        erratic (JP starters 0.07–1.0). Digits align poorly → never anchor on one (the
        English engine's numberish bar)."""
        for q in range(p - 1, -1, -1):
            if old_text[q] in PUNCT:
                return None
            if q in pos and not audio_core.is_numberish(old_text[q]):
                return q
        return None

    def placed_after(p: int) -> int | None:
        """First PLACED char after position ``p`` (the next clause's first char). Only its
        START time is used — to bound the silence gap — so its (often low) confidence is
        irrelevant."""
        for q in range(p + 1, len(old_text)):
            if q in pos:
                return q
        return None

    def gap_cut(boundary_p: int):
        """Deepest silence in the pause BETWEEN the clause ender before ``boundary_p`` and the
        next clause's first char. The ender (confidently placed) bounds the near edge and
        supplies the trust score; the next char's start bounds the far edge (clamped, so a
        mis-placed start can't run the search into the next clause). Returns
        ``(t, energy, ender_conf)`` or ``None`` if there is no usable ender."""
        q = ender_before(boundary_p)
        if q is None:
            return None
        lo = pos[q][1]                                    # ender.end (near edge of the pause)
        nxt = placed_after(boundary_p)
        hi = pos[nxt][0] if nxt is not None else lo + _GAP_MAX   # far edge = next clause onset
        hi = min(max(hi, lo + _GAP_MIN), lo + _GAP_MAX)
        t, e = emin(lo - _GAP_PAD, hi + _GAP_PAD)
        return t, e, pos[q][2]

    # 4) cut times = the deepest silence in the pause on each side of the re-voiced clause. A
    #    cut at the CLIP boundary (change touches the first/last clause) takes the clip edge
    #    and is NOT energy-refined — refining can only move it inward, truncating real audio
    #    (the English engine's C1 lesson).
    if pL < 0:
        tL, eL, cL, left_pause = 0.0, 0.0, 1.0, True   # first clause → clip start
    else:
        g = gap_cut(pL)
        if g is None:
            return None
        tL, eL, cL = g
        left_pause = False
    if pR >= len(old_text):
        tR, eR, cR, right_pause = dur, 0.0, 1.0, True   # last clause → clip end
    else:
        g = gap_cut(pR)
        if g is None:
            return None
        tR, eR, cR = g
        right_pause = False
    if tR - tL < 0.05:
        return None

    # 5) guards (mirror the English engine): both INTERIOR cuts must sit in real silence AND
    #    the anchor chars must be confidently aligned; else whole-regenerate.
    floor = _ANCHOR_FLOOR.get(lang, 0.50)
    sil_ok = (left_pause or eL < thr) and (right_pause or eR < thr)
    conf_ok = cL >= floor and cR >= floor
    if not (sil_ok and conf_ok):
        return None

    # 6) safe splice — hand back the geometry + the expanded NEW clause and its surrounding
    #    NEW text (prosody context for EL previous_text/next_text). jL/jR (the clause's NEW
    #    char range) let the highlight entry locate a selection inside revoiced_new.
    return {
        "tL": tL, "tR": tR, "dur": dur,
        "revoiced_new": revoiced_new, "jL": jL, "jR": jR,
        "prev_text": new_text[:jL].strip() or None,
        "next_text": new_text[jR:].strip() or None,
        "detail": {
            "revoiced_old": revoiced_old, "revoiced_new": revoiced_new,
            "anchorL_conf": round(cL, 3), "anchorR_conf": round(cR, 3),
            "mean_conf": round(mean_conf, 3), "removed_s": round(tR - tL, 3),
            "cutL_energy": round(eL, 5), "cutR_energy": round(eR, 5),
            "sil_thr": round(thr, 5), "left_pause": left_pause, "right_pause": right_pause,
        },
    }


# --------------------------------------------------------------------------- #
# Selection tools (highlight / alt-text / trim-noise / insert- & remove-pause)
# --------------------------------------------------------------------------- #
def _new_pos_to_old(ops, p: int) -> int:
    """Monotone NEW→OLD position map: exact inside an equal run, an edit region's OLD
    start inside a replace/insert. ``p == len(new)`` maps to ``len(old)``."""
    out = 0
    for tag, i1, i2, j1, j2 in ops:
        if j1 <= p < j2:
            return i1 + (p - j1) if tag == "equal" else i1
        out = i2
    return out


def map_new_span_to_old(old_text: str, new_text: str, s: int, e: int) -> tuple[int, int]:
    """Map a char span ``[s, e)`` in the NEW (displayed) text to the OLD span whose audio
    it occupies, via the diff opcodes: every opcode whose NEW side overlaps the span
    contributes its OLD range (a pure insertion contributes its zero-width OLD insertion
    point; a deletion sitting inside the span is bridged by the equal runs around it).
    Identity when the texts are equal. Always defined — a boundary-only/zero-width result
    falls back to the point map, and clause expansion widens it afterwards anyway."""
    if old_text == new_text:
        return s, e
    ops = difflib.SequenceMatcher(None, old_text, new_text, autojunk=False).get_opcodes()
    lo = hi = None
    for tag, i1, i2, j1, j2 in ops:
        if j2 <= s or j1 >= e:        # no overlap with [s,e) (deletes: j1 == j2 → skipped)
            continue
        if tag == "equal":
            a, b = i1 + (max(s, j1) - j1), i1 + (min(e, j2) - j1)
        else:
            a, b = i1, i2
        lo = a if lo is None else min(lo, a)
        hi = b if hi is None else max(hi, b)
    if lo is None:
        p = _new_pos_to_old(ops, s)
        return p, p
    return lo, hi


def plan_span_cuts(audio_path: str, old_text: str, new_text: str, lang: str,
                   new_span: tuple[int, int]) -> dict | None:
    """Highlight counterpart of ``plan_cuts``: the target is a char range in the NEW
    (displayed) spoken text instead of a diff — and unlike ``plan_cuts``, ``old == new``
    is FINE (the common case: re-voice words whose text is already right). Returns the
    ``plan_cuts`` dict plus ``hl_rel`` — the selection clamped to offsets within
    ``revoiced_new`` (for alt-text substitution) — or ``None`` (→ caller falls back).
    May raise ``AlignerError`` (callers treat as ``None``)."""
    old_text = (old_text or "").strip()
    new_text = (new_text or "").strip()
    s, e = new_span
    s = max(0, min(int(s), len(new_text)))
    e = max(s, min(int(e), len(new_text)))
    if not old_text or not new_text or e <= s:
        return None
    if not cjk_align.available():
        return None
    ops = difflib.SequenceMatcher(None, old_text, new_text, autojunk=False).get_opcodes()
    oa, ob = map_new_span_to_old(old_text, new_text, s, e)
    cuts = _cuts_for_old_span(audio_path, old_text, new_text, lang, oa, ob, _old2new(ops))
    if cuts is None:
        return None
    # The selection, relative to revoiced_new (= new_text[jL:jR].strip() — account for the
    # lstrip skew), clamped inside it. Alt text replaces exactly these chars in the clause.
    lead = len(new_text[cuts["jL"]:cuts["jR"]]) \
        - len(new_text[cuts["jL"]:cuts["jR"]].lstrip())
    hs = max(0, min(s - cuts["jL"] - lead, len(cuts["revoiced_new"])))
    he = max(hs, min(e - cuts["jL"] - lead, len(cuts["revoiced_new"])))
    cuts["hl_rel"] = (hs, he)
    return cuts


def plan_cjk_span(audio_path: str, old_text: str, new_text: str,
                  new_span: tuple[int, int], alt_text: str | None,
                  voice_id: str, voice_settings: dict, model_id: str,
                  lang: str) -> RegenPlan | None:
    """Highlight / alt-text sibling of ``plan_cjk``: re-voice the clause enclosing the
    reviewer's selection (with ``alt_text`` substituted for exactly the selected chars,
    when given) and splice it between silence-anchored cuts. The re-voiced unit is always
    the ENCLOSING CLAUSE — clause expansion is intrinsic to the CJK engine (cuts must land
    in inter-clause silence), so unlike the English engine alt text does not require the
    selection itself to sit at pauses. Returns ``None`` on ANY uncertainty (caller decides
    the fallback: whole-regen for highlight, edit_required for alt). NEVER raises."""
    try:
        cuts = plan_span_cuts(audio_path, old_text, new_text, lang, new_span)
        if cuts is None:
            return None
        phrase = cuts["revoiced_new"]
        alt = (alt_text or "").strip()
        if alt:
            hs, he = cuts["hl_rel"]
            phrase = (phrase[:hs] + alt + phrase[he:]).strip()
        if not phrase:
            return None
        # eleven_v3 (Japanese) rejects previous_text/next_text on /with-timestamps → omit.
        ctx = model_id != "eleven_v3"
        mp3, cand_words = audio_core.generate_with_timestamps(
            phrase, voice_id, voice_settings,
            cuts["prev_text"] if ctx else None, cuts["next_text"] if ctx else None, model_id)
        meta = {
            "mode": "segment", "span_only": True, "cjk": True, "cjk_lang": lang,
            "tL": cuts["tL"], "tR": cuts["tR"], "orig_duration": cuts["dur"],
            "changed_tokens": max(1, len(_spoken(phrase))),
            "cand_words": cand_words, "phrase": phrase,
            "cjk_detail": {**cuts["detail"], "revoiced_new": phrase,
                           "selection": [new_span[0], new_span[1]], "alt": bool(alt)},
        }
        return RegenPlan(candidate_mp3=mp3, meta=meta)
    except cjk_align.AlignerError:
        return None
    except Exception:  # noqa: BLE001 — any failure must fall back, never 500
        return None


def char_times(audio_path: str, old_text: str, lang: str
               ) -> tuple[dict[int, tuple[float, float, float]], float] | None:
    """Per-char times of ``old_text`` (what the audio SAYS) on a working take, for the
    direct char→time tools (trim-noise / insert- & remove-pause):
    ``({old_pos: (start, end, score)}, mean_score)`` over placed chars — or ``None`` when
    the mean is below the language floor (the audio does not say ``old_text``, so any
    mapped time would be a lie). Raises ``AlignerError`` when the aligner is missing or
    broken (callers surface a 409 — the direct tools never silently fall back)."""
    if not cjk_align.available():
        raise cjk_align.AlignerError("CJK aligner not installed")
    pos, mean = _char_pos(cjk_align.align(audio_path, old_text))
    if mean < _MEAN_FLOOR.get(lang, 0.40):
        return None
    return pos, mean
