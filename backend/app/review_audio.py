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
from concurrent.futures import ThreadPoolExecutor
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


# --------------------------------------------------------------------------- #
# Archiving the delivered master
# --------------------------------------------------------------------------- #
# ``review-audio/<cid>/<i>.mp3`` is the trip's LATEST ACCEPTED audio, and on a host with
# no local master trees (the live Ubuntu laptop, for most trips) it is the only durable
# home that audio has: the promote step there writes into an ephemeral seed cache, so
# what a reviewer corrects reaches R2 via this key or not at all. It therefore gets
# overwritten by every combine/import — by design, and it must keep doing so.
#
# What was missing is an archive of what was there FIRST. Once a review touched scene 3,
# the as-delivered 3.mp3 existed nowhere on R2, so there was no way back if a review went
# wrong or a take was corrupted. So the first time the app is about to overwrite a
# master, it copies the existing object aside to ``<cid>/originals/<i>.mp3`` — once,
# idempotently, and never again (a second copy would capture an EDIT as the "original").
#
# ⚠ This is an ARCHIVE, not a seed source. `download_dir` deliberately does NOT prefer
# it — seeding a re-review from the pre-review master would throw away the corrections,
# which are the expensive artefact (a master can be regenerated; a confirmed take is
# hours of human work). See the warning on download_dir.
ORIGINALS_PREFIX = "originals/"


def preserve_original(content_id: str, name: str) -> None:
    """Archive ``<cid>/<name>`` to ``<cid>/originals/<name>`` before it is first
    overwritten, so the as-delivered master stays recoverable.

    Writes ONLY the originals/ key — never touches the canonical one, so corrected audio
    is never at risk. No-op when already archived (the existing copy is by definition
    closer to the delivered master than anything we could put over it), when there is
    nothing there yet, or when R2 is unreachable. Never raises."""
    try:
        s3 = _r2()
        if s3 is None:
            return
        src = f"{content_id}/{name}"
        dst = f"{content_id}/{ORIGINALS_PREFIX}{name}"
        try:
            s3.head_object(Bucket=BUCKET, Key=dst)
            return                       # already preserved — never overwrite it
        except Exception:                # noqa: BLE001 — 404 is the expected path
            pass
        try:
            s3.head_object(Bucket=BUCKET, Key=src)
        except Exception:                # noqa: BLE001
            return                       # nothing to preserve (no bulk-uploaded master)
        s3.copy_object(Bucket=BUCKET, Key=dst,
                       CopySource={"Bucket": BUCKET, "Key": src})
        print(f"[review_audio] preserved pristine master -> {BUCKET}/{dst}")
    except Exception as e:  # noqa: BLE001 — best-effort, never break a review op
        print(f"[review_audio] WARN could not preserve original {content_id}/{name}: {e}")


# --------------------------------------------------------------------------- #
# Off-thread mirroring
# --------------------------------------------------------------------------- #
# Nothing in a review READS the mirror back mid-session (the player streams from the
# backend), so the upload need not be on the request's critical path. Doing it inline
# made every audio edit — each pause tweak, each combine — block on a network round-trip
# before the reviewer's request returned. ONE worker, so uploads still run in submission
# order: when two edits overwrite the same canonical key, the later bytes still land
# last, and a preserve always precedes the overwrite it guards.
_POOL: "ThreadPoolExecutor | None" = None


def _pool():
    global _POOL
    if _POOL is None:
        with _LOCK:
            if _POOL is None:
                _POOL = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="r2-review-audio")
    return _POOL


def _submit(fn, *args) -> None:
    try:
        _pool().submit(fn, *args)
    except Exception as e:  # noqa: BLE001
        print(f"[review_audio] WARN could not queue {getattr(fn, '__name__', fn)}: {e}")


