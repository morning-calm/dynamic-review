"""
Best-effort Cloudflare R2 mirror for corrected/combined review audio clips.

Every corrected clip produced by combine, import-mp3 or fallback is pushed to the
``review-audio`` bucket under ``<content_id>/<name>`` so the corrected takes sit
alongside the bulk-uploaded originals in a per-trip folder keyed by contentID.

Thread-safe lazy singleton that reuses the same ``Cloudfare_*`` env vars as
thumbs.py (the intentional misspelling is in the existing config/documentation).
Never raises — any failure logs a WARN and returns False so the caller's operation
is never disrupted.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

_LOCK = threading.Lock()
_R2_CLIENT = None
_R2_TRIED = False

BUCKET = "review-audio"


def _build_r2():
    try:
        import boto3
    except Exception as e:  # noqa: BLE001
        print(f"[review_audio] boto3 unavailable: {e}")
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
        print("[review_audio] R2 creds missing (Cloudfare_*) — "
              "corrected audio will not upload to R2")
        return None
    return boto3.client("s3", endpoint_url=ep,
                        aws_access_key_id=ak, aws_secret_access_key=sk)


def _r2():
    """Cached R2/S3 client from Cloudfare_* (.env) creds, or None if unavailable."""
    global _R2_CLIENT, _R2_TRIED
    if not _R2_TRIED:
        with _LOCK:
            if not _R2_TRIED:
                _R2_CLIENT = _build_r2()
                _R2_TRIED = True
    return _R2_CLIENT


def upload(content_id: str, local_path: "str | Path", name: str) -> bool:
    """Upload *local_path* to ``review-audio/<content_id>/<name>``.

    Returns True on success. On any error: logs a WARN and returns False.
    Never raises.
    """
    try:
        s3 = _r2()
        if s3 is None:
            return False
        key = f"{content_id}/{name}"
        s3.upload_file(str(local_path), BUCKET, key,
                       ExtraArgs={"ContentType": "audio/mpeg"})
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[review_audio] WARN upload failed {content_id}/{name}: {e}")
        return False


def download_dir(content_id: str, dest_dir: "str | Path") -> bool:
    """Seed-time fallback for hosts with no local master audio (the Ubuntu
    server, per docs/server-migration.md Phase 2): pull every object under
    ``review-audio/<content_id>/`` down into *dest_dir*, flattened (R2 keys are
    already flat — ``<content_id>/<file>.mp3``). Skips takes that aren't plain
    numbered scene masters (``versions/``, ``_fallback``) has no bearing here —
    those keys don't exist for a trip that was only ever bulk-uploaded.

    Returns True if at least one file was downloaded. Never raises; any error
    logs a WARN and returns False so callers keep their existing empty-dir
    handling as the ultimate fallback.
    """
    try:
        s3 = _r2()
        if s3 is None:
            return False
        prefix = f"{content_id}/"
        dest = Path(dest_dir)
        got_any = False
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                name = key[len(prefix):]
                if not name or "/" in name:
                    continue  # skip versions/ subfolders — masters only
                dest.mkdir(parents=True, exist_ok=True)
                s3.download_file(BUCKET, key, str(dest / name))
                got_any = True
        return got_any
    except Exception as e:  # noqa: BLE001
        print(f"[review_audio] WARN download_dir failed {content_id}: {e}")
        return False
