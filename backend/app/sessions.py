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
from .config import (COUNTRY_VOICE_GUESS, COVERAGE_DONE_FRACTION,
                     LANGUAGE_FALLBACK_VOICE, WORK_ROOT)
from .locks import WHISPER_LOCK
from .staging import (db as fb_db, get_trip, get_tripgroup, merge_categories,
                      paths_for, tripgroup_id_for, update_trip_text,
                      update_tripgroup)

from stage9.common import COUNTRY_CFG
from stage9.whisper_timing import transcribe_words

_TRIP_CACHE: dict[str, dict] = {}   # trip_id → {title, folder_name} (process-life)

# Per-trip voice override file (highest priority). Shape: {"<trip_id>": "isla"}.
_VOICE_OVERRIDES = config.BACKEND_ROOT / "voice_overrides.json"


def _staging_choices(trip_id: str) -> dict:
    """The drafting choices for a trip — staging_choices.json from whichever
    language data root holds it (CEFR English / N4 Japanese / HSK Mandarin / native).
    Shape: {"voice": "Andrea", "gender": "female", "music": ..., ...}. {} if absent."""
    for root in config.RW_DATA_ROOTS:
        p = root / trip_id / "staging_choices.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except Exception:
            return {}
    return {}


def _override_voice(trip_id: str) -> str | None:
    try:
        v = (json.loads(_VOICE_OVERRIDES.read_text(encoding="utf-8"))
             .get(trip_id) or "").strip().lower()
        return v if v in audio_core.VOICE_REGISTRY else None
    except Exception:
        return None


def _manifest_voice(trip_id: str) -> str | None:
    """The voice set on the trip's Trello card and carried into the manifest's `voice`
    field by export_review_trips.py. Authoritative — the card is the source of truth."""
    try:
        data = json.loads(config.MANIFEST_PATH.read_text(encoding="utf-8"))
        entries = data if isinstance(data, list) else (data.get("trips") or [])
        for t in entries:
            if t.get("trip_id") == trip_id:
                v = (t.get("voice") or "").strip().lower()
                return v if v in audio_core.VOICE_REGISTRY else None
    except Exception:
        pass
    return None


def resolve_voice(trip_id: str, country: str) -> str:
    """Resolve the narrator voice for a trip, in priority order:
      1. manifest `voice`               — set on the Trello card (source of truth)
      2. backend/voice_overrides.json   — manual escape hatch / card-voice bridge
      3. staging_choices.json "voice"   — an explicit voice name from drafting
      4. staging_choices.json "gender"  — male/female → (language, country) voice
      5. COUNTRY_VOICE_GUESS            — last-resort country default
      6. LANGUAGE_FALLBACK_VOICE        — last-of-last: at least stay in-language
    The drafting/card decision is authoritative; the human listen is the backstop."""
    mv = _manifest_voice(trip_id)
    if mv:
        return mv
    ov = _override_voice(trip_id)
    if ov:
        return ov
    ch = _staging_choices(trip_id)
    v = (ch.get("voice") or "").strip().lower()
    if v in audio_core.VOICE_REGISTRY:
        return v
    language = audio_core.language_of(trip_id)
    by_gender = audio_core.voice_for_gender(language, country, ch.get("gender"))
    if by_gender:
        return by_gender
    return (COUNTRY_VOICE_GUESS.get(country)
            or LANGUAGE_FALLBACK_VOICE.get(language, "harry"))


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _new_sid() -> str:
    return "sess_" + uuid.uuid4().hex[:12]


def work_dirs(sid: str) -> dict[str, Path]:
    base = WORK_ROOT / sid
    d = {k: base / k for k in ("orig", "working", "versions", "candidate", "fallback", "clips")}
    for p in d.values():
        p.mkdir(parents=True, exist_ok=True)
    return d


def mp3_name(field_path: str, scene_index: int | None, option_index: int | None) -> str | None:
    if field_path == "SceneDesc":
        return f"{scene_index}.mp3"
    if field_path == "questionKey":
        return f"{scene_index}_q.mp3"
    if field_path == "questionOption":
        # Options are 1-indexed on disk: opt[0]→_a1, opt[1]→_a2 … ({i}_a.mp3 with no
        # number is the ANSWER, not an option).
        return f"{scene_index}_a{(option_index or 0) + 1}.mp3"
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


def _session_meta(tid: str) -> tuple[bool, str | None, bool]:
    """(has_session, status, any_edit_required) for the latest session of a trip."""
    srow = db.query_one(
        "SELECT id,status FROM sessions WHERE trip_id=? "
        "ORDER BY created_at DESC LIMIT 1", (tid,))
    if not srow:
        return False, None, False
    er = db.query_one("SELECT 1 FROM field_edits WHERE session_id=? AND "
                      "flag='edit_required' LIMIT 1", (srow["id"],))
    return True, srow["status"], er is not None


def list_trips(user=None) -> list[dict]:
    """Trello-manifest-driven when ``trips_to_review.json`` exists, else the legacy
    Quicktrips MP3-dir scan.

    A present-but-unreadable manifest (e.g. a partial write mid re-export, or a bare
    list) returns an EMPTY list — it must NOT fall back to the all-audio-dir scan, which
    surfaces every `_EN` trip including ones outside the review lanes (Edinburgh etc.).
    The scan is only for first-run setups with no manifest at all.

    Language scoping is applied HERE (in the wrapper) so neither the manifest branch nor
    the scan branch can forget it — admins see all; reviewers only their language(s)."""
    if config.MANIFEST_PATH.exists():
        try:
            trips = _list_trips_from_manifest()
        except Exception as e:  # noqa: BLE001 - never 500 the list on a bad manifest
            print(f"[trips] manifest unreadable ({e}); returning empty list — fix "
                  "trips_to_review.json (NOT scanning all audio dirs)")
            trips = []
    else:
        trips = _list_trips_from_scan()
    if user is not None:
        from . import auth   # lazy (auth imports sessions) — no module-load cycle
        trips = [t for t in trips if auth.language_allowed(user, t["trip_id"])]
    return trips


def _list_trips_from_manifest() -> list[dict]:
    data = json.loads(config.MANIFEST_PATH.read_text(encoding="utf-8"))
    # Tolerate both the canonical {"trips":[…]} and a bare […] list.
    entries = data if isinstance(data, list) else (data.get("trips") or [])
    out: list[dict] = []
    for t in entries:
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
        has_session, status, edit_required = _session_meta(tid)
        lvl, fam = _level_family(tid)
        out.append({
            "trip_id": tid,
            "title": title,
            "folder_name": folder_name,
            "lane": t.get("lane"),
            "level": t.get("level") or lvl,        # prefer the manifest's own (export-set)
            "family": t.get("family") or fam,
            "has_session": has_session,
            "status": status,
            "edit_required": edit_required,
            "reviewable": reviewable,
        })
    return out


# trip-id suffix → (level label, family-base). Longest/most-specific suffix first.
_LEVEL_SUFFIXES = [
    ("_A12_EN", "A12"), ("_B1_EN", "B1"), ("_B2_EN", "B2"),
    ("_Beg_N5_JP", "N5"), ("_Beg_N4_JP", "N4"), ("_N5_JP", "N5"), ("_N4_JP", "N4"),
    ("_HSK12_ZH", "HSK1-2"), ("_HSK3_ZH", "HSK3"),
    ("_EN", "EN"), ("_ZH", "ZH"), ("_JP", "JP"),
]


