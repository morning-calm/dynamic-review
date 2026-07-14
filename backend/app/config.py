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
# REVIEW_APP_SCRIPTS_ROOT overrides the Windows default so the app can run on a
# host (e.g. the Ubuntu server) where the Scripts repo lives at a different path,
# or where it's absent entirely (masters then come from R2 — see
# sessions.resolve_audio_dir's R2 fallback). Importing stage9.* still requires
# SOME Scripts checkout on sys.path; a bare Linux box needs at least the repo
# cloned even if the source audio trees themselves aren't synced.
SCRIPTS_ROOT = Path(os.environ.get("REVIEW_APP_SCRIPTS_ROOT", r"D:\Dynamic Languages\Scripts"))
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
# These source trees are the UPLOAD side only: a host that has them mirrors each JPG
# to R2 on first use. A host without them (the Ubuntu server) indexes 0 files and
# serves whatever is already in the bucket (thumbs._remote_keys). Overridable
# via REVIEW_APP_THUMB_ROOTS (comma-separated) for hosts that DO have local
# copies at different paths.
VIDEOIDS_JSON = SCRIPTS_ROOT / "VRD" / "VideoIds-1782220834.json"
_thumb_roots_env = os.environ.get("REVIEW_APP_THUMB_ROOTS")
if _thumb_roots_env:
    THUMB_ROOTS = [Path(p.strip()) for p in _thumb_roots_env.split(",") if p.strip()]
else:
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

# 4K re-encodes of the static-360 panoramas (app/static360.py). The {i}.jpg a PIC scene
# ships is the 7680×7680 VR master (~15 MB) — fine for the headset, absurd as a review
# card thumbnail. These are the 4096×2048 mono re-encodes (~1 MB), laid out
# <Country>/<Region>/<leaf>/<sceneIndex>-4k.jpg. Workstation-only, like the VID-PIC
# thumbnail trees: the live host resolves them from the R2 mirror instead.
STATIC_4K_ROOT = Path(os.environ.get(
    "REVIEW_APP_STATIC_4K_ROOT", r"D:\Final stitch\Re-Encodes\Static-Images-4k-mono"))

# Static-360 stills + flat overlays are served locally from the source-audio trees
# (OVERLAY_SEARCH_DIRS etc.), which don't exist on the hosted/laptop deployment — so
# they're ALSO mirrored to R2 (same thumbs bucket, distinct prefix) and served publicly
# when the local file is absent. Keyed by the canonical (reduced `_EN`) base trip id so
# every level/language sibling of a group shares one copy.
OVERLAY_KEY_PREFIX = "review-overlays/"

# --- Review audio (mp3) -> Cloudflare R2 'review-audio', one folder per contentID ---
# The bulk stage-6/7 uploader (upload_review_audio_r2.py), stage 5c (run_levels.py),
# and the review app's combine/import/fallback all push here; served publicly at
# REVIEW_AUDIO_PUBLIC_BASE/<contentID>/<file>.mp3.
REVIEW_AUDIO_BUCKET = "review-audio"
REVIEW_AUDIO_PUBLIC_BASE = "https://reviewaudio.dynamiclanguages.org/"

# --- Security / server ------------------------------------------------------
# Auth is DB-backed (users + auth_sessions; see app/auth.py). The old static
# REVIEW_APP_TOKEN shared-secret path has been removed entirely.
#   AUTH_COOKIE_SECURE  — mark the review_session cookie Secure. MUST be off in dev
#                         (plain-HTTP localhost) so <audio>/<img> media GETs carry the
#                         cookie; turn ON once served over HTTPS (the tunnel/edge).
#   AUTH_TOKEN_TTL_SECONDS — opaque bearer/cookie token lifetime (default 14 days).
AUTH_COOKIE_NAME = "review_session"
AUTH_COOKIE_SECURE = os.environ.get("REVIEW_APP_COOKIE_SECURE", "0").strip().lower() in (
    "1", "true", "yes", "on")
AUTH_TOKEN_TTL_SECONDS = int(os.environ.get("REVIEW_APP_TOKEN_TTL", str(14 * 24 * 3600)))
CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
HOST = "127.0.0.1"
PORT = int(os.environ.get("REVIEW_APP_PORT", "8000"))

# Single-origin deploy (phase-1 tunnel): serve the built frontend (frontend/dist) from
# this app so ONE hostname fronts the UI + /api + /audio + /overlays. Off by default
# (dev uses the Vite server on :5173); set REVIEW_APP_SERVE_FRONTEND=1 for the tunnel.
SERVE_FRONTEND = os.environ.get("REVIEW_APP_SERVE_FRONTEND", "0").strip().lower() in (
    "1", "true", "yes", "on")
FRONTEND_DIST = REVIEW_APP_ROOT / "frontend" / "dist"

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

# Where a trip's display images (flat overlays + the {i}.jpg static-360 stills) actually
# live for the KP-era / leveled trips, searched per BASE trip id (a leveled
# Taichung_HSK3_ZH shows Taichung_EN's images — see sessions._image_base_ids):
#   Audio Generation/ogg/<base_id>/           (Japan Tokyo_06-10 EN, all Taiwan EN, A12/B1)
#   Japanese/Trips/Day Series/Ogg/<loc>/<id>  (Japan Day Series N5/EN: Tokyo_01-05, Shikoku…)
AUDIO_GENERATION_OGG = AUDIO_GENERATION_ROOT / "ogg"
_extra_ogg_env = os.environ.get("REVIEW_APP_EXTRA_IMAGE_OGG_ROOTS")
if _extra_ogg_env:
    EXTRA_IMAGE_OGG_ROOTS = [Path(p.strip()) for p in _extra_ogg_env.split(",") if p.strip()]
else:
    EXTRA_IMAGE_OGG_ROOTS = [
        Path(r"D:\Dynamic Languages\Japanese\Trips\Day Series\Ogg"),
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
