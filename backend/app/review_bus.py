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
import os
import re
import secrets
import threading
import time

from fastapi import HTTPException

from .review_audio import BUCKET, _r2

JOBS_PREFIX = "_bus/jobs/"
SNAPSHOT_PREFIX = "_bus/prod-snapshot/"

# The completed-trips handshake with Stage 9 (Scripts repo). This used to be ONLY a
# local file next to trips_to_review.json — which silently stopped working the day the
# server moved to the Ubuntu laptop: the app kept rewriting the file on the laptop while
# Stage 9 kept reading a Windows copy last touched 2026-07-08, so every approval since
# the migration was invisible to it. Same bus, same fix as the publish jobs above: put
# the snapshot on R2, where both machines can reach it even when the laptop is asleep.
#
# CURRENT-STATE snapshot, not a log — a plain overwrite of one key. A trip VANISHING
# from the payload is how "un-completed" is expressed, so every completion event
# (approve / manual-complete / un-complete) must re-put it.
COMPLETED_KEY = "_bus/completed_trips.json"

# The reverse handshake: Stage 9 publishes its finalise ledger here at the end of every
# finalise run that actually uploaded. The app is STRICTLY READ-ONLY on this key (mirror
# of the rule that Scripts never writes COMPLETED_KEY). Same current-state semantics: a
# trip vanishing means "no longer considered finalised", newest generated_at is truth.
FINALISED_KEY = "_bus/finalised_trips.json"
STATIC4K_KEY = "_bus/static4k.json"

# Short-TTL cache for the two read-only bus snapshots. They were fetched from R2 on
# EVERY Completed-page and Releases-board load (a fixed ~100-400 ms floor each), and
# neither changes more than a few times a day. Completion/publish events call
# invalidate_snapshots() so the pages still flip promptly after an in-app action; a
# Scripts-side write (a finalise run finishing) is visible within the TTL. On a fetch
# failure the last good snapshot is served (previously: {}), which is strictly better —
# the restale/shipped badges shouldn't all vanish on one R2 hiccup.
_SNAP_TTL_S = float(os.environ.get("REVIEW_BUS_SNAPSHOT_TTL", "30"))
_snap_lock = threading.Lock()
_snap_cache: dict[str, dict] = {}   # key -> {"at": epoch, "value": …}


def invalidate_snapshots() -> None:
    """Expire the snapshot cache so the next read re-fetches from R2 immediately.
    Call after any event that changes what the snapshots mean for the UI
    (approve / un-complete / manual complete / a publish or tool job applying).
    Entries are marked stale rather than deleted — the last good value must
    survive as the serve-on-failure fallback."""
    with _snap_lock:
        for ent in _snap_cache.values():
            ent["at"] = 0.0


def _cached_snapshot(key: str, fetch) -> dict:
    """TTL + serve-stale-on-failure wrapper. `fetch` returns the parsed value or
    raises; a raise (or R2 being unavailable) serves the previous value."""
    with _snap_lock:
        ent = _snap_cache.get(key)
        if ent and time.time() - ent["at"] < _SNAP_TTL_S:
            return ent["value"]
    try:
        value = fetch()
    except Exception as e:  # noqa: BLE001 — NoSuchKey expected pre-first-write
        if "NoSuchKey" not in type(e).__name__ and "NoSuchKey" not in str(e):
            print(f"[bus-snapshot] read failed ({BUCKET}/{key}): {e} — "
                  "serving the previous snapshot")
            with _snap_lock:
                ent = _snap_cache.get(key)
                return ent["value"] if ent else {}
        value = {}
    with _snap_lock:
        _snap_cache[key] = {"at": time.time(), "value": value}
    return value


