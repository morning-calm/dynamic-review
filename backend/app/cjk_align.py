"""Backend client for the isolated CJK forced-alignment service (Path B).

The aligner (``research/cjk-aligner/align_service.py``) runs in its OWN torch venv so this
backend process stays torch-free (CTranslate2/faster-whisper). We spawn it lazily, keep it
warm across requests (the model load is ~6s, each align ~1s), and talk JSON over its pipes.

Config via env:
  CJK_ALIGNER_PYTHON  the venv python that has torch/torchaudio/uroman (default: the venv
                      created from research/cjk-aligner/requirements.txt next to the script)
  CJK_ALIGNER_SCRIPT  path to align_service.py

`text` must be what is SPOKEN: hanzi for Mandarin, the KANA line for Japanese.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent          # …/review-app
_DEF_SCRIPT = _REPO / "research" / "cjk-aligner" / "align_service.py"
_DEF_PY = _REPO / "research" / "cjk-aligner" / "venv" / "Scripts" / "python.exe"

ALIGNER_PYTHON = os.environ.get("CJK_ALIGNER_PYTHON", str(_DEF_PY))
ALIGNER_SCRIPT = os.environ.get("CJK_ALIGNER_SCRIPT", str(_DEF_SCRIPT))

_proc: subprocess.Popen | None = None
_lock = threading.Lock()


class AlignerError(RuntimeError):
    """The aligner subprocess failed to start, crashed, or returned an error."""


def available() -> bool:
    """True if the aligner venv + script are present (so callers can fall back to
    whole-regeneration when the CJK splice backend isn't installed)."""
    return Path(ALIGNER_PYTHON).exists() and Path(ALIGNER_SCRIPT).exists()


def _spawn() -> subprocess.Popen:
    if not available():
        raise AlignerError(f"aligner not installed (python={ALIGNER_PYTHON}, script={ALIGNER_SCRIPT})")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")   # the service emits hanzi/kana over the pipe
    p = subprocess.Popen([ALIGNER_PYTHON, ALIGNER_SCRIPT],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         text=True, encoding="utf-8", env=env, bufsize=1)
    ready = p.stdout.readline() if p.stdout else ""
    if '"ready"' not in ready:
        p.kill()
        raise AlignerError(f"aligner did not report ready: {ready!r}")
    return p


def _ensure() -> subprocess.Popen:
    global _proc
    if _proc is None or _proc.poll() is not None:
        _proc = _spawn()
    return _proc


def align(audio_path: str | Path, text: str) -> list[dict]:
    """Char-level forced alignment of `text` against `audio_path`.
    Returns [{pos, c, start, end, score}, ...] (start/end seconds; score 0..1;
    start/end may be None for a char the model couldn't place). Raises AlignerError."""
    req = json.dumps({"audio": str(audio_path), "text": text}) + "\n"
    with _lock:
        p = _ensure()
        try:
            p.stdin.write(req)
            p.stdin.flush()
            line = p.stdout.readline()
        except (BrokenPipeError, OSError) as e:
            _shutdown_locked()
            raise AlignerError(f"aligner pipe broke: {e}")
    if not line:
        raise AlignerError("aligner returned no output (crashed?)")
    res = json.loads(line)
    if "error" in res:
        raise AlignerError(res["error"])
    return res["chars"]


def _shutdown_locked() -> None:
    global _proc
    if _proc is not None:
        try:
            if _proc.stdin:
                _proc.stdin.write('{"cmd": "quit"}\n')
                _proc.stdin.flush()
            _proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            _proc.kill()
        _proc = None


def shutdown() -> None:
    with _lock:
        _shutdown_locked()


if __name__ == "__main__":   # smoke test: python cjk_align.py <audio.mp3> "<hanzi/kana>"
    import sys
    chars = align(sys.argv[1], sys.argv[2])
    ok = [c for c in chars if c["start"] is not None]
    mean = sum(c["score"] for c in ok) / max(1, len(ok))
    print(f"{len(chars)} chars, {len(ok)} placed, mean score {mean:.3f}, "
          f"span {ok[0]['start']}-{ok[-1]['end']}s")
