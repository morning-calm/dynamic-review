"""
Scene thumbnails: map a Trip scene's Vimeo videoId to a local VID/PIC thumbnail JPG,
upload it once to Cloudflare R2, and hand back a public URL the review UI can
``<img>``-load.

Best-effort throughout — a missing JSON / JPG / R2 credential yields a null thumb,
never an exception, so seeding and the session read-model never fail on it.

Mapping chain (all caches are process-lifetime, built lazily under one lock so the
sync route handlers in FastAPI's threadpool stay safe):

    scene["videoUrl"]  (the 6K Vimeo id)
      → VideoIds JSON   : entry["filename"] stem, else parsed from the
                          progressive-redirect download URL
      → thumbnail index : <stem normkey> → local "<Title Words>.jpg"
                          (named with spaces, leading vid/pic title-cased)
      → R2 upload (idempotent)
      → https://thumbs.dynamiclanguages.org/scene-thumbs/<stem>.jpg
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from urllib.parse import unquote

from . import config

_NORM_RE = re.compile(r"[\s_]+")
_MP4_RE = re.compile(r"\.mp4$", re.I)

# One reentrant lock guards lazy construction of every module cache below. The
# network upload itself runs OUTSIDE the lock (we don't serialise uploads), only the
# in-memory/JSON bookkeeping is locked.
_LOCK = threading.RLock()
_VIDEO_DATA: dict | None = None             # videoId → entry
_THUMB_INDEX: dict[str, Path] | None = None  # normkey(stem) → jpg path
_R2_CLIENT = None                            # boto3 s3 client or None
_R2_TRIED = False
_UPLOADED: set[str] | None = None            # R2 keys known to exist


def _norm(s: str) -> str:
    return _NORM_RE.sub("", s or "").lower()


# --------------------------------------------------------------------------- #
# videoId → filename stem
# --------------------------------------------------------------------------- #
def _video_data() -> dict:
    global _VIDEO_DATA
    if _VIDEO_DATA is None:
        with _LOCK:
            if _VIDEO_DATA is None:
                try:
                    raw = json.loads(config.VIDEOIDS_JSON.read_text(encoding="utf-8"))
                    _VIDEO_DATA = raw.get("data") or {}
                except Exception as e:  # noqa: BLE001 - best effort
                    print(f"[thumbs] could not load {config.VIDEOIDS_JSON}: {e}")
                    _VIDEO_DATA = {}
    return _VIDEO_DATA


def _vimeo_id(v) -> str | None:
    """Bare Vimeo id from a scene videoUrl (already an id, but tolerate a URL)."""
    if not v:
        return None
    v = str(v).strip()
    if "/" in v:
        v = v.rstrip("/").split("/")[-1]
    v = v.split("?")[0].strip()
    return v or None


def stem_for_video_id(video_id: str) -> str | None:
    """Filename stem for a videoId: entry['filename'] sans .mp4 if present, else the
    last path segment of entry['url'] (URL-decoded, .mp4 stripped)."""
    entry = _video_data().get(str(video_id))
    if not entry:
        return None
    fn = (entry.get("filename") or "").strip()
    if fn:
        return _MP4_RE.sub("", fn)
    url = entry.get("url") or ""
    if not url:
        return None
    seg = url.split("?")[0].rstrip("/").split("/")[-1]
    return _MP4_RE.sub("", unquote(seg)) or None


# --------------------------------------------------------------------------- #
# stem → local JPG (one-time index over the five thumbnail trees)
# --------------------------------------------------------------------------- #
def _thumb_index() -> dict[str, Path]:
    global _THUMB_INDEX
    if _THUMB_INDEX is None:
        with _LOCK:
            if _THUMB_INDEX is None:
                idx: dict[str, Path] = {}
                for root in config.THUMB_ROOTS:
                    if not root.is_dir():
                        continue
                    for dirpath, _dirs, names in os.walk(root):
                        for nm in names:
                            if nm.lower().endswith(".jpg"):
                                idx.setdefault(_norm(Path(nm).stem),
                                               Path(dirpath) / nm)
                _THUMB_INDEX = idx
                print(f"[thumbs] indexed {len(idx)} thumbnail JPGs "
                      f"from {len(config.THUMB_ROOTS)} roots")
    return _THUMB_INDEX


def jpg_for_stem(stem: str) -> Path | None:
    if not stem:
        return None
    return _thumb_index().get(_norm(stem))


# --------------------------------------------------------------------------- #
# R2 upload (idempotent)
# --------------------------------------------------------------------------- #
def _r2():
    """Cached R2/S3 client from the Cloudfare_* (.env) creds, or None if unavailable."""
    global _R2_CLIENT, _R2_TRIED
    if not _R2_TRIED:
        with _LOCK:
            if not _R2_TRIED:
                _R2_CLIENT = _build_r2()
                _R2_TRIED = True
    return _R2_CLIENT


def _build_r2():
    try:
        import boto3
    except Exception as e:  # noqa: BLE001
        print(f"[thumbs] boto3 unavailable, no thumbnail uploads: {e}")
        return None

    def envget(*names):
        for n in names:
            if os.environ.get(n):
                return os.environ[n]
        return None

    ak = envget("Cloudfare_Access_Key_ID", "R2_ACCESS_KEY_ID")
    sk = envget("Cloudfare_Secret_Access_Key", "R2_SECRET_ACCESS_KEY")
    ep = envget("Cloudfare_S3_API_Endpoint", "R2_ENDPOINT")
    if not (ak and sk and ep):
        print("[thumbs] R2 creds missing (Cloudfare_*) — thumbnails will not upload")
        return None
    return boto3.client("s3", endpoint_url=ep,
                        aws_access_key_id=ak, aws_secret_access_key=sk)


def _uploaded_set() -> set[str]:
    global _UPLOADED
    if _UPLOADED is None:
        with _LOCK:
            if _UPLOADED is None:
                try:
                    data = json.loads(
                        config.THUMB_UPLOAD_CACHE.read_text(encoding="utf-8"))
                    _UPLOADED = set(data.get("keys") or [])
                except Exception:
                    _UPLOADED = set()
    return _UPLOADED


def _remember_uploaded(key: str) -> None:
    s = _uploaded_set()
    with _LOCK:
        s.add(key)
        try:
            config.THUMB_UPLOAD_CACHE.write_text(
                json.dumps({"keys": sorted(s)}, indent=0), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            print(f"[thumbs] could not persist upload cache: {e}")


def _key_for(stem: str) -> str:
    return f"{config.THUMB_KEY_PREFIX}{stem}.jpg"


def _ensure_uploaded(stem: str, jpg: Path) -> None:
    """Upload <stem>.jpg to R2 once. No-op when already known/present. Never raises."""
    key = _key_for(stem)
    if key in _uploaded_set():
        return
    s3 = _r2()
    if s3 is None:
        return
    try:
        try:  # already on R2 from a previous run?
            s3.head_object(Bucket=config.THUMB_BUCKET, Key=key)
            _remember_uploaded(key)
            return
        except Exception:
            pass
        s3.upload_file(str(jpg), config.THUMB_BUCKET, key,
                       ExtraArgs={"ContentType": "image/jpeg"})
        _remember_uploaded(key)
    except Exception as e:  # noqa: BLE001
        print(f"[thumbs] upload failed for {key}: {e}")


def _public_url(stem: str) -> str:
    return f"{config.THUMB_PUBLIC_BASE}{_key_for(stem)}"


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def thumb_url_for_scene(scene: dict) -> str | None:
    """Public thumbnail URL for a VID scene, uploading the JPG to R2 on first use.
    Returns None when the scene has no videoId or no matching JPG (never raises)."""
    try:
        vid = _vimeo_id((scene or {}).get("videoUrl"))
        if not vid:
            return None
        stem = stem_for_video_id(vid)
        if not stem:
            return None
        jpg = jpg_for_stem(stem)
        if not jpg:
            return None
        _ensure_uploaded(stem, jpg)
        return _public_url(stem)
    except Exception as e:  # noqa: BLE001
        print(f"[thumbs] thumb_url_for_scene error: {e}")
        return None


def prewarm(scenes) -> None:
    """Resolve + upload every scene's thumbnail for a trip (best-effort, at seed)."""
    for s in scenes or []:
        try:
            thumb_url_for_scene(s)
        except Exception:  # noqa: BLE001
            pass