def _level_family(trip_id: str) -> tuple[str, str]:
    """(level, family-base) from a content-id suffix — so the trip list can group a
    place's variants (EN / A12 / B1 / N4 / HSK…) under one family."""
    for suf, lvl in _LEVEL_SUFFIXES:
        if trip_id.endswith(suf):
            return lvl, trip_id[: -len(suf)]
    return "", trip_id


# Beginner trips keep a fixed trailing pause so the learner has time to absorb the
# clip; every other level has its excess end-silence removed. Level labels are the
# ones _level_family returns.
_BEGINNER_LEVELS = {"A12", "N5", "HSK1-2"}
_BEGINNER_TAIL_SECONDS = 3.0
_DEFAULT_TAIL_SECONDS = 0.4   # small natural tail kept on all other levels


def _target_tail_seconds(trip_id: str) -> float:
    """Required trailing silence for a trip: 3s on beginner levels (A1-2 / N5 / HSK1-2),
    otherwise a small 0.4s tail (excess beyond that is trimmed)."""
    lvl, _ = _level_family(trip_id)
    return _BEGINNER_TAIL_SECONDS if lvl in _BEGINNER_LEVELS else _DEFAULT_TAIL_SECONDS


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
                has_session, status, edit_required = _session_meta(tid)
                out.append({
                    "trip_id": tid,
                    "title": meta["title"],
                    "folder_name": meta["folder_name"],
                    "lane": None,
                    "has_session": has_session,
                    "status": status,
                    "edit_required": edit_required,
                    "reviewable": True,
                })
    return out


# --------------------------------------------------------------------------- #
# Seed / resume
# --------------------------------------------------------------------------- #
def create_or_resume(trip_id: str, user) -> dict:
    # [P0-1] Language gate at the TOP — the create is keyed on trip_id, so the
    # per-{sid} scoping dependency structurally can't cover it. Admins bypass.
    from . import auth   # lazy import (auth imports sessions) — no module-load cycle
    if not auth.language_allowed(user, trip_id):
        raise HTTPException(403, detail={
            "error": "forbidden",
            "detail": "this trip's narration language is not assigned to you"})
    # Resume the newest non-terminal session (in_review / submitted / changes_requested);
    # `approved` is terminal, so a fresh open re-seeds from the now-promoted masters.
    existing = db.query_one(
        "SELECT id FROM sessions WHERE trip_id=? AND "
        "status IN ('in_review','submitted','changes_requested') "
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
    # Prefer the TripGroup description; fall back to the Trip doc's own descriptionTarget
    # (leveled English trips — Bath1_A12_EN — nest in the base group, so their TripGroup
    # lookup misses; their per-level description lives on the Trip doc).
    tg_desc = (tg or {}).get("descriptionTarget") or trip.get("descriptionTarget") or ""

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
                  option_index=None, source_text=""):
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
        # English translation (non-_EN trips carry the *En sibling) — an editable second
        # text shown under the target; suppress only when it's identical to the target
        # (e.g. an _EN trip, or a bilingual titleKey that already embeds the English).
        src = source_text or ""
        if src.strip() == (original_text or "").strip():
            src = ""
        fid = db.execute(
            "INSERT INTO field_edits(session_id,scene_index,field_path,option_index,"
            "has_audio,mp3_name,original_text,current_text,current_mp3_path,"
            "working_audio_hash,source_text,original_source,working_text,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, scene_index, field_path, option_index, 1 if has_audio else 0,
             name if has_audio else None, original_text or "", original_text or "",
             cur_path, whash, src, src, original_text or "", time.time()))
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
    add_field(None, "tripgroup_description", tg_desc, False,
              source_text=trip.get("descriptionHome") or "")

    # scenes (non-_EN trips carry English-source siblings: *En)
    for i, s in enumerate(trip.get("quickTrips") or []):
        add_field(i, "titleKey", s.get("titleKey") or "", False,
                  source_text=s.get("titleKeyEn") or "")
        desc = (s.get("SceneDesc") or "").strip()
        has_a = bool(s.get("hasAudio"))
        if desc or has_a:
            add_field(i, "SceneDesc", s.get("SceneDesc") or "", has_a,
                      source_text=s.get("SceneDescEn") or "")
        if (s.get("questionKey") or "").strip():
            add_field(i, "questionKey", s.get("questionKey") or "", True,
                      source_text=s.get("questionKeyEn") or "")
        opts_en = s.get("questionOptionKeysEn") or []
        for k, opt in enumerate(s.get("questionOptionKeys") or []):
            add_field(i, "questionOption", opt or "", True, option_index=k,
                      source_text=(opts_en[k] if k < len(opts_en) else ""))

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


# Reviewer-editable states. `submitted`/`approving` are locked (admin owns the review
# snapshot); `approved` is terminal. `changes_requested` is editable again.
_EDITABLE_STATUSES = ("in_review", "changes_requested")


def trip_id_for_session(sid: str) -> str:
    """Resolve a session's trip_id (for the auth language-scoping dependency). 404 if
    the session is unknown."""
    row = db.query_one("SELECT trip_id FROM sessions WHERE id=?", (sid,))
    if not row:
        raise HTTPException(404, detail={"error": "no_session", "detail": sid})
    return row["trip_id"]


def assert_editable(sid: str) -> None:
    """403 while a session is locked (submitted/approving/approved). Called by the
    scope_sid_editable dependency to gate every editing route once a trip is submitted."""
    row = db.query_one("SELECT status FROM sessions WHERE id=?", (sid,))
    if not row:
        raise HTTPException(404, detail={"error": "no_session", "detail": sid})
    if row["status"] not in _EDITABLE_STATUSES:
        raise HTTPException(403, detail={
            "error": "locked",
            "detail": f"session is '{row['status']}' and read-only; it is awaiting "
                      "admin approval (or already approved)"})


def _working_duration(frow) -> float:
    if not frow["has_audio"] or not frow["current_mp3_path"]:
        return 0.0
    p = Path(frow["current_mp3_path"])
    return audio_io.mp3_duration_seconds(p) if p.exists() else 0.0


def _orig_path(sid: str, frow) -> Path | None:
    if not (frow["has_audio"] and frow["mp3_name"]):
        return None
    p = work_dirs(sid)["orig"] / frow["mp3_name"]
    return p if p.exists() else None


def _original_done(sid: str, frow) -> bool:
    """Whether full playback of the ORIGINAL satisfies Done — allowed ONLY when the field
    is genuinely untouched (text unchanged, no pending candidate, working still equals the
    master). That keeps the 'heard the original, it's correct' path from ever green-lighting
    an unheard regenerated take."""
    if frow["current_text"] != frow["original_text"] or frow["candidate_mp3_path"]:
        return False
    orig = _orig_path(sid, frow)
    if orig is None or (frow["working_audio_hash"] or "") != _file_hash(orig):
        return False
    ranges = (json.loads(frow["original_coverage_json"] or "{}")).get("ranges", [])
    dur = audio_io.mp3_duration_seconds(orig)
    return bool(dur) and _coverage_total(ranges) >= COVERAGE_DONE_FRACTION * dur


def _coverage_for(sid: str, frow) -> tuple[list[list[float]], bool]:
    """Return (working_coverage_ranges, can_mark_done). Done unlocks on full playback of
    the working take OR (untouched field only) of the original."""
    if not frow["has_audio"]:
        return [], True
    cov = json.loads(frow["played_coverage_json"] or "{}")
    ranges = cov.get("ranges", []) if cov.get("hash") == frow["working_audio_hash"] else []
    dur = _working_duration(frow)
    working_done = bool(dur) and _coverage_total(ranges) >= COVERAGE_DONE_FRACTION * dur
    return ranges, working_done or _original_done(sid, frow)


