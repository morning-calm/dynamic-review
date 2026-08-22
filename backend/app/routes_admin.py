"""Admin-only staging-wide endpoints (WS4 phase 1 — the Firefoo replacement's entry
point): search/open ANY staging trip, bypassing the Trello manifest and the
completed-trips exclusion that gate the normal reviewer flow.

Read/open only — every write still goes through the session editor + approve
(targeted single-`.update()` discipline in sessions.commit). STAGING ONLY."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import audio_core, auth, db, review_bus, sessions, staging, structure
from .config import SCRIPTS_ROOT
from .models import (CreateSession, QueueJob, RunJob, StructureAdd,
                     StructureCategories, StructureRemove, StructureReorder,
                     StructureStaticImages, StructureSwapVideo)

router = APIRouter(prefix="/api/admin")

# Publisher mode: the WORKSTATION instance of the app (the one machine holding the
# production key). The laptop deploy must never set this — it only queues/views jobs.
PUBLISHER_MODE = os.environ.get("REVIEW_APP_PUBLISHER") == "1"

# One light Firestore sweep of Trips ids + display fields, cached: search-as-you-type
# must not re-stream ~900 docs per keystroke. Refresh after TTL or on ?refresh=1.
_INDEX_TTL_SECONDS = 600
_index_lock = threading.Lock()
_index: dict = {"at": 0.0, "rows": []}


def _staging_index(force: bool = False) -> list[dict]:
    with _index_lock:
        if not force and _index["rows"] and time.time() - _index["at"] < _INDEX_TTL_SECONDS:
            return _index["rows"]
        from .staging import db as fb_db   # lazy: Firestore init on first use
        fs = fb_db()

        # TripLocations: locationName/locationCountry keyed by the TripGroup ids it
        # lists. Some TGs appear in SEVERAL TripLocations (a city AND a theme location,
        # e.g. London + "UK Theme") — keep ALL pairs, deduped, in encounter order.
        # Stale-duplicate guard: when several docs share the same effective
        # locationName AND country, only the one with the most trips is authoritative
        # (staging has a leftover 'JapaneseTrips' doc duplicating JPHistory's
        # "Discover_Past_Series" name but listing the Spanish 'Cuevas_Trip' — it would
        # mislabel Cuevas as Japan). Keyed on (name, country), NOT name alone: the
        # three Alps docs legitimately share a name across Germany/France/Italy and
        # must all survive even if their trip counts diverge.
        loc_docs: list[tuple[str, str, list]] = []   # (name, country, tg_ids)
        biggest: dict[tuple[str, str], int] = {}     # (name, country) -> max n_trips
        for snap in fs.collection("TripLocations").stream():
            d = snap.to_dict() or {}
            name = d.get("locationName") or snap.id
            country = d.get("locationCountry") or ""
            tg_ids = d.get("trips") or []
            loc_docs.append((name, country, tg_ids))
            biggest[(name, country)] = max(biggest.get((name, country), 0), len(tg_ids))
        # tg_id -> [(location_name, country), …]
        tg_to_locs: dict[str, list[tuple[str, str]]] = {}
        for name, country, tg_ids in loc_docs:
            if len(tg_ids) < biggest[(name, country)]:
                continue   # stale duplicate of a bigger same-name+country location
            loc = (name, country)
            for tg_id in tg_ids:
                if isinstance(tg_id, str) and tg_id:
                    pairs = tg_to_locs.setdefault(tg_id, [])
                    if loc not in pairs:
                        pairs.append(loc)

        # TripGroups: trips[].tripId -> the TripGroup's (location, country) pairs.
        trip_to_locs: dict[str, list[tuple[str, str]]] = {}
        for snap in fs.collection("TripGroups").select(["trips"]).stream():
            locs = tg_to_locs.get(snap.id)
            if not locs:
                continue
            d = snap.to_dict() or {}
            for entry in (d.get("trips") or []):
                if isinstance(entry, dict):
                    tid = entry.get("tripId")
                elif isinstance(entry, str):
                    tid = entry
                else:
                    tid = None
                if tid:
                    pairs = trip_to_locs.setdefault(tid, [])
                    for loc in locs:
                        if loc not in pairs:
                            pairs.append(loc)

        def _dedup(vals) -> list[str]:
            return list(dict.fromkeys(v for v in vals if v))

        rows: list[dict] = []
        for snap in fs.collection("Trips").select(
                ["contentTitleKey", "folderName"]).stream():
            d = snap.to_dict() or {}
            pairs = trip_to_locs.get(snap.id, [])
            locations = _dedup(p[0] for p in pairs)
            countries = _dedup(p[1] for p in pairs)
            rows.append({
                "trip_id": snap.id,
                "title": d.get("contentTitleKey") or snap.id,
                "folder_name": d.get("folderName") or "",
                "locations": locations,     # all values, for any-match filtering
                "countries": countries,
                # ", "-joined display strings (the FE row meta shows these as-is)
                "location": ", ".join(locations),
                "country": ", ".join(countries),
            })
        rows.sort(key=lambda r: r["trip_id"].lower())
        _index["rows"] = rows
        _index["at"] = time.time()
        return rows


@router.get("/staging-trips")
def staging_trips(q: str = "", location: str = "", country: str = "", refresh: int = 0,
                  admin=Depends(auth.require_admin)):
    """Search the WHOLE staging Trips collection by id/title substring (admin only).
    Every row also says where the trip sits in the review workflow, so the admin can
    see at a glance whether opening it resumes a session or seeds a fresh one.
    `location`/`country` are independent case-insensitive filters matching ANY of a
    row's `locations`/`countries` (a trip can sit in several TripLocations)."""
    full = _staging_index(force=bool(refresh))
    locations = sorted({l for r in full for l in r["locations"]}, key=str.lower)
    countries = sorted({c for r in full for c in r["countries"]}, key=str.lower)

    rows = full
    needle = (q or "").strip().lower()
    if needle:
        rows = [r for r in rows
                if needle in r["trip_id"].lower() or needle in r["title"].lower()]
    loc_needle = (location or "").strip().lower()
    if loc_needle:
        rows = [r for r in rows
                if any(l.lower() == loc_needle for l in r["locations"])]
    country_needle = (country or "").strip().lower()
    if country_needle:
        rows = [r for r in rows
                if any(c.lower() == country_needle for c in r["countries"])]

    out: list[dict] = []
    for r in rows[:200]:   # hard cap — the FE asks for narrower queries, not paging
        tid = r["trip_id"]
        has_session, status, edit_required = sessions._session_meta(tid)
        crow = db.query_one(
            "SELECT method, completed_by FROM completed_trips WHERE trip_id=?", (tid,))
        out.append({
            **r,
            "language": audio_core.language_of(tid),
            "has_session": has_session,
            "status": status,
            "edit_required": edit_required,
            "completed_method": crow["method"] if crow else None,
            "completed_by": crow["completed_by"] if crow else None,
        })
    return {"total": len(rows), "shown": len(out), "trips": out,
            "locations": locations, "countries": countries}


