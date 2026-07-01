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


def silence_run(samples: np.ndarray, sr: int, t0: float, t1: float,
                thresh_db: float = -38.0, min_ms: float = 25.0) -> tuple[float, float] | None:
    """(start, end) of the FIRST genuine-silence run (frame RMS below ``thresh_db`` re.
    peak, lasting ≥ ``min_ms``) whose START falls in [t0, t1]; ``end`` is the run's TRUE
    end (it may extend past t1). None if no silence starts in [t0, t1]. Used to anchor a
    splice cut in real silence instead of at an imprecise (often very wrong) Whisper word
    boundary, and to detect connected speech (no run = no safe seam)."""
    n = len(samples)
    a, b = max(0, int(t0 * sr)), min(n, int((t1 + 0.7) * sr))   # extend to capture run end
    if b - a < int(min_ms / 1000 * sr):
        return None
    times, rms = _frame_rms(samples[a:b], sr, frame_ms=10.0, hop_ms=5.0)
    if len(rms) == 0:
        return None
    thr = (float(np.max(np.abs(samples))) or 1e-9) * (10.0 ** (thresh_db / 20.0))
    silent = rms < thr
    t1_rel = t1 - t0
    i, m = 0, len(silent)
    while i < m:
        if silent[i]:
            j = i
            while j < m and silent[j]:
                j += 1
            s0, s1 = float(times[i]), float(times[j - 1])
            if (s1 - s0) >= min_ms / 1000.0 and s0 <= t1_rel:
                return (t0 + s0, t0 + s1)
            i = j
        else:
            i += 1
    return None


def silence_run_nearest(samples: np.ndarray, sr: int, t_anchor: float,
                        back: float, fwd: float, thresh_db: float = -38.0,
                        min_ms: float = 40.0) -> tuple[float, float] | None:
    """(start, end) of the genuine-silence run (RMS below ``thresh_db`` re. peak, ≥
    ``min_ms``) whose nearest edge is CLOSEST to ``t_anchor``, among runs starting in
    [t_anchor−back, t_anchor+fwd]. Unlike :func:`silence_run` (first run in a window) this
    picks the pause ADJACENT to a word boundary regardless of which side it falls — Whisper
    routinely absorbs an inter-word pause into the *start* of the following word (stretching
    it), so the real pause can sit forward of the reported word start. Returns None if no
    qualifying run. ``min_ms`` is higher than silence_run's so sub-phoneme dips are ignored."""
    t0 = max(0.0, t_anchor - back)
    t1 = t_anchor + fwd
    n = len(samples)
    a, b = max(0, int(t0 * sr)), min(n, int((t1 + 0.7) * sr))
    if b - a < int(min_ms / 1000 * sr):
        return None
    times, rms = _frame_rms(samples[a:b], sr, frame_ms=10.0, hop_ms=5.0)
    if len(rms) == 0:
        return None
    thr = (float(np.max(np.abs(samples))) or 1e-9) * (10.0 ** (thresh_db / 20.0))
    silent = rms < thr
    t1_rel = t1 - t0
    best, best_d = None, 1e9
    i, m = 0, len(silent)
    while i < m:
        if silent[i]:
            j = i
            while j < m and silent[j]:
                j += 1
            s0, s1 = float(times[i]), float(times[j - 1])
            if (s1 - s0) >= min_ms / 1000.0 and s0 <= t1_rel:
                a0, a1 = t0 + s0, t0 + s1
                d = 0.0 if a0 <= t_anchor <= a1 else min(abs(a0 - t_anchor), abs(a1 - t_anchor))
                if d < best_d:
                    best_d, best = d, (a0, a1)
            i = j
        else:
            i += 1
    return best


