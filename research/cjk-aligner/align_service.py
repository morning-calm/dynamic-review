"""Isolated CJK forced-alignment service (Path B).

Runs in its own torch venv (see requirements.txt) so the review-app backend stays
torch-free. Loads the MMS_FA model ONCE, then serves JSON-per-line requests over
stdin/stdout — the backend spawns this once (subprocess.Popen) and pipes requests,
paying the model-load cost only at startup.

Protocol (one JSON object per line):
  startup ->            {"ready": true}                       (emitted once the model loads)
  request  <- stdin     {"audio": "<path.mp3>", "text": "<hanzi or kana>"}
  response ->           {"chars": [{"pos","c","start","end","score"}, ...]}
  error    ->           {"error": "<message>"}
  shutdown <- stdin     {"cmd": "quit"}

`text` is whatever is SPOKEN: hanzi for Mandarin, the KANA line for Japanese (never the
kanji). Punctuation is dropped from alignment but `pos` indexes back into `text`.
"""
import json
import re
import sys

import numpy as np
import soundfile as sf
import torch
import torchaudio
import uroman as _ur

_URO = _ur.Uroman()
PUNCT = set("。，、！？；：…—～「」『』（）()，.!?;:　 ")
SR16 = 16000


def _roman(ch: str) -> str:
    r = re.sub(r"[^a-z']", "", _URO.romanize_string(ch).strip().lower())
    return r or "'"


_B = torchaudio.pipelines.MMS_FA
_MODEL = _B.get_model(with_star=False).eval()
_TOK = _B.get_tokenizer()
_ALN = _B.get_aligner()


def align(audio_path: str, text: str) -> list[dict]:
    data, sr = sf.read(audio_path, dtype="float32", always_2d=True)
    wav = torchaudio.functional.resample(
        torch.from_numpy(data.mean(axis=1)).unsqueeze(0), sr, SR16)
    chars = [(i, ch) for i, ch in enumerate(text) if ch not in PUNCT]
    if not chars:
        return []
    with torch.inference_mode():
        emission, _ = _MODEL(wav)
    spans = _ALN(emission[0], _TOK([_roman(ch) for _, ch in chars]))
    ratio = wav.shape[1] / emission.shape[1]
    out = []
    for (pos, ch), sp in zip(chars, spans):
        if sp:
            out.append({"pos": pos, "c": ch,
                        "start": round(sp[0].start * ratio / SR16, 3),
                        "end": round(sp[-1].end * ratio / SR16, 3),
                        "score": round(float(np.mean([s.score for s in sp])), 3)})
        else:
            out.append({"pos": pos, "c": ch, "start": None, "end": None, "score": 0.0})
    return out


def main() -> None:
    sys.stdout.write(json.dumps({"ready": True}) + "\n")
    sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if req.get("cmd") == "quit":
                break
            res = {"chars": align(req["audio"], req["text"])}
        except Exception as e:  # noqa: BLE001 — never die on a bad request; report it
            res = {"error": f"{type(e).__name__}: {e}"}
        sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