def get_static4k_snapshot() -> dict[str, dict]:
    """The 4K-assets ledger written by Scripts' static_pic_4k_s3.py (gap/build):
    {cid: {built_at, scenes}} = every isStaticImage scene has its -4k.jpg on S3.
    Best-effort like the finalised snapshot — {} on any trouble. TTL-cached
    (_SNAP_TTL_S) with serve-stale-on-failure."""
    def fetch() -> dict:
        s3 = _r2()
        if s3 is None:
            return {}
        obj = s3.get_object(Bucket=BUCKET, Key=STATIC4K_KEY)
        data = json.loads(obj["Body"].read().decode("utf-8"))
        return data if isinstance(data, dict) else {}
    return _cached_snapshot(STATIC4K_KEY, fetch)

# Trip ids are Firestore doc ids like Taipei101_HSK12_ZH — enforce that shape at queue
# time so a job never carries an id that breaks the key layout or (defence in depth)
# could be mistaken for a CLI flag by the workstation executors.
# Real content ids carry dots, spaces, commas and apostrophes
# ("A._A. Milne and Rudyard Kipling_A12_EN", "Hyde_Park, Holland Park Richmond
# Park_A12_EN", "Kent_Coastline II_A12_EN") — the old [A-Za-z0-9_-] set 422'd
# every such trip's Ready-to-publish (found by dave on Milne, 2026-08-22).
# Still anchored on an alphanumeric (an argv can never look like a flag).
_TRIP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-.,' ]{0,149}$")


def _client():
    s3 = _r2()
    if s3 is None:
        raise HTTPException(502, detail={
            "error": "bus_unavailable",
            "detail": "R2 credentials unavailable — cannot reach the review bus"})
    return s3