def trim_slivers(samples: np.ndarray, sr: int, t0: float, t1: float,
                 thresh_db: float = -38.0, sliver_max: float = 0.13,
                 sil_min: float = 0.04) -> np.ndarray:
    """Within [t0, t1], drop SHORT isolated voiced blips (leftover word-fragment slivers)
    that are bordered by silence — keeping real words (longer voiced runs) and the pauses
    untouched. Returns new full-length samples. Manual backstop for residual splice noise;
    conservative so it rarely touches genuine short words."""
    n = len(samples)
    a, b = max(0, int(t0 * sr)), min(n, int(t1 * sr))
    if b - a < int(0.03 * sr):
        return samples
    win = samples[a:b]
    hop_ms = 5.0
    _, rms = _frame_rms(win, sr, frame_ms=10.0, hop_ms=hop_ms)
    if len(rms) == 0:
        return samples
    thr = (float(np.max(np.abs(samples))) or 1e-9) * (10.0 ** (thresh_db / 20.0))
    silent = rms < thr
    runs = []                                    # (is_silent, frame_start, frame_end)
    i, m = 0, len(silent)
    while i < m:
        j = i
        while j < m and silent[j] == silent[i]:
            j += 1
        runs.append((bool(silent[i]), i, j))
        i = j
    hop = hop_ms / 1000.0
    drop = set()
    for k, (sil, fi, fj) in enumerate(runs):
        if sil or (fj - fi) * hop > sliver_max:
            continue                             # silence or a real (long) word — keep
        left_sil = k > 0 and runs[k - 1][0] and (runs[k - 1][2] - runs[k - 1][1]) * hop >= sil_min
        right_sil = k + 1 < len(runs) and runs[k + 1][0] and (runs[k + 1][2] - runs[k + 1][1]) * hop >= sil_min
        if left_sil or right_sil:
            drop.add(k)                          # short voiced blip in a gap → sliver
    if not drop:
        return samples
    pieces = [win[int(fi * hop * sr):int(fj * hop * sr)]
              for k, (_s, fi, fj) in enumerate(runs) if k not in drop]
    # butt_join (NOT crossfade_join): crossfade OVERLAPS the kept pieces, lopping ~fade_ms
    # off every silence/voice seam and so eating the ends of perfectly good words. butt_join
    # is length-preserving — only the dropped slivers are removed, kept audio is verbatim.
    cleaned = butt_join(pieces, sr, 6.0) if pieces else win[:0]
    return np.concatenate([samples[:a], cleaned, samples[b:]]).astype(np.float32)


def first_voice_onset(samples: np.ndarray, sr: int, t0: float, t1: float,
                      rel_db: float = 26.0, min_run_ms: float = 60.0) -> float | None:
    """First time in [t0, t1] where the RMS stays above (peak − ``rel_db``) for at least
    ``min_run_ms`` — i.e. a real voiced onset, not a breath/click. ``None`` if the region
    is all silence/artefacts. Absorption-proof: Whisper routinely reports the next word's
    ``start`` early (it swallows the preceding pause), so the gap before a word is found
    from energy, not from the word boundary."""
    n = len(samples)
    a, b = max(0, int(t0 * sr)), min(n, int(t1 * sr))
    if b - a < int(min_run_ms / 1000 * sr):
        return None
    times, rms = _frame_rms(samples[a:b], sr, frame_ms=10.0, hop_ms=5.0)
    if len(rms) == 0:
        return None
    thr = (float(np.max(np.abs(samples))) or 1e-9) * (10.0 ** (-rel_db / 20.0))
    voiced = rms >= thr
    need = max(1, int(min_run_ms / 1000.0 / 0.005))      # frames (5 ms hop)
    i, m = 0, len(voiced)
    while i < m:
        if voiced[i]:
            j = i
            while j < m and voiced[j]:
                j += 1
            if (j - i) >= need:
                return t0 + float(times[i])
            i = j
        else:
            i += 1
    return None


def first_silence_after(samples: np.ndarray, sr: int, t0: float, t1: float,
                        rel_db: float = 34.0, min_run_ms: float = 20.0) -> float | None:
    """First time in [t0, t1] where the RMS DROPS below (peak − ``rel_db``) for at least
    ``min_run_ms``. Used to start a gap-blank AFTER a word's release so genuinely voiced
    audio is never silenced. ``None`` if it never goes quiet in the window."""
    n = len(samples)
    a, b = max(0, int(t0 * sr)), min(n, int(t1 * sr))
    if b - a < int(min_run_ms / 1000 * sr):
        return None
    times, rms = _frame_rms(samples[a:b], sr, frame_ms=10.0, hop_ms=5.0)
    if len(rms) == 0:
        return None
    thr = (float(np.max(np.abs(samples))) or 1e-9) * (10.0 ** (-rel_db / 20.0))
    quiet = rms < thr
    need = max(1, int(min_run_ms / 1000.0 / 0.005))
    i, m = 0, len(quiet)
    while i < m:
        if quiet[i]:
            j = i
            while j < m and quiet[j]:
                j += 1
            if (j - i) >= need:
                return t0 + float(times[i])
            i = j
        else:
            i += 1
    return None


