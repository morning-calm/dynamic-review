"""
Low-level audio DSP for the splice engine. ffmpeg is used ONLY to transcode
mp3<->wav (decode 6.0 is on PATH); all energy / level / peak / cut analysis and the
PCM splice itself run in numpy on the decoded mono float32 samples — far more
controllable than parsing ffmpeg-filter text output.

All internal audio is mono float32 at the master sample rate (44100). Masters are
mp3/44100/mono and we decode ElevenLabs candidates to match.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100


# --------------------------------------------------------------------------- #
# ffmpeg transcode
# --------------------------------------------------------------------------- #
def _run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {' '.join(cmd[:3])}…\n{p.stderr[-400:]}")


def mp3_to_samples(mp3_path: str | Path, sr: int = SR) -> np.ndarray:
    """Decode an mp3 to a mono float32 numpy array at ``sr``."""
    with tempfile.TemporaryDirectory() as td:
        wav = str(Path(td) / "d.wav")
        _run(["ffmpeg", "-v", "error", "-y", "-i", str(mp3_path),
              "-ar", str(sr), "-ac", "1", "-f", "wav", wav])
        data, _ = sf.read(wav, dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    return np.ascontiguousarray(data, dtype=np.float32)


def samples_to_mp3(samples: np.ndarray, mp3_path: str | Path, sr: int = SR) -> None:
    """Encode a mono float32 array to mp3 (libmp3lame) — one re-encode."""
    samples = np.clip(samples, -1.0, 1.0).astype(np.float32)
    with tempfile.TemporaryDirectory() as td:
        wav = str(Path(td) / "e.wav")
        sf.write(wav, samples, sr, subtype="PCM_16")
        _run(["ffmpeg", "-v", "error", "-y", "-i", wav,
              "-c:a", "libmp3lame", "-q:a", "2", str(mp3_path)])


def mp3_to_mp3_copy(src: str | Path, dst: str | Path) -> None:
    """Byte-copy an mp3 (used for versioning / promote)."""
    Path(dst).write_bytes(Path(src).read_bytes())


def mp3_duration_seconds(path: str | Path) -> float:
    """Cheap container duration via ffprobe (no full decode)."""
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True)
    try:
        return float(p.stdout.strip())
    except (TypeError, ValueError):
        return 0.0


def duration_seconds(samples: np.ndarray, sr: int = SR) -> float:
    return len(samples) / float(sr)


# --------------------------------------------------------------------------- #
# Energy / level / peak
# --------------------------------------------------------------------------- #
def _frame_rms(samples: np.ndarray, sr: int, frame_ms: float = 20.0,
               hop_ms: float = 5.0) -> tuple[np.ndarray, np.ndarray]:
    """Return (frame_centre_times, frame_rms_linear)."""
    n = len(samples)
    fl = max(1, int(sr * frame_ms / 1000))
    hop = max(1, int(sr * hop_ms / 1000))
    if n < fl:
        rms = float(np.sqrt(np.mean(samples ** 2))) if n else 0.0
        return np.array([n / (2 * sr)]), np.array([rms])
    starts = np.arange(0, n - fl + 1, hop)
    times = (starts + fl / 2) / sr
    rms = np.empty(len(starts), dtype=np.float64)
    for i, s in enumerate(starts):
        seg = samples[s:s + fl]
        rms[i] = np.sqrt(np.mean(seg.astype(np.float64) ** 2))
    return times, rms


def _to_db(x: float) -> float:
    return 20.0 * np.log10(max(x, 1e-9))


def gated_rms_db(samples: np.ndarray, sr: int = SR,
                 rel_gate_db: float = 30.0, abs_floor_db: float = -50.0) -> float:
    """Speech-gated RMS level (dBFS). Excludes silence/breath frames: keeps frames
    within ``rel_gate_db`` of the loudest frame AND above ``abs_floor_db``."""
    if len(samples) == 0:
        return -120.0
    _, rms = _frame_rms(samples, sr)
    if len(rms) == 0 or rms.max() <= 0:
        return -120.0
    peak = rms.max()
    gate_lin = max(peak * (10 ** (-rel_gate_db / 20)), 10 ** (abs_floor_db / 20))
    voiced = rms[rms >= gate_lin]
    if len(voiced) == 0:
        voiced = rms
    return _to_db(float(np.sqrt(np.mean(voiced ** 2))))


def peak_db(samples: np.ndarray, oversample: int = 4) -> float:
    """Approximate true-peak (dBFS) via simple ×oversample linear interpolation."""
    if len(samples) == 0:
        return -120.0
    if oversample > 1 and len(samples) > 1:
        x = np.arange(len(samples))
        xi = np.linspace(0, len(samples) - 1, len(samples) * oversample)
        up = np.interp(xi, x, samples)
    else:
        up = samples
    return _to_db(float(np.max(np.abs(up))))


def apply_gain_db(samples: np.ndarray, gain_db: float) -> np.ndarray:
    if abs(gain_db) < 1e-6:
        return samples
    return (samples * (10 ** (gain_db / 20.0))).astype(np.float32)


def limit_peak(samples: np.ndarray, ceiling_db: float) -> np.ndarray:
    """Scale the whole clip down (no distortion) if it exceeds the ceiling."""
    pk = peak_db(samples)
    if pk <= ceiling_db:
        return samples
    return apply_gain_db(samples, ceiling_db - pk)


def find_energy_min(samples: np.ndarray, sr: int, t_center: float,
                    window_s: float = 0.120) -> tuple[float, float]:
    """Snap a cut time to the local RMS-energy minimum within ±window_s.

    Returns (refined_time_seconds, depth) where ``depth`` is how far below the local
    median the minimum sits (linear ratio, 0..1; deeper = better seam)."""
    lo = max(0.0, t_center - window_s)
    hi = min(len(samples) / sr, t_center + window_s)
    a, b = int(lo * sr), int(hi * sr)
    if b - a < 8:
        return t_center, 0.0
    times, rms = _frame_rms(samples[a:b], sr, frame_ms=10.0, hop_ms=2.0)
    if len(rms) == 0:
        return t_center, 0.0
    j = int(np.argmin(rms))
    refined = lo + float(times[j])
    med = float(np.median(rms)) or 1e-9
    depth = float(max(0.0, 1.0 - rms[j] / med))   # 0 = no dip, →1 = deep minimum
    return refined, depth


# --------------------------------------------------------------------------- #
# Splice assembly: butt-join with equal-power edge fades (length-preserving).
# acrossfade is deliberately avoided because it shortens total length.
# --------------------------------------------------------------------------- #
def _edge_fade(piece: np.ndarray, sr: int, fade_ms: float,
               fade_in: bool, fade_out: bool) -> np.ndarray:
    out = piece.copy()
    f = min(int(sr * fade_ms / 1000), len(out) // 2)
    if f <= 0:
        return out
    # raised-cosine taper to zero (smooth, click-free at a low-energy seam)
    ramp = 0.5 * (1 - np.cos(np.linspace(0, np.pi, f)))   # 0→1
    if fade_in:
        out[:f] *= ramp.astype(np.float32)
    if fade_out:
        out[-f:] *= ramp[::-1].astype(np.float32)
    return out


def butt_join(pieces: list[np.ndarray], sr: int, fade_ms: float) -> np.ndarray:
    """Concatenate pieces end-to-end (NO overlap → length = sum of pieces), applying
    a short equal-power edge fade on each side of every internal seam."""
    pieces = [p for p in pieces if len(p) > 0]
    if not pieces:
        return np.zeros(0, dtype=np.float32)
    faded: list[np.ndarray] = []
    last = len(pieces) - 1
    for i, p in enumerate(pieces):
        faded.append(_edge_fade(p, sr, fade_ms,
                                fade_in=(i != 0), fade_out=(i != last)))
    return np.concatenate(faded).astype(np.float32)
