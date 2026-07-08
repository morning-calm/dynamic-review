"""R2 ``review-bus`` — the cross-machine publish handshake (WS4 phase 4).

Production credentials live ONLY on the workstation, so the laptop-hosted app can
never publish directly. Instead it writes small JOB objects to the ``review-audio``
bucket under ``_bus/jobs/`` (the same one-way-file-bus idiom as completed_trips.json,
made cross-machine via R2). The workstation consumes them two ways, both deliberate
human acts: the review-app running in PUBLISHER MODE (env ``REVIEW_APP_PUBLISHER=1``),
or the ``publish_inbox.py`` CLI in the Scripts repo. Executors run
``publish_trip_text.py`` (dry-run by default; a real write needs its own
``--apply --i-am-sure`` gates) and write the result back onto the job object.

Job shape: {id, kind:'publish', trip_id, note, requested_by, requested_at,
status: queued|dry_run|done|failed, resolved_by?, resolved_at?, log?}.

Unlike the audio mirror this is NOT silent-best-effort — queueing a publish is an
explicit admin action, so R2 failures surface as HTTP errors."""

from __future__ import annotations

import json
import secrets
import time

from fastapi import HTTPException

from .review_audio import BUCKET, _r2

JOBS_PREFIX = "_bus/jobs/"
SNAPSHOT_PREFIX = "_bus/prod-snapshot/"


def _client():
    s3 = _r2()
    if s3 is None:
        raise HTTPException(502, detail={
            "error": "bus_unavailable",
            "detail": "R2 credentials unavailable — cannot reach the review bus"})
    return s3


def _job_key(job_id: str) -> str:
    return f"{JOBS_PREFIX}{job_id}.json"


def queue_job(kind: str, trip_id: str, user, note: str = "") -> dict:
    if kind != "publish":
        raise HTTPException(422, detail={"error": "bad_kind", "detail": kind})
    job = {
        "id": f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}",
        "kind": kind,
        "trip_id": trip_id,
        "note": note or "",
        "requested_by": getattr(user, "username", None) or "",
        "requested_at": time.time(),
        "status": "queued",
    }
    _client().put_object(Bucket=BUCKET, Key=_job_key(job["id"]),
                         Body=json.dumps(job, ensure_ascii=False).encode("utf-8"),
                         ContentType="application/json")
    return job


def get_job(job_id: str) -> dict:
    try:
        obj = _client().get_object(Bucket=BUCKET, Key=_job_key(job_id))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(404, detail={"error": "no_job", "detail": job_id})
    return json.loads(obj["Body"].read().decode("utf-8"))


def update_job(job_id: str, **patch) -> dict:
    job = get_job(job_id)
    job.update(patch)
    _client().put_object(Bucket=BUCKET, Key=_job_key(job_id),
                         Body=json.dumps(job, ensure_ascii=False).encode("utf-8"),
                         ContentType="application/json")
    return job


def list_jobs(trip_id: str | None = None, limit: int = 100) -> list[dict]:
    s3 = _client()
    out: list[dict] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=JOBS_PREFIX):
        for obj in page.get("Contents", []):
            try:
                body = s3.get_object(Bucket=BUCKET, Key=obj["Key"])["Body"].read()
                job = json.loads(body.decode("utf-8"))
            except Exception:  # noqa: BLE001 — skip a malformed object, don't fail the list
                continue
            if trip_id and job.get("trip_id") != trip_id:
                continue
            out.append(job)
    out.sort(key=lambda j: j.get("requested_at") or 0, reverse=True)
    return out[:limit]


def prod_snapshot(trip_id: str) -> dict | None:
    """The workstation-exported production snapshot for a trip (written by
    ``publish_inbox.py snapshot`` — the laptop never holds prod creds; this is how it
    sees live state for the drift indicator). None when no snapshot exists yet."""
    try:
        obj = _client().get_object(Bucket=BUCKET,
                                   Key=f"{SNAPSHOT_PREFIX}{trip_id}.json")
        return json.loads(obj["Body"].read().decode("utf-8"))
    except HTTPException:
        raise
    except Exception:
        return None