def trim_trailing_breath(samples: np.ndarray, sr: int = SR, rel_db: float = 26.0,
                         min_speech_ms: float = 80.0, release_ms: float = 90.0) -> np.ndarray:
    """Drop a trailing breath / next-sound bleed that TTS leaves AFTER the last word.
    Finds the end of the last sustained voiced run (above peak − ``rel_db`` for ≥
    ``min_speech_ms``), keeps a ``release_ms`` tail so the natural word release is intact,
    and cuts everything after it. Never cuts into sustained speech; returns the samples
    unchanged when the tail beyond the last word is negligible (< 40 ms)."""
    n = len(samples)
    if n < int(0.05 * sr):
        return samples
    times, rms = _frame_rms(samples, sr, frame_ms=10.0, hop_ms=5.0)
    if len(rms) == 0:
        return samples
    thr = (float(np.max(np.abs(samples))) or 1e-9) * (10.0 ** (-rel_db / 20.0))
    voiced = rms >= thr
    need = max(1, int(min_speech_ms / 1000.0 / 0.005))
    end_idx = None
    k, m = 0, len(voiced)
    while k < m:
        if voiced[k]:
            j = k
            while j < m and voiced[j]:
                j += 1
            if (j - k) >= need:
                end_idx = j - 1
            k = j
        else:
            k += 1
    if end_idx is None:
        return samples
    cut = int(round((float(times[end_idx]) + release_ms / 1000.0) * sr))
    if cut >= n - int(0.04 * sr):                        # negligible tail → no-op
        return samples
    return samples[: max(0, cut)].astype(np.float32)


def trailing_silence_seconds(samples: np.ndarray, sr: int = SR,
                             thresh_db: float = -50.0) -> float:
    """Seconds of (near-)silence at the END of the clip — how much trailing pause to
    conserve when a regenerated take replaces a master (e.g. the ~3s beginner SceneDesc
    tail). Returns the full length if it's entirely below threshold."""
    n = len(samples)
    if n == 0:
        return 0.0
    times, rms = _frame_rms(samples, sr, frame_ms=20.0, hop_ms=10.0)
    if len(rms) == 0:
        return 0.0
    above = np.nonzero(rms >= 10.0 ** (thresh_db / 20.0))[0]
    total = n / sr
    if len(above) == 0:
        return total
    return max(0.0, total - (float(times[int(above[-1])]) + 0.02))


def set_trailing_silence(samples: np.ndarray, sr: int = SR,
                         target_seconds: float = 0.0,
                         thresh_db: float = -50.0) -> np.ndarray:
    """Normalize the END pause to ``target_seconds``: cut excess trailing silence, or
    pad with zeros when short (beginner trips need a fixed ~3s tail). Only ever removes
    silence — the cut is bounded by the measured trailing-silence run, so a voiced sample
    is never clipped. Returns the (possibly unchanged) samples."""
    n = len(samples)
    if n == 0:
        return samples
    cur = trailing_silence_seconds(samples, sr, thresh_db)
    delta = cur - max(0.0, target_seconds)            # >0 too much, <0 too little
    if delta > 0:                                     # trim excess silence off the end
        cut = min(n, int(round(delta * sr)))
        return samples[: n - cut].copy() if cut > 0 else samples
    if delta < 0:                                     # pad up to the required tail
        pad = int(round((-delta) * sr))
        return (np.concatenate([samples, np.zeros(pad, dtype=np.float32)])
                if pad > 0 else samples)
    return samples


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


def crossfade_join(pieces: list[np.ndarray], sr: int, fade_ms: float) -> np.ndarray:
    """Join pieces with an OVERLAPPING equal-power (cos/sin) crossfade at each seam.

    Unlike butt_join (which fades both sides to zero → a tiny amplitude notch when the
    cut isn't dead silence), the crossfade keeps constant power across the boundary, so
    a seam that sits in low-but-not-silent audio blends instead of clicking. Shortens
    total length by ~fade_ms per seam — fine for a surgical word splice."""
    pieces = [p.astype(np.float32) for p in pieces if len(p) > 0]
    if not pieces:
        return np.zeros(0, dtype=np.float32)
    out = pieces[0].copy()
    for p in pieces[1:]:
        n = min(int(sr * fade_ms / 1000), len(out), len(p))
        if n <= 0:
            out = np.concatenate([out, p])
            continue
        t = np.linspace(0.0, np.pi / 2, n, dtype=np.float32)
        seam = out[-n:] * np.cos(t) + p[:n] * np.sin(t)   # equal power
        out = np.concatenate([out[:-n], seam, p[n:]])
    return out.astype(np.float32)