def put_completed_snapshot(payload: dict) -> bool:
    """Mirror the completed-trips snapshot to ``review-audio/_bus/completed_trips.json``.

    BEST-EFFORT, deliberately unlike the publish jobs in this module: those are an
    explicit admin action, so an R2 failure is theirs to see as an HTTP error. This
    fires as a side effect of a reviewer's approve, and the approve is the thing that
    matters — a mirror failure must never fail it. So it logs loudly and returns False.

    The lag that a swallowed failure could hide is detectable downstream instead:
    ``payload["generated_at"]`` is the snapshot's own timestamp, and the consumer warns
    when it goes stale."""
    try:
        s3 = _r2()
        if s3 is None:
            print("[completed-export] !! R2 unavailable (no Cloudfare_* creds) — wrote "
                  "the LOCAL file only. Stage 9 on another machine will NOT see this "
                  "until the mirror is repaired: py -3.12 scripts/export_completed.py")
            return False
        s3.put_object(
            Bucket=BUCKET, Key=COMPLETED_KEY,
            Body=json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json")
        return True
    except Exception as e:  # noqa: BLE001 — see docstring: never fail the caller's op
        print(f"[completed-export] !! R2 mirror FAILED ({BUCKET}/{COMPLETED_KEY}): {e} "
              "— the local file is current but Stage 9 on another machine is now STALE. "
              "Repair with: py -3.12 scripts/export_completed.py")
        return False


def get_finalised_snapshot() -> dict[str, dict]:
    """The Stage-9 finalised-trips snapshot, keyed by trip_id. BEST-EFFORT and
    READ-ONLY: any failure (no creds, NoSuchKey before Scripts first publishes,
    network) degrades to the last good value ({} before the first success) — it must
    never break the completed-list load, exactly like put_completed_snapshot never
    fails an approve. TTL-cached (_SNAP_TTL_S); completion events call
    invalidate_snapshots() so an in-app action still reflects promptly."""
    def fetch() -> dict:
        s3 = _r2()
        if s3 is None:
            return {}
        obj = s3.get_object(Bucket=BUCKET, Key=FINALISED_KEY)
        data = json.loads(obj["Body"].read().decode("utf-8"))
        return {t["trip_id"]: t for t in data.get("trips", [])
                if isinstance(t, dict) and t.get("trip_id")}
    return _cached_snapshot(FINALISED_KEY, fetch)


def _job_key(job_id: str) -> str:
    return f"{JOBS_PREFIX}{job_id}.json"


# Job kinds the bus accepts (docs/post-approval-admin-spec.md §4.1). trip_id carries
# the kind's TARGET id: trip cid (publish/publish_docs), TripGroup id
# (add_to_location), TripLocation id (publish_pin).
JOB_KINDS = ("publish", "publish_docs", "publish_pin", "add_to_location",
             "thumbnail_local_copy", "replace_overlay", "publish_credits",
             "trello_move", "tool")


def queue_job(kind: str, trip_id: str, user, note: str = "") -> dict:
    if kind not in JOB_KINDS:
        raise HTTPException(422, detail={"error": "bad_kind", "detail": kind})
    if not _TRIP_ID_RE.fullmatch(trip_id or ""):
        raise HTTPException(422, detail={"error": "bad_trip_id", "detail": trip_id})
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
    invalidate_jobs_cache()
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
    invalidate_jobs_cache()
    return job


# Short-TTL cache of the UNFILTERED newest-jobs listing (a full list is up to
# fetch_cap serial GETs — the Publisher inbox AND the Releases board's inline job
# chips both read it per page load). queue_job/update_job invalidate, so an admin's
# own action shows instantly; a workstation executor's status flip lands within TTL.
_JOBS_TTL_S = float(os.environ.get("REVIEW_BUS_JOBS_TTL", "15"))
_jobs_cache: dict = {"at": 0.0, "jobs": None}


def invalidate_jobs_cache() -> None:
    with _snap_lock:
        _jobs_cache["jobs"] = None
        _jobs_cache["at"] = 0.0


def list_jobs(trip_id: str | None = None, limit: int = 100,
              fetch_cap: int = 300) -> list[dict]:
    """Newest jobs on the bus (optionally one trip's). Job ids start with a UTC-ish
    timestamp, so KEYS sort chronologically — list the keys (cheap), walk them newest
    first, and stop at `limit` matches / `fetch_cap` object GETs. Older jobs than the
    cap can horizon out of a trip-filtered view; the bus is an inbox, not an archive.

    The default-shaped listing (limit=100/fetch_cap=300) is served from a short TTL
    cache and trip filters apply in memory — see _JOBS_TTL_S above. A trip-filtered
    call CAN see fewer old jobs than the uncached walk did (the cache holds the
    newest 100 overall, the old walk kept GETting past non-matches up to 300) — an
    accepted trade at current volumes; the inbox horizon note above already applies."""
    if limit == 100 and fetch_cap == 300:
        with _snap_lock:
            cached = _jobs_cache["jobs"]
            fresh = time.time() - _jobs_cache["at"] < _JOBS_TTL_S
        if cached is not None and fresh:
            return [j for j in cached if j.get("trip_id") == trip_id] \
                if trip_id else list(cached)
        jobs = _list_jobs_uncached(None, limit, fetch_cap)
        with _snap_lock:
            _jobs_cache["jobs"] = jobs
            _jobs_cache["at"] = time.time()
        return [j for j in jobs if j.get("trip_id") == trip_id] if trip_id else list(jobs)
    return _list_jobs_uncached(trip_id, limit, fetch_cap)


def _list_jobs_uncached(trip_id: str | None, limit: int, fetch_cap: int) -> list[dict]:
    s3 = _client()
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=JOBS_PREFIX):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    out: list[dict] = []
    for key in sorted(keys, reverse=True)[:fetch_cap]:
        if len(out) >= limit:
            break
        try:
            body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
            job = json.loads(body.decode("utf-8"))
        except Exception:  # noqa: BLE001 — skip a malformed object, don't fail the list
            continue
        if trip_id and job.get("trip_id") != trip_id:
            continue
        out.append(job)
    out.sort(key=lambda j: j.get("requested_at") or 0, reverse=True)
    return out


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
