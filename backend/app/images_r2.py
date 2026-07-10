"""
Cloudflare R2 mirror for static-360 stills + flat overlay images (display-only).

Thumbnails already live on R2 (thumbs.py), but static-360 stills and flat overlays
are served from the LOCAL source-audio trees (sessions._resolve_overlay_file) — which
don't exist on the hosted / Ubuntu-laptop deployment, so those images 404 there. This
module mirrors them to the same ``dynamic-languages-thumbs`` bucket under the
``review-overlays/`` prefix and serves them publicly when the local file is absent.

Keyed by the CANONICAL base trip id (``<base>_EN`` — see sessions._image_base_ids;
every level/language sibling of a group reduces to it) so one upload covers the group:
    review-overlays/<canonical_base>/<filename>
served at https://thumbs.dynamiclanguages.org/review-overlays/<canonical_base>/<filename>

Best-effort throughout — any missing credential / network error logs a WARN and yields
None/False, never an exception, so seeding and image serving never fail on R2.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from . import config

_LOCK = threading.RLock()
_R2_CLIENT = None
_R2_TRIED = False
_UPLOADED: set[str] = set()                 # keys known to exist on R2 (this process)
_LISTING: dict[str, set[str]] = {}          # base -> filenames present under its prefix


def _build_r2():
    try:
        import boto3
    except Exception as e:  # noqa: BLE001
        print(f"[images_r2] boto3 unavailable: {e}")
        return None

    def _env(*names: str) -> str | None:
        for n in names:
            v = os.environ.get(n)
            if v:
                return v
        return None

    ak = _env("Cloudfare_Access_Key_ID", "R2_ACCESS_KEY_ID")
    sk = _env("Cloudfare_Secret_Access_Key", "R2_SECRET_ACCESS_KEY")
    ep = _env("Cloudfare_S3_API_Endpoint", "R2_ENDPOINT")
    if not (ak and sk and ep):
        print("[images_r2] R2 creds missing (Cloudfare_*) — overlay images will not "
              "upload/serve from R2")
        return None
    return boto3.client("s3", endpoint_url=ep,
                        aws_access_key_id=ak, aws_secret_access_key=sk)


def _r2():
    global _R2_CLIENT, _R2_TRIED
    if not _R2_TRIED:
        with _LOCK:
            if not _R2_TRIED:
                _R2_CLIENT = _build_r2()
                _R2_TRIED = True
    return _R2_CLIENT


def _key(base: str, filename: str) -> str:
    return f"{config.OVERLAY_KEY_PREFIX}{base}/{Path(filename).name}"


def public_url(base: str, filename: str) -> str:
    return f"{config.THUMB_PUBLIC_BASE}{_key(base, filename)}"


def _listing(base: str) -> set[str]:
    """Filenames present under ``review-overlays/<base>/`` — one cached list per base."""
    if base in _LISTING:
        return _LISTING[base]
    with _LOCK:
        if base in _LISTING:
            return _LISTING[base]
        names: set[str] = set()
        s3 = _r2()
        if s3 is not None:
            try:
                prefix = f"{config.OVERLAY_KEY_PREFIX}{base}/"
                paginator = s3.get_paginator("list_objects_v2")
                for page in paginator.paginate(Bucket=config.THUMB_BUCKET, Prefix=prefix):
                    for obj in page.get("Contents", []):
                        names.add(obj["Key"].rsplit("/", 1)[-1])
            except Exception as e:  # noqa: BLE001
                print(f"[images_r2] WARN list failed {base}: {e}")
        _LISTING[base] = names
        return names


def exists(base: str, filename: str) -> bool:
    """Is <filename> already on R2 for this base? (cached listing — no per-file call)."""
    return Path(filename).name in _listing(base)


def ensure_uploaded(base: str, filename: str, local_path: "str | Path") -> bool:
    """Upload the local image to R2 once (idempotent). No-op when already present.
    Best-effort — returns False and logs on any error, never raises."""
    key = _key(base, filename)
    if key in _UPLOADED:
        return True
    try:
        s3 = _r2()
        if s3 is None:
            return False
        name = Path(filename).name
        if name in _listing(base):        # already there from a prior run/host
            _UPLOADED.add(key)
            return True
        ext = Path(name).suffix.lower()
        ctype = "image/png" if ext == ".png" else "image/jpeg"
        s3.upload_file(str(local_path), config.THUMB_BUCKET, key,
                       ExtraArgs={"ContentType": ctype})
        with _LOCK:
            _UPLOADED.add(key)
            _LISTING.setdefault(base, set()).add(name)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[images_r2] WARN upload failed {key}: {e}")
        return False
