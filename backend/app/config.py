"""
Process configuration + the one-time sys.path / .env bootstrap that lets us reuse
the D:\\Dynamic Languages\\Scripts modules (stage9.common, whisper_timing,
pronunciation_overrides, the Stage-09 category parser).

Importing this module has the side effect of putting SCRIPTS_ROOT on sys.path and
loading the Scripts .env, so it MUST be imported before anything that touches the
reused modules. app.main imports it first.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# --- The reused codebase (READ-ONLY; we never modify it) -------------------
SCRIPTS_ROOT = Path(r"D:\Dynamic Languages\Scripts")
RW_STAGES = SCRIPTS_ROOT / "Research and Writing" / "stages"

for p in (str(SCRIPTS_ROOT), str(RW_STAGES)):
    if p not in sys.path:
        sys.path.insert(0, p)

# Load API keys (ELEVENLABS_API_KEY, GEMINI_API_KEY) from the Scripts .env.
try:
    from dotenv import load_dotenv

    load_dotenv(SCRIPTS_ROOT / ".env")
except Exception:  # pragma: no cover - dotenv always present per requirements
    pass

# UTF-8 stdout (CJK-safe diagnostics).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# --- This app's own state ---------------------------------------------------
BACKEND_ROOT = Path(__file__).resolve().parent.parent      # …\review-app\backend
WORK_ROOT = BACKEND_ROOT / "work"                          # per-session audio copies
DB_PATH = BACKEND_ROOT / "review.db"
WORK_ROOT.mkdir(parents=True, exist_ok=True)

# --- Security / server ------------------------------------------------------
REVIEW_TOKEN = os.environ.get("REVIEW_APP_TOKEN", "dev-token")
CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
HOST = "127.0.0.1"
PORT = int(os.environ.get("REVIEW_APP_PORT", "8000"))

# --- Audio / DSP constants --------------------------------------------------
ANALYSIS_SR = 44100          # masters are mp3/44100/mono; we analyse at source rate
ENERGY_MIN_WINDOW = 0.120    # ±120 ms snap-to-energy-minimum for cut refinement
SEAM_FADE_MS = 6.0           # equal-power edge fade at each butt-join
TRUE_PEAK_CEILING_DB = -1.5  # post-splice peak target
COVERAGE_DONE_FRACTION = 0.95
SPLICE_CONFIDENCE_FLOOR = 0.55   # below this → auto edit_required
ANCHOR_EXPAND_CAP = 20       # S1: max tokens to extend an anchor to a sentence boundary
SEAM_DEPTH_MIN = 0.5         # S1: required energy-min dip at a non-boundary cut (else edit_required)

# Overlay image search roots (best-effort display only; never authoritative).
OVERLAY_SEARCH_DIRS = [
    SCRIPTS_ROOT / "Research and Writing" / "data",          # …/{trip}/static_images/
    SCRIPTS_ROOT / "Research and Writing" / "data" / "0 Flat images",
]

# Country → best-effort voice guess (voice_settings is a guess; the human listen is
# load-bearing per the plan's residual-risk note).
COUNTRY_VOICE_GUESS = {
    "Scotland": "isla",
    "England": "harry",
}