def serialize_field(sid: str, frow) -> dict:
    fid = frow["id"]
    has_audio = bool(frow["has_audio"])
    field_path = frow["field_path"]
    if field_path == "questionOption":
        field_path_out = f"questionOption[{frow['option_index']}]"
    else:
        field_path_out = field_path

    ranges, done_ok = _coverage_for(sid, frow)

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

    # Undo/redo through the audio version history (v0 master → each edit). cursor=None
    # means "on the latest take".
    max_n = max((v_["n"] for v_ in db.query(
        "SELECT n FROM audio_versions WHERE field_id=?", (fid,))), default=0)
    cursor = frow["version_cursor"] if frow["version_cursor"] is not None else max_n
    can_undo = has_audio and cursor > 0
    can_redo = has_audio and cursor < max_n

    return {
        "fid": fid,
        "scene_index": frow["scene_index"],
        "field_path": field_path_out,
        "has_audio": has_audio,
        "original_text": frow["original_text"],
        "current_text": frow["current_text"],
        "source_text": frow["source_text"] or "",
        "original_source": frow["original_source"] or "",
        "flag": frow["flag"],
        "comment": frow["comment"],
        "splice_confidence": frow["splice_confidence"],
        "played_coverage": ranges,
        "original_played_coverage": (
            json.loads(frow["original_coverage_json"] or "{}")).get("ranges", []),
        "can_mark_done": done_ok,
        "can_undo": can_undo,
        "can_redo": can_redo,
        "audio": audio,
        "versions": versions,
        "manual_clips": _clips_for(sid, fid) if has_audio else [],
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
        "submitted_by": _srow_get(srow, "submitted_by"),
        "approved_by": _srow_get(srow, "approved_by"),
        "review_note": _srow_get(srow, "review_note"),
        "voice": srow["voice"],
        "voice_display": audio_core.display_name(srow["voice"]),
        "speed": _effective_speed(srow),
        "speed_override": _srow_get(srow, "speed_override"),
        "model": _effective_model(srow),
        "model_override": _srow_get(srow, "model_override"),
        "trip_categories": json.loads(srow["trip_categories_json"] or "[]"),
        "trip_fields": trip_fields,
        "scenes": scenes_out,
    }


# --------------------------------------------------------------------------- #
# Lazy caches (Whisper + cleaned original)
# --------------------------------------------------------------------------- #
def _cleaned_orig(srow, frow) -> tuple[str, bool]:
    """Cleaned text of the CURRENT working audio — the base a segment/highlight splice
    diffs against, so successive edits accumulate on the combined take. Re-cleans
    whenever the working text changes (cache keyed on its hash)."""
    base = frow["working_text"] if frow["working_text"] else (frow["original_text"] or "")
    base = audio_core.strip_url_lines(base)
    h = hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]
    cache = json.loads(srow["cleaned_orig_json"] or "{}")
    key = str(frow["id"])
    c = cache.get(key)
    if c and c.get("h") == h:
        return c["text"], c.get("fallback", False)
    cleaned, fb = audio_core.validate_and_clean(base, srow["trip_id"], frow["scene_index"])
    cache[key] = {"text": cleaned, "fallback": fb, "h": h}
    db.execute("UPDATE sessions SET cleaned_orig_json=? WHERE id=?",
               (json.dumps(cache), srow["id"]))
    return cleaned, fb


