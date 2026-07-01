"""MMS forced-alignment spike: can a char-level forced aligner place CJK cut points
in real silences on our actual Mandarin masters? Writes results to UTF-8 files."""
import json
import re
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import torchaudio

import sys
SPIKE = Path(__file__).resolve().parent
MF = sys.argv[1] if len(sys.argv) > 1 else "manifest.json"
TAG = "" if MF == "manifest.json" else "_" + Path(MF).stem
manifest = json.loads((SPIKE / MF).read_text(encoding="utf-8"))

# --- romanizer: MMS_FA aligns uroman-romanized tokens ---
import uroman as _ur
_uro = _ur.Uroman()


def romanize_char(ch: str) -> str:
    r = _uro.romanize_string(ch).strip().lower()
    r = re.sub(r"[^a-z']", "", r)
    return r or "'"


bundle = torchaudio.pipelines.MMS_FA
SR = bundle.sample_rate
model = bundle.get_model(with_star=False).eval()
tokenizer = bundle.get_tokenizer()
aligner = bundle.get_aligner()

PUNCT = set("。，、！？；：…—～「」『』（）()，.!?;:　 ")


def load_16k_mono(path: Path):
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    wav = torch.from_numpy(mono).unsqueeze(0)
    if sr != SR:
        wav = torchaudio.functional.resample(wav, sr, SR)
    return wav


def rms_env(wav, hop=160, win=400):
    x = wav.squeeze(0).numpy()
    n = max(1, (len(x) - win) // hop + 1)
    env = np.array([np.sqrt(np.mean(x[i * hop:i * hop + win] ** 2) + 1e-12) for i in range(n)])
    return env, hop / SR


results = []
lines = []
for m in manifest:
    clip, hans = m["clip"], (m.get("hans") or m.get("text"))
    wav = load_16k_mono(SPIKE / "clips" / clip)
    dur = wav.shape[1] / SR

    chars = [(i, ch) for i, ch in enumerate(hans) if ch not in PUNCT]
    transcript = [romanize_char(ch) for _, ch in chars]

    with torch.inference_mode():
        emission, _ = model(wav)
    spans = aligner(emission[0], tokenizer(transcript))
    ratio = wav.shape[1] / emission.shape[1]

    def t(frame):
        return frame * ratio / SR

    ctimes = []  # (orig_pos, char, start_s, end_s, conf)
    for (pos, ch), sp in zip(chars, spans):
        if not sp:
            ctimes.append((pos, ch, None, None, 0.0))
        else:
            ctimes.append((pos, ch, t(sp[0].start), t(sp[-1].end), float(np.mean([s.score for s in sp]))))

    env, spf = rms_env(wav)
    med = float(np.median(env))
    thr = 0.35 * med

    def nearest_sil_ms(ts):
        c = int(ts / spf)
        for w in range(int(0.30 / spf) + 1):
            for k in (c - w, c + w):
                if 0 <= k < len(env) and env[k] < thr:
                    return round(abs(k - c) * spf * 1000)
        return None

    punct = []
    for i, ch in enumerate(hans):
        if ch in PUNCT and ch != " " and ch != "　":
            prev = next((c for c in reversed(ctimes) if c[0] < i and c[3] is not None), None)
            if prev:
                off = nearest_sil_ms(prev[3])
                punct.append({"after": prev[1], "mark": ch, "boundary_s": round(prev[3], 3),
                              "sil_within_120ms": bool(off is not None and off <= 120),
                              "nearest_sil_ms": off})

    confs = [c[4] for c in ctimes if c[2] is not None]
    good = sum(1 for p in punct if p["sil_within_120ms"])
    res = {"clip": clip, "dur_s": round(dur, 2), "n_chars": len(chars),
           "mean_conf": round(float(np.mean(confs)), 3), "min_conf": round(float(np.min(confs)), 3),
           "first_start_s": round(ctimes[0][2] or 0, 3), "last_end_s": round(ctimes[-1][3] or 0, 3),
           "punct_good": good, "punct_total": len(punct), "punct": punct}
    results.append(res)
    lines.append(f"=== {clip}  dur={dur:.2f}s  chars={len(chars)}  mean_conf={res['mean_conf']}  min_conf={res['min_conf']}")
    lines.append(f"    aligned span: {res['first_start_s']}s .. {res['last_end_s']}s")
    lines.append(f"    clause/sentence boundaries within +-120ms of a silence: {good}/{len(punct)}")
    for p in punct:
        lines.append(f"      '{p['after']}'{p['mark']} @ {p['boundary_s']}s  ok={p['sil_within_120ms']}  nearest_silence={p['nearest_sil_ms']}ms")

(SPIKE / f"results{TAG}.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
(SPIKE / f"results{TAG}.txt").write_text("\n".join(lines), encoding="utf-8")
print("WROTE results.json / results.txt for", len(results), "clips")