def upload_async(content_id: str, local_path: "str | Path", name: str) -> None:
    """Queue an upload and return immediately. Never raises (a failed/rejected queue is
    logged and dropped — the mirror is best-effort, the review op is what matters)."""
    _submit(upload, content_id, str(local_path), name)


def upload_master_async(content_id: str, local_path: "str | Path", name: str) -> None:
    """Overwrite a canonical master (``<cid>/<i>.mp3``) with a corrected take, archiving
    the as-delivered one first. Use this — never ``upload_async`` — for that key."""
    _submit(preserve_original, content_id, name)
    _submit(upload, content_id, str(local_path), name)


def upload_master(content_id: str, local_path: "str | Path", name: str) -> bool:
    """The SYNCHRONOUS form, for approve/promotion.

    Corrected audio is the expensive artefact — hours of human work, versus a master the
    pipeline can regenerate on demand — and on a masters-less host (the live laptop) this
    R2 key is its only durable home: promotion there writes into an ephemeral seed cache.
    So at the one moment the correction becomes the deliverable, the mirror must not be
    in a queue that a restart could drop. Approve is an admin op measured in seconds; it
    can afford the round-trip. Best-effort still (it must never fail an approve), but
    LOUD, and by then it has already been retried by every combine along the way."""
    preserve_original(content_id, name)
    return upload(content_id, local_path, name)


def shutdown(wait: bool = True) -> None:
    """Drain queued mirror uploads on a clean shutdown, so a `systemctl restart` right
    after a reviewer's combine can't strand that correction outside R2."""
    global _POOL
    with _LOCK:
        pool, _POOL = _POOL, None
    if pool is not None:
        pool.shutdown(wait=wait)


def download_file(content_id: str, name: str, dest_path: "str | Path") -> bool:
    """Pull ONE object (``review-audio/<content_id>/<name>``) down to *dest_path*.

    The delta-review seed uses this instead of download_dir: a delta's clips must come
    FRESH from R2 (the remediation's authoritative home) every time — the seed-cache
    short-circuit in resolve_audio_dir would hand back pre-remediation takes. Returns
    False (WARN, never raises) when the object is absent or R2 is unreachable.
    """
    try:
        s3 = _r2()
        if s3 is None:
            return False
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(BUCKET, f"{content_id}/{name}", str(dest))
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[review_audio] WARN download_file failed {content_id}/{name}: {e}")
        return False


def download_dir(content_id: str, dest_dir: "str | Path") -> bool:
    """Seed-time fallback for hosts with no local master audio (the Ubuntu
    server, per docs/server-migration.md Phase 2): pull every object directly under
    ``review-audio/<content_id>/`` down into *dest_dir*, flattened (those keys are
    already flat — ``<content_id>/<file>.mp3``). Sub-prefixes (``originals/``) are
    skipped — see below, this is deliberate.

    ⚠ SEEDS FROM THE CANONICAL KEY, WHICH MAY HOLD CORRECTED AUDIO — AND MUST.
    It is tempting to seed the "original" from ``originals/`` (the pre-review master
    that `preserve_original` sets aside). Do NOT. On a masters-less host the promote
    step writes only into an ephemeral seed cache, so this canonical key is the ONLY
    durable home of a trip's CORRECTED audio. Seeding a re-review from ``originals/``
    would hand back the pre-review master and quietly discard the human corrections —
    and corrections are the expensive artefact here. A master can always be
    regenerated by the pipeline; a confirmed take cannot.

    So the canonical key always means "the latest ACCEPTED audio for this trip", which
    is exactly what a fresh review should start from. ``originals/`` is an archive for
    recovery ("give me back the delivered master"), never a seed source.

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
                    continue  # skip originals/ + versions/ subfolders — masters only
                dest.mkdir(parents=True, exist_ok=True)
                s3.download_file(BUCKET, key, str(dest / name))
                got_any = True
        return got_any
    except Exception as e:  # noqa: BLE001
        print(f"[review_audio] WARN download_dir failed {content_id}: {e}")
        return False
