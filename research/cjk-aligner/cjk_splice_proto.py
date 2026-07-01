"""Path B prototype: CJK surgical splice driven by MMS char-alignment.

Proves the core mechanic on a REAL Mandarin master: char-align the hanzi, pick a
middle clause to remove, derive silence-anchored cut points (refined to a local energy
minimum within +-120ms, the same S3 window the English engine uses), and splice
orig[:tL] + orig[tR:] at the MASTER's native sample rate with an equal-power seam
crossfade. Outputs a WAV so the seam can be heard. Deletion (no new audio needed) is
enough to validate cut-point + seam quality; the backend adds an EL candidate in the middle.
"""
import json
import re
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import torchaudio

SPIKE = Path(__file__).resolve().parent
OUT = SPIKE / "out"
OUT.mkdir(exist_ok=True)

CLIP = "1.mp3"
HANS = "这是高雄的莲池潭。它是一个很大的湖，水很干净。夏天的时候，湖里开很多花，非常漂亮。"
DEL_SPAN = (9, 21)   # original char positions to remove: 它…净 (the whole 2nd sentence)

PUNCT = set("。，、！？；：…—～「」『』（）()，.!?;:　 ")
SR16 = 16000

import uroman as _ur
_uro = _ur.Uroman()

def roman_char(ch):
    r = re.sub(r"[^a-z']", "", _uro.romanize_string(ch).strip().lower())
    return r or "'"

bundle = torchaudio.pipelines.MMS_FA
model = bundle.get_model(with_star=False).eval()
tokenizer = bundle.get_tokenizer()
aligner = bundle.get_aligner()

# --- load native master (for the splice) + a 16k mono copy (for alignment) ---
native, sr = sf.read(str(SPIKE / "clips" / CLIP), dtype="float32", always_2d=True)
native = native.mean(axis=1)                      # mono, native sr
wav16 = torchaudio.functional.resample(torch.from_numpy(native).unsqueeze(0), sr, SR16)

chars = [(i, ch) for i, ch in enumerate(HANS) if ch not in PUNCT]
transcript = [roman_char(ch) for _, ch in chars]
with torch.inference_mode():
    emission, _ = model(wav16)
spans = aligner(emission[0], tokenizer(transcript))
ratio = wav16.shape[1] / emission.shape[1]
def t_of(fr): return fr * ratio / SR16
aligned = []
for (pos, ch), sp in zip(chars, spans):
    if sp:
        aligned.append((pos, ch, t_of(sp[0].start), t_of(sp[-1].end), float(np.mean([s.score for s in sp]))))

# --- energy envelope on the NATIVE audio (5ms hop, 20ms win) ---
hop = max(1, int(0.005 * sr)); win = max(hop, int(0.020 * sr))
n = max(1, (len(native) - win) // hop + 1)
env = np.array([np.sqrt(np.mean(native[i*hop:i*hop+win]**2) + 1e-12) for i in range(n)])
med = float(np.median(env)); thr = 0.35 * med
def env_at(t): return env[min(len(env)-1, max(0, int(t / (hop/sr))))]
def refine(t, lo, hi):
    """nearest local energy MIN in [t+lo, t+hi] seconds; returns (time, energy)."""
    a = max(0, int((t+lo)/(hop/sr))); b = min(len(env), int((t+hi)/(hop/sr))+1)
    if b <= a: return t, env_at(t)
    k = a + int(np.argmin(env[a:b]))
    return k*hop/sr, float(env[k])

dsel = [c for c in aligned if DEL_SPAN[0] <= c[0] <= DEL_SPAN[1]]
tL_raw, tR_raw = dsel[0][2], dsel[-1][3]
tL, eL = refine(tL_raw, -0.120, 0.040)     # bias LEFT into the preceding pause
tR, eR = refine(tR_raw, -0.040, 0.120)     # bias RIGHT into the following pause
guardL = eL < thr; guardR = eR < thr        # both cuts must land in real silence

# --- splice at native sr with a 10ms equal-power crossfade ---
sL, sR = int(tL*sr), int(tR*sr)
L, R = native[:sL], native[sR:]
cf = min(int(0.010*sr), len(L), len(R))
if cf > 0:
    tt = np.linspace(0, 1, cf, endpoint=False)
    fo, fi = np.cos(tt*np.pi/2), np.sin(tt*np.pi/2)
    blend = L[-cf:]*fo + R[:cf]*fi
    spliced = np.concatenate([L[:-cf], blend, R[cf:]])
else:
    spliced = np.concatenate([L, R])

sf.write(str(OUT/"original_ref.wav"), native, sr)
sf.write(str(OUT/"splice_demo.wav"), spliced, sr)

lines = [
    f"clip={CLIP} native_sr={sr} dur={len(native)/sr:.2f}s aligned_chars={len(aligned)}",
    f"removed chars (orig pos {DEL_SPAN}): {''.join(c[1] for c in dsel)}",
    f"kept text: 这是高雄的莲池潭。 + 夏天的时候，湖里开很多花，非常漂亮。",
    f"cut L: raw={tL_raw:.3f}s -> refined={tL:.3f}s  energy={eL:.5f} (silence<{thr:.5f}? {guardL})",
    f"cut R: raw={tR_raw:.3f}s -> refined={tR:.3f}s  energy={eR:.5f} (silence<{thr:.5f}? {guardR})",
    f"GUARD (both cuts in real silence): {'PASS' if guardL and guardR else 'FAIL -> would edit_required'}",
    f"result dur={len(spliced)/sr:.2f}s (removed {(sR-sL)/sr:.2f}s)  seam crossfade=10ms equal-power",
    f"min char confidence in kept spans: {min(c[4] for c in aligned):.3f}",
]
(OUT/"splice_report.txt").write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
print("WROTE", OUT/"splice_demo.wav", "and original_ref.wav")
