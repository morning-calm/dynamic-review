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
REVIEW_APP_ROOT = BACKEND_ROOT.parent                      # …\review-app
WORK_ROOT = BACKEND_ROOT / "work"                          # per-session audio copies
DB_PATH = BACKEND_ROOT / "review.db"
WORK_ROOT.mkdir(parents=True, exist_ok=True)

# Trello-driven review queue (written by Trello/export_review_trips.py). When present
# it drives GET /api/trips; absent → fall back to the Quicktrips MP3-dir scan.
MANIFEST_PATH = REVIEW_APP_ROOT / "trips_to_review.json"

# Column-7 English trips (A12/B1) have NO masters under the Quicktrips tree — their
# reviewed MP3s live here as Audio Generation/<trip_id>/{i}.mp3.
AUDIO_GENERATION_ROOT = SCRIPTS_ROOT / "Audio Generation"

# --- Scene thumbnails (videoId → local VID/PIC JPG → Cloudflare R2) ----------
VIDEOIDS_JSON = SCRIPTS_ROOT / "VRD" / "VideoIds-1782220834.json"
THUMB_ROOTS = [
    Path(r"D:\Final stitch\Backed Up\England VID-PIC Thumbnails"),
    Path(r"D:\Final stitch\Backed Up\Japan VID-PIC Thumbnails"),
    Path(r"D:\Final stitch\Backed Up\Korea VID-PIC Thumbnails"),
    Path(r"D:\Final stitch\Backed Up\Scotland VID-PIC Thumbnails"),
    Path(r"D:\Final stitch\Backed Up\Taiwan VID-PIC Thumbnails"),
]
THUMB_BUCKET = "dynamic-languages-thumbs"
THUMB_KEY_PREFIX = "scene-thumbs/"
THUMB_PUBLIC_BASE = "https://thumbs.dynamiclanguages.org/"
THUMB_UPLOAD_CACHE = BACKEND_ROOT / "thumb_upload_cache.json"

# --- Review audio (mp3) -> Cloudflare R2 'review-audio', one folder per contentID ---
# The bulk stage-6/7 uploader (upload_review_audio_r2.py), stage 5c (run_levels.py),
# and the review app's combine/import/fallback all push here; served publicly at
# REVIEW_AUDIO_PUBLIC_BASE/<contentID>/<file>.mp3.
REVIEW_AUDIO_BUCKET = "review-audio"
REVIEW_AUDIO_PUBLIC_BASE = "https://reviewaudio.dynamiclanguages.org/"

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

# Per-trip drafting choices live in <root>/<trip_id>/staging_choices.json. The
# leveled pipelines keep their data under language-specific roots (NOT the single
# Research and Writing/data dir), so resolve_voice must search all of them.
_RW = SCRIPTS_ROOT / "Research and Writing"
RW_DATA_ROOTS = [
    _RW / "data",                       # native EN / Scotland / Taiwan trips
    _RW / "CEFR English" / "data",      # English A12 / B1 (lane 7)
    _RW / "N4 Japanese" / "data",       # Japanese N4 / N5 (lane 6)
    _RW / "HSK Mandarin" / "data",      # Mandarin HSK
]

# Country → best-effort voice guess. LAST resort only: used when a trip has no
# staging_choices.json voice/gender. The drafting choice (gender → audio_core
# .voice_for_gender) is the real source; the human listen is the final backstop.
COUNTRY_VOICE_GUESS = {
    "Scotland": "isla",
    "England": "harry",
}

# Last-of-last resort: when neither a drafting choice nor a country guess applies,
# at least stay in the trip's narration language (an English voice on a Japanese
# trip is always wrong). Gender is a guess here — the human listen catches it.
LANGUAGE_FALLBACK_VOICE = {
    "English": "harry",
    "Japanese": "daisuke",
    "Mandarin": "yu",
}