@router.post("/open")
def open_staging_trip(body: CreateSession, admin=Depends(auth.require_admin)):
    """Open (create or resume) a session for ANY staging trip — including a COMPLETED
    one (the reviewer flow 409s there; the admin editor is exactly for post-completion
    fixes). Unresolvable MP3 masters no longer 422: the session seeds TEXT-ONLY and
    the FE shows a soft `audio_unavailable` warning (audio tools disabled per-field)."""
    return sessions.create_or_resume(body.trip_id, admin, allow_completed=True,
                                     allow_no_audio=True)


# --------------------------------------------------------------------------- #
# Scene-structure editor (WS4 phases 2–3): direct STAGING writes, admin-only,
# refused (409 active_session) while any active session exists on the trip.
# --------------------------------------------------------------------------- #
@router.get("/structure/{trip_id}")
def get_structure(trip_id: str, admin=Depends(auth.require_admin)):
    return structure.get_structure(trip_id)


@router.post("/structure/{trip_id}/reorder")
def post_structure_reorder(trip_id: str, body: StructureReorder,
                           admin=Depends(auth.require_admin)):
    return structure.reorder(trip_id, body.order, body.base, admin)


@router.post("/structure/{trip_id}/remove")
def post_structure_remove(trip_id: str, body: StructureRemove,
                          admin=Depends(auth.require_admin)):
    return structure.remove(trip_id, body.index, body.base, admin)


@router.post("/structure/{trip_id}/add")
def post_structure_add(trip_id: str, body: StructureAdd,
                       admin=Depends(auth.require_admin)):
    return structure.add(trip_id, body.position, body.base, admin,
                         video_url=body.video_url, is_static=body.is_static,
                         scene_id=body.scene_id)


@router.post("/structure/{trip_id}/swap-video")
def post_structure_swap_video(trip_id: str, body: StructureSwapVideo,
                              admin=Depends(auth.require_admin)):
    return structure.swap_video(trip_id, body.index, body.video_url, body.rekey,
                                body.base, admin, scene_id=body.scene_id)


@router.post("/structure/{trip_id}/static-images")
def post_structure_static_images(trip_id: str, body: StructureStaticImages,
                                 admin=Depends(auth.require_admin)):
    return structure.set_static_images(trip_id, body.index, body.filenames,
                                       body.base, admin)


@router.post("/structure/{trip_id}/categories")
def post_structure_categories(trip_id: str, body: StructureCategories,
                              admin=Depends(auth.require_admin)):
    return structure.set_categories(trip_id, body.categories, admin)


@router.get("/enrichment-categories/{trip_id}")
def enrichment_categories(trip_id: str, admin=Depends(auth.require_admin)):
    """Content-enrichment category proposals for a trip (staging ContentEnrichment
    sidecar) — one-tap add suggestions for the review-page category editor."""
    return staging.get_enrichment_categories(trip_id)