def _whisper_orig(srow, frow) -> list[dict]:
    """Word timings for the CURRENT working audio (so cut times track the combined take,
    not the pristine master). Content-hash keyed → re-transcribes after each combine."""
    audio = work_dirs(srow["id"])["working"] / frow["mp3_name"]
    if not audio.exists():
        return []
    meta_dir = config.WORK_ROOT / srow["id"] / "whisper"
    meta_dir.mkdir(parents=True, exist_ok=True)
    cache_json = meta_dir / (audio.stem + ".json")
    sidecar = meta_dir / (audio.stem + ".audiohash")
    cur_hash = _file_hash(audio)
    prev_hash = sidecar.read_text().strip() if sidecar.exists() else None
    refresh = cache_json.exists() and prev_hash != cur_hash
    with WHISPER_LOCK:
        data = transcribe_words(audio, lang="en", model_name="small",
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


def update_source(sid: str, fid: int, text: str) -> dict:
    """Autosave the editable English translation (the *En sibling) of a non-EN field.
    Text-only — no audio, so it never touches coverage/done."""
    _session_row(sid)
    _field_row(sid, fid)
    db.update_fields(fid, source_text=text or "")
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
    # the working take now sits on this newest version → undo can step back to n-1,
    # and any redo branch that an earlier undo left open is truncated.
    db.execute("UPDATE field_edits SET version_cursor=? WHERE id=?", (n, frow["id"]))
    return _file_hash(working)


def regenerate(sid: str, fid: int, mode: str, rng: dict | None,
               alt_text: str | None = None) -> dict:
    srow = _session_row(sid)
    frow = _field_row(sid, fid)
    if not frow["has_audio"]:
        raise HTTPException(400, detail={"error": "no_audio",
                                         "detail": "field has no audio"})
    voice_id, voice_settings = audio_core.VOICES[srow["voice"]]
    voice_settings = {**voice_settings, "speed": _effective_speed(srow)}
    model_id = _effective_model(srow)
    dirs = work_dirs(sid)
    cand_path = dirs["candidate"] / f"{fid}.mp3"

    field_path = frow["field_path"]
    cur = audio_core.strip_url_lines(frow["current_text"] or "")

    # Q&A fields and SceneDesc 'whole' → whole regenerate (no splice). Alt text (if
    # supplied) is voiced VERBATIM as the whole block — "regenerate with alt text" for a
    # question option, mirroring highlight-with-alt-text but for the entire field.
    if field_path != "SceneDesc" or mode == "whole":
        if alt_text is not None and alt_text.strip():
            plan = audio_splice.plan_whole(alt_text.strip(), False, voice_id,
                                           voice_settings, model_id)
        else:
            cleaned, fb = audio_core.validate_and_clean(
                cur, srow["trip_id"], frow["scene_index"])
            plan = audio_splice.plan_whole(cleaned, fb, voice_id, voice_settings, model_id)
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
                if mode in ("highlight", "alt") and rng:
                    hl_span = audio_splice.highlight_span_in_cleaned(
                        cur, cleaned_new, int(rng["start"]), int(rng["end"]))
                base_samples = audio_io.mp3_to_samples(dirs["working"] / frow["mp3_name"])
                plan = audio_splice.plan_segment(
                    srow["trip_id"], cleaned_orig, cleaned_new, fb_new, words,
                    voice_id, voice_settings, base_samples, audio_io.SR,
                    highlight_orig_span=hl_span, model_id=model_id,
                    alt_text=alt_text if mode == "alt" else None)

    # Hard reject (segment guards): edit_required AND no candidate audio at all.
    if plan.edit_required and plan.candidate_mp3 is None:
        db.update_fields(fid, flag="edit_required",
                         candidate_mp3_path=None,
                         comment=_append_note(frow["comment"], plan.reason))
        db.touch_session(sid)
        return serialize_field(sid, _field_row(sid, fid))

    # Candidate available (segment splice plan, or a whole regen that may be S2-flagged).
    # Keep a pristine copy, then auto-trim a trailing breath / next-sound bleed off the END
    # so the audition (and the splice) don't carry it. Trailing-only → candidate word
    # timings (cand_words, indexed from the start) stay valid for the span splice. The
    # reviewer can fine-tune via /trim-candidate, re-derived from this pristine copy.
    cand_path.write_bytes(plan.candidate_mp3)
    pristine = dirs["candidate"] / f"{fid}.orig.mp3"
    pristine.write_bytes(plan.candidate_mp3)
    cand_samples = audio_io.mp3_to_samples(cand_path)
    trimmed = audio_io.trim_trailing_breath(cand_samples, audio_io.SR)
    if len(trimmed) < len(cand_samples):
        audio_io.samples_to_mp3(trimmed, cand_path)
    plan.meta["cand_trim_ms"] = round(
        (len(cand_samples) - len(trimmed)) / audio_io.SR * 1000.0, 1)
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
    # Every combined take ends on the trip's required trailing pause: beginner trips
    # (A1-2 / N5 / HSK1-2) keep 3s, other levels a small 0.4s. A whole TTS candidate
    # lacks the beginner tail and a segment splice can disturb it, so normalize here
    # rather than relying on what the candidate happens to carry.
    tail_target = _target_tail_seconds(srow["trip_id"])

    if meta.get("mode") == "whole":
        # whole-block / Q&A: replace working with the candidate take.
        cand = audio_io.mp3_to_samples(frow["candidate_mp3_path"])
        cand = audio_io.set_trailing_silence(cand, audio_io.SR, tail_target)
        whash = _set_working(sid, frow, samples=cand, kind="splice")
        _r2_upload_working(srow["trip_id"], dirs, frow)
        patch = {"working_audio_hash": whash, "splice_confidence": None,
                 "candidate_mp3_path": None, "working_text": frow["current_text"]}
        patch.update(_clear_coverage_and_done(frow))
        db.update_fields(fid, **patch)
        db.touch_session(sid)
        return serialize_field(sid, _field_row(sid, fid))

    # segment splice — into the CURRENT working take, so successive edits accumulate
    # (the cut times in meta were read from this same working audio's Whisper).
    name = frow["mp3_name"]
    base = audio_io.mp3_to_samples(dirs["working"] / name)
    cand = audio_io.mp3_to_samples(frow["candidate_mp3_path"])
    result = audio_splice.do_splice(base, cand, meta)

    spliced = audio_io.set_trailing_silence(result.samples, audio_io.SR, tail_target)
    whash = _set_working(sid, frow, samples=spliced, kind="splice")
    _r2_upload_working(srow["trip_id"], dirs, frow)
    patch = {"working_audio_hash": whash,
             "splice_confidence": result.confidence,
             "candidate_mp3_path": None, "working_text": frow["current_text"],
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


def trim_candidate(sid: str, fid: int, delta_ms: float) -> dict:
    """Nudge how much is trimmed off the END of the current candidate before combining
    (Issue 3 — TTS leaves a breath/next-sound bleed). ``delta_ms`` > 0 trims more, < 0
    restores. Re-derived from the pristine candidate copy so it is fully reversible; the
    span splice respects the shorter of this trim and its own word-based end."""
    srow = _session_row(sid)
    frow = _field_row(sid, fid)
    cand = frow["candidate_mp3_path"]
    if not cand or not Path(cand).exists():
        raise HTTPException(409, detail={"error": "no_candidate", "detail": "regenerate first"})
    dirs = work_dirs(sid)
    pristine = dirs["candidate"] / f"{fid}.orig.mp3"
    if not pristine.exists():                     # older candidate → seed pristine from it
        audio_io.mp3_to_mp3_copy(cand, pristine)
    meta = json.loads(frow["splice_meta_json"] or "{}")
    samples = audio_io.mp3_to_samples(pristine)
    sr = audio_io.SR
    n = len(samples)
    max_trim_ms = max(0.0, (n / sr - 0.15) * 1000.0)        # always keep ≥150 ms
    new_trim = min(max(0.0, float(meta.get("cand_trim_ms", 0.0)) + float(delta_ms)),
                   max_trim_ms)
    keep = max(0, n - int(round(new_trim / 1000.0 * sr)))
    audio_io.samples_to_mp3(samples[:keep], Path(cand))
    meta["cand_trim_ms"] = round(new_trim, 1)
    db.update_fields(fid, splice_meta_json=json.dumps(meta))
    db.touch_session(sid)
    return serialize_field(sid, _field_row(sid, fid))


def fallback(sid: str, fid: int, extent: str, text: str | None, description: str) -> dict:
    srow = _session_row(sid)
    frow = _field_row(sid, fid)
    if not frow["has_audio"]:
        raise HTTPException(400, detail={"error": "no_audio", "detail": "text field"})
    voice_id, voice_settings = audio_core.VOICES[srow["voice"]]
    voice_settings = {**voice_settings, "speed": _effective_speed(srow)}
    clip_text = text if (extent == "custom" and text) else frow["current_text"]
    cleaned, _ = audio_core.validate_and_clean(
        audio_core.strip_url_lines(clip_text or ""), srow["trip_id"], frow["scene_index"])
    mp3 = audio_core.generate_audio(cleaned, voice_id, voice_settings,
                                    _effective_model(srow))
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
             "candidate_mp3_path": None, "working_text": frow["current_text"]}
    patch.update(_clear_coverage_and_done(frow))
    db.update_fields(fid, **patch)
    db.touch_session(sid)
    return serialize_field(sid, _field_row(sid, fid))


# --------------------------------------------------------------------------- #
# Manual-edit clip workspace — per-field scratch clips (generate / import /
# regenerate / delete) with one promotable to the working take.
# --------------------------------------------------------------------------- #
def _clip_row(sid: str, fid: int, cid: int):
    row = db.query_one(
        "SELECT * FROM manual_clips WHERE id=? AND field_id=? AND session_id=?",
        (cid, fid, sid))
    if not row:
        raise HTTPException(404, detail={"error": "no_clip", "detail": str(cid)})
    return row


def _serialize_clip(sid: str, c) -> dict:
    p = Path(c["path"])
    h = (_file_hash(p) or "")[:8] if p.exists() else ""
    return {"id": c["id"], "text": c["text"], "kind": c["kind"],
            "comment": (c["comment"] if "comment" in c.keys() else "") or "",
            "created_at": c["created_at"],
            "url": (f"/audio/{sid}/{c['field_id']}/clip/{c['id']}"
                    + (f"?v={h}" if h else ""))}


def _clips_for(sid: str, fid: int) -> list[dict]:
    return [_serialize_clip(sid, c) for c in db.query(
        "SELECT * FROM manual_clips WHERE field_id=? ORDER BY id", (fid,))]


def _render_clip(srow, cid: int, text: str) -> None:
    """Voice ``text`` VERBATIM (no Gemini cleaning — manual edit = full control) at the
    session voice/speed/model and write it to the clip's file."""
    voice_id, voice_settings = audio_core.VOICES[srow["voice"]]
    voice_settings = {**voice_settings, "speed": _effective_speed(srow)}
    mp3 = audio_core.generate_audio(text, voice_id, voice_settings, _effective_model(srow))
    path = work_dirs(srow["id"])["clips"] / f"clip_{cid}.mp3"
    path.write_bytes(mp3)
    db.execute("UPDATE manual_clips SET path=? WHERE id=?", (str(path), cid))


def _flag_edit_required_for_clip(fid: int, frow) -> None:
    """A 'Create new' attachment is an instruction to the admin → the field is edit_required
    (it is NOT the working take). Never downgrades an existing flag away from edit_required."""
    if frow["flag"] != "edit_required":
        db.update_fields(fid, flag="edit_required")


def create_clip(sid: str, fid: int, text: str, comment: str = "") -> dict:
    """Voice a 'Create new' take. Comment is OPTIONAL here: the reviewer generates a DRAFT
    (no comment, no flag), auditions it, then commits it with a note via set_clip_comment —
    which is what flags the field edit-required. A clip with no comment is an unsaved draft."""
    srow = _session_row(sid)
    frow = _field_row(sid, fid)
    if not frow["has_audio"]:
        raise HTTPException(400, detail={"error": "no_audio", "detail": "text field"})
    txt = audio_core.strip_url_lines((text or "").strip())
    if not txt:
        raise HTTPException(400, detail={"error": "empty", "detail": "clip text required"})
    cid = db.execute(
        "INSERT INTO manual_clips(session_id,field_id,text,kind,comment,path,created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (sid, fid, text or "", "generated", (comment or "").strip(), "", time.time()))
    _render_clip(srow, cid, txt)
    if (comment or "").strip():
        _flag_edit_required_for_clip(fid, frow)
    db.touch_session(sid)
    return serialize_field(sid, _field_row(sid, fid))


def set_clip_comment(sid: str, fid: int, cid: int, comment: str) -> dict:
    """Attach / edit the admin note on a 'Create new' take. A non-empty note commits the
    draft → the field is flagged edit-required so the admin acts on the attachment."""
    _session_row(sid)
    frow = _field_row(sid, fid)
    _clip_row(sid, fid, cid)
    db.execute("UPDATE manual_clips SET comment=? WHERE id=?", ((comment or "").strip(), cid))
    if (comment or "").strip():
        _flag_edit_required_for_clip(fid, frow)
    db.touch_session(sid)
    return serialize_field(sid, _field_row(sid, fid))


def regenerate_clip(sid: str, fid: int, cid: int, text: str | None) -> dict:
    srow = _session_row(sid)
    _field_row(sid, fid)
    c = _clip_row(sid, fid, cid)
    new_text = c["text"] if text is None else text
    txt = audio_core.strip_url_lines((new_text or "").strip())
    if not txt:
        raise HTTPException(400, detail={"error": "empty", "detail": "clip text required"})
    db.execute("UPDATE manual_clips SET text=? WHERE id=?", (new_text or "", cid))
    _render_clip(srow, cid, txt)
    db.touch_session(sid)
    return serialize_field(sid, _field_row(sid, fid))


def import_clip(sid: str, fid: int, data: bytes, comment: str = "") -> dict:
    _session_row(sid)
    frow = _field_row(sid, fid)
    if not frow["has_audio"]:
        raise HTTPException(400, detail={"error": "no_audio", "detail": "text field"})
    dirs = work_dirs(sid)
    cid = db.execute(
        "INSERT INTO manual_clips(session_id,field_id,text,kind,comment,path,created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (sid, fid, "(imported file)", "imported", (comment or "").strip(), "", time.time()))
    tmp = dirs["clips"] / f"import_{cid}.tmp.mp3"
    tmp.write_bytes(data)
    # re-encode to a clean 44100/mono master (any source rate/channels → consistent).
    path = dirs["clips"] / f"clip_{cid}.mp3"
    audio_io.samples_to_mp3(audio_io.mp3_to_samples(tmp), path)
    tmp.unlink(missing_ok=True)
    db.execute("UPDATE manual_clips SET path=? WHERE id=?", (str(path), cid))
    if (comment or "").strip():
        _flag_edit_required_for_clip(fid, frow)
    db.touch_session(sid)
    return serialize_field(sid, _field_row(sid, fid))


def delete_clip(sid: str, fid: int, cid: int) -> dict:
    _session_row(sid)
    _field_row(sid, fid)
    c = _clip_row(sid, fid, cid)
    try:
        Path(c["path"]).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    db.execute("DELETE FROM manual_clips WHERE id=?", (cid,))
    db.touch_session(sid)
    return serialize_field(sid, _field_row(sid, fid))


def use_clip_as_working(sid: str, fid: int, cid: int) -> dict:
    """Promote a manual clip to the working take — the manual-edit outcome. Archives a
    version, resets coverage/done, clears an outstanding edit-required flag."""
    srow = _session_row(sid)
    frow = _field_row(sid, fid)
    if not (frow["has_audio"] and frow["mp3_name"]):
        raise HTTPException(400, detail={"error": "no_audio", "detail": "text field"})
    c = _clip_row(sid, fid, cid)
    if not Path(c["path"]).exists():
        raise HTTPException(404, detail={"error": "no_clip_audio", "detail": str(cid)})
    dirs = work_dirs(sid)
    whash = _set_working(sid, frow, samples=audio_io.mp3_to_samples(c["path"]),
                         kind="manual_edit")
    _r2_upload_working(srow["trip_id"], dirs, frow)
    patch = {"working_audio_hash": whash, "splice_confidence": None,
             "candidate_mp3_path": None, "working_text": frow["current_text"]}
    patch.update(_clear_coverage_and_done(frow))
    if frow["flag"] == "edit_required":
        patch["flag"] = "none"
    db.update_fields(fid, **patch)
    db.touch_session(sid)
    return serialize_field(sid, _field_row(sid, fid))


def clip_path(sid: str, fid: int, cid: int) -> Path:
    return Path(_clip_row(sid, fid, cid)["path"])


def trim_noise(sid: str, fid: int, start: int, end: int) -> dict:
    """Manual backstop: the reviewer highlights where an unwanted noise/artefact sits and
    we clean it off the working take. Two cases (both honour 'trust my highlight'):

    * GAP — the selection is the SPACE between two words (overlaps no word). The artefact
      lives in that inter-word gap, so we blank the gap to clean silence (≥0.2 s), bounded
      to the prev word's release and the next word's true (energy-detected) onset so no
      speech is touched.
    * WORDS — the selection overlaps one or more words. We strip non-speech blips/breaths
      inside that window (and only that window — clamped to the neighbouring words).

    Archives a version + resets the done gate; revertable."""
    srow = _session_row(sid)
    frow = _field_row(sid, fid)
    if not (frow["has_audio"] and frow["mp3_name"]):
        raise HTTPException(400, detail={"error": "no_audio", "detail": "text field"})
    dirs = work_dirs(sid)
    base = audio_io.mp3_to_samples(dirs["working"] / frow["mp3_name"])
    sr = audio_io.SR
    dur = audio_io.duration_seconds(base, sr)
    cleaned_working, _ = _cleaned_orig(srow, frow)
    cur = audio_core.strip_url_lines(frow["current_text"] or "")
    words = _whisper_orig(srow, frow)
    if not words:
        raise HTTPException(400, detail={"error": "no_audio", "detail": "no word timing"})
    wmap = audio_splice._whisper_index_map(audio_splice.tokens(cleaned_working), words)
    raw_toks = list(audio_splice._TOKEN_RE.finditer(cur))

    def _word_times(ri: int) -> tuple[float, float] | None:
        """Audio (start, end) of raw token ``ri`` via its cleaned-token span + Whisper map."""
        m = raw_toks[ri]
        blo, bhi = audio_splice.highlight_span_in_cleaned(
            cur, cleaned_working, m.start(), m.end())
        ss = [wmap[t][0] for t in range(blo, bhi) if t in wmap]
        ee = [wmap[t][1] for t in range(blo, bhi) if t in wmap]
        return (min(ss), max(ee)) if ss else None

    overlap = [i for i, m in enumerate(raw_toks) if m.end() > start and m.start() < end]

    if overlap:
        # WORDS highlighted: strip non-speech inside the selected words' window only.
        spans = [t for t in (_word_times(i) for i in overlap) if t]
        if not spans:
            raise HTTPException(400, detail={"error": "no_window",
                                             "detail": "couldn't locate the highlighted audio"})
        tA = max(0.0, min(s for s, _ in spans) - 0.05)
        tB = min(dur, max(e for _, e in spans) + 0.05)
        lo, hi = min(overlap), max(overlap)
        if lo - 1 >= 0 and (pt := _word_times(lo - 1)):
            tA = max(tA, pt[1] + 0.02)
        if hi + 1 < len(raw_toks) and (nt := _word_times(hi + 1)):
            tB = min(tB, nt[0] - 0.02)
        if tB <= tA:
            return serialize_field(sid, frow)
        new = audio_io.trim_slivers(base, sr, tA, tB, sliver_max=0.35, sil_min=0.03)
        if len(new) >= len(base) - int(0.01 * sr):       # nothing removed
            return serialize_field(sid, frow)
    else:
        # GAP highlighted between two words: blank the inter-word space to clean silence.
        prev = max((i for i, m in enumerate(raw_toks) if m.end() <= start), default=None)
        nxt = min((i for i, m in enumerate(raw_toks) if m.start() >= end), default=None)
        pt = _word_times(prev) if prev is not None else None
        nt = _word_times(nxt) if nxt is not None else None
        g0 = pt[1] if pt else 0.0
        search_to = nt[1] if nt else dur
        # start the blank AFTER the prev word's release; end at the next word's true onset.
        q0 = audio_io.first_silence_after(base, sr, g0, search_to)
        g0 = q0 if q0 is not None else g0
        onset = audio_io.first_voice_onset(base, sr, g0, search_to)
        g1 = onset if onset is not None else (nt[0] if nt else dur)
        s0 = max(0, min(int(round(g0 * sr)), len(base)))
        s1 = max(s0, min(int(round(g1 * sr)), len(base)))
        gap_len = (s1 - s0) / sr
        sil = np.zeros(int(round(max(0.2, gap_len) * sr)), dtype=np.float32)
        new = np.concatenate([base[:s0], sil, base[s1:]]).astype(np.float32)

    whash = _set_working(sid, frow, samples=new, kind="noise_trim")
    _r2_upload_working(srow["trip_id"], dirs, frow)
    patch = {"working_audio_hash": whash, "splice_confidence": None}
    patch.update(_clear_coverage_and_done(frow))
    db.update_fields(fid, **patch)
    db.touch_session(sid)
    return serialize_field(sid, _field_row(sid, fid))


def trim_silence(sid: str, fid: int) -> dict:
    """Normalize the trailing pause on the working take to the trip's level requirement:
    beginner trips (A1-2 / N5 / HSK1-2) keep 3s of end silence, every other level has its
    excess trailing silence removed. Only touches end-silence (never voiced audio); when
    nothing needs changing the working take is left untouched. Archives a version + resets
    the done gate; revertable."""
    srow = _session_row(sid)
    frow = _field_row(sid, fid)
    if not (frow["has_audio"] and frow["mp3_name"]):
        raise HTTPException(400, detail={"error": "no_audio", "detail": "text field"})
    dirs = work_dirs(sid)
    base = audio_io.mp3_to_samples(dirs["working"] / frow["mp3_name"])
    sr = audio_io.SR
    target = _target_tail_seconds(srow["trip_id"])
    new = audio_io.set_trailing_silence(base, sr, target)
    if abs(len(new) - len(base)) < int(0.02 * sr):       # <20 ms change → no-op
        return serialize_field(sid, frow)
    whash = _set_working(sid, frow, samples=new, kind="silence_trim")
    _r2_upload_working(srow["trip_id"], dirs, frow)
    patch = {"working_audio_hash": whash, "splice_confidence": None}
    patch.update(_clear_coverage_and_done(frow))
    db.update_fields(fid, **patch)
    db.touch_session(sid)
    return serialize_field(sid, _field_row(sid, fid))


def insert_silence(sid: str, fid: int, pos: int, seconds: float = 1.0) -> dict:
    """EXTEND an existing pause by ``seconds`` at the TEXT caret ``pos`` (a char offset into
    current_text — normally just after a full stop). The caret is mapped to an audio time via
    the clip's word timing, then the lengthening is dropped INTO the genuine silence run at
    that word boundary. If there is no pause there (connected speech) it refuses rather than
    split a word. Archives a version + resets the done gate; revertable."""
    srow = _session_row(sid)
    frow = _field_row(sid, fid)
    if not (frow["has_audio"] and frow["mp3_name"]):
        raise HTTPException(400, detail={"error": "no_audio", "detail": "text field"})
    seconds = max(0.05, min(float(seconds), 10.0))
    dirs = work_dirs(sid)
    base = audio_io.mp3_to_samples(dirs["working"] / frow["mp3_name"])
    sr = audio_io.SR
    n = len(base)
    dur = audio_io.duration_seconds(base, sr)

    # Map the caret to the end-time of the last spoken word before it (same char→audio
    # alignment the highlight/trim tools use). pos<=0 → the clip's lead-in.
    t_ins = 0.0
    if int(pos) > 0:
        cleaned_working, _ = _cleaned_orig(srow, frow)
        cur = audio_core.strip_url_lines(frow["current_text"] or "")
        blo, bhi = audio_splice.highlight_span_in_cleaned(cur, cleaned_working, 0, int(pos))
        words = _whisper_orig(srow, frow)
        if not words:
            raise HTTPException(400, detail={"error": "no_audio", "detail": "no word timing"})
        wmap = audio_splice._whisper_index_map(audio_splice.tokens(cleaned_working), words)
        t_ins = dur                          # default to the end if nothing maps
        for t in range(bhi - 1, blo - 1, -1):
            if t in wmap:
                t_ins = wmap[t][1]
                break

    # Only ever lengthen a REAL pause — never cut into voiced audio and split a word.
    run = audio_io.silence_run_nearest(base, sr, t_ins, 0.4, 0.4)
    if run is None:
        raise HTTPException(409, detail={
            "error": "no_pause",
            "detail": "No pause at the cursor to extend — put the caret right after a "
                      "full stop (or other gap). This lengthens a pause, it won't split a word."})
    mid = (run[0] + run[1]) / 2.0            # drop the gap inside the existing silence
    cut = max(0, min(int(round(mid * sr)), n))
    gap = np.zeros(int(round(seconds * sr)), dtype=np.float32)
    new = np.concatenate([base[:cut], gap, base[cut:]])
    whash = _set_working(sid, frow, samples=new, kind="insert_silence")
    _r2_upload_working(srow["trip_id"], dirs, frow)
    patch = {"working_audio_hash": whash, "splice_confidence": None}
    patch.update(_clear_coverage_and_done(frow))
    db.update_fields(fid, **patch)
    db.touch_session(sid)
    return serialize_field(sid, _field_row(sid, fid))


def played(sid: str, fid: int, ranges: list[list[float]],
           track: str = "working") -> dict:
    _session_row(sid)
    frow = _field_row(sid, fid)
    if not frow["has_audio"]:
        return {"played_coverage": [], "can_mark_done": True}
    if track == "original":
        orig = _orig_path(sid, frow)
        dur = audio_io.mp3_duration_seconds(orig) if orig else 0.0
        existing = (json.loads(frow["original_coverage_json"] or "{}")).get("ranges", [])
    else:
        cov = json.loads(frow["played_coverage_json"] or "{}")
        existing = (cov.get("ranges", [])
                    if cov.get("hash") == frow["working_audio_hash"] else [])
        dur = _working_duration(frow)
    # S8: clamp each incoming range to [0, duration] so a bogus [[0, 1e9]] can't satisfy
    # the 95% done gate.
    incoming = []
    for r in ranges:
        a = max(0.0, min(float(r[0]), dur))
        b = max(0.0, min(float(r[1]), dur))
        if b > a:
            incoming.append([a, b])
    merged = _merge_ranges(existing + incoming)
    if track == "original":
        db.update_fields(fid, original_coverage_json=json.dumps({"ranges": merged}))
    else:
        db.update_fields(fid, played_coverage_json=json.dumps(
            {"hash": frow["working_audio_hash"], "ranges": merged}))
    frow = _field_row(sid, fid)
    _, done_ok = _coverage_for(sid, frow)
    return {"played_coverage": merged, "can_mark_done": done_ok}


def set_flag(sid: str, fid: int, flag: str) -> dict:
    _session_row(sid)
    frow = _field_row(sid, fid)
    if flag == "done":
        _, done_ok = _coverage_for(sid, frow)
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
             "played_coverage_json": "{}", "working_text": frow["original_text"],
             "version_cursor": 0}   # working now == the pristine master (v0)
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


def _max_version_n(fid: int) -> int:
    row = db.query_one(
        "SELECT COALESCE(MAX(n),0) AS mx FROM audio_versions WHERE field_id=?", (fid,))
    return int(row["mx"] or 0)


def _restore_audio_version(sid: str, fid: int, target_n: int) -> dict:
    """Make audio_versions.n == ``target_n`` the working take (undo/redo step). Does NOT
    archive a new version — it just moves the cursor and copies that take back to working.
    Clears any pending candidate and resets coverage/done (the audio changed)."""
    frow = _field_row(sid, fid)
    if not (frow["has_audio"] and frow["mp3_name"]):
        raise HTTPException(400, detail={"error": "no_audio", "detail": "text field"})
    row = db.query_one(
        "SELECT path FROM audio_versions WHERE field_id=? AND n=?", (fid, target_n))
    if not row or not Path(row["path"]).exists():
        raise HTTPException(404, detail={"error": "no_version", "detail": str(target_n)})
    dirs = work_dirs(sid)
    working = dirs["working"] / frow["mp3_name"]
    audio_io.mp3_to_mp3_copy(Path(row["path"]), working)
    patch = {"working_audio_hash": _file_hash(working), "version_cursor": target_n,
             "candidate_mp3_path": None, "splice_confidence": None}
    patch.update(_clear_coverage_and_done(frow))
    db.update_fields(fid, **patch)
    _r2_upload_working(_session_row(sid)["trip_id"], dirs, frow)
    db.touch_session(sid)
    return serialize_field(sid, _field_row(sid, fid))


def undo_audio(sid: str, fid: int) -> dict:
    """Step the working take back one version (v0 = pristine master)."""
    _session_row(sid)
    frow = _field_row(sid, fid)
    cur = frow["version_cursor"] if frow["version_cursor"] is not None else _max_version_n(fid)
    if cur <= 0:
        raise HTTPException(409, detail={"error": "nothing_to_undo",
                                         "detail": "already at the earliest take"})
    return _restore_audio_version(sid, fid, cur - 1)


def redo_audio(sid: str, fid: int) -> dict:
    """Step the working take forward one version (towards the most recent edit)."""
    _session_row(sid)
    frow = _field_row(sid, fid)
    max_n = _max_version_n(fid)
    cur = frow["version_cursor"] if frow["version_cursor"] is not None else max_n
    if cur >= max_n:
        raise HTTPException(409, detail={"error": "nothing_to_redo",
                                         "detail": "already at the latest take"})
    return _restore_audio_version(sid, fid, cur + 1)


# --------------------------------------------------------------------------- #
# Narration settings (voice / speed / model) — per-session, correctable mid-review
# --------------------------------------------------------------------------- #
def _srow_get(srow, col):
    try:
        return srow[col]
    except (IndexError, KeyError):
        return None


def _effective_speed(srow) -> float:
    so = _srow_get(srow, "speed_override")
    return so if so is not None else audio_core.speed_for_trip(srow["trip_id"])


def _effective_model(srow) -> str:
    return _srow_get(srow, "model_override") or audio_core.model_for_voice(srow["voice"])


def _field_was_regenerated(dirs, frow) -> bool:
    """True if this field's audio was produced in-review (candidate pending, or working
    differs from the pristine master). The master itself is the real narrator voice and
    never changes — only regenerated takes can carry a wrong app-side voice."""
    if frow["candidate_mp3_path"]:
        return True
    if not (frow["has_audio"] and frow["mp3_name"]):
        return False
    orig = dirs["orig"] / frow["mp3_name"]
    return orig.exists() and (frow["working_audio_hash"] or "") != _file_hash(orig)


def _reset_field_audio_to_master(dirs, frow) -> None:
    """Drop a regenerated take: restore working = pristine master, clear candidate /
    confidence / coverage, and undo a 'done' that was earned on the stale audio. The
    reviewer's text edit + comment are preserved so they can re-regenerate."""
    patch = {"candidate_mp3_path": None, "splice_confidence": None,
             "splice_meta_json": None, "played_coverage_json": "{}"}
    if frow["flag"] == "done":
        patch["flag"] = "none"
    name = frow["mp3_name"]
    orig = dirs["orig"] / name
    if frow["has_audio"] and name and orig.exists():
        audio_io.mp3_to_mp3_copy(orig, dirs["working"] / name)
        patch["working_audio_hash"] = _file_hash(dirs["working"] / name)
    db.update_fields(frow["id"], **patch)


def get_voices() -> dict:
    """The approved-voice registry + model list for the UI narration picker."""
    return {"voices": audio_core.registry_list(), "models": audio_core.EL_MODELS}


def set_narration(sid: str, voice: str | None, speed: float | None,
                  model: str | None, clear_speed: bool, clear_model: bool,
                  reset_regenerated: bool = False) -> dict:
    """Correct a session's narration voice/speed/model mid-review. When
    ``reset_regenerated`` is true, takes regenerated under the previous settings are
    discarded back to the master so they can be re-done (text edits kept); otherwise
    they are left untouched. No-op fields always keep their master audio + coverage.
    The master audio itself is never changed."""
    srow = _session_row(sid)
    if srow["status"] != "in_review":
        raise HTTPException(409, detail={"error": "not_in_review",
                                         "detail": "session is not open for editing"})
    new_voice = srow["voice"]
    if voice is not None:
        v = voice.strip().lower()
        if v not in audio_core.VOICE_REGISTRY:
            raise HTTPException(422, detail={"error": "bad_voice",
                                             "detail": f"unknown voice {voice!r}"})
        new_voice = v
    if model is not None and model not in audio_core.EL_MODELS:
        raise HTTPException(422, detail={"error": "bad_model",
                                         "detail": f"unknown model {model!r}"})
    if speed is not None and not (0.5 <= speed <= 1.2):
        raise HTTPException(422, detail={"error": "bad_speed",
                                         "detail": "speed must be 0.5–1.2"})

    new_speed = None if clear_speed else (speed if speed is not None
                                          else _srow_get(srow, "speed_override"))
    new_model = None if clear_model else (model if model is not None
                                          else _srow_get(srow, "model_override"))

    changed = (new_voice != srow["voice"]
               or new_speed != _srow_get(srow, "speed_override")
               or new_model != _srow_get(srow, "model_override"))

    voice_id, vs = audio_core.VOICES[new_voice]
    eff_speed = new_speed if new_speed is not None else audio_core.speed_for_trip(srow["trip_id"])
    voice_settings = {**vs, "speed": eff_speed}
    db.execute(
        "UPDATE sessions SET voice=?, voice_settings_json=?, speed_override=?, "
        "model_override=?, updated_at=? WHERE id=?",
        (new_voice, json.dumps(voice_settings), new_speed, new_model, time.time(), sid))

    if changed and reset_regenerated:
        dirs = work_dirs(sid)
        for frow in db.query("SELECT * FROM field_edits WHERE session_id=?", (sid,)):
            if _field_was_regenerated(dirs, frow):
                _reset_field_audio_to_master(dirs, frow)
    return get_session(sid)


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


def validate(sid: str) -> tuple[list[dict], list[dict]]:
    """PURE pre-submit validation — NO writes. Returns (hard, soft) issue lists.
    Reads the FRESH live staging trip for the final-360 check (a read, not a write).
    Shared by reviewer ``submit`` (gate) and admin ``approve`` (re-check vs live)."""
    srow = _session_row(sid)
    trip_id = srow["trip_id"]
    frows = db.query(
        "SELECT * FROM field_edits WHERE session_id=? ORDER BY id", (sid,))

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

    # every field must be listened-to (the `done` flag only unlocks after full
    # playback) and explicitly marked done before the trip can be submitted
    not_done = sum(1 for f in frows if (f["flag"] or "none") != "done")
    if not_done:
        hard.append({"scene_index": None, "field_path": "*",
                     "issue": f"{not_done} section(s) not yet marked done — listen to "
                              "the audio and mark every section done before submitting",
                     "severity": "block"})

    return hard, soft


def commit(sid: str, user) -> dict:
    """Perform the STAGING WRITES + master promotion for an approved session. Assumes
    ``validate`` has already passed. Re-fetches the live trip so it writes onto the
    freshest quickTrips. Returns {"written": [...], "promoted_mp3": [...]}.

    INVARIANT (§5): the working->master promotion + versions/ archive happen ONLY here
    (admin approve), NEVER in reviewer submit."""
    srow = _session_row(sid)
    trip_id = srow["trip_id"]
    try:
        trip_live = get_trip(trip_id)
    except SystemExit as e:
        raise HTTPException(404, detail=str(e))
    qt_live = list(trip_live.get("quickTrips") or [])
    frows = db.query(
        "SELECT * FROM field_edits WHERE session_id=? ORDER BY id", (sid,))
    changed = [f for f in frows if (f["current_text"] or "") != (f["original_text"] or "")]

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

    # ---- editable English translation (*En) writeback for non-EN trips ----
    for f in frows:
        if (f["source_text"] or "") == (f["original_source"] or ""):
            continue
        fp, si = f["field_path"], f["scene_index"]
        sval = f["source_text"]
        if fp == "tripgroup_description":
            top_level["descriptionHome"] = sval
            written.append("descriptionHome")
        elif si is not None and si < len(qt_live):
            sc = qt_live[si]
            if fp == "SceneDesc":
                sc["SceneDescEn"] = sval
            elif fp == "titleKey":
                sc["titleKeyEn"] = sval
            elif fp == "questionKey":
                sc["questionKeyEn"] = sval
            elif fp == "questionOption":
                opts = list(sc.get("questionOptionKeysEn") or [])
                k = f["option_index"] or 0
                while len(opts) <= k:
                    opts.append("")
                opts[k] = sval
                sc["questionOptionKeysEn"] = opts
            else:
                continue
            scene_changed = True
            written.append(f"quickTrips[{si}].{fp}En")

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

    return {"written": written, "promoted_mp3": promoted}


def submit(sid: str, user) -> dict:
    """Reviewer/admin (own language): VALIDATE ONLY. On success flip to `submitted`
    (locked read-only, awaiting admin). NO staging writes, NO master promotion."""
    srow = _session_row(sid)
    if srow["status"] not in _EDITABLE_STATUSES:
        raise HTTPException(403, detail={
            "error": "locked",
            "detail": f"session is '{srow['status']}' — cannot submit"})
    hard, soft = validate(sid)
    if hard:
        return {"ok": False, "validation": hard + soft}
    db.execute(
        "UPDATE sessions SET status='submitted', submitted_by=?, updated_at=? WHERE id=?",
        (getattr(user, "username", None), time.time(), sid))
    return {"ok": True, "validation": soft}


def approve(sid: str, user) -> dict:
    """ADMIN ONLY: claim-first CAS (submitted -> approving), re-validate against live
    staging, run `commit` (Firebase text + master promotion), then approved + audit.
    409 if the session isn't currently `submitted`. Any failure reverts to `submitted`."""
    _session_row(sid)   # 404 if missing
    claimed = db.execute_rowcount(
        "UPDATE sessions SET status='approving', updated_at=? "
        "WHERE id=? AND status='submitted'",
        (time.time(), sid))
    if claimed == 0:
        raise HTTPException(409, detail={
            "error": "not_submitted",
            "detail": "session is not awaiting approval (must be 'submitted')"})
    try:
        hard, soft = validate(sid)
        if hard:
            # Live staging drifted / a gate now fails: don't write. Revert so an admin
            # can send it back (request-changes) for the reviewer to fix.
            db.execute("UPDATE sessions SET status='submitted', updated_at=? WHERE id=?",
                       (time.time(), sid))
            return {"ok": False, "validation": hard + soft, "written": [],
                    "promoted_mp3": [], "awaiting_stage9": False}
        result = commit(sid, user)
        now = time.time()
        db.execute(
            "UPDATE sessions SET status='approved', approved_by=?, updated_at=? WHERE id=?",
            (getattr(user, "username", None), now, sid))
        db.execute(
            "INSERT INTO approvals(session_id,trip_id,approved_by,approved_at,written_json)"
            " VALUES(?,?,?,?,?)",
            (sid, trip_id_for_session(sid), getattr(user, "username", None), now,
             json.dumps(result["written"])))
        return {"ok": True, "validation": soft, "written": result["written"],
                "promoted_mp3": result["promoted_mp3"], "awaiting_stage9": True}
    except Exception:
        # Never leave a session stuck in the transient `approving` claim.
        db.execute("UPDATE sessions SET status='submitted', updated_at=? WHERE id=?",
                   (time.time(), sid))
        raise


def request_changes(sid: str, user, note: str) -> dict:
    """ADMIN ONLY: send a submitted trip back to the reviewer with a note (editable
    again)."""
    srow = _session_row(sid)
    if srow["status"] not in ("submitted", "approving"):
        raise HTTPException(409, detail={
            "error": "bad_state",
            "detail": f"cannot request changes from '{srow['status']}'"})
    db.execute(
        "UPDATE sessions SET status='changes_requested', review_note=?, updated_at=? "
        "WHERE id=?", (note or "", time.time(), sid))
    return {"ok": True}


def review_queue() -> list[dict]:
    """ADMIN ONLY: the submitted sessions awaiting approval. (submitted_at is the row's
    updated_at — a session is locked once submitted, so it stays the submit time.)"""
    rows = db.query(
        "SELECT id, trip_id, submitted_by, updated_at FROM sessions "
        "WHERE status='submitted' ORDER BY updated_at")
    out: list[dict] = []
    for r in rows:
        tid = r["trip_id"]
        meta = _trip_meta(tid)
        er = db.query_one("SELECT 1 FROM field_edits WHERE session_id=? AND "
                          "flag='edit_required' LIMIT 1", (r["id"],))
        out.append({
            "sid": r["id"],
            "trip_id": tid,
            "title": meta.get("title") or tid,
            "language": audio_core.language_of(tid),
            "submitted_by": r["submitted_by"],
            "submitted_at": r["updated_at"],
            "edit_required": er is not None,
        })
    return out


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
