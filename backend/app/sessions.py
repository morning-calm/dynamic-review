"""
Session orchestration: seed/resume, field building + contract serialisation, all
field operations (text autosave, regenerate, combine, fallback, import, coverage,
flag, comment, revert) and submit.

Audio/versioning model (plan): on seed each master {i}.mp3 is copied to
work/{sid}/orig/ (pristine v0) and work/{sid}/working/ (current). Masters are NEVER
mutated during review; combine/import write the working copy and archive a v{n}
under work/{sid}/versions/. On submit, changed working files are promoted to the
masters (prior master archived under mp3_dir/versions/).

Whisper transcription and Gemini cleaning of the original are done LAZILY (first
time a scene needs a segment splice) and cached — so seeding a session never blocks
on the GPU or the network, and read-only browsing is instant. This is a deliberate,
equivalent-correctness deviation from 'cache at seed' (same cached values, computed
on demand, persisted: Whisper to metadata_dir on disk, cleaned-orig in the session
row).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path

import numpy as np
from fastapi import HTTPException

from . import config  # noqa: F401  (ensures SCRIPTS_ROOT on sys.path) — keep first
from . import audio_core, audio_io, audio_splice, db, review_audio, thumbs
from .config import (COUNTRY_VOICE_GUESS, COVERAGE_DONE_FRACTION, WORK_ROOT)
from .locks import WHISPER_LOCK
from .staging import (db as fb_db, get_trip, get_tripgroup, merge_categories,
                      paths_for, tripgroup_id_for, update_trip_text,
                      update_tripgroup)

from stage9.common import COUNTRY_CFG
from stage9.whisper_timing import transcribe_words

_TRIP_CACHE: dict[str, dict] = {}   # trip_id → {title, folder_name} (process-life)

# Per-trip voice override file (highest priority). Shape: {"<trip_id>": "isla"}.
_VOICE_OVERRIDES = config.BACKEND_ROOT / "voice_overrides.json"


def _staging_choice_voice(trip_id: str) -> str | None:
    """The voice chosen at staging, persisted in the trip's local cache
    (Research and Writing/data/<trip>/staging_choices.json: {"voice": "Isla", ...})."""
    p = (config.SCRIPTS_ROOT / "Research and Writing" / "data" / trip_id
         / "staging_choices.json")
    try:
        v = (json.loads(p.read_text(encoding="utf-8")).get("voice") or "").strip().lower()
        return v if v in audio_core.VOICES else None
    except Exception:
        return None


def _override_voice(trip_id: str) -> str | None:
    try:
        v = (json.loads(_VOICE_OVERRIDES.read_text(encoding="utf-8"))
             .get(trip_id) or "").strip().lower()
        return v if v in audio_core.VOICES else None
    except Exception:
        return None


def resolve_voice(trip_id: str, country: str) -> str:
    """Per-trip override > staging_choices.json (the real chosen voice) > country guess."""
    return (_override_voice(trip_id)
            or _staging_choice_voice(trip_id)
            or COUNTRY_VOICE_GUESS.get(country, "harry"))


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _new_sid() -> str:
    return "sess_" + uuid.uuid4().hex[:12]


def work_dirs(sid: str) -> dict[str, Path]:
    base = WORK_ROOT / sid
    d = {k: base / k for k in ("orig", "working", "versions", "candidate", "fallback")}
    for p in d.values():
        p.mkdir(parents=True, exist_ok=True)
    return d


def mp3_name(field_path: str, scene_index: int | None, option_index: int | None) -> str | None:
    if field_path == "SceneDesc":
        return f"{scene_index}.mp3"
    if field_path == "questionKey":
        return f"{scene_index}_q.mp3"
    if field_path == "questionOption":
        suffix = "" if (option_index or 0) == 0 else str(option_index)
        return f"{scene_index}_a{suffix}.mp3"
    return None


def _file_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha1(path.read_bytes()).hexdigest()[:16]


def _next_version_suffix(ver_dir: Path, stem: str) -> int:
    """Highest existing {stem}v<N>.mp3 suffix + 1 (S6: len()+1 overwrites on gaps
    like v1, v3)."""
    mx = 0
    pat = re.compile(rf"^{re.escape(stem)}v(\d+)\.mp3$")
    for p in ver_dir.glob(f"{stem}v*.mp3"):
        m = pat.match(p.name)
        if m:
            mx = max(mx, int(m.group(1)))
    return mx + 1


def _r2_upload_working(trip_id: str, dirs: dict, frow) -> None:
    """Best-effort: push the canonical working clip and the latest version snapshot to
    review-audio/<trip_id>/. Called immediately after _set_working(); never raises."""
    try:
        name = frow["mp3_name"]
        if not name:
            return
        # Canonical promoted clip (e.g. 3.mp3, 3_q.mp3, 3_a.mp3)
        working = dirs["working"] / name
        if working.exists():
            review_audio.upload(trip_id, working, name)
        # Latest version snapshot just inserted by _set_working() (e.g. 3v2.mp3)
        ver = db.query_one(
            "SELECT path, label FROM audio_versions "
            "WHERE field_id=? ORDER BY n DESC LIMIT 1",
            (frow["id"],))
        if ver and ver["path"]:
            vpath = Path(ver["path"])
            if vpath.exists():
                review_audio.upload(trip_id, vpath, f"{ver['label']}.mp3")
    except Exception as e:  # noqa: BLE001
        print(f"[sessions] R2 upload skipped ({frow['mp3_name']}): {e}")


def _vimeo_id(v) -> str | None:
    """S9: read-model video_id must be the bare Vimeo id, not a URL."""
    if not v:
        return None
    v = str(v).strip()
    if "/" in v:
        v = v.rstrip("/").split("/")[-1]
    v = v.split("?")[0].strip()
    return v or None


def _merge_ranges(ranges: list[list[float]], tol: float = 0.3) -> list[list[float]]:
    clean = sorted([[float(a), float(b)] for a, b in ranges if b > a])
    out: list[list[float]] = []
    for a, b in clean:
        if out and a <= out[-1][1] + tol:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _coverage_total(ranges: list[list[float]]) -> float:
    return sum(b - a for a, b in ranges)


# --------------------------------------------------------------------------- #
# Audio-dir resolution: Quicktrips masters first, else the Audio Generation
# fallback (column-7 English A12/B1 trips have NO Quicktrips masters).
# --------------------------------------------------------------------------- #
_SCENE_MP3_RE = re.compile(r"^\d+\.mp3$", re.I)


def _has_scene_mp3(d: Path | None) -> bool:
    """True if the dir holds at least one numbered scene-narration master ({i}.mp3).
    Ignores *.bak_quiet / *.bak_loud (the regex anchors on a bare ``\\d+.mp3``)."""
    if not d or not d.is_dir():
        return False
    try:
        for p in d.iterdir():
            if p.is_file() and _SCENE_MP3_RE.match(p.name):
                return True
    except OSError:
        return False
    return False


def resolve_audio_dir(trip_id: str, trip: dict) -> Path:
    """Folder holding this trip's MP3 masters. Try the Quicktrips tree
    (``paths_for``); if that can't be resolved (no Stage-9 COUNTRY_CFG) OR it holds no
    ``{i}.mp3``, fall back to ``Audio Generation/<trip_id>/``."""
    try:
        mp3_dir = paths_for(trip, trip_id)["mp3_dir"]
        if _has_scene_mp3(mp3_dir):
            return mp3_dir
    except SystemExit:
        pass
    ag = config.AUDIO_GENERATION_ROOT
    # flat Audio Generation/<trip>, then the 'Sent to KP/MP3' staging area (the Japan
    # _EN masters live there), then a shallow nested search.
    for cand in (ag / trip_id, ag / "Sent to KP" / "MP3" / trip_id):
        if _has_scene_mp3(cand):
            return cand
    for pat in (f"*/{trip_id}", f"*/*/{trip_id}"):
        for cand in ag.glob(pat):
            if _has_scene_mp3(cand):
                return cand
    return ag / trip_id


# --------------------------------------------------------------------------- #
# Trip listing
# --------------------------------------------------------------------------- #
def _trip_meta(trip_id: str) -> dict:
    if trip_id in _TRIP_CACHE:
        return _TRIP_CACHE[trip_id]
    meta = {"title": trip_id, "folder_name": ""}
    try:
        snap = fb_db().collection("Trips").document(trip_id).get()
        if snap.exists:
            t = snap.to_dict()
            meta = {"title": t.get("contentTitleKey") or trip_id,
                    "folder_name": t.get("folderName") or ""}
    except Exception:
        pass
    _TRIP_CACHE[trip_id] = meta
    return meta


def _session_meta(tid: str) -> tuple[bool, str | None]:
    srow = db.query_one(
        "SELECT id,status FROM sessions WHERE trip_id=? "
        "ORDER BY created_at DESC LIMIT 1", (tid,))
    return srow is not None, (srow["status"] if srow else None)


def list_trips() -> list[dict]:
    """Trello-manifest-driven when ``trips_to_review.json`` exists, else the legacy
    Quicktrips MP3-dir scan."""
    if config.MANIFEST_PATH.exists():
        try:
            return _list_trips_from_manifest()
        except Exception as e:  # noqa: BLE001 - never let a bad manifest 500 the list
            print(f"[trips] manifest unreadable ({e}); falling back to MP3 scan")
    return _list_trips_from_scan()


def _list_trips_from_manifest() -> list[dict]:
    data = json.loads(config.MANIFEST_PATH.read_text(encoding="utf-8"))
    out: list[dict] = []
    for t in data.get("trips") or []:
        tid = t.get("trip_id")
        if not tid:
            continue
        trip = None
        try:
            trip = get_trip(tid)                 # staging; may be absent
        except SystemExit:
            trip = None
        folder_name = (trip.get("folderName") or "") if trip else ""
        title = (trip.get("contentTitleKey") if trip else None) or t.get("title") or tid
        reviewable = False
        if trip is not None:
            try:
                reviewable = _has_scene_mp3(resolve_audio_dir(tid, trip))
            except Exception:  # noqa: BLE001
                reviewable = False
        has_session, status = _session_meta(tid)
        out.append({
            "trip_id": tid,
            "title": title,
            "folder_name": folder_name,
            "lane": t.get("lane"),
            "has_session": has_session,
            "status": status,
            "reviewable": reviewable,
        })
    return out


def _list_trips_from_scan() -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for cfg in COUNTRY_CFG.values():
        mp3_root = cfg["audio_root"] / "MP3"
        if not mp3_root.is_dir():
            continue
        for loc in sorted(mp3_root.iterdir()):
            if not loc.is_dir():
                continue
            for trip in sorted(loc.iterdir()):
                if not trip.is_dir() or not trip.name.endswith("_EN"):
                    continue
                tid = trip.name
                if tid in seen:
                    continue
                seen.add(tid)
                meta = _trip_meta(tid)
                has_session, status = _session_meta(tid)
                out.append({
                    "trip_id": tid,
                    "title": meta["title"],
                    "folder_name": meta["folder_name"],
                    "lane": None,
                    "has_session": has_session,
                    "status": status,
                    "reviewable": True,
                })
    return out


# --------------------------------------------------------------------------- #
# Seed / resume
# --------------------------------------------------------------------------- #
def create_or_resume(trip_id: str) -> dict:
    existing = db.query_one(
        "SELECT id FROM sessions WHERE trip_id=? AND status='in_review' "
        "ORDER BY created_at DESC LIMIT 1", (trip_id,))
    if existing:
        return get_session(existing["id"])

    try:
        trip = get_trip(trip_id)                       # staging = source of truth
    except SystemExit as e:
        raise HTTPException(404, detail=str(e))

    # Quicktrips masters when present, else the Audio Generation fallback (column-7
    # English trips). folderName/country are derived directly so a country with no
    # Stage-9 COUNTRY_CFG (e.g. 'GreatBritain') still seeds.
    folder_name = (trip.get("folderName") or "").replace("\\", "/").strip("/")
    country = folder_name.split("/")[0] if folder_name else ""
    mp3_dir = resolve_audio_dir(trip_id, trip)
    if not _has_scene_mp3(mp3_dir):
        raise HTTPException(status_code=422, detail={
            "error": "bad_folder",
            "detail": f"{trip_id}: no MP3 masters under the Quicktrips tree or "
                      f"Audio Generation/{trip_id}"})

    voice = resolve_voice(trip_id, country)
    voice_id, voice_settings = audio_core.VOICES[voice]
    voice_settings = {**voice_settings, "speed": audio_core.speed_for_trip(trip_id)}
    tg_id, tg = get_tripgroup(trip_id)
    categories = (tg or {}).get("tripCategories") or trip.get("tripCategories") or []
    tg_desc = (tg or {}).get("descriptionTarget") or ""

    sid = _new_sid()
    now = time.time()
    db.execute(
        "INSERT INTO sessions(id,trip_id,folder_name,voice,voice_settings_json,"
        "orig_loudness_json,cleaned_orig_json,loaded_trip_json,trip_categories_json,"
        "status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, trip_id, folder_name, voice, json.dumps(voice_settings),
         "{}", "{}", json.dumps(trip, default=str), json.dumps(categories),
         "in_review", now, now))

    dirs = work_dirs(sid)

    def add_field(scene_index, field_path, original_text, has_audio,
                  option_index=None):
        name = mp3_name(field_path, scene_index, option_index) if has_audio else None
        cur_path = None
        whash = None
        if name:
            master = mp3_dir / name
            if master.exists():
                audio_io.mp3_to_mp3_copy(master, dirs["orig"] / name)
                audio_io.mp3_to_mp3_copy(master, dirs["working"] / name)
                cur_path = str(dirs["working"] / name)
                whash = _file_hash(dirs["working"] / name)
            else:
                has_audio = False   # no master on disk → text-only, no gate
        fid = db.execute(
            "INSERT INTO field_edits(session_id,scene_index,field_path,option_index,"
            "has_audio,mp3_name,original_text,current_text,current_mp3_path,"
            "working_audio_hash,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (sid, scene_index, field_path, option_index, 1 if has_audio else 0,
             name if has_audio else None, original_text or "", original_text or "",
             cur_path, whash, time.time()))
        if has_audio and name and (dirs["orig"] / name).exists():
            stem = name[:-4]
            db.execute(
                "INSERT INTO audio_versions(session_id,field_id,scene_index,n,kind,"
                "path,label,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (sid, fid, scene_index, 0, "v0_original",
                 str(dirs["orig"] / name), f"{stem}v0", time.time()))
        return fid

    # trip-level
    add_field(None, "contentTitleKey", trip.get("contentTitleKey") or "", False)
    add_field(None, "tripgroup_description", tg_desc, False)

    # scenes
    for i, s in enumerate(trip.get("quickTrips") or []):
        add_field(i, "titleKey", s.get("titleKey") or "", False)
        desc = (s.get("SceneDesc") or "").strip()
        has_a = bool(s.get("hasAudio"))
        if desc or has_a:
            add_field(i, "SceneDesc", s.get("SceneDesc") or "", has_a)
        if (s.get("questionKey") or "").strip():
            add_field(i, "questionKey", s.get("questionKey") or "", True)
        for k, opt in enumerate(s.get("questionOptionKeys") or []):
            add_field(i, "questionOption", opt or "", True, option_index=k)

    # Best-effort: resolve + upload scene thumbnails to R2 so the read-model below can
    # hand back thumb_url. Never fail the seed if the JSON / JPGs / R2 are unavailable.
    try:
        thumbs.prewarm(trip.get("quickTrips") or [])
    except Exception as e:  # noqa: BLE001
        print(f"[seed] thumbnail prewarm skipped for {trip_id}: {e}")

    db.touch_session(sid)
    return get_session(sid)


# --------------------------------------------------------------------------- #
# Fetch + serialise
# --------------------------------------------------------------------------- #
def _session_row(sid: str):
    row = db.query_one("SELECT * FROM sessions WHERE id=?", (sid,))
    if not row:
        raise HTTPException(404, detail={"error": "no_session", "detail": sid})
    return row


def _field_row(sid: str, fid: int):
    row = db.query_one(
        "SELECT * FROM field_edits WHERE id=? AND session_id=?", (fid, sid))
    if not row:
        raise HTTPException(404, detail={"error": "no_field", "detail": str(fid)})
    return row


def _working_duration(frow) -> float:
    if not frow["has_audio"] or not frow["current_mp3_path"]:
        return 0.0
    p = Path(frow["current_mp3_path"])
    return audio_io.mp3_duration_seconds(p) if p.exists() else 0.0


def _coverage_for(frow) -> tuple[list[list[float]], bool]:
    """Return (coverage_ranges, can_mark_done)."""
    if not frow["has_audio"]:
        return [], True
    cov = json.loads(frow["played_coverage_json"] or "{}")
    ranges = cov.get("ranges", []) if cov.get("hash") == frow["working_audio_hash"] else []
    dur = _working_duration(frow)
    done_ok = bool(dur) and _coverage_total(ranges) >= COVERAGE_DONE_FRACTION * dur
    return ranges, done_ok


def serialize_field(sid: str, frow) -> dict:
    fid = frow["id"]
    has_audio = bool(frow["has_audio"])
    field_path = frow["field_path"]
    if field_path == "questionOption":
        field_path_out = f"questionOption[{frow['option_index']}]"
    else:
        field_path_out = field_path

    ranges, done_ok = _coverage_for(frow)

    audio = {"original": None, "working": None, "candidate": None, "fallback": None}
    if has_audio:
        # NEW: ?v=<8-char content hash> so the URL changes whenever the bytes change
        # (frontend cache-bust). original is pristine and immutable → no version tag.
        audio["original"] = f"/audio/{sid}/{fid}/original"
        wh = (frow["working_audio_hash"] or "")[:8]
        audio["working"] = f"/audio/{sid}/{fid}/working" + (f"?v={wh}" if wh else "")
        cand = frow["candidate_mp3_path"]
        if cand and Path(cand).exists():
            ch = (_file_hash(Path(cand)) or "")[:8]
            audio["candidate"] = f"/audio/{sid}/{fid}/candidate?v={ch}"
        fb = frow["fallback_mp3_path"]
        if fb and Path(fb).exists():
            fh = (_file_hash(Path(fb)) or "")[:8]
            audio["fallback"] = f"/audio/{sid}/{fid}/fallback?v={fh}"

    versions = []
    for v in db.query(
            "SELECT n,kind,label FROM audio_versions WHERE field_id=? ORDER BY n", (fid,)):
        versions.append({"label": v["label"], "kind": v["kind"],
                         "url": f"/audio/{sid}/{fid}/v/{v['n']}"})

    return {
        "fid": fid,
        "scene_index": frow["scene_index"],
        "field_path": field_path_out,
        "has_audio": has_audio,
        "original_text": frow["original_text"],
        "current_text": frow["current_text"],
        "flag": frow["flag"],
        "comment": frow["comment"],
        "splice_confidence": frow["splice_confidence"],
        "played_coverage": ranges,
        "can_mark_done": done_ok,
        "audio": audio,
        "versions": versions,
    }


def get_session(sid: str) -> dict:
    srow = _session_row(sid)
    frows = db.query(
        "SELECT * FROM field_edits WHERE session_id=? ORDER BY id", (sid,))
    fields = [serialize_field(sid, f) for f in frows]

    trip_fields = [f for f, fr in zip(fields, frows) if fr["scene_index"] is None]

    trip_id = srow["trip_id"]
    trip = json.loads(srow["loaded_trip_json"])
    try:
        _p = paths_for(trip, trip_id)
        mp3_dir, ogg_dir = _p["mp3_dir"], _p["ogg_dir"]
    except SystemExit:
        mp3_dir = ogg_dir = None

    scenes_out = []
    by_scene: dict[int, list] = {}
    for f, fr in zip(fields, frows):
        if fr["scene_index"] is not None:
            by_scene.setdefault(fr["scene_index"], []).append(f)

    for i, s in enumerate(trip.get("quickTrips") or []):
        overlays = []
        for si in (s.get("staticImages") or []):
            fn = si.get("filename")
            if fn:
                overlays.append({"filename": fn,
                                 "url": f"/overlays/{sid}/{fn}"})
        # S9: only advertise a static-360 image_url when the {i}.jpg actually resolves
        # locally (the 360 still isn't under Research and Writing/data) — else null,
        # never a URL that 404s.
        image_url = None
        if s.get("isStaticImage") and _resolve_overlay_file(
                trip_id, mp3_dir, ogg_dir, f"{i}.jpg"):
            image_url = f"/overlays/{sid}/{i}.jpg"
        scenes_out.append({
            "index": i,
            "video_id": _vimeo_id(s.get("videoUrl")),
            "is_static_image": bool(s.get("isStaticImage")),
            "has_audio": bool(s.get("hasAudio")),
            "image_url": image_url,
            # VID scenes get an R2 thumbnail; static/PIC scenes stay null (the
            # frontend falls back to image_url).
            "thumb_url": thumbs.thumb_url_for_scene(s),
            "overlays": overlays,
            "fields": by_scene.get(i, []),
        })

    return {
        "id": sid,
        "trip_id": srow["trip_id"],
        "folder_name": srow["folder_name"],
        "status": srow["status"],
        "voice": srow["voice"],
        "trip_categories": json.loads(srow["trip_categories_json"] or "[]"),
        "trip_fields": trip_fields,
        "scenes": scenes_out,
    }


# --------------------------------------------------------------------------- #
# Lazy caches (Whisper + cleaned original)
# --------------------------------------------------------------------------- #
def _cleaned_orig(srow, frow) -> tuple[str, bool]:
    cache = json.loads(srow["cleaned_orig_json"] or "{}")
    key = str(frow["id"])
    if key in cache:
        c = cache[key]
        return c["text"], c.get("fallback", False)
    raw = audio_core.strip_url_lines(frow["original_text"] or "")
    cleaned, fb = audio_core.validate_and_clean(raw, srow["trip_id"], frow["scene_index"])
    cache[key] = {"text": cleaned, "fallback": fb}
    db.execute("UPDATE sessions SET cleaned_orig_json=? WHERE id=?",
               (json.dumps(cache), srow["id"]))
    return cleaned, fb


def _whisper_orig(srow, frow) -> list[dict]:
    trip = json.loads(srow["loaded_trip_json"])
    master = resolve_audio_dir(srow["trip_id"], trip) / frow["mp3_name"]
    if not master.exists():
        return []
    try:
        meta_dir = paths_for(trip, srow["trip_id"])["metadata_dir"]
    except SystemExit:                       # column-7 trip with no COUNTRY_CFG
        meta_dir = config.WORK_ROOT / "_whisper_meta" / srow["trip_id"]
    meta_dir.mkdir(parents=True, exist_ok=True)
    # S3: the Whisper cache is keyed only on the stem, so a master replaced between
    # reviews (e.g. promoted by a prior submit) would otherwise be cut on the OLD
    # take's word times. Track the master's content hash in a sidecar and force a
    # re-transcribe whenever it changes.
    cache_json = meta_dir / (master.stem + ".json")
    sidecar = meta_dir / (master.stem + ".audiohash")
    cur_hash = _file_hash(master)
    prev_hash = sidecar.read_text().strip() if sidecar.exists() else None
    refresh = cache_json.exists() and prev_hash != cur_hash
    with WHISPER_LOCK:
        data = transcribe_words(master, lang="en", model_name="small",
                                cache_dir=meta_dir, refresh=refresh)
    sidecar.write_text(cur_hash or "")
    return data.get("words") or []


# --------------------------------------------------------------------------- #
# Mutations
# --------------------------------------------------------------------------- #
def _clear_coverage_and_done(frow) -> dict:
    patch = {"played_coverage_json": "{}"}
    if frow["flag"] == "done":
        patch["flag"] = "none"
    return patch


def update_text(sid: str, fid: int, text: str) -> dict:
    srow = _session_row(sid)
    frow = _field_row(sid, fid)
    patch = {"current_text": text}
    if text != frow["current_text"]:
        patch.update(_clear_coverage_and_done(frow))
    db.update_fields(fid, **patch)
    db.touch_session(sid)
    return serialize_field(sid, _field_row(sid, fid))


def _set_working(sid: str, frow, samples: np.ndarray | None = None,
                 src_mp3: Path | None = None, kind: str = "splice") -> str:
    """Write new working audio (from samples or an mp3 source), archive a v{n},
    return the new working_audio_hash."""
    dirs = work_dirs(sid)
    name = frow["mp3_name"]
    working = dirs["working"] / name
    if samples is not None:
        audio_io.samples_to_mp3(samples, working)
    elif src_mp3 is not None:
        audio_io.mp3_to_mp3_copy(src_mp3, working)
    stem = name[:-4]
    row = db.query_one(
        "SELECT COALESCE(MAX(n),0) AS mx FROM audio_versions WHERE field_id=?", (frow["id"],))
    n = (row["mx"] or 0) + 1
    vpath = dirs["versions"] / f"{stem}v{n}.mp3"
    audio_io.mp3_to_mp3_copy(working, vpath)
    db.execute(
        "INSERT INTO audio_versions(session_id,field_id,scene_index,n,kind,path,"
        "label,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (sid, frow["id"], frow["scene_index"], n, kind, str(vpath),
         f"{stem}v{n}", time.time()))
    return _file_hash(working)


def regenerate(sid: str, fid: int, mode: str, rng: dict | None) -> dict:
    srow = _session_row(sid)
    frow = _field_row(sid, fid)
    if not frow["has_audio"]:
        raise HTTPException(400, detail={"error": "no_audio",
                                         "detail": "field has no audio"})
    voice_id, voice_settings = audio_core.VOICES[srow["voice"]]
    voice_settings = {**voice_settings, "speed": audio_core.speed_for_trip(srow["trip_id"])}
    dirs = work_dirs(sid)
    cand_path = dirs["candidate"] / f"{fid}.mp3"

    field_path = frow["field_path"]
    cur = audio_core.strip_url_lines(frow["current_text"] or "")

    # Q&A fields and SceneDesc 'whole' → whole regenerate (no splice).
    if field_path != "SceneDesc" or mode == "whole":
        cleaned, fb = audio_core.validate_and_clean(cur, srow["trip_id"], frow["scene_index"])
        plan = audio_splice.plan_whole(cleaned, fb, voice_id, voice_settings)
    else:
        cleaned_new, fb_new = audio_core.validate_and_clean(
            cur, srow["trip_id"], frow["scene_index"])
        cleaned_orig, fb_orig = _cleaned_orig(srow, frow)
        if fb_orig:
            plan = audio_splice.RegenPlan(
                edit_required=True,
                reason="Original text could not be cleaned reliably — "
                       "whole-regenerate advised.")
        else:
            words = _whisper_orig(srow, frow)
            if not words:
                plan = audio_splice.RegenPlan(
                    edit_required=True,
                    reason="No word timing available for this take's audio.")
            else:
                hl_span = None
                if mode == "highlight" and rng:
                    hl_span = audio_splice.highlight_span_in_cleaned(
                        cur, cleaned_new, int(rng["start"]), int(rng["end"]))
                plan = audio_splice.plan_segment(
                    srow["trip_id"], cleaned_orig, cleaned_new, fb_new, words,
                    voice_id, voice_settings, highlight_orig_span=hl_span)

    # Hard reject (segment guards): edit_required AND no candidate audio at all.
    if plan.edit_required and plan.candidate_mp3 is None:
        db.update_fields(fid, flag="edit_required",
                         candidate_mp3_path=None,
                         comment=_append_note(frow["comment"], plan.reason))
        db.touch_session(sid)
        return serialize_field(sid, _field_row(sid, fid))

    # Candidate available (segment splice plan, or a whole regen that may be S2-flagged).
    cand_path.write_bytes(plan.candidate_mp3)
    patch = {"candidate_mp3_path": str(cand_path),
             "splice_meta_json": json.dumps(plan.meta)}
    if plan.edit_required:        # S2: whole regen voiced from raw (uncleaned) text
        patch["flag"] = "edit_required"
        patch["comment"] = _append_note(frow["comment"], plan.reason)
    db.update_fields(fid, **patch)
    db.touch_session(sid)
    return serialize_field(sid, _field_row(sid, fid))


def combine(sid: str, fid: int) -> dict:
    srow = _session_row(sid)
    frow = _field_row(sid, fid)
    if not frow["candidate_mp3_path"] or not Path(frow["candidate_mp3_path"]).exists():
        raise HTTPException(409, detail={"error": "no_candidate",
                                         "detail": "regenerate first"})
    meta = json.loads(frow["splice_meta_json"] or "{}")
    dirs = work_dirs(sid)

    if meta.get("mode") == "whole":
        # whole-block / Q&A: replace working with the candidate take.
        whash = _set_working(sid, frow, src_mp3=Path(frow["candidate_mp3_path"]),
                             kind="splice")
        _r2_upload_working(srow["trip_id"], dirs, frow)
        patch = {"working_audio_hash": whash, "splice_confidence": None,
                 "candidate_mp3_path": None}
        patch.update(_clear_coverage_and_done(frow))
        db.update_fields(fid, **patch)
        db.touch_session(sid)
        return serialize_field(sid, _field_row(sid, fid))

    # segment splice — ALWAYS from the pristine original.
    name = frow["mp3_name"]
    orig = audio_io.mp3_to_samples(dirs["orig"] / name)
    cand = audio_io.mp3_to_samples(frow["candidate_mp3_path"])
    result = audio_splice.do_splice(orig, cand, meta)

    whash = _set_working(sid, frow, samples=result.samples, kind="splice")
    _r2_upload_working(srow["trip_id"], dirs, frow)
    patch = {"working_audio_hash": whash,
             "splice_confidence": result.confidence,
             "candidate_mp3_path": None,
             "splice_meta_json": json.dumps({**meta, "splice_detail": result.detail})}
    patch.update(_clear_coverage_and_done(frow))
    if result.edit_required:
        patch["flag"] = "edit_required"
        patch["comment"] = _append_note(
            frow["comment"],
            f"Low splice confidence ({result.confidence}); please verify or send to "
            f"manual edit.")
    db.update_fields(fid, **patch)
    db.touch_session(sid)
    return serialize_field(sid, _field_row(sid, fid))


def fallback(sid: str, fid: int, extent: str, text: str | None, description: str) -> dict:
    srow = _session_row(sid)
    frow = _field_row(sid, fid)
    if not frow["has_audio"]:
        raise HTTPException(400, detail={"error": "no_audio", "detail": "text field"})
    voice_id, voice_settings = audio_core.VOICES[srow["voice"]]
    voice_settings = {**voice_settings, "speed": audio_core.speed_for_trip(srow["trip_id"])}
    clip_text = text if (extent == "custom" and text) else frow["current_text"]
    cleaned, _ = audio_core.validate_and_clean(
        audio_core.strip_url_lines(clip_text or ""), srow["trip_id"], frow["scene_index"])
    mp3 = audio_core.generate_audio(cleaned, voice_id, voice_settings)
    dirs = work_dirs(sid)
    fpath = dirs["fallback"] / f"{fid}.mp3"
    fpath.write_bytes(mp3)
    try:
        _fb_mp3n = frow["mp3_name"]
        if _fb_mp3n:
            review_audio.upload(srow["trip_id"], fpath,
                                f"{_fb_mp3n[:-4]}_fallback.mp3")
    except Exception:  # noqa: BLE001
        pass
    db.update_fields(fid, fallback_mp3_path=str(fpath), fallback_desc=description or "",
                     flag="edit_required",
                     comment=_append_note(frow["comment"],
                                          f"Manual edit requested: {description}"))
    db.touch_session(sid)
    return serialize_field(sid, _field_row(sid, fid))


def import_mp3(sid: str, fid: int, data: bytes) -> dict:
    srow = _session_row(sid)
    frow = _field_row(sid, fid)
    if not frow["has_audio"]:
        raise HTTPException(400, detail={"error": "no_audio", "detail": "text field"})
    dirs = work_dirs(sid)
    tmp = dirs["candidate"] / f"import_{fid}.mp3"
    tmp.write_bytes(data)
    # S7: actually re-encode — decode to 44100/mono then re-encode (libmp3lame) so a
    # hand-edited import (any rate/channel count) lands as a clean, consistent master.
    samples = audio_io.mp3_to_samples(tmp)
    whash = _set_working(sid, frow, samples=samples, kind="admin_import")
    _r2_upload_working(srow["trip_id"], dirs, frow)
    tmp.unlink(missing_ok=True)
    patch = {"working_audio_hash": whash, "splice_confidence": None,
             "candidate_mp3_path": None}
    patch.update(_clear_coverage_and_done(frow))
    db.update_fields(fid, **patch)
    db.touch_session(sid)
    return serialize_field(sid, _field_row(sid, fid))


def played(sid: str, fid: int, ranges: list[list[float]]) -> dict:
    _session_row(sid)
    frow = _field_row(sid, fid)
    if not frow["has_audio"]:
        return {"played_coverage": [], "can_mark_done": True}
    cov = json.loads(frow["played_coverage_json"] or "{}")
    existing = cov.get("ranges", []) if cov.get("hash") == frow["working_audio_hash"] else []
    # S8: clamp each incoming range to [0, working_duration] so a bogus [[0, 1e9]]
    # can't satisfy the 95% done gate.
    dur = _working_duration(frow)
    incoming = []
    for r in ranges:
        a = max(0.0, min(float(r[0]), dur))
        b = max(0.0, min(float(r[1]), dur))
        if b > a:
            incoming.append([a, b])
    merged = _merge_ranges(existing + incoming)
    db.update_fields(fid, played_coverage_json=json.dumps(
        {"hash": frow["working_audio_hash"], "ranges": merged}))
    frow = _field_row(sid, fid)
    _, done_ok = _coverage_for(frow)
    return {"played_coverage": merged, "can_mark_done": done_ok}


def set_flag(sid: str, fid: int, flag: str) -> dict:
    _session_row(sid)
    frow = _field_row(sid, fid)
    if flag == "done":
        _, done_ok = _coverage_for(frow)
        if not done_ok:
            raise HTTPException(409, detail={
                "error": "coverage", "detail": "play the whole clip before marking done"})
    db.update_fields(fid, flag=flag)
    db.touch_session(sid)
    return serialize_field(sid, _field_row(sid, fid))


def set_comment(sid: str, fid: int, text: str) -> dict:
    _session_row(sid)
    _field_row(sid, fid)
    db.update_fields(fid, comment=text or "")
    db.touch_session(sid)
    return serialize_field(sid, _field_row(sid, fid))


def revert(sid: str, fid: int) -> dict:
    _session_row(sid)
    frow = _field_row(sid, fid)
    patch = {"current_text": frow["original_text"], "flag": "none",
             "candidate_mp3_path": None, "splice_confidence": None,
             "played_coverage_json": "{}"}
    if frow["has_audio"] and frow["mp3_name"]:
        dirs = work_dirs(sid)
        name = frow["mp3_name"]
        orig = dirs["orig"] / name
        if orig.exists():
            audio_io.mp3_to_mp3_copy(orig, dirs["working"] / name)
            patch["working_audio_hash"] = _file_hash(dirs["working"] / name)
    db.update_fields(fid, **patch)
    db.touch_session(sid)
    return serialize_field(sid, _field_row(sid, fid))


def _append_note(comment: str, note: str) -> str:
    note = (note or "").strip()
    if not note:
        return comment or ""
    if comment and note in comment:
        return comment
    return (comment + "\n" if comment else "") + f"[auto] {note}"


# --------------------------------------------------------------------------- #
# Submit
# --------------------------------------------------------------------------- #
_LEAK_PATTERNS = [
    (re.compile(r"https?://", re.I), "leaked URL"),
    (re.compile(r"\b(VID|PIC)[\s_]", re.I), "media/scene heading leaked"),
    (re.compile(r"\.(mp3|ogg|mp4|jpe?g|png|srt)\b", re.I), "media filename leaked"),
    (re.compile(r"\[(?:source|cite|ref)[^\]]*\]", re.I), "citation tag leaked"),
    (re.compile(r"⚠|NEEDS REVIEW|SOURCE NEEDED", re.I), "review marker leaked"),
]


def _validate_text(field_path: str, scene_index, text: str) -> list[dict]:
    issues = []
    for rx, msg in _LEAK_PATTERNS:
        if rx.search(text or ""):
            issues.append({"scene_index": scene_index, "field_path": field_path,
                           "issue": msg, "severity": "block"})
    return issues


def submit(sid: str) -> dict:
    srow = _session_row(sid)
    trip_id = srow["trip_id"]
    frows = db.query(
        "SELECT * FROM field_edits WHERE session_id=? ORDER BY id", (sid,))

    # ---- validation ----
    hard: list[dict] = []
    soft: list[dict] = []
    changed = [f for f in frows if (f["current_text"] or "") != (f["original_text"] or "")]

    for f in changed:
        fp = f["field_path"]
        out_fp = (f"questionOption[{f['option_index']}]"
                  if fp == "questionOption" else fp)
        hard += _validate_text(out_fp, f["scene_index"], f["current_text"])
        if fp in ("questionKey", "questionOption"):
            soft.append({"scene_index": f["scene_index"], "field_path": out_fp,
                         "issue": "edited question/option — check additionalAnswerKeys "
                                  "(American spelling / numbers written out)",
                         "severity": "note"})

    # last 360 scene must carry no question/keyword
    trip_live = None
    try:
        trip_live = get_trip(trip_id)
    except SystemExit as e:
        raise HTTPException(404, detail=str(e))
    qt_live = list(trip_live.get("quickTrips") or [])
    if qt_live:
        last = len(qt_live) - 1
        last_q = next((f for f in frows
                       if f["scene_index"] == last and f["field_path"] == "questionKey"
                       and (f["current_text"] or "").strip()), None)
        if last_q:
            hard.append({"scene_index": last, "field_path": "questionKey",
                         "issue": "question/keyword on the final 360 scene — not allowed",
                         "severity": "block"})

    if hard:
        return {"ok": False, "validation": hard + soft, "written": [],
                "awaiting_stage9": False}

    # ---- write changed TEXT onto the FRESH live quickTrips (one update) ----
    written: list[str] = []
    top_level: dict = {}
    scene_changed = False
    for f in changed:
        fp, si = f["field_path"], f["scene_index"]
        val = f["current_text"]
        if fp == "contentTitleKey":
            top_level["contentTitleKey"] = val
            written.append("contentTitleKey")
        elif fp == "tripgroup_description":
            continue   # handled on the TripGroup below
        elif si is not None and si < len(qt_live):
            sc = qt_live[si]
            applied = True
            if fp == "SceneDesc":
                sc["SceneDesc"] = val
            elif fp == "titleKey":
                sc["titleKey"] = val
            elif fp == "questionKey":
                sc["questionKey"] = val
            elif fp == "questionOption":
                opts = list(sc.get("questionOptionKeys") or [])
                k = f["option_index"] or 0
                if k < len(opts):
                    opts[k] = val
                    sc["questionOptionKeys"] = opts
                else:
                    applied = False
            else:
                applied = False
            if applied:
                scene_changed = True
                written.append(f"quickTrips[{si}].{fp}")

    # S5: only rewrite the quickTrips array when a scene field actually changed —
    # a title-only / description-only edit must not clobber concurrent scene edits.
    update_trip_text(trip_id, qt_live if scene_changed else None, top_level)

    # ---- TripGroup description + re-derived categories ----
    desc_field = next((f for f in changed if f["field_path"] == "tripgroup_description"),
                      None)
    if desc_field:
        tg_id, tg_live = get_tripgroup(trip_id)
        live_cats = (tg_live or {}).get("tripCategories") or []
        old_desc = (tg_live or {}).get("descriptionTarget") or desc_field["original_text"]
        new_desc = desc_field["current_text"]
        cats = merge_categories(old_desc, new_desc, live_cats)
        update_tripgroup(tg_id, new_desc, cats)
        written.append("TripGroup.descriptionTarget")

    # ---- promote changed working mp3s to the masters (archive prior master) ----
    mp3_dir = resolve_audio_dir(trip_id, trip_live)   # Quicktrips, else Audio Generation
    ver_dir = mp3_dir / "versions"
    dirs = work_dirs(sid)
    promoted: list[str] = []
    for f in frows:
        if not f["has_audio"] or not f["mp3_name"]:
            continue
        name = f["mp3_name"]
        working = dirs["working"] / name
        pristine = dirs["orig"] / name
        if not working.exists():
            continue
        if _file_hash(working) == _file_hash(pristine):
            continue   # audio unchanged → leave master alone
        master = mp3_dir / name
        if master.exists():
            ver_dir.mkdir(parents=True, exist_ok=True)
            stem = name[:-4]
            n = _next_version_suffix(ver_dir, stem)
            audio_io.mp3_to_mp3_copy(master, ver_dir / f"{stem}v{n}.mp3")
        audio_io.mp3_to_mp3_copy(working, master)
        promoted.append(name)

    db.execute("UPDATE sessions SET status='submitted', updated_at=? WHERE id=?",
               (time.time(), sid))
    return {"ok": True, "validation": soft, "written": written,
            "promoted_mp3": promoted, "awaiting_stage9": True}


# --------------------------------------------------------------------------- #
# Audio path resolution (for routes_audio)
# --------------------------------------------------------------------------- #
def audio_path(sid: str, fid: int, which: str) -> Path:
    frow = _field_row(sid, fid)
    dirs = work_dirs(sid)
    name = frow["mp3_name"]
    if which == "original" and name:
        return dirs["orig"] / name
    if which == "working" and name:
        return dirs["working"] / name
    if which == "candidate" and frow["candidate_mp3_path"]:
        return Path(frow["candidate_mp3_path"])
    if which == "fallback" and frow["fallback_mp3_path"]:
        return Path(frow["fallback_mp3_path"])
    raise HTTPException(404, detail={"error": "no_audio", "detail": which})


def version_path(sid: str, fid: int, n: int) -> Path:
    row = db.query_one(
        "SELECT path FROM audio_versions WHERE session_id=? AND field_id=? AND n=?",
        (sid, fid, n))
    if not row:
        raise HTTPException(404, detail={"error": "no_version", "detail": str(n)})
    return Path(row["path"])


def _resolve_overlay_file(trip_id: str, mp3_dir: Path | None, ogg_dir: Path | None,
                          filename: str) -> Path | None:
    """Pure resolver (no DB) for an overlay / static-360 image, display only. Static-360
    stills ({i}.jpg) live in the OGG folder; flat overlays live under the trip's data
    cache. Returns None if nothing is found (caller nulls the URL)."""
    from .config import OVERLAY_SEARCH_DIRS
    safe = Path(filename).name
    candidates = [
        OVERLAY_SEARCH_DIRS[0] / trip_id / "static_images" / safe,
        OVERLAY_SEARCH_DIRS[0] / trip_id / safe,
    ]
    if ogg_dir is not None:
        candidates.append(ogg_dir / safe)
    if mp3_dir is not None:
        candidates.append(mp3_dir / safe)
    candidates += [d / safe for d in OVERLAY_SEARCH_DIRS]
    for c in candidates:
        if c.exists():
            return c
    return None


def overlay_path(sid: str, filename: str) -> Path | None:
    """Best-effort overlay/static-360 image resolution for display only."""
    srow = _session_row(sid)
    trip_id = srow["trip_id"]
    try:
        _p = paths_for(json.loads(srow["loaded_trip_json"]), trip_id)
        mp3_dir, ogg_dir = _p["mp3_dir"], _p["ogg_dir"]
    except SystemExit:
        mp3_dir = ogg_dir = None
    return _resolve_overlay_file(trip_id, mp3_dir, ogg_dir, filename)