# --------------------------------------------------------------------------- #
# Pipeline: R2 review-bus publish handshake (WS4 phase 4)
# --------------------------------------------------------------------------- #
@router.post("/pipeline/queue")
def queue_pipeline_job(body: QueueJob, admin=Depends(auth.require_admin)):
    """Queue a staging→production job (any client-queueable kind — see models.QueueJob;
    `trip_id` carries the kind's target id). Writes a job object to the R2 bus —
    nothing executes until a human runs it on the workstation (publisher mode below,
    or `publish_inbox.py` in the Scripts repo)."""
    return review_bus.queue_job(body.kind, body.trip_id, admin, body.note)


@router.get("/pipeline/jobs")
def pipeline_jobs(trip_id: str = "", admin=Depends(auth.require_admin)):
    return {"publisher_mode": PUBLISHER_MODE,
            "jobs": review_bus.list_jobs(trip_id or None)}


@router.get("/drift/{trip_id}")
def drift(trip_id: str, admin=Depends(auth.require_admin)):
    """Staging vs production DISPLAY-TEXT drift for one trip, diffed against the
    workstation-exported prod snapshot on the bus (`publish_inbox.py snapshot`).
    The laptop never reads production directly — no prod credential of any kind."""
    from .staging import get_trip
    snap = review_bus.prod_snapshot(trip_id)
    if snap is None:
        return {"trip_id": trip_id, "snapshot_at": None, "fields_differ": None}
    try:
        stg = get_trip(trip_id)
    except SystemExit as e:
        raise HTTPException(404, detail={"error": "no_staging_trip", "detail": str(e)})
    prod = snap.get("trip") or {}
    differ: list[str] = []
    for f in ("contentTitleKey", "descriptionTarget", "descriptionHome"):
        if (stg.get(f) or "") != (prod.get(f) or ""):
            differ.append(f)
    s_scenes = stg.get("quickTrips") or []
    p_scenes = prod.get("quickTrips") or []
    if len(s_scenes) != len(p_scenes):
        differ.append(f"scene_count ({len(p_scenes)} live vs {len(s_scenes)} staging)")
    else:
        for i, (ss, ps) in enumerate(zip(s_scenes, p_scenes)):
            for f in ("titleKey", "titleKeyEn", "SceneDesc", "SceneDescEn",
                      "questionKey", "questionKeyEn", "questionOptionKeys",
                      "questionOptionKeysEn"):
                if (ss or {}).get(f) != (ps or {}).get(f):
                    differ.append(f"quickTrips[{i}].{f}")
    return {"trip_id": trip_id, "snapshot_at": snap.get("fetched_at"),
            "fields_differ": differ}


