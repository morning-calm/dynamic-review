"""Path B core: CJK char-diff -> silence-anchored cut-point planner.

The counterpart of audio_splice.plan_segment(), for CJK: diff old vs new hanzi at the
CHARACTER level, find the changed span, then EXPAND the re-voiced region outward to the
nearest clause/sentence pause on each side (so cuts land in real silence, never mid-syllable),
refine each cut to a local energy minimum (+-120ms), and gate on the anchor chars' alignment
confidence. Returns a plan (tL, tR, re-voiced original text, verdict) or edit_required.
The backend will feed the re-voiced NEW text to ElevenLabs and splice its audio between tL/tR.
"""
import difflib
import re
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import torchaudio

SPIKE = Path(__file__).resolve().parent
PUNCT = set("。，、！？；：…—～「」『』（）()，.!?;:　 ")
SR16 = 16000
CONF_FLOOR = 0.30   # an anchor char below this is an unreliable cut point

import uroman as _ur
_uro = _ur.Uroman()
def _roman(ch):
    r = re.sub(r"[^a-z']", "", _uro.romanize_string(ch).strip().lower())
    return r or "'"

_bundle = torchaudio.pipelines.MMS_FA
_model = _bundle.get_model(with_star=False).eval()
_tok = _bundle.get_tokenizer()
_aln = _bundle.get_aligner()


def align(clip, hans):
    native, sr = sf.read(str(SPIKE / "clips" / clip), dtype="float32", always_2d=True)
    native = native.mean(axis=1)
    wav16 = torchaudio.functional.resample(torch.from_numpy(native).unsqueeze(0), sr, SR16)
    chars = [(i, ch) for i, ch in enumerate(hans) if ch not in PUNCT]
    with torch.inference_mode():
        emission, _ = _model(wav16)
    spans = _aln(emission[0], _tok([_roman(ch) for _, ch in chars]))
    ratio = wav16.shape[1] / emission.shape[1]
    pos = {}
    for (p, ch), sp in zip(chars, spans):
        if sp:
            pos[p] = (sp[0].start * ratio / SR16, sp[-1].end * ratio / SR16,
                      float(np.mean([s.score for s in sp])))
    hop = max(1, int(0.005 * sr)); win = max(hop, int(0.020 * sr))
    n = max(1, (len(native) - win) // hop + 1)
    env = np.array([np.sqrt(np.mean(native[i*hop:i*hop+win]**2) + 1e-12) for i in range(n)])
    return native, sr, pos, env, hop, float(np.median(env)) * 0.35


def plan(clip, old_hans, new_hans):
    native, sr, pos, env, hop, thr = align(clip, old_hans)
    dur = len(native) / sr
    spf = hop / sr

    def refine(t, lo, hi):
        a = max(0, int((t + lo) / spf)); b = min(len(env), int((t + hi) / spf) + 1)
        if b <= a:
            k = min(len(env) - 1, max(0, int(t / spf)))
        else:
            k = a + int(np.argmin(env[a:b]))
        return k * spf, float(env[k])

    # 1) char-level diff -> changed span [oa, ob) in the ORIGINAL string
    ops = difflib.SequenceMatcher(None, old_hans, new_hans, autojunk=False).get_opcodes()
    ch = [(i1, i2) for tag, i1, i2, j1, j2 in ops if tag != "equal"]
    if not ch:
        return {"verdict": "no_change"}
    oa = min(i1 for i1, _ in ch); ob = max(i2 for _, i2 in ch)
    old2new = {}
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            for d in range(i2 - i1):
                old2new[i1 + d] = j1 + d

    # 2) expand to the nearest clause/sentence PUNCTUATION on each side (= the pauses)
    pL = next((p for p in range(oa - 1, -1, -1) if old_hans[p] in PUNCT), -1)
    pR = next((p for p in range(ob, len(old_hans)) if old_hans[p] in PUNCT), len(old_hans))
    jL = (old2new.get(pL, -1) + 1) if pL >= 0 else 0
    jR = old2new.get(pR, len(new_hans)) if pR < len(old_hans) else len(new_hans)

    # 3) cut times from the aligned chars adjacent to those boundaries
    def first_aligned_after(p):
        return next((q for q in range(p + 1, len(old_hans)) if q in pos), None)
    def last_aligned_before(p):
        return next((q for q in range(p - 1, -1, -1) if q in pos), None)

    if pL < 0:
        tL, eL, cL = 0.0, 0.0, 1.0
    else:
        q = first_aligned_after(pL); tL_raw = pos[q][0] if q is not None else 0.0
        tL, eL = refine(tL_raw, -0.120, 0.040); cL = pos[q][2] if q is not None else 0.0
    if pR >= len(old_hans):
        tR, eR, cR = dur, 0.0, 1.0
    else:
        q = last_aligned_before(pR); tR_raw = pos[q][1] if q is not None else dur
        tR, eR = refine(tR_raw, -0.040, 0.120); cR = pos[q][2] if q is not None else 0.0

    # 4) guards (mirror the English engine): both cuts must sit in real silence, and the
    #    anchor chars must be confidently aligned; else advise whole-regenerate.
    sil_ok = (pL < 0 or eL < thr) and (pR >= len(old_hans) or eR < thr)
    conf_ok = cL >= CONF_FLOOR and cR >= CONF_FLOOR
    verdict = "splice" if (sil_ok and conf_ok) else "edit_required"

    return {"verdict": verdict, "clip": clip, "dur_s": round(dur, 2),
            "changed_orig": old_hans[oa:ob], "revoiced_old": old_hans[pL + 1:pR],
            "revoiced_new": new_hans[jL:jR],  # the expanded NEW clause the backend sends to EL
            "tL_s": round(tL, 3), "tR_s": round(tR, 3), "removed_s": round(tR - tL, 2),
            "cutL_energy": round(eL, 5), "cutR_energy": round(eR, 5), "sil_thr": round(thr, 5),
            "anchorL_conf": round(cL, 3), "anchorR_conf": round(cR, 3),
            "sil_ok": sil_ok, "conf_ok": conf_ok}


if __name__ == "__main__":
    OLD = "这是高雄的莲池潭。它是一个很大的湖，水很干净。夏天的时候，湖里开很多花，非常漂亮。"
    NEW = "这是高雄的莲池潭。它是一个很大的湖，水很清澈。夏天的时候，湖里开很多花，非常漂亮。"  # 干净 -> 清澈
    p = plan("1.mp3", OLD, NEW)
    lines = [f"{k}: {v}" for k, v in p.items()]
    (SPIKE / "out" / "planner_report.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