@router.post("/pipeline/run")
def run_pipeline_job(body: RunJob, admin=Depends(auth.require_admin)):
    """PUBLISHER MODE ONLY (403 otherwise — the laptop can never execute): run a queued
    publish job via the Scripts repo's `publish_trip_text.py`. Default is a DRY RUN
    (field-level diff, no write); a real write requires apply+i_am_sure here AND rides
    that script's own `--apply --i-am-sure` gates with the workstation-local prod key."""
    if not PUBLISHER_MODE:
        raise HTTPException(403, detail={
            "error": "not_publisher",
            "detail": "this instance is not running in publisher mode "
                      "(REVIEW_APP_PUBLISHER=1 on the workstation only)"})
    job = review_bus.get_job(body.job_id)
    kind = job.get("kind")
    target = str(job.get("trip_id") or "")
    if not target or target.startswith("-"):
        # Defence in depth (queue_job validates too): never pass an argv that argparse
        # could read as a flag on a production-writing script.
        raise HTTPException(422, detail={"error": "bad_trip_id", "detail": target})
    apply_write = bool(body.apply and body.i_am_sure)
    # Per-kind command lines (docs/post-approval-admin-spec.md §4). The console never
    # reimplements a script — it only sequences them; every path is dry-run-default
    # and the real write rides each script's own --apply --i-am-sure gates.
    if kind == "publish":
        script, argv, timeout = "publish_trip_text.py", [target], 300
    elif kind == "publish_docs":
        script, argv, timeout = "publish_trips_cli.py", ["--trip", target], 600
    elif kind == "publish_pin":
        script, argv, timeout = "publish_trips_cli.py", ["--pin", target], 300
    elif kind == "add_to_location":
        script, argv, timeout = "publish_trips_cli.py", ["--add-to-location", target], 300
    elif kind == "publish_credits":
        script, argv, timeout = "publish_trips_cli.py", ["--credits"], 300
    elif kind == "trello_move":
        # note carries "to=<lane label>", default 11 (the checklist's ready flow).
        note = dict(kv.split("=", 1) for kv in str(job.get("note") or "").split(";")
                    if "=" in kv)
        lane = (note.get("to") or "11").strip()
        if not re.fullmatch(r"\d{1,2}b?", lane):
            raise HTTPException(422, detail={"error": "bad_lane", "detail": lane})
        cmd = [sys.executable, str(SCRIPTS_ROOT / "Trello" / "trello_move.py"),
               "--to", lane, target, "--strict"]
        if not apply_write:
            cmd.append("--dry-run")
        return _finish_subprocess_job(job, cmd, 120, apply_write, admin)
    elif kind == "thumbnail_local_copy":
        # Not a Scripts shell-out: pull the R2 thumbnail into the workstation's
        # local tree (replace in place / land in _from_review_app).
        return _run_thumbnail_local_copy(job, target, apply=bool(body.apply), admin=admin)
    elif kind == "replace_overlay":
        # Download the staged R2 overlay, then stage10_static_check.py `replace`
        # does the canonical distribution: the country folders under
        # D:\Dynamic Languages + Drive + every rung's OGG folder + S3.
        return _run_replace_overlay(job, target, apply=bool(body.apply), admin=admin)
    else:
        raise HTTPException(422, detail={"error": "bad_kind", "detail": kind})
    cmd = [sys.executable, str(SCRIPTS_ROOT / script), *argv]
    if apply_write:
        cmd += ["--apply", "--i-am-sure"]
    try:
        # utf-8 explicitly: text=True alone decodes with the Windows locale codepage,
        # which garbles/raises on the scripts' CJK diff output.
        proc = subprocess.run(cmd, cwd=str(SCRIPTS_ROOT), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
        log = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        status = ("failed" if proc.returncode != 0
                  else ("done" if apply_write else "dry_run"))
        if status == "done" and kind in ("publish", "publish_docs"):
            # Release record: stamp `published=<today>` on this rung's row in the
            # family card's [review] block (one card per family — dave, 2026-08-22).
            # Best-effort: a stamp failure never fails the publish that succeeded.
            try:
                # --move-complete-to 12: the family card jumps to "Live on App"
                # only when its LAST rung gets stamped (dave, 2026-08-22).
                mk = subprocess.run(
                    [sys.executable, str(SCRIPTS_ROOT / "Trello" / "mark_published.py"),
                     "--cid", target, "--move-complete-to", "12"],
                    cwd=str(SCRIPTS_ROOT), capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=120)
                log += ("\n-- Trello [review] block --\n" + (mk.stdout or "")
                        + (("\n" + mk.stderr) if mk.stderr else ""))
            except Exception as e:  # noqa: BLE001
                log += f"\n-- Trello [review] block --\n!! mark_published failed: {e}"
            # Durable publish ledger (drives "Recently published" — the R2 job inbox
            # is capped and not an archive). Best-effort like the Trello stamp.
            try:
                db.execute(
                    "INSERT INTO published_trips(trip_id, published_at, published_by,"
                    " source, note) VALUES(?,?,?,?,?) ON CONFLICT(trip_id) DO UPDATE "
                    "SET published_at=excluded.published_at, "
                    "published_by=excluded.published_by, source=excluded.source, "
                    "note=excluded.note",
                    (target, time.time(), getattr(admin, "username", None) or "",
                     "publisher", kind))
            except Exception as e:  # noqa: BLE001
                log += f"\n!! publish ledger stamp failed: {e}"
        if status == "done":
            # Prod state just changed — the Releases board must not serve cached
            # facts about it until re-probed (same hygiene as _finish_subprocess_job).
            try:
                from . import final_checks
                final_checks.invalidate_probes([target])
                review_bus.invalidate_snapshots()
            except Exception as e:  # noqa: BLE001
                print(f"[publish] cache invalidation failed: {e}")
    except subprocess.TimeoutExpired as e:
        # Don't leave the job looking 'queued' after a hung run — surface the failure
        # on the job object itself (the bus is deliberately not silent-best-effort).
        log = ((e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes)
               else (e.stdout or ""))
        log += f"\n!! {script} timed out after {e.timeout:.0f}s"
        status = "failed"
    except OSError as e:
        log = f"!! could not launch {script}: {e}"
        status = "failed"
    return review_bus.update_job(
        body.job_id, status=status, log=log[-8000:],
        resolved_by=getattr(admin, "username", None) or "",
        resolved_at=time.time())


# The canonical local thumbnail tree (hand-organized by country/region — ~800 jpgs).
# A replaced thumbnail OVERWRITES its existing file wherever it lives in the tree;
# only a brand-new family lands in _from_review_app/ for dave to file.
THUMBNAILS_TREE = os.environ.get(
    "REVIEW_APP_THUMBNAILS_LOCAL",
    r"D:\Dynamic Languages\Images\App thumbnails")


def _find_local_thumbnail(stem: str):
    """The existing file for this stem anywhere under the App-thumbnails tree.
    Exact filename first; then upload_thumbnails_r2.py's matching rule (the part
    before ' - ' equals the stem's base, ignoring spaces/underscores/case)."""
    from pathlib import Path
    root = Path(THUMBNAILS_TREE)
    if not root.is_dir():
        return None

    def norm(s: str) -> str:
        return re.sub(r"[\s_]+", "", s or "").lower()

    base = norm(stem.split(" - ")[0])
    fallback = None
    for p in root.rglob("*.jpg"):
        if p.stem == stem:
            return p
        if fallback is None and norm(p.stem.split(" - ")[0]) == base:
            fallback = p
    return fallback


# locationCountry -> the App-thumbnails top folder (the tree's actual names).
_THUMB_COUNTRY_DIRS = {
    "greatbritain": "UK Trips", "uk": "UK Trips", "scotland": "UK Trips",
    "germany": "DE Trips", "france": "FR trips", "italy": "IT Trips",
    "japan": "JP Trips", "spain": "Spain Trips",
}


def _thumbnail_dest_dir(tg_id: str):
    """The RELEVANT local folder for a NEW family thumbnail (dave, 2026-08-21):
    country top folder from the TripLocation's locationCountry, then the regional
    subfolder matched against the trip's folderName segments + location names
    (e.g. folderName "Scotland/…" → UK Trips\\Scotland). None when nothing
    resolves — the caller falls back to _from_review_app/ for hand-filing."""
    from pathlib import Path

    from . import tripdesc
    root = Path(THUMBNAILS_TREE)
    if not root.is_dir():
        return None
    locs = tripdesc._triplocations_index().get(tg_id) or []
    segments: list[str] = [n for n, _c in locs] + [c for _n, c in locs]
    top = None
    for _n, c in locs:
        top = _THUMB_COUNTRY_DIRS.get((c or "").strip().lower())
        if top:
            break
    # folderName segments (e.g. "Scotland/Edinburgh/…") are the regional signal.
    try:
        from .staging import db as fb_db
        tg = (fb_db().collection("TripGroups").document(tg_id).get().to_dict() or {})
        first = next((t.get("tripId") if isinstance(t, dict) else t
                      for t in (tg.get("trips") or [])), None)
        if first:
            trip = (fb_db().collection("Trips").document(first).get().to_dict() or {})
            parts = [p for p in (trip.get("folderName") or "").replace("\\", "/").split("/") if p]
            segments = parts + segments
            if top is None and parts:
                top = _THUMB_COUNTRY_DIRS.get(parts[0].strip().lower())
    except Exception as e:  # noqa: BLE001 — placement is best-effort
        print(f"[thumbnail-copy] folderName lookup failed for {tg_id}: {e}")
    if top is None or not (root / top).is_dir():
        return None
    top_dir = root / top
    seg_lower = {s.strip().lower() for s in segments if s and s.strip()}
    for sub in sorted(p for p in top_dir.iterdir() if p.is_dir()):
        if sub.name.strip().lower() in seg_lower:
            return sub
    return top_dir


def _run_thumbnail_local_copy(job: dict, tg_id: str, apply: bool, admin) -> dict:
    """Publisher-side half of a Final-check thumbnail upload: mirror the R2 object
    into the workstation's canonical local tree. Uploads are NEW families (dave,
    2026-08-21), so the jpg lands in the RELEVANT country/region folder
    (_thumbnail_dest_dir); an existing same-stem file is still overwritten in
    place if one turns up, and an unresolvable country falls back to
    _from_review_app/. No prod creds involved, so plain `apply` (no i_am_sure)."""
    from pathlib import Path

    from . import config as cfg
    from . import images_r2
    stem = ""
    note = str(job.get("note") or "")
    if note.startswith("stem="):
        stem = note[5:].strip()
    if not stem:
        from .staging import db as fb_db
        snap = fb_db().collection("TripGroups").document(tg_id).get()
        stem = ((snap.to_dict() or {}).get("thumbnailTextureId") or "").strip()
    if not stem:
        log_text, status = f"!! no thumbnailTextureId for {tg_id}", "failed"
    else:
        key = f"{stem}.jpg"
        existing = _find_local_thumbnail(stem)
        if existing:
            dest, what = existing, "REPLACE existing"
        else:
            folder = _thumbnail_dest_dir(tg_id)
            if folder is not None:
                dest, what = folder / key, "NEW family — into"
            else:
                dest = Path(THUMBNAILS_TREE) / "_from_review_app" / key
                what = "NEW family, country unresolved — land in"
        if not apply:
            log_text = (f"DRY RUN: would download r2:{cfg.THUMB_BUCKET}/{key} "
                        f"-> {what} {dest}")
            status = "dry_run"
        else:
            try:
                s3 = images_r2._r2()
                if s3 is None:
                    raise RuntimeError("no R2 credentials on this host")
                dest.parent.mkdir(parents=True, exist_ok=True)
                s3.download_file(cfg.THUMB_BUCKET, key, str(dest))
                log_text = f"downloaded r2:{cfg.THUMB_BUCKET}/{key} -> {what} {dest}"
                if not existing:
                    log_text += "\n(file the jpg into its country subfolder when convenient)"
                status = "done"
            except Exception as e:  # noqa: BLE001 — surfaced on the job object
                log_text, status = f"!! local copy failed: {e}", "failed"
    return review_bus.update_job(
        job["id"], status=status, log=log_text[-8000:],
        resolved_by=getattr(admin, "username", None) or "",
        resolved_at=time.time())


def _finish_subprocess_job(job: dict, cmd: list[str], timeout: int,
                           apply_write: bool, admin) -> dict:
    """Run `cmd` synchronously and stamp the outcome onto the job (the shared
    tail of every script-backed kind)."""
    try:
        proc = subprocess.run(cmd, cwd=str(SCRIPTS_ROOT), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
        log = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        status = ("failed" if proc.returncode != 0
                  else ("done" if apply_write else "dry_run"))
    except subprocess.TimeoutExpired:
        log, status = f"!! {cmd[1]} timed out after {timeout}s", "failed"
    except OSError as e:
        log, status = f"!! could not launch {cmd[1]}: {e}", "failed"
    if status == "done":
        # An applied job just changed what the board's cached facts assert (a
        # stage-10b run writes recallQuiz/questionOptionKeys, a 4K build fills the
        # ledger, a publish flips prod state) — re-probe the family's rungs and
        # re-fetch the bus snapshots on the next load instead of at TTL expiry.
        try:
            from . import final_checks, tripdesc
            _by_trip, tg_docs = tripdesc._tripgroup_index()
            target = job.get("trip_id") or ""
            fam = (tg_docs.get(target) or {}).get("trips") or []
            final_checks.invalidate_probes([target, *fam] if target else None)
            review_bus.invalidate_snapshots()
            # A quiz landing also needs the recallQuiz sweep re-run, not just probes.
            final_checks.invalidate_recall_sweep()
        except Exception as e:  # noqa: BLE001 — cache hygiene must not fail the job
            print(f"[tool] cache invalidation after job {job['id']} failed: {e}")
    return review_bus.update_job(
        job["id"], status=status, log=log[-8000:],
        resolved_by=getattr(admin, "username", None) or "",
        resolved_at=time.time())


def _run_replace_overlay(job: dict, trip_id: str, apply: bool, admin) -> dict:
    """Workstation half of an overlay replacement: fetch the staged image from R2
    review-overlays/<base>/ and shell stage10_static_check.py `replace`, which
    writes the canonical copies (local country store + Drive + OGG folders + S3).
    Dry run reports the exact command. No prod-Firebase write, so plain `apply`."""
    import tempfile
    from pathlib import Path

    from . import config as cfg
    from . import images_r2, sessions
    note = dict(kv.split("=", 1) for kv in str(job.get("note") or "").split(";")
                if "=" in kv)
    family, filename = note.get("family", ""), note.get("file", "")
    if not family or not filename:
        return review_bus.update_job(
            job["id"], status="failed",
            log=f"!! job note lacks family=/file= ({job.get('note')!r})",
            resolved_by=getattr(admin, "username", None) or "",
            resolved_at=time.time())
    base = sessions._overlay_base(trip_id)
    key = f"{cfg.OVERLAY_KEY_PREFIX}{base}/{Path(filename).name}"
    cmd_desc = (f"stage10_static_check.py replace --family {family} "
                f"--file {filename} --image <r2:{key}>")
    if not apply:
        log_text, status = f"DRY RUN: would download r2 {key} then run {cmd_desc}", "dry_run"
    else:
        try:
            s3 = images_r2._r2()
            if s3 is None:
                raise RuntimeError("no R2 credentials on this host")
            tmp = Path(tempfile.mkdtemp(prefix="overlay_")) / Path(filename).name
            s3.download_file(cfg.THUMB_BUCKET, key, str(tmp))
            proc = subprocess.run(
                [sys.executable, "-u", str(SCRIPTS_ROOT / "stage10_static_check.py"),
                 "replace", "--family", family, "--file", filename,
                 "--image", str(tmp)],
                cwd=str(SCRIPTS_ROOT), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=600)
            log_text = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
            status = "done" if proc.returncode == 0 else "failed"
        except Exception as e:  # noqa: BLE001 — surfaced on the job object
            log_text, status = f"!! replace failed: {e}", "failed"
    return review_bus.update_job(
        job["id"], status=status, log=log_text[-8000:],
        resolved_by=getattr(admin, "username", None) or "",
        resolved_at=time.time())


@router.get("/publisher-mode")
def publisher_mode(admin=Depends(auth.require_admin)):
    """Cheap probe for the FE nav: is THIS instance the workstation publisher?"""
    return {"publisher_mode": PUBLISHER_MODE}


# ------------------------------------------------------------------ tools --- #
# The Publisher console's tool rack (spec §4.4/§4.5): post-publish sequence +
# the local-copy/S3 wrappers. Each is a WHITELISTED command over the Scripts
# repo — the console never reimplements a script, only sequences them. Long
# runs go to a background thread; progress lands on a bus job (kind "tool")
# that the normal inbox displays.
# Keep in sync with review_bus._TRIP_ID_RE (dots/spaces/commas/apostrophes are
# real-cid characters); anything this admits must also pass the bus regex, or
# queue_job 422s the run after validation here said OK.
_TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_,.'\- ]{0,149}$")
_STEPS_RE = re.compile(r"^[a-z0-9_,]{1,120}$")
_LANE_RE = re.compile(r"^\d{1,2}b?$")

# tool -> (writes_something, build(target, steps, lane, apply) -> (cmd, timeout)).
# `writes_something` tools REQUIRE apply to run at all when they have no native
# dry-run (we never invent one); the rest run with their own dry/apply flags.


def _tool_specs() -> dict:
    py = sys.executable

    def s(name: str) -> str:
        return str(SCRIPTS_ROOT / name)

    return {
        # --- post-publish sequence (spec §4.4, in order) ---
        "bump_version": (True, lambda t, st, ln, ap: (
            [py, s("BumpContentVersion.py"), "--prod"], 120)),
        "trello_move": (False, lambda t, st, ln, ap: (
            [py, str(SCRIPTS_ROOT / "Trello" / "trello_move.py"), "--to", ln or "12", t,
             "--strict"] + ([] if ap else ["--dry-run"]), 120)),
        "docids_append": (True, None),   # inline file append, not a script
        "snapshot": (False, lambda t, st, ln, ap: (
            [py, s("publish_inbox.py"), "snapshot", t], 300)),
        # --- local copies & S3 (spec §4.5) ---
        "tripdocs": (False, lambda t, st, ln, ap: (
            [py, s("tripdocs_local.py"), t] + (["--upload"] if ap else []), 900)),
        "static_pic_4k": (False, lambda t, st, ln, ap: (
            [py, s("static_pic_4k_s3.py"), "build", "--cids", t]
            + (["--apply"] if ap else []), 1800)),
        "upload_thumbs": (False, lambda t, st, ln, ap: (
            [py, s("upload_thumbnails_r2.py"), t] + ([] if ap else ["--dry-run"]), 300)),
        "stage9_finalise": (True, lambda t, st, ln, ap: (
            [py, s("stage9_finalise.py"), t, "--steps", st or ""], 5400)),
        # NO --apply-trello: the family card moves lanes ONLY when every rung is
        # stamped published (mark_published.py policy, dave 2026-08-22) — 10b's
        # own Trello step just prints what it would do.
        "stage10b": (False, lambda t, st, ln, ap: (
            [py, s("stage10b.py"), "--families", t], 1800)),
        # Released-trips button/tooltip drift vs staging (standardisation passes).
        # Target = one TGID or "all"; apply writes the targeted TripGroup updates.
        "group_text_drift": (False, lambda t, st, ln, ap: (
            [py, s("publish_trips_cli.py"), "--sync-group-text", t or "all"]
            + (["--apply", "--i-am-sure"] if ap else []), 1800)),
    }


def _docids_append(target: str, admin) -> str:
    """Append the publish marker to Content_DocIDs.md (a curated doc — the entry
    is deliberately one short dated line under an auto section at the end)."""
    path = SCRIPTS_ROOT / "Content_DocIDs.md"
    marker = "\n## Publisher log (auto-appended by the review-app console)\n"
    text = path.read_text(encoding="utf-8")
    line = (f"- {time.strftime('%Y-%m-%d %H:%M')} — published **{target}** "
            f"(by {getattr(admin, 'username', '?')}, publisher console)\n")
    if marker not in text:
        text += marker
    path.write_text(text + line, encoding="utf-8")
    return f"appended to Content_DocIDs.md: {line.strip()}"


class ToolRun(BaseModel):
    tool: str
    target: str = ""
    steps: str = ""      # stage9_finalise only
    lane: str = ""       # trello_move only (default 12 here — post-publish)
    apply: bool = False


@router.post("/pipeline/tool")
def run_tool(body: ToolRun, admin=Depends(auth.require_admin)):
    """PUBLISHER MODE ONLY. Run one whitelisted Scripts tool; returns the bus job
    (kind "tool") the run reports into — long tools finish in the background and
    the inbox shows the log when they land."""
    if not PUBLISHER_MODE:
        raise HTTPException(403, detail={"error": "not_publisher",
                                         "detail": "tools run on the workstation only"})
    specs = _tool_specs()
    if body.tool not in specs:
        raise HTTPException(422, detail={"error": "bad_tool", "detail": body.tool})
    # group_text_drift: blank target = the full "all" sweep (builder default).
    needs_target = body.tool not in ("bump_version", "group_text_drift") or bool(body.target.strip())
    target = body.target.strip()
    if needs_target and not _TARGET_RE.fullmatch(target):
        raise HTTPException(422, detail={"error": "bad_target", "detail": target})
    if body.steps and not _STEPS_RE.fullmatch(body.steps):
        raise HTTPException(422, detail={"error": "bad_steps", "detail": body.steps})
    if body.lane and not _LANE_RE.fullmatch(body.lane):
        raise HTTPException(422, detail={"error": "bad_lane", "detail": body.lane})
    if body.tool == "stage9_finalise" and not _STEPS_RE.fullmatch(body.steps or ""):
        raise HTTPException(422, detail={"error": "bad_steps",
                                         "detail": "stage9_finalise needs --steps"})
    writes, build = specs[body.tool]

    # The FULL target (it's already _TARGET_RE-validated and the bus regex now
    # accepts real cids with dots/spaces — word-splitting used to mangle
    # "A._A. Milne…" into the invalid "A._A." and 422 the whole tool run).
    job_target = (target.strip() or "workstation")
    job = review_bus.queue_job("tool", job_target, admin,
                               note=f"{body.tool}"
                                    + (f" {target}" if target else "")
                                    + (f" steps={body.steps}" if body.steps else "")
                                    + (f" to={body.lane}" if body.lane else "")
                                    + (" APPLY" if body.apply else " (dry)"))

    if body.tool == "docids_append":
        if not body.apply:
            return review_bus.update_job(
                job["id"], status="dry_run",
                log=f"DRY RUN: would append a dated '{target}' line to Content_DocIDs.md",
                resolved_by=admin.username, resolved_at=time.time())
        try:
            return review_bus.update_job(
                job["id"], status="done", log=_docids_append(target, admin),
                resolved_by=admin.username, resolved_at=time.time())
        except Exception as e:  # noqa: BLE001
            return review_bus.update_job(
                job["id"], status="failed", log=f"!! {e}",
                resolved_by=admin.username, resolved_at=time.time())

    cmd, timeout = build(target, body.steps, body.lane, body.apply)
    if writes and not body.apply:
        # No native dry-run on this script — never invent one; describe instead.
        return review_bus.update_job(
            job["id"], status="dry_run",
            log="DRY RUN (this script has no preview mode): would run\n  "
                + " ".join(cmd) + "\nRe-run with apply to execute.",
            resolved_by=admin.username, resolved_at=time.time())

    def runner() -> None:
        try:
            _finish_subprocess_job(job, cmd, timeout, body.apply, admin)
        except Exception as e:  # noqa: BLE001 — an unhandled error here would vanish
            # with the thread and leave the job stuck "queued" (update_job itself can
            # raise on an R2 hiccup). Log it; the inbox shows the stale status.
            print(f"[tool] !! background {body.tool} job {job['id']} could not record "
                  f"its result: {e}")

    threading.Thread(target=runner, daemon=True,
                     name=f"tool-{body.tool}-{job['id']}").start()
    return job


class Investigate(BaseModel):
    job_id: str


@router.post("/pipeline/investigate")
def investigate_job(body: Investigate, admin=Depends(auth.require_admin)):
    """PUBLISHER MODE ONLY (Tier 2 of the release wizard's failure handling,
    dave 2026-08-22): write a diagnostic bundle for a failed job into the
    Scripts repo and open a NEW terminal running `claude --model opus --effort
    high` pre-briefed on it — one click instead of hand-assembling context."""
    if not PUBLISHER_MODE:
        raise HTTPException(403, detail={"error": "not_publisher",
                                         "detail": "investigation launches on the workstation only"})
    job = review_bus.get_job(body.job_id)
    diag_dir = SCRIPTS_ROOT / "Logs" / "publish-diagnostics"
    diag_dir.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", body.job_id)
    bundle = diag_dir / f"{safe_id}.md"
    bundle.write_text(
        f"# Publish diagnostic — job {job.get('id')}\n\n"
        f"- kind: {job.get('kind')}\n"
        f"- target: {job.get('trip_id')}\n"
        f"- status: {job.get('status')}\n"
        f"- note: {job.get('note')}\n"
        f"- requested_by: {job.get('requested_by')} · resolved_by: {job.get('resolved_by')}\n\n"
        "The review-app Publisher ran this job and it needs investigating. The\n"
        "scripts live in THIS repo (publish_trips_cli.py, stage9_finalise.py,\n"
        "stage10b.py, stage10_verify.py …); the app only sequences them. Diagnose\n"
        "the failure below, fix the underlying issue (files/S3/staging as needed),\n"
        "and say what to re-run in the Publisher.\n\n"
        "## Full job log\n\n```\n" + str(job.get("log") or "(empty)") + "\n```\n",
        encoding="utf-8")
    prompt = (f"Read '{bundle}' — a failed publish job from the review-app "
              "Publisher — and investigate/resolve the failure it describes.")
    try:
        subprocess.Popen(
            ["cmd", "/c", "start", "Claude publish debug", "cmd", "/k",
             "claude", "--model", "opus", "--effort", "high", prompt],
            cwd=str(SCRIPTS_ROOT))
    except OSError as e:
        raise HTTPException(500, detail={"error": "launch_failed", "detail": str(e)})
    log_admin = getattr(admin, "username", "?")
    print(f"[investigate] {log_admin} launched Claude (opus/high) on {body.job_id}")
    return {"bundle": str(bundle), "launched": True}


@router.post("/pipeline/gate-report")
def gate_report(admin=Depends(auth.require_admin)):
    """PUBLISHER MODE ONLY: run `publish_audio_gate.py --report` (read-only sweep of
    every staging TripGroup → READY / BLOCKED / ALREADY-BROKEN-LIVE buckets) and
    return its output. Long: the sweep does an S3 audio check per rung."""
    if not PUBLISHER_MODE:
        raise HTTPException(403, detail={
            "error": "not_publisher",
            "detail": "gate report runs on the workstation publisher only"})
    cmd = [sys.executable, str(SCRIPTS_ROOT / "publish_audio_gate.py"), "--report"]
    try:
        proc = subprocess.run(cmd, cwd=str(SCRIPTS_ROOT), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=900)
        log = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        ok = proc.returncode == 0
    except subprocess.TimeoutExpired:
        log, ok = "!! publish_audio_gate.py --report timed out after 900s", False
    except OSError as e:
        log, ok = f"!! could not launch publish_audio_gate.py: {e}", False
    return {"ok": ok, "log": log[-30000:]}
