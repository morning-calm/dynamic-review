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
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
from fastapi import HTTPException

from . import config  # noqa: F401  (ensures SCRIPTS_ROOT on sys.path) — keep first
from . import (audio_core, audio_io, audio_splice, auth, auto_checks, auto_review_ingest,
               cjk_align, cjk_splice, db, images_r2, review_audio, review_bus, static360,
               thumbs, trello)
from .config import (COUNTRY_VOICE_GUESS, COVERAGE_DONE_FRACTION,
                     LANGUAGE_FALLBACK_VOICE, WORK_ROOT)
from .locks import WHISPER_LOCK
from .staging import (db as fb_db, get_trip, get_tripgroup, merge_categories,
                      paths_for, tripgroup_id_for, update_trip_text,
                      update_tripgroup)
from .statuses import ACTIVE_STATUSES, EDITABLE_STATUSES

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
    review-audio/<trip_id>/. Called immediately after _set_working(); never raises.

    The DB/file lookups happen HERE (on the request thread, where the row is valid);
    only the network PUTs are handed to review_audio's single background worker. The
    reviewer's edit therefore no longer waits on R2 — a per-edit round-trip that made
    the pause tools feel slow for something no review step ever reads back."""
    try:
        name = frow["mp3_name"]
        if not name:
            return
        # Canonical promoted clip (e.g. 3.mp3, 3_q.mp3, 3_a.mp3). This key is ALSO where
        # a host with no local masters re-seeds this trip's pristine original from, so it
        # must not be clobbered without the original being copied aside first —
        # upload_master_async does that (see review_audio.preserve_original).
        working = dirs["working"] / name
        if working.exists():
            review_audio.upload_master_async(trip_id, working, name)
        # Latest version snapshot just inserted by _set_working() (e.g. 3v2.mp3) — its own
        # key, never re-seeded from, so a plain upload is right.
        ver = db.query_one(
            "SELECT path, label FROM audio_versions "
            "WHERE field_id=? ORDER BY n DESC LIMIT 1",
            (frow["id"],))
        if ver and ver["path"]:
            vpath = Path(ver["path"])
            if vpath.exists():
                review_audio.upload_async(trip_id, vpath, f"{ver['label']}.mp3")
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
    ``{i}.mp3``, fall back to ``Audio Generation/<trip_id>/``. Last resort (a host with
    no local master trees, e.g. the Ubuntu server — server-migration.md Phase 2): pull
    the masters from the R2 ``review-audio/<trip_id>/`` mirror into a local seed cache
    and reuse it on later calls."""
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
    seed_cache = config.WORK_ROOT / "_r2_seed_cache" / trip_id
    if _has_scene_mp3(seed_cache):
        return seed_cache
    if review_audio.download_dir(trip_id, seed_cache) and _has_scene_mp3(seed_cache):
        return seed_cache
    return ag / trip_id


# --------------------------------------------------------------------------- #
# Mandarin (_ZH) 4-script + A/B-audio review mode.
#
# The prepared HSK3 trips carry two ElevenLabs takes per field (V2 base + V3) under
# Audio Generation/_voice_test/<trip>__* and a TripLocalizations/{id} doc with the
# Traditional / Simplified / Zhuyin / English of every field. This mode surfaces the 4
# scripts for editing and both audio takes for side-by-side audition; approval writes the
# reviewed text back to TripLocalizations (+ the Trip doc). It is PRESENCE-DRIVEN: delete
# the _voice_test sets and a _ZH trip reverts to the normal single-audio flow, no code
# change. All _ZH branches are additive — non-_ZH trips are byte-for-byte unaffected.
# --------------------------------------------------------------------------- #
_ZH_SCRIPTS = ("Hans", "Hant", "zhuyin", "en")       # scene fields carry phonetics
_ZH_DESC_SCRIPTS = ("Hans", "Hant", "en")            # description: no phonetics (a blurb)
_ZH_IS_CACHE: dict[str, bool] = {}                   # sid -> is_zh (immutable after seed)


def _is_zh_session(sid: str) -> bool:
    v = _ZH_IS_CACHE.get(sid)
    if v is None:
        row = db.query_one("SELECT is_zh FROM sessions WHERE id=?", (sid,))
        v = bool(row and _srow_get(row, "is_zh"))
        _ZH_IS_CACHE[sid] = v
    return v


def _zh_v3_set(trip_id: str) -> Path | None:
    """The ElevenLabs V3 take for a pre-final _ZH trip, or None. Mandarin is V3-only
    (dave/Ted 2026-07-02) — the old V2/V3 side-by-side audition is retired. Prefers the
    …__V3_1x set under Audio Generation/_voice_test/<trip>__*; if the trip carries a
    single unmarked set (V3-only generation), use that. Must hold scene mp3s to count."""
    root = config.AUDIO_GENERATION_ROOT / "_voice_test"
    if not root.is_dir():
        return None
    v3 = other = None
    for p in sorted(root.glob(f"{trip_id}__*")):
        if not p.is_dir():
            continue
        if p.name.endswith("__V3_1x"):
            v3 = v3 or p
        else:
            other = other or p
    chosen = v3 or other
    return chosen if chosen and _has_scene_mp3(chosen) else None


def _zh_voicetest_trip_ids() -> list[str]:
    """Content-ids of _ZH trips that have a V3 voice-test set — pre-final Mandarin trips
    whose audio is still in _voice_test (not the standard location), so the Trello export
    never surfaces them. list_trips injects these so the Mandarin reviewer sees them; they
    disappear the moment the _voice_test set is removed."""
    root = config.AUDIO_GENERATION_ROOT / "_voice_test"
    if not root.is_dir():
        return []
    ids: set[str] = set()
    for p in root.iterdir():
        if p.is_dir() and "__" in p.name:
            tid = p.name.split("__", 1)[0]
            if tid.upper().endswith("_ZH") and _zh_v3_set(tid):
                ids.add(tid)
    return sorted(ids)


def _fetch_localization(trip_id: str) -> dict | None:
    """The live TripLocalizations/{id} doc (4-script text), or None if absent/unreadable."""
    try:
        snap = fb_db().collection("TripLocalizations").document(trip_id).get()
        return snap.to_dict() if snap.exists else None
    except Exception as e:  # noqa: BLE001
        print(f"[zh] TripLocalizations fetch failed for {trip_id}: {e}")
        return None


def _loc_block(node: dict | None, scripts=_ZH_SCRIPTS) -> dict | None:
    """Flatten a localization node {target:{Hans,Hant,zhuyin}, home:{en}} into a flat
    {Hans,Hant,zhuyin,en} (only the requested scripts). None if the node is falsy."""
    if not node:
        return None
    target = node.get("target") or {}
    home = node.get("home") or {}
    out: dict = {}
    for s in scripts:
        out[s] = (home.get("en") if s == "en" else target.get(s)) or ""
    return out


def _index_localization(loc: dict | None) -> dict:
    """(scene_index, field_path, option_index) -> {Hans,Hant,zhuyin,en} from a
    TripLocalizations doc. Trip description keys on (None,'tripgroup_description',None).
    contentTitleKey is a plain string in the doc (not a 4-script block) → not indexed."""
    out: dict = {}
    if not loc:
        return out
    for sc in (loc.get("scenes") or []):
        i = sc.get("index")
        if i is None:
            continue
        for fp in ("titleKey", "SceneDesc", "questionKey"):
            blk = _loc_block(sc.get(fp))
            if blk:
                out[(i, fp, None)] = blk
        for k, opt in enumerate(sc.get("questionOptionKeys") or []):
            blk = _loc_block(opt)
            if blk:
                out[(i, "questionOption", k)] = blk
    desc = _loc_block(loc.get("description"), scripts=_ZH_DESC_SCRIPTS)
    if desc:
        out[(None, "tripgroup_description", None)] = desc
    return out


def _zh_join2(a: str, b: str) -> str:
    """Mirror hsk_lib/build_firebase.join2 exactly: '<a>\\n<b>' (both stripped) or just
    <a>. The Trip doc's line-1 hanzi drives the audio; line-2 is display (pinyin, or
    English on titleKey)."""
    a, b = (a or "").strip(), (b or "").strip()
    return f"{a}\n{b}" if b else a


def _zh_regen_pinyin(zhuyin: str, hans: str) -> tuple[str, list[str]]:
    """Toned pinyin for the display line, from the human-confirmed zhuyin
    (hsk_lib.zhuyin_to_pinyin). hsk_lib is imported LAZILY (its dir isn't on the default
    sys.path) and every failure DEGRADES GRACEFULLY — we NEVER write raw bopomofo or other
    garbage into a pinyin line:
      * import fails            -> ('', [warn])            caller flags edit_required
      * conversion has warnings -> deterministic to_pinyin(Hans) fallback + [warn]
      * conversion is clean      -> (pinyin, [])
    """
    try:
        import sys as _sys
        hsk_dir = str(config.SCRIPTS_ROOT / "Research and Writing" / "HSK Mandarin" / "stages")
        if hsk_dir not in _sys.path:
            _sys.path.insert(0, hsk_dir)
        import hsk_lib  # noqa: F401  (lazy — only needed at writeback)
    except Exception as e:  # noqa: BLE001
        return "", [f"hsk_lib unavailable ({e}) — pinyin not regenerated"]
    try:
        py, warns = hsk_lib.zhuyin_to_pinyin(zhuyin or "", hans or "")
    except Exception as e:  # noqa: BLE001
        return "", [f"zhuyin_to_pinyin raised {e}"]
    if warns:
        try:
            fb = hsk_lib.to_pinyin(hans or "")
        except Exception:  # noqa: BLE001
            fb = ""
        return fb, [f"zhuyin_to_pinyin warnings {warns}; used to_pinyin(Hans) fallback"]
    return py, []


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
    # Presence-driven _ZH inject: pre-final Mandarin trips with A/B voice-test audio aren't
    # in the manifest (their audio isn't in the standard location yet). Surface them for the
    # Mandarin reviewer; they vanish once the _voice_test sets are deleted.
    have = {t["trip_id"] for t in trips}
    for tid in _zh_voicetest_trip_ids():
        if tid in have:
            continue
        try:
            trip = get_trip(tid)
        except SystemExit:
            trip = None
        lvl, fam = _level_family(tid)
        has_session, status, edit_required = _session_meta(tid)
        trips.append({
            "trip_id": tid,
            "title": (trip.get("contentTitleKey") if trip else None) or tid,
            "folder_name": (trip.get("folderName") or "") if trip else "",
            "lane": "6", "level": lvl, "family": fam,
            "has_session": has_session, "status": status,
            "edit_required": edit_required, "reviewable": True,
        })
    # Session-backed injection: any trip with an existing (non-completed) session that the
    # manifest no longer lists — e.g. a Trello [review] block was edited to drop a sibling
    # variant (an HSK3 script whose card now lists only HSK1-2), or a manifest refresh
    # otherwise removed it. In-progress review work must NEVER be hidden by a manifest
    # change, so surface it here regardless of the manifest. (The `done` filter below still
    # removes any that have since been completed; language scope still applies.) reviewable
    # is True — a resumable session already has its working audio in work/{sid}/.
    have = {t["trip_id"] for t in trips}
    for srow in db.query("SELECT DISTINCT trip_id FROM sessions"):
        tid = srow["trip_id"]
        if tid in have:
            continue
        try:
            trip = get_trip(tid)
        except SystemExit:
            trip = None
        lvl, fam = _level_family(tid)
        has_session, status, edit_required = _session_meta(tid)
        trips.append({
            "trip_id": tid,
            "title": (trip.get("contentTitleKey") if trip else None) or tid,
            "folder_name": (trip.get("folderName") or "") if trip else "",
            "lane": "6", "level": lvl, "family": fam,
            "has_session": has_session, "status": status,
            "edit_required": edit_required, "reviewable": True,
        })
    # Completed trips (approved or admin-marked) leave the active queue entirely — for
    # ALL roles. An admin un-completes to return one to the list.
    done = {r["trip_id"] for r in db.query("SELECT trip_id FROM completed_trips")}
    if done:
        trips = [t for t in trips if t["trip_id"] not in done]
    if user is not None:
        from . import auth   # lazy (auth imports sessions) — no module-load cycle
        trips = [t for t in trips if auth.language_allowed(user, t["trip_id"])]
    # In-app pins float to the top (newest pin first); everything else keeps the manifest
    # = Trello-card order (a stable sort preserves it). `pinned` drives the UI indicator.
    pins = {r["trip_id"]: r["pinned_at"]
            for r in db.query("SELECT trip_id, pinned_at FROM trip_priority")}
    for t in trips:
        t["pinned"] = t["trip_id"] in pins
    if pins:
        trips.sort(key=lambda t: (0, -pins[t["trip_id"]]) if t["pinned"] else (1, 0.0))
    return trips


def _fetch_trip_and_reviewable(tid: str) -> tuple[dict | None, bool]:
    """Off-thread I/O for one manifest entry: the staging Firestore read plus
    (best-effort) audio resolution, including ``resolve_audio_dir``'s R2 seed-cache
    download fallback. Both are blocking network calls, so this is run in a thread
    pool by ``_list_trips_from_manifest`` rather than serially per trip."""
    trip = None
    try:
        trip = get_trip(tid)                 # staging; may be absent
    except SystemExit:
        trip = None
    reviewable = False
    if trip is not None:
        try:
            reviewable = _has_scene_mp3(resolve_audio_dir(tid, trip))
        except Exception:  # noqa: BLE001
            reviewable = False
    return trip, reviewable


def _list_trips_from_manifest() -> list[dict]:
    data = json.loads(config.MANIFEST_PATH.read_text(encoding="utf-8"))
    # Tolerate both the canonical {"trips":[…]} and a bare […] list.
    entries = data if isinstance(data, list) else (data.get("trips") or [])
    entries = [t for t in entries if t.get("trip_id")]
    # stage9.common.db()'s lazy singleton init (`if _DB is None: ... initialize_app()`)
    # isn't lock-guarded — read-only reused code, not ours to change — so the first
    # Firestore call must happen alone, on this thread, before any concurrent get_trip()
    # calls race on initialize_app() and crash with "default app already exists".
    try:
        fb_db()
    except Exception:  # noqa: BLE001 - individual per-trip fetches handle their own failures
        pass
    # get_trip (Firestore) + resolve_audio_dir (R2 fallback download on a host with no
    # local masters, e.g. this one) are per-trip network I/O — sequentially that's
    # ~0.4s/trip (tens of seconds for a full manifest on modest hardware, enough to trip
    # the tunnel/edge timeout). Both release the GIL on I/O and use shared, thread-safe
    # clients (boto3, the Firestore client), so fan them out instead.
    with ThreadPoolExecutor(max_workers=16) as pool:
        fetched = list(pool.map(_fetch_trip_and_reviewable, (t["trip_id"] for t in entries)))
    out: list[dict] = []
    for t, (trip, reviewable) in zip(entries, fetched):
        tid = t["trip_id"]
        folder_name = (trip.get("folderName") or "") if trip else ""
        title = (trip.get("contentTitleKey") if trip else None) or t.get("title") or tid
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
    ("_Beg_JP", "N5"),   # real N5 ids use _Beg_JP (e.g. Tokyo_07_Olympic_Beg_JP)
    ("_HSK12_ZH", "HSK1-2"), ("_HSK3_ZH", "HSK3"),
    # EU languages (2026-07-16): _A12_* joins _BEGINNER_LEVELS (3s SceneDesc tail);
    # _Beg_* EU trips are 0.7x speed but do NOT take the beginner tail.
    ("_A12_ES", "A12"), ("_A12_FR", "A12"), ("_A12_DE", "A12"), ("_A12_IT", "A12"),
    ("_B1_ES", "B1"), ("_B1_FR", "B1"), ("_B1_DE", "B1"), ("_B1_IT", "B1"),
    ("_Beg_ES", "ES-Beg"), ("_Beg_FR", "FR-Beg"), ("_Beg_DE", "DE-Beg"), ("_Beg_IT", "IT-Beg"),
    ("_EN", "EN"), ("_ZH", "ZH"), ("_JP", "JP"),
    ("_ES", "ES"), ("_FR", "FR"), ("_DE", "DE"), ("_IT", "IT"),
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
# Promote-time pad-marker gate: tag a beginner narration as padded only when its tail
# is genuinely there. Combine normalizes to exactly 3.0 s; pipeline-padded masters
# carry ~3.4 s (3 s pad + natural release) — 2.5 s separates both cleanly from an
# offline-edited import that lost the pad (~≤0.5 s), which must stay untagged so
# Stage 9's pad step supplies the missing tail.
_PAD_TAG_MIN_TAIL_S = 2.5


def _target_tail_seconds(trip_id: str, field_path: str | None = None) -> float:
    """Required trailing silence for a clip: the long beginner pause (3s on A1-2 / N5 /
    HSK1-2) applies ONLY to the NARRATION (SceneDesc) — it's the learner-absorption beat
    after the scene's description. Questions, question options and titles are short,
    self-contained prompts; a 3s tail on them is just dead air (dave, 2026-07-15). So any
    non-SceneDesc field, and every non-beginner level, gets the small 0.4s natural tail."""
    lvl, _ = _level_family(trip_id)
    if field_path == "SceneDesc" and lvl in _BEGINNER_LEVELS:
        return _BEGINNER_TAIL_SECONDS
    return _DEFAULT_TAIL_SECONDS


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
def _is_en_source_of_nonenglish_group(trip_id: str, tg: dict | None) -> bool:
    """True when this is an English (`_EN`) trip that is only the SOURCE member of a
    non-English TripGroup (i.e. the group also has `_JP`/`_ZH`/… member trips). Such a
    trip's quiz questions/options are English-only artefacts that never ship in the
    target-language product, so they aren't reviewed (dave, 2026-07-10). Detected from
    the TripGroup `trips` array (already fetched at seed): any member whose id resolves
    to a non-English language means the English member is a translation source."""
    if not trip_id.endswith("_EN"):
        return False
    for m in (tg or {}).get("trips") or []:
        mid = str(m.get("tripId") or "")
        if mid and audio_core.language_of(mid) != "English":
            return True
    return False


def create_or_resume(trip_id: str, user, *,
                     allow_completed: bool = False,
                     allow_no_audio: bool = False) -> dict:
    # [P0-1] Language gate at the TOP — the create is keyed on trip_id, so the
    # per-{sid} scoping dependency structurally can't cover it. Admins bypass.
    from . import auth   # lazy import (auth imports sessions) — no module-load cycle
    if not auth.language_allowed(user, trip_id):
        raise HTTPException(403, detail={
            "error": "forbidden",
            "detail": "this trip's narration language is not assigned to you"})
    # Completed trips are view-only — an admin must un-complete before it can be reviewed
    # again (checked before resume/seed so a leftover session can't reopen a done trip).
    # allow_completed=True is the ADMIN staging-editor path (routes_admin.open) only.
    if not allow_completed and db.query_one(
            "SELECT 1 FROM completed_trips WHERE trip_id=?", (trip_id,)):
        raise HTTPException(409, detail={
            "error": "completed",
            "detail": "trip is completed — un-complete it to review"})
    # Resume the newest still-live session. `approved` is the only terminal status, so a
    # fresh open after approval re-seeds from the now-promoted masters — but EVERY other
    # status must resume, including the transient `approving` (an open during that window,
    # or after a crash mid-approve, resumes read-only rather than seeding a blank shadow).
    # The set is DERIVED (statuses.ACTIVE_STATUSES), never hand-listed: a missing status
    # here re-seeds a blank session which, being the newest, permanently shadows the
    # reviewer's real one — see statuses.py for the incident this cost.
    ph = ",".join("?" * len(ACTIVE_STATUSES))
    existing = db.query_one(
        f"SELECT id FROM sessions WHERE trip_id=? AND status IN ({ph}) "
        "ORDER BY created_at DESC LIMIT 1", (trip_id, *ACTIVE_STATUSES))
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

    # 4-script (permanent) is DECOUPLED from the audio source: TripLocalizations is fetched
    # for ANY _ZH trip → the 4-script review pane (zh_mode). Mandarin is V3-only — a
    # pre-final _ZH trip's _voice_test V3 set seeds as the normal single working take (no
    # V2/V3 audition); a finalised _ZH trip with no set keeps its normal masters.
    is_zh_lang = audio_core.language_of(trip_id) == "Mandarin"
    v3_set = _zh_v3_set(trip_id) if is_zh_lang else None
    localization = _fetch_localization(trip_id) if is_zh_lang else None
    zh_mode = localization is not None
    loc_index = _index_localization(localization)

    # Audio source: the V3 voice-test take drives the seed for pre-final Mandarin trips (no
    # Quicktrips masters); otherwise the normal masters (incl. a finalised _ZH trip).
    mp3_dir = v3_set if v3_set else resolve_audio_dir(trip_id, trip)
    if not _has_scene_mp3(mp3_dir):
        # allow_no_audio (admin staging editor): seed anyway — every audio field
        # degrades to text-only via the per-field master.exists() fallback below, and
        # get_session surfaces `audio_unavailable` so the FE shows a soft warning.
        if not allow_no_audio:
            raise HTTPException(status_code=422, detail={
                "error": "bad_folder",
                "detail": f"{trip_id}: no MP3 masters under the Quicktrips tree or "
                          f"Audio Generation/{trip_id}"})

    voice = resolve_voice(trip_id, country)
    voice_id, voice_settings = audio_core.VOICES[voice]
    voice_settings = {**voice_settings, "speed": audio_core.speed_for_trip(trip_id)}
    tg_id, tg = get_tripgroup(trip_id)
    skip_en_questions = _is_en_source_of_nonenglish_group(trip_id, tg)
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
        "status,is_zh,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, trip_id, folder_name, voice, json.dumps(voice_settings),
         "{}", "{}", json.dumps(trip, default=str), json.dumps(categories),
         "in_review", 1 if zh_mode else 0, now, now))
    _ZH_IS_CACHE[sid] = zh_mode

    dirs = work_dirs(sid)

    def add_field(scene_index, field_path, original_text, has_audio,
                  option_index=None, source_text=""):
        name = mp3_name(field_path, scene_index, option_index) if has_audio else None
        cur_path = None
        whash = None
        if name:
            master = mp3_dir / name
            if master.exists():
                # Every trip (incl. a V3-only _ZH) gets the normal single working take.
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
        # _ZH: attach the 4-script block (cur seeded == orig, for diffing at writeback).
        loc_blk = loc_index.get((scene_index, field_path, option_index))
        loc_json = (json.dumps({"cur": dict(loc_blk), "orig": dict(loc_blk)},
                               ensure_ascii=False) if loc_blk else None)
        fid = db.execute(
            "INSERT INTO field_edits(session_id,scene_index,field_path,option_index,"
            "has_audio,mp3_name,original_text,current_text,current_mp3_path,"
            "working_audio_hash,source_text,original_source,working_text,"
            "localization_json,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, scene_index, field_path, option_index, 1 if has_audio else 0,
             name if has_audio else None, original_text or "", original_text or "",
             cur_path, whash, src, src, original_text or "", loc_json, time.time()))
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
        # English quiz questions on a translation-source `_EN` trip never ship in the
        # target-language product — don't seed them (so they're neither reviewed nor
        # counted by the all-done gate). Narration/titles are still reviewed.
        if not skip_en_questions:
            if (s.get("questionKey") or "").strip():
                add_field(i, "questionKey", s.get("questionKey") or "", True,
                          source_text=s.get("questionKeyEn") or "")
            opts_en = s.get("questionOptionKeysEn") or []
            for k, opt in enumerate(s.get("questionOptionKeys") or []):
                add_field(i, "questionOption", opt or "", True, option_index=k,
                          source_text=(opts_en[k] if k < len(opts_en) else ""))

    # Mandarin is V3-only: pin the session to eleven_v3 @ speed 1.0 and record the version
    # so regenerate/combine match the seeded take (no V2/V3 audition or pick step).
    if v3_set:
        db.execute("UPDATE sessions SET preferred_version='v3', model_override='eleven_v3', "
                   "speed_override=1.0, updated_at=? WHERE id=?", (time.time(), sid))

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


# Reviewer-editable states — defined in statuses.py with the rest of the vocabulary (see
# there: a hand-copied status list is what caused the 2026-07-13 blank-session incident).
_EDITABLE_STATUSES = EDITABLE_STATUSES


def trip_id_for_session(sid: str) -> str:
    """Resolve a session's trip_id (for the auth language-scoping dependency). 404 if
    the session is unknown."""
    row = db.query_one("SELECT trip_id FROM sessions WHERE id=?", (sid,))
    if not row:
        raise HTTPException(404, detail={"error": "no_session", "detail": sid})
    return row["trip_id"]


def assert_editable(sid: str, user=None) -> None:
    """403 while a session is locked (submitted/approving/approved). Called by the
    scope_sid_editable dependency to gate every editing route once a trip is submitted.
    EXCEPTION: an ADMIN may edit while 'submitted' — final touch-ups on the approve page
    (the whole toolbox rides on this one gate). `approving` (mid-approve claim) and
    `approved` (terminal) stay read-only for everyone."""
    row = db.query_one("SELECT status FROM sessions WHERE id=?", (sid,))
    if not row:
        raise HTTPException(404, detail={"error": "no_session", "detail": sid})
    status = row["status"]
    if status in _EDITABLE_STATUSES:
        return
    if status == "submitted" and user is not None and getattr(user, "is_admin", False):
        return
    raise HTTPException(403, detail={
        "error": "locked",
        "detail": f"session is '{status}' and read-only; it is awaiting "
                  "admin approval (or already approved)"})


def _working_duration(sid: str, frow) -> float:
    if not frow["has_audio"] or not frow["current_mp3_path"] or not frow["mp3_name"]:
        return 0.0
    # Reconstruct from the work dir rather than trusting the stored absolute
    # current_mp3_path: a session seeded on one host (e.g. Windows "D:\...") and
    # later served from another (the Linux review server) has a baked-in path that
    # won't resolve, which would silently zero the duration and permanently block
    # coverage recording + the Done gate.
    p = work_dirs(sid)["working"] / frow["mp3_name"]
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
    # _ZH BEFORE a pick = A/B audition (no working take yet → current_mp3_path NULL):
    # not playback-gated, the human A/B listen is the backstop. AFTER the pick (promoted)
    # or a finalised single-audio _ZH trip, current_mp3_path is set → normal playback gate.
    if _is_zh_session(sid) and not frow["current_mp3_path"]:
        return [], True
    cov = json.loads(frow["played_coverage_json"] or "{}")
    ranges = cov.get("ranges", []) if cov.get("hash") == frow["working_audio_hash"] else []
    dur = _working_duration(sid, frow)
    working_done = bool(dur) and _coverage_total(ranges) >= COVERAGE_DONE_FRACTION * dur
    return ranges, working_done or _original_done(sid, frow)


_UNSAFE_FN = re.compile(r"[^A-Za-z0-9._-]+")

_TRIP_ID_CACHE: dict[str, str] = {}                  # sid -> trip_id (immutable after seed)


def _trip_id_cached(sid: str) -> str:
    """serialize_field runs once per FIELD (a session GET serialises every field), so the
    per-sid trip_id lookup is cached the way `_ZH_IS_CACHE` caches is_zh — a session's
    trip_id never changes after seed."""
    v = _TRIP_ID_CACHE.get(sid)
    if v is None:
        v = trip_id_for_session(sid)
        _TRIP_ID_CACHE[sid] = v
    return v


def field_download_name(trip_id: str, frow) -> str:
    """The mp3 filename an admin sees when they take a scene's audio off to a desktop
    editor. It NAMES the field it came from (`…_scene3_questionOption1.mp3`), because the
    only thing standing between a downloaded take and being re-imported into the WRONG
    slot is the filename. The frontend compares an upload against this same name before
    importing, so this is the single source of truth for both sides."""
    if frow["field_path"] == "questionOption":
        label = f"questionOption{frow['option_index']}"
    else:
        label = frow["field_path"]
    trip = _UNSAFE_FN.sub("_", trip_id)          # trip ids carry spaces + dots
    return f"{trip}_scene{frow['scene_index']}_{label}.mp3"


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

    result = {
        "fid": fid,
        "scene_index": frow["scene_index"],
        "field_path": field_path_out,
        "has_audio": has_audio,
        "original_text": frow["original_text"],
        "current_text": frow["current_text"],
        # What the WORKING take says (seeded to original_text; re-set at each combine) —
        # the FE compares the JP kana line against THIS, not the seed, to gate "Generate
        # from edit" (the localization block's working_hans is the _ZH sibling).
        "working_text": frow["working_text"],
        "source_text": frow["source_text"] or "",
        "original_source": frow["original_source"] or "",
        "flag": frow["flag"],
        "comment": frow["comment"],
        # Best-effort audit hint: who last changed this field (stamped by db.update_fields
        # from the request context) — the approve page badges fields the ADMIN touched.
        "edited_by": _srow_get(frow, "edited_by"),
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
        # The filename this field's take carries in the per-scene download zip. The FE
        # warns when an uploaded mp3's name doesn't match it (wrong-slot import guard).
        "download_name": (field_download_name(_trip_id_cached(sid), frow)
                          if has_audio else None),
    }

    # _ZH: attach the editable 4-script block. Mandarin is V3-only — the single working
    # take (built above) plays like any other language; no V2/V3 side-by-side audition.
    if _is_zh_session(sid):
        loc_raw = _srow_get(frow, "localization_json")
        result["localization"] = json.loads(loc_raw) if loc_raw else None
    return result


def get_session(sid: str) -> dict:
    srow = _session_row(sid)
    if srow["status"] in EDITABLE_STATUSES:
        warm_whisper_async(sid)   # pre-warm word-timing caches for fast first edits
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

    # Every isStaticImage index of this trip — the corroboration static360 needs before
    # it will trust a 4K folder (see that module: same location, different numbering).
    static_idx = {i for i, s in enumerate(trip.get("quickTrips") or [])
                  if (s or {}).get("isStaticImage")}

    for i, s in enumerate(trip.get("quickTrips") or []):
        overlays = []
        for si in (s.get("staticImages") or []):
            fn = si.get("filename")
            if fn:
                overlays.append({"filename": fn,
                                 "url": f"/overlays/{sid}/{fn}"})
        # S9: only advertise a static-360 image_url when the file actually resolves —
        # locally OR on R2 (the hosted/laptop case has no local source trees) — else
        # null, never a URL that 404s. A local hit is mirrored to R2 for other hosts.
        #
        # PREFER the 4K re-encode ({i}-4k.jpg, ~1 MB) over the {i}.jpg VR master
        # (7680×7680, ~15 MB): same picture, and the master was never meant to be
        # <img>-loaded. Fall back to the master when a trip has no 4K copy.
        image_url = None
        thumb_url = thumbs.thumb_url_for_scene(s)
        if s.get("isStaticImage"):
            for still, local in (
                    (static360.name_for(i),
                     static360.resolve(trip_id, _image_base_ids(trip_id), static_idx, i)),
                    (f"{i}.jpg",
                     _resolve_overlay_file(trip_id, mp3_dir, ogg_dir, f"{i}.jpg",
                                           srow["folder_name"] or "")),
            ):
                if local is not None:
                    images_r2.ensure_uploaded(_overlay_base(trip_id), still, local)
                if local is not None or images_r2.exists(_overlay_base(trip_id), still):
                    image_url = f"/overlays/{sid}/{still}"
                    break
        elif thumb_url is None:
            # VID scene whose stitched thumbnail doesn't resolve — e.g. gallery/legacy
            # trips whose Vimeo ids aren't in the VideoIds JSON (thumbs.py yields null).
            # Fall back to the per-scene 360 still that the content side uploads to
            # review-overlays/<cid>/<i>.jpg, resolved and served exactly like a static
            # scene's still. thumb_url still wins whenever it resolves, so this never
            # overrides a real stitched thumbnail; it only fills the "no thumbnail" gap.
            still = f"{i}.jpg"
            local = _resolve_overlay_file(trip_id, mp3_dir, ogg_dir, still,
                                          srow["folder_name"] or "")
            if local is not None:
                images_r2.ensure_uploaded(_overlay_base(trip_id), still, local)
            if local is not None or images_r2.exists(_overlay_base(trip_id), still):
                image_url = f"/overlays/{sid}/{still}"
        scenes_out.append({
            "index": i,
            "video_id": _vimeo_id(s.get("videoUrl")),
            "is_static_image": bool(s.get("isStaticImage")),
            "has_audio": bool(s.get("hasAudio")),
            "image_url": image_url,
            # VID scenes get an R2 thumbnail; static/PIC scenes stay null (the frontend
            # falls back to image_url). A VID scene with no resolvable thumbnail also
            # falls back to image_url (the per-scene still), computed above.
            "thumb_url": thumb_url,
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
        "is_zh": bool(_srow_get(srow, "is_zh")),
        # Narration language ("English"/"Mandarin"/"Japanese") — lets the FE gate the
        # CJK-specific SceneDesc controls (JP hides the English selection ops; the kana
        # line is what's voiced). is_zh stays the Mandarin A/B-audition flag.
        "language": audio_core.language_of(srow["trip_id"]),
        "preferred_version": _srow_get(srow, "preferred_version"),
        # True when the trip EXPECTS narration audio but the session seeded with none
        # (masters unresolvable locally/R2 — the admin text-only editing path). A trip
        # that genuinely has no audio fields stays False.
        "audio_unavailable": (
            any((s or {}).get("hasAudio") or ((s or {}).get("questionKey") or "").strip()
                or (s or {}).get("questionOptionKeys")
                for s in trip.get("quickTrips") or [])
            and not any(fr["has_audio"] for fr in frows)),
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


def _whisper_paths(sid: str, frow) -> tuple[Path, Path, Path] | None:
    """(audio, cache_json, sidecar) for a field's Whisper word cache, or None."""
    name = frow["mp3_name"]
    if not name:
        return None
    audio = work_dirs(sid)["working"] / name
    meta_dir = config.WORK_ROOT / sid / "whisper"
    return audio, meta_dir / (audio.stem + ".json"), meta_dir / (audio.stem + ".audiohash")


# Whisper transcription language by narration language. Transcribing in the WRONG
# language poisons the whole splice path: the tokens barely align with the cleaned
# text, so _whisper_index_map goes sparse and _silence_cut "expands" through words
# that were never mapped — heard in the field (2026-07-17, Hirsau_Abbey_A12_DE) as a
# highlight of one sentence re-voicing the next sentence too. CJK trips normally use
# the MMS aligner, but map them honestly anyway.
_WHISPER_LANGS = {"English": "en", "Spanish": "es", "French": "fr",
                  "German": "de", "Italian": "it", "Japanese": "ja", "Mandarin": "zh"}


def _whisper_lang(trip_id: str) -> str:
    return _WHISPER_LANGS.get(audio_core.language_of(trip_id), "en")


def _split_sidecar(text: str | None) -> tuple[str | None, str]:
    """Sidecar format ``<audiohash>|<lang>``. Pre-2026-07-17 sidecars hold just the
    hash — those caches were all transcribed as 'en', so that's the implied lang."""
    if not text:
        return None, "en"
    hash_part, _, lang_part = text.partition("|")
    return hash_part or None, (lang_part or "en")


_FG_WHISPER_LOCK = threading.Lock()   # guards _fg_whisper_count
_fg_whisper_count = 0                 # foreground callers waiting on / holding WHISPER_LOCK
_WARM_STATE_LOCK = threading.Lock()   # guards the two warm-tracking structures below
_WARM_SESSIONS: set[str] = set()      # sids with a session-wide warm thread in flight
_WARM_LAST: dict[str, float] = {}     # sid → last session-wide warm start (throttle)
_WARM_THROTTLE_S = 60.0


def _whisper_orig(srow, frow, background: bool = False) -> list[dict]:
    """Word timings for the CURRENT working audio (so cut times track the combined take,
    not the pristine master). Content-hash + language keyed → re-transcribes after each
    combine, and when a cache was built in the wrong language (the forced-'en' EU caches).

    ``background=True`` marks a cache pre-warm call: it yields to any foreground caller
    (a reviewer waiting on a regen) before taking WHISPER_LOCK, so warming never adds
    more than one in-flight file's latency to a real request."""
    global _fg_whisper_count
    paths = _whisper_paths(srow["id"], frow)
    if paths is None:
        return []
    audio, cache_json, sidecar = paths
    if not audio.exists():
        return []
    meta_dir = cache_json.parent
    meta_dir.mkdir(parents=True, exist_ok=True)
    lang = _whisper_lang(srow["trip_id"])
    cur_hash = _file_hash(audio)
    prev_hash, prev_lang = _split_sidecar(
        sidecar.read_text().strip() if sidecar.exists() else None)
    refresh = cache_json.exists() and (prev_hash != cur_hash or prev_lang != lang)
    if not background:
        with _FG_WHISPER_LOCK:
            _fg_whisper_count += 1
    else:
        # Yield: don't queue a warm transcription ahead of a waiting reviewer.
        while True:
            with _FG_WHISPER_LOCK:
                if _fg_whisper_count == 0:
                    break
            time.sleep(0.25)
    try:
        with WHISPER_LOCK:
            data = transcribe_words(audio, lang=lang, model_name="small",
                                    cache_dir=meta_dir, refresh=refresh)
    finally:
        if not background:
            with _FG_WHISPER_LOCK:
                _fg_whisper_count -= 1
    sidecar.write_text(f"{cur_hash or ''}|{lang}")
    return data.get("words") or []


def _warm_whisper_session(sid: str) -> None:
    """Background thread body: pre-transcribe every audio field's working take so the
    first highlight/pause edit doesn't pay a full faster-whisper pass. SceneDesc first
    (where highlight-fix happens), in scene order. Pure optimisation — swallow everything."""
    try:
        srow = _session_row(sid)
        frows = db.query(
            "SELECT * FROM field_edits WHERE session_id=? ORDER BY "
            "CASE WHEN field_path='SceneDesc' THEN 0 ELSE 1 END, scene_index, id",
            (sid,))
        for frow in frows:
            if not (frow["has_audio"] and frow["mp3_name"]):
                continue
            try:
                _whisper_orig(srow, frow, background=True)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        with _WARM_STATE_LOCK:
            _WARM_SESSIONS.discard(sid)


def _warm_whisper_field(sid: str, fid: int) -> None:
    try:
        srow = _session_row(sid)
        frow = _field_row(sid, fid)
        if frow["has_audio"] and frow["mp3_name"]:
            _whisper_orig(srow, frow, background=True)
    except Exception:  # noqa: BLE001
        pass


def warm_whisper_async(sid: str, fid: int | None = None) -> None:
    """Fire-and-forget Whisper cache warm: the whole session (on trip open, throttled +
    single-flight) or one field (right after an op replaced its working audio). Never
    raises, never blocks the caller."""
    try:
        if fid is None:
            now = time.time()
            with _WARM_STATE_LOCK:
                if sid in _WARM_SESSIONS:
                    return
                if now - _WARM_LAST.get(sid, 0.0) < _WARM_THROTTLE_S:
                    return
                _WARM_SESSIONS.add(sid)
                _WARM_LAST[sid] = now
            threading.Thread(target=_warm_whisper_session, args=(sid,),
                             daemon=True, name=f"whisper-warm-{sid}").start()
        else:
            threading.Thread(target=_warm_whisper_field, args=(sid, fid),
                             daemon=True, name=f"whisper-warm-{sid}-{fid}").start()
    except Exception:  # noqa: BLE001
        pass


def _reindex_word_cache(sid: str, frow, a: float, b: float, ins: float,
                        new_hash: str | None, pre_hash: str | None) -> None:
    """Re-time the Whisper word cache after an in-place edit that replaced the working
    take's audio span ``[a, b)`` with ``ins`` seconds of SILENCE, and re-stamp the
    audio-hash sidecar so the next read is a cache HIT.

    Why this exists: the cache is keyed on the working mp3's content hash, so EVERY
    pause edit invalidated it, and the next tool call (another pause, a trim, a
    'generate from edit') paid a full faster-whisper re-transcription of the whole clip
    — beam 5, `small`, on the laptop's CPU. That re-transcription was the entire
    perceived cost of the pause tools.

    It is safe to skip precisely BECAUSE the edited span is silence: no word lives
    inside [a, b), so the true timings of the new audio are the old ones with every
    instant after the edit displaced by ``ins - (b - a)``. Each timestamp is mapped
    independently, so a word whose Whisper-reported end happens to overhang the cut
    (Whisper routinely stretches a word across an adjacent pause) simply grows to cover
    the inserted silence — which is what the new audio actually sounds like.

    ONLY call this for pure-silence edits. Anything that removes or re-voices SPEECH
    (trim-noise, splice, import) must let the cache go stale and re-transcribe.

    ``pre_hash`` is the hash of the working file the edit was applied TO (read before
    _set_working rewrote it). The re-time is only valid when the cache actually
    described that audio — the sidecar must match ``pre_hash``. Without this check, a
    cache left stale by a speech-altering op (combine/import/undo/wave edit) would be
    shifted and stamped as CURRENT, and the next splice would cut at the old take's
    word times, silently. On a mismatch we simply leave the cache alone: its sidecar
    already disagrees with the new file's hash, so the next read re-transcribes.

    Best-effort: on any problem we delete the sidecar, which forces an honest
    re-transcription — never a silently wrong timeline."""
    paths = _whisper_paths(sid, frow)
    if paths is None:
        return
    _audio, cache_json, sidecar = paths
    if not cache_json.exists():
        return                       # never transcribed → nothing to re-time
    prev_hash, prev_lang = _split_sidecar(
        sidecar.read_text().strip() if sidecar.exists() else None)
    if not pre_hash or prev_hash != pre_hash:
        return                       # cache was already stale → let it re-transcribe
    if prev_lang != _whisper_lang(_session_row(sid)["trip_id"]):
        return                       # wrong-language cache → let it re-transcribe
    delta = float(ins) - (float(b) - float(a))

    def remap(t: float) -> float:
        if t <= a:
            return t
        if t >= b:
            return t + delta
        return a                     # inside the replaced span (silence) → clamp to its start

    try:
        data = json.loads(cache_json.read_text(encoding="utf-8"))
        new_dur = None
        if data.get("duration") is not None:
            new_dur = max(0.0, round(float(data["duration"]) + delta, 3))
            data["duration"] = new_dur
        for w in data.get("words") or []:
            for k in ("start", "end"):
                if w.get(k) is not None:
                    t = round(remap(float(w[k])), 3)
                    # trim_silence is modelled end-anchored (times unchanged, duration
                    # shrunk), so a final-word `end` Whisper stretched into the old
                    # trailing silence can overhang the new clip end — clamp it, exactly
                    # as a re-transcription would report it.
                    if new_dur is not None:
                        t = min(t, new_dur)
                    w[k] = t
        cache_json.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        sidecar.write_text(f"{new_hash or ''}|{prev_lang}")
    except Exception as e:  # noqa: BLE001
        print(f"[sessions] word-cache re-time failed ({cache_json.name}): {e} "
              "— falling back to re-transcription")
        sidecar.unlink(missing_ok=True)


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


def update_localization(sid: str, fid: int, script: str, text: str) -> dict:
    """Autosave one script (Hans|Hant|zhuyin|en) of a _ZH field's 4-script block. The
    reviewer corrects the text directly (never pinyin — pinyin is regenerated from the
    confirmed zhuyin at writeback). Chinese audio is A/B, not spliced, so this touches no
    audio; it only drops a stale `done` when the value actually changes."""
    _session_row(sid)
    frow = _field_row(sid, fid)
    loc_raw = _srow_get(frow, "localization_json")
    if not loc_raw:
        raise HTTPException(400, detail={
            "error": "not_localized",
            "detail": "this field has no 4-script block (not a _ZH localized field)"})
    loc = json.loads(loc_raw)
    cur = loc.get("cur") or {}
    if script not in cur:
        raise HTTPException(422, detail={
            "error": "bad_script",
            "detail": f"{script!r} is not editable on this field "
                      f"(editable: {sorted(cur.keys())})"})
    changed = (cur.get(script) or "") != (text or "")
    cur[script] = text or ""
    loc["cur"] = cur
    patch = {"localization_json": json.dumps(loc, ensure_ascii=False)}
    if changed and frow["flag"] == "done":
        patch["flag"] = "none"
    db.update_fields(fid, **patch)
    db.touch_session(sid)
    return serialize_field(sid, _field_row(sid, fid))


def apply_suggested_fix(sid: str, scene: int, field: str,
                        option: int | None) -> dict:
    """Apply the machine-verified suggested fix from the latest Gate-2 (Claude) report to
    the identified _ZH field, via the normal `update_localization` autosave path. The fix
    is an already-computed, already-hsk-verified value (`scripts/claude_review.py:verify_fixes`
    → `suggested_fix_verified`); this only plumbs it into the field so the reviewer doesn't
    retype it — they still listen/approve. Scoped to _ZH: that's where suggested fixes carry a
    verification badge; for EN/JP the panel stays read-only.

    Guards: (1) _ZH only; (2) refuse if the report marks the fix `suggested_fix_verified=False`
    (hsk_lib said the fix itself is inconsistent); (3) re-run Gate-1 AFTER applying and return
    it, so a fix that would introduce a new blocker is visible immediately."""
    if not _is_zh_session(sid):
        raise HTTPException(422, detail={
            "error": "apply_zh_only",
            "detail": "suggested-fix apply is available for Mandarin (_ZH) fields only"})
    row = db.query_one(
        "SELECT * FROM auto_reviews WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
        (sid,))
    if not row:
        raise HTTPException(404, detail={"error": "no_auto_review",
                                         "detail": "no Gate-2 report for this session yet"})
    report_fields = json.loads(row["report_json"]).get("fields", [])
    rf = next((f for f in report_fields
               if f.get("scene") == scene and f.get("field") == field
               and f.get("option") == option), None)
    if rf is None:
        raise HTTPException(404, detail={
            "error": "no_report_field",
            "detail": f"scene={scene} field={field} option={option} not in the report"})
    fix = rf.get("suggested_fix")
    if not isinstance(fix, dict) or not fix:
        raise HTTPException(422, detail={"error": "no_suggested_fix",
                                         "detail": "this field has no suggested fix to apply"})
    if rf.get("suggested_fix_verified") is False:
        raise HTTPException(409, detail={
            "error": "fix_unverified",
            "detail": "the report marks this fix as FAILED verification — do not apply as-is"})

    # Locate the field_edits row for (scene, field, option) — option_index may be NULL.
    cands = db.query(
        "SELECT * FROM field_edits WHERE session_id=? AND scene_index=? AND field_path=?",
        (sid, scene, field))
    frow = next((c for c in cands if c["option_index"] == option), None)
    if frow is None:
        raise HTTPException(404, detail={"error": "no_field",
                                         "detail": f"no field row for scene={scene} {field}"})
    fid = frow["id"]

    loc = json.loads(_srow_get(frow, "localization_json") or "{}")
    cur_scripts = set((loc.get("cur") or {}).keys())
    applied: list[str] = []
    skipped: list[dict] = []
    result = serialize_field(sid, frow)
    for script, text in fix.items():
        if script not in cur_scripts:
            skipped.append({"script": script, "reason": "not an editable script on this field"})
            continue
        result = update_localization(sid, fid, script, text)
        applied.append(script)
    if not applied:
        raise HTTPException(422, detail={
            "error": "nothing_applied",
            "detail": f"none of {sorted(fix.keys())} are editable here "
                      f"(editable: {sorted(cur_scripts)})"})

    hard, soft = validate(sid, mode="submit")
    return {"field": result, "applied": applied, "skipped": skipped,
            "gate1": {"hard": hard, "soft": soft}}


# --------------------------------------------------------------------------- #
# Gate-2 findings triage — the reviewer answers the AI, then the trip goes to the admin.
# (docs/auto-review-proposal.md; workflow decided by dave 2026-07-13.)
# --------------------------------------------------------------------------- #
def _open_findings_count(sid: str) -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM auto_review_findings "
                       "WHERE session_id=? AND status='open'", (sid,))
    return int(row["n"]) if row else 0


def _finding_dict(r) -> dict:
    return {
        "id": r["id"], "scene": r["scene_index"], "field": r["field_path"],
        "option": r["option_index"], "verdict": r["verdict"],
        "reasons": json.loads(r["reasons_json"] or "[]"),
        "suggested_fix": json.loads(r["fix_json"]) if r["fix_json"] else None,
        "suggested_fix_verified": (None if r["fix_verified"] is None
                                   else bool(r["fix_verified"])),
        "status": r["status"], "note": r["response_note"] or "",
        "responded_by": r["responded_by"], "responded_at": r["responded_at"],
        "created_at": r["created_at"],
    }


def findings(sid: str) -> dict:
    """Every Gate-2 triage item for this session + how many are still unanswered.
    Reviewers act on these; admins read them (the notes are addressed TO the admin)."""
    rows = db.query(
        "SELECT * FROM auto_review_findings WHERE session_id=? "
        "ORDER BY COALESCE(scene_index, -1), id", (sid,))
    items = [_finding_dict(r) for r in rows]
    return {"findings": items,
            "open": sum(1 for i in items if i["status"] == "open"),
            "status": _session_row(sid)["status"]}


def respond_finding(sid: str, fid: int, user, action: str, note: str = "") -> dict:
    """Answer ONE finding. Three answers, per the workflow dave specified:

      resolved — the reviewer actioned the suggestion (they edited the text, possibly via
                 apply_suggested_fix). Note optional.
      rejected — the reviewer keeps their version. A note is REQUIRED: it is the admin's
                 only record of WHY the AI was overruled.
      deferred — the finding is about the ENGLISH/source, which is not the reviewer's to
                 change; it goes to the admin to action or dismiss. Note optional.

    Answering does NOT re-open a closed session and does NOT edit any text; the reviewer's
    edits go through the normal autosave path. Clearing the last open finding simply
    unblocks re-submit (they still press Submit — we never auto-hand-back)."""
    if action not in auto_review_ingest.RESPONSES:
        raise HTTPException(422, detail={
            "error": "bad_action",
            "detail": f"action must be one of {list(auto_review_ingest.RESPONSES)}"})
    note = (note or "").strip()
    if action in auto_review_ingest.NOTE_REQUIRED and not note:
        raise HTTPException(422, detail={
            "error": "note_required",
            "detail": "rejecting a suggestion needs a short reason — the admin sees it "
                      "instead of the change"})
    row = db.query_one("SELECT * FROM auto_review_findings WHERE id=? AND session_id=?",
                       (fid, sid))
    if not row:
        raise HTTPException(404, detail={"error": "no_finding", "detail": str(fid)})
    db.execute(
        "UPDATE auto_review_findings SET status=?, response_note=?, responded_by=?, "
        "responded_at=? WHERE id=?",
        (action, note, getattr(user, "username", None), time.time(), fid))
    return findings(sid)


def skip_findings_triage(sid: str, user, note: str = "") -> dict:
    """ADMIN override: take a trip back from the reviewer without waiting for them to
    answer the AI (they're away, it's urgent, the findings are noise). Every open finding
    is marked 'deferred' to the admin — an honest record of what was NOT answered — and the
    session returns to 'submitted' so approve() can claim it.

    This is the escape hatch that keeps the gate from ever wedging a trip."""
    srow = _session_row(sid)
    if srow["status"] != "ai_review":
        raise HTTPException(409, detail={
            "error": "bad_state",
            "detail": f"session is '{srow['status']}' — nothing to skip (triage only "
                      "holds a session in 'ai_review')"})
    who = getattr(user, "username", None)
    stamp = (note or "").strip() or f"[admin {who} took this back without reviewer triage]"
    db.execute(
        "UPDATE auto_review_findings SET status='deferred', responded_by=?, responded_at=?, "
        "response_note=CASE WHEN response_note='' THEN ? ELSE response_note END "
        "WHERE session_id=? AND status='open'",
        (who, time.time(), stamp, sid))
    db.execute("UPDATE sessions SET status='submitted', updated_at=? "
               "WHERE id=? AND status='ai_review'", (time.time(), sid))
    return findings(sid)


def findings_inbox(user) -> dict:
    """Sessions in 'ai_review' this user should act on — the nav badge + queue chips.
    A reviewer sees the ones THEY submitted; an admin sees all (they can override)."""
    rows = db.query(
        "SELECT s.id, s.trip_id, s.submitted_by, s.updated_at, "
        "  (SELECT COUNT(*) FROM auto_review_findings f "
        "    WHERE f.session_id=s.id AND f.status='open') AS open_n "
        "FROM sessions s WHERE s.status='ai_review' ORDER BY s.updated_at")
    out = []
    for r in rows:
        if not user.is_admin and r["submitted_by"] != user.username:
            continue
        if not auth.language_allowed(user, r["trip_id"]):
            continue
        out.append({"session_id": r["id"], "trip_id": r["trip_id"],
                    "submitted_by": r["submitted_by"], "open": r["open_n"],
                    "updated_at": r["updated_at"]})
    return {"sessions": out, "count": sum(s["open"] for s in out)}


def set_version(sid: str, version: str | None) -> dict:
    """Collapse a _ZH session onto its single ElevenLabs take by promoting
    work/{sid}/v3 into the orig/working splice slots (regenerate/combine/trim/import/
    fallback all apply, and Done becomes playback-gated). Mandarin is V3-only
    (dave/Ted 2026-07-02), so V2 and the revert-to-audition path are retired — only
    'v3' is accepted. New sessions already seed collapsed to V3; this remains to migrate
    any legacy un-picked session. Re-picking v3 when already collapsed is a no-op (keeps
    in-progress audio edits). 4-script text edits live on the field row and always survive."""
    srow = _session_row(sid)
    if version != "v3":
        raise HTTPException(409, detail={
            "error": "v3_only",
            "detail": "Mandarin uses ElevenLabs V3 only — the V2/V3 audition is retired."})
    src_root = WORK_ROOT / sid / version
    prev = _srow_get(srow, "preferred_version")
    # A/B is V2 (eleven_multilingual_v2, honours the HSK speed) vs V3 (eleven_v3, speed
    # ignored). Pin the session's model/speed to the picked version so a later regenerate
    # matches the take the reviewer chose (dave: "a V3 pick must regenerate with v3").
    model_ov = "eleven_v3" if version == "v3" else "eleven_multilingual_v2"
    speed_ov = 1.0 if version == "v3" else None   # v2 → speed_for_trip (HSK3 = 0.85)
    # Re-picking the SAME already-collapsed version is a no-op (don't wipe in-progress
    # audio edits). A finalised _ZH trip has no A/B sets staged → just record the choice.
    already_collapsed = bool(db.query_one(
        "SELECT 1 FROM field_edits WHERE session_id=? AND has_audio=1 "
        "AND current_mp3_path IS NOT NULL LIMIT 1", (sid,)))
    if not src_root.is_dir() or (prev == version and already_collapsed):
        db.execute("UPDATE sessions SET preferred_version=?, updated_at=? WHERE id=?",
                   (version, time.time(), sid))
        return get_session(sid)

    dirs = work_dirs(sid)
    for frow in db.query("SELECT * FROM field_edits WHERE session_id=?", (sid,)):
        if not frow["has_audio"] or not frow["mp3_name"]:
            continue
        name = frow["mp3_name"]
        src = src_root / name
        if not src.exists():
            continue
        audio_io.mp3_to_mp3_copy(src, dirs["orig"] / name)
        audio_io.mp3_to_mp3_copy(src, dirs["working"] / name)
        # reset the audio history to a clean v0 on the chosen take
        db.execute("DELETE FROM audio_versions WHERE field_id=?", (frow["id"],))
        stem = name[:-4]
        db.execute(
            "INSERT INTO audio_versions(session_id,field_id,scene_index,n,kind,path,"
            "label,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (sid, frow["id"], frow["scene_index"], 0, "v0_original",
             str(dirs["orig"] / name), f"{stem}v0", time.time()))
        patch = {
            "current_mp3_path": str(dirs["working"] / name),
            "working_audio_hash": _file_hash(dirs["working"] / name),
            "candidate_mp3_path": None,
            "fallback_mp3_path": None,
            "played_coverage_json": "{}",
            "original_coverage_json": "{}",
            "version_cursor": None,
        }
        if frow["flag"] == "done":
            patch["flag"] = "none"
        db.update_fields(frow["id"], **patch)

    db.execute("UPDATE sessions SET preferred_version=?, model_override=?, speed_override=?, "
               "updated_at=? WHERE id=?",
               (version, model_ov, speed_ov, time.time(), sid))
    db.touch_session(sid)
    return get_session(sid)


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


def _zh_hans_for_tts(frow) -> str | None:
    """The Simplified-hanzi narration text for a _ZH audio field (localization cur.Hans),
    or None if the field has no 4-script block. This is what the reviewer edits (never
    current_text), so it is the source of truth for regenerating the Mandarin take."""
    loc_raw = _srow_get(frow, "localization_json")
    if not loc_raw:
        return None
    hans = ((json.loads(loc_raw).get("cur") or {}).get("Hans") or "").strip()
    return hans or None


def _last_spoken_line(text: str) -> str:
    """The last non-empty line — for Japanese, the KANA (phonetic) line under the kanji.
    The kanji line is NOT forced-alignable (0.27 conf); the kana is what's voiced/aligned."""
    text = audio_core.strip_url_lines(text or "")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _zh_working_hans_patch(frow) -> dict:
    """After a ZH combine, record the hanzi the working take now says (cur.Hans) as the
    re-baseline for the NEXT surgical splice's OLD text (see _cjk_spoken). A no-op (empty
    patch) for non-ZH fields — localization_json is only populated for Mandarin."""
    loc_raw = _srow_get(frow, "localization_json")
    if not loc_raw:
        return {}
    try:
        loc = json.loads(loc_raw)
    except (ValueError, TypeError):
        return {}
    hans = ((loc.get("cur") or {}).get("Hans") or "").strip()
    if not hans:
        return {}
    loc["working_hans"] = hans
    return {"localization_json": json.dumps(loc, ensure_ascii=False)}


def _cjk_spoken(srow, frow) -> tuple[str, str, str] | None:
    """``(lang, OLD, NEW)`` spoken text for a CJK audio field — the surgical-splice /
    whole-regen source — or ``None`` for Latin trips (English/Welsh untouched).

      _ZH → ``('zh', orig.Hans, cur.Hans)``: the Simplified hanzi. OLD = what the master was
            voiced from; NEW = the reviewer's edited hanzi (the 4-script block, never
            current_text).
      _JP → ``('jp', <kana of working/original text>, <kana of current_text>)``: the KANA
            line. OLD tracks the current working take (working_text, set at combine); NEW is
            the reviewer's edit (current_text).
    A near-zero aligner mean-score on OLD↔audio (stale text↔audio) makes the splice bail to
    whole-regen, so OLD need only be a good approximation of the take."""
    lang_full = audio_core.language_of(srow["trip_id"])
    if lang_full == "Mandarin":
        new = _zh_hans_for_tts(frow)
        if not new:
            return None
        loc = json.loads(_srow_get(frow, "localization_json") or "{}")
        # OLD = the hanzi the WORKING take currently says. Re-baselined to cur.Hans at every
        # combine (working_hans); falls back to the seed orig.Hans before the first combine.
        # Using the seed AFTER a combine would align stale text against the new audio (the
        # mean-conf gate only catches large divergence, not a one-char drift), so a second
        # consecutive edit could mis-place a cut or needlessly re-voice a correct clause.
        old = ((loc.get("working_hans")
                or (loc.get("orig") or {}).get("Hans") or "").strip())
        return ("zh", old, new)
    if lang_full == "Japanese":
        new = _last_spoken_line(frow["current_text"] or "")
        if not new:
            return None
        old = _last_spoken_line(frow["working_text"] or frow["original_text"] or "")
        return ("jp", old, new)
    return None


def _cjk_sel_range(frow, lang: str, new_text: str, start: int, end: int) -> tuple[int, int]:
    """Normalize a FE selection/caret (char offsets into the textarea the reviewer
    highlighted in) to offsets into the SPOKEN text ``new_text`` (see ``_cjk_spoken``).

      zh → the Hans textarea (localization cur.Hans): same string minus the leading
           whitespace ``_zh_hans_for_tts`` strips.
      jp → the whole kanji⏎kana textarea (current_text): the selection must fall in the
           KANA line (what's voiced) — clamped when it merely bleeds past an edge, 409
           with a hint when it doesn't touch the kana line at all (e.g. kanji selected).
    """
    start, end = int(start), int(end)
    if lang == "jp":
        raw = frow["current_text"] or ""
        k0 = raw.rfind(new_text)          # the kana line's span within the raw textarea
        if k0 < 0:                        # can't happen: new_text IS a line of raw
            raise HTTPException(409, detail={
                "error": "kana_line_only",
                "detail": "Couldn't locate the kana line in the narration text."})
        k1 = k0 + len(new_text)
        err = HTTPException(409, detail={
            "error": "kana_line_only",
            "detail": "Highlight (or place the cursor) in the LAST line — the kana. "
                      "That is the text that is voiced; the kanji line has no audio."})
        if start == end:                  # caret (pause tools): clamp trailing-ws → k1
            if start < k0:
                raise err
            p = min(start, k1) - k0
            return p, p
        s, e = max(start, k0), min(end, k1)
        if s >= e:                        # selection doesn't touch the kana line
            raise err
        return s - k0, e - k0
    # zh: offsets are into the Hans textarea; the spoken text is its .strip().
    loc_raw = _srow_get(frow, "localization_json")
    raw = ((json.loads(loc_raw).get("cur") or {}).get("Hans") or "") if loc_raw else ""
    lead = len(raw) - len(raw.lstrip())
    s = max(0, min(start - lead, len(new_text)))
    e = max(s, min(end - lead, len(new_text)))
    return s, e


def _cjk_char_times(srow, frow, lang: str, old_text: str):
    """Gated per-char times of the WORKING take for the direct char→time tools. The
    direct tools NEVER silently fall back (they edit the take in place) — a missing
    aligner or a text↔audio mismatch is a 409 the reviewer sees."""
    audio = work_dirs(srow["id"])["working"] / frow["mp3_name"]
    try:
        ct = cjk_splice.char_times(str(audio), old_text, lang)
    except cjk_align.AlignerError as e:
        raise HTTPException(409, detail={
            "error": "aligner_unavailable",
            "detail": f"Can't map the text to the audio — CJK aligner unavailable ({e})."})
    if ct is None:
        raise HTTPException(409, detail={
            "error": "text_audio_mismatch",
            "detail": "The audio doesn't match this text closely enough to locate the "
                      "highlighted spot — regenerate the narration instead."})
    return ct


def _cjk_caret_seed(srow, frow, cjk, caret: int) -> float:
    """Audio seed time for the pause tools at TEXT caret ``caret``: the end of the last
    placed spoken char before it. Walking back across punctuation is correct — a caret
    right after 。 seeds from the clause ENDER, whose end abuts the very pause being
    edited (enders are the confidently-placed anchors; see cjk_splice.gap_cut). The seed
    must clear the anchor floor: a mis-placed seed could silently edit the WRONG pause."""
    lang, old, new = cjk
    s, _e = _cjk_sel_range(frow, lang, new, caret, caret)
    oa, _ob = cjk_splice.map_new_span_to_old(old, new, s, s)
    pos, _mean = _cjk_char_times(srow, frow, lang, old)
    seed = max((p for p in pos if p < oa), default=None)
    if seed is None:
        return 0.0                        # caret before any speech → the clip's lead-in
    _st, en, score = pos[seed]
    if score < cjk_splice._ANCHOR_FLOOR.get(lang, 0.50):
        raise HTTPException(409, detail={
            "error": "no_pause",
            "detail": "Couldn't locate the audio at the cursor confidently — try placing "
                      "the cursor right after the clause punctuation (。/、)."})
    return en


def _commit_working_edit(sid: str, srow, frow, samples: np.ndarray, kind: str,
                         silence_edit: tuple[float, float, float] | None = None) -> dict:
    """Archive + persist an in-place working-take edit (the trim/pause tools): new
    version, R2 upload, and DROP any pending regenerate candidate — its tL/tR were read
    from the pre-edit audio, so combining it now would splice at stale offsets (mirrors
    undo/redo/revert). Call only when the take actually changed (no-op early returns stay
    in the callers).

    ``silence_edit`` = ``(a, b, ins)``: this edit replaced the audio span [a, b) with
    ``ins`` seconds of SILENCE and touched no speech. Pass it ONLY for such edits — it
    lets the Whisper word cache be re-timed arithmetically instead of re-transcribed
    (see _reindex_word_cache). Omit it for anything that alters speech (trim-noise,
    splice, import): the cache then goes stale on the content hash and is honestly
    rebuilt, which is the safe default."""
    dirs = work_dirs(sid)
    pre_hash = None
    if silence_edit is not None:
        # Hash of the audio the edit was applied TO — must be read BEFORE _set_working
        # rewrites the file. _reindex_word_cache only trusts the cached words when their
        # sidecar matches this (i.e. the cache described the pre-edit take).
        paths = _whisper_paths(sid, frow)
        pre_hash = _file_hash(paths[0]) if paths else None
    whash = _set_working(sid, frow, samples=samples, kind=kind)
    if silence_edit is not None:
        _reindex_word_cache(sid, frow, *silence_edit, whash, pre_hash)
    _r2_upload_working(srow["trip_id"], dirs, frow)
    patch = {"working_audio_hash": whash, "splice_confidence": None,
             "candidate_mp3_path": None}
    patch.update(_clear_coverage_and_done(frow))
    db.update_fields(frow["id"], **patch)
    db.touch_session(sid)
    return serialize_field(sid, _field_row(sid, frow["id"]))


# Candidate lead handling: a lead longer than _CAND_LEAD_MAX_S before the first aligned
# word is a previous_text leak / dead air — front-trim it down to _CAND_LEAD_KEEP_S of
# lead-in (enough attack for the first word; the span splice cuts inside it anyway).
_CAND_LEAD_MAX_S = 0.25
_CAND_LEAD_KEEP_S = 0.10


# The trailing trim's hard floor beyond the last word's aligned LETTER end: covers the
# ~50 ms alignment slack plus a natural release. (ASR verification was tried and
# REJECTED: Whisper hallucinates the completion of an audibly truncated word from
# context — it transcribed "Shogunate." on a clip that ends at "shoguna".)
_CAND_TAIL_FLOOR_PAD_S = 0.13


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

    # CJK (_ZH hanzi / _JP kana): the narrated text is the Simplified hanzi (localization
    # cur.Hans) or the kana line — NOT current_text/Gemini (the number-speller is
    # English-only). This branch is ADDITIVE and SEPARATE from the English token engine
    # below. On a SceneDesc text edit it tries a surgical CHAR-LEVEL splice (cjk_splice, via
    # the isolated MMS forced aligner); on ANY uncertainty plan_cjk returns None and we
    # WHOLE-regenerate the narration (the safe Path-A floor). Q&A fields, an explicit 'whole'
    # request, and alt text always whole-regenerate.
    cjk_fell_back = False   # surgical splice was requested but bailed to whole-regen (#5)
    cjk = _cjk_spoken(srow, frow)
    if cjk is not None:
        cjk_lang, cjk_old, cjk_new = cjk
        plan = None
        cjk_wanted_surgical = False
        working_mp3 = (dirs["working"] / frow["mp3_name"]) if frow["mp3_name"] else None
        can_surgical = (field_path == "SceneDesc" and cjk_old and cjk_new
                        and working_mp3 is not None and working_mp3.exists())
        if mode in ("highlight", "alt") and rng is not None and can_surgical:
            # Selection ops: re-voice the clause enclosing the highlighted chars (alt text
            # replaces exactly those chars inside it). Offsets arrive in the coordinates
            # of the textarea the reviewer highlighted in (JP: current_text, must touch
            # the kana line — 409 hint otherwise; ZH: the Hans field) and are normalized
            # to the spoken text here. old==new is fine (re-voice words already spelled
            # right — the usual reason to highlight).
            sel = _cjk_sel_range(frow, cjk_lang, cjk_new,
                                 int(rng["start"]), int(rng["end"]))
            plan = cjk_splice.plan_cjk_span(
                str(working_mp3), cjk_old, cjk_new, sel,
                alt_text if mode == "alt" else None,
                voice_id, voice_settings, model_id, cjk_lang)
            if plan is None and mode == "alt":
                # NEVER voice the alt as the whole field (the old bug) and never silently
                # drop it — mirror the English engine: flag for manual handling instead.
                plan = audio_splice.RegenPlan(
                    edit_required=True,
                    reason="Couldn't splice the alt text cleanly at the highlighted spot "
                           "— try a highlight within one clause, or use Create new.")
            elif plan is None:
                cjk_wanted_surgical = True   # highlight → the Path-A whole-regen floor
        elif mode == "alt" and rng is not None:
            # Alt with a selection but nothing to splice into (no working take) — refuse
            # rather than mis-voice the alt as the whole field or drop it.
            plan = audio_splice.RegenPlan(
                edit_required=True,
                reason="No working audio to splice the alt text into — use "
                       "whole-regenerate or Create new.")
        elif alt_text and alt_text.strip():
            plan = audio_splice.plan_whole(alt_text.strip(), False, voice_id,
                                           voice_settings, model_id)
        elif mode != "whole" and can_surgical:
            if cjk_old == cjk_new:
                # "Generate from edit" with the VOICED line unchanged (only the kanji
                # line / a non-Hans script was edited): regenerating would silently voice
                # the same text — tell the reviewer which line drives the audio instead.
                raise HTTPException(409, detail={
                    "error": "spoken_line_unchanged",
                    "detail": ("The kana (last) line — the voiced text — is unchanged. "
                               "Edit the kana line to change the audio."
                               if cjk_lang == "jp" else
                               "The Simplified (Hans) script — the voiced text — is "
                               "unchanged. Edit the Hans line to change the audio.")})
            cjk_wanted_surgical = True
            plan = cjk_splice.plan_cjk(str(working_mp3), cjk_old, cjk_new,
                                       voice_id, voice_settings, model_id, cjk_lang)
        if plan is None:      # Q&A / whole / no clean splice → whole-regenerate the narration
            cjk_fell_back = cjk_wanted_surgical
            plan = audio_splice.plan_whole(cjk_new, False, voice_id, voice_settings, model_id)
    # Q&A fields and SceneDesc 'whole' → whole regenerate (no splice). Alt text (if
    # supplied) is voiced VERBATIM as the whole block — "regenerate with alt text" for a
    # question option, mirroring highlight-with-alt-text but for the entire field.
    elif field_path != "SceneDesc" or mode == "whole":
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
    # so the audition (and the splice) don't carry it. The reviewer can fine-tune via
    # /trim-candidate, re-derived from the pristine copy.
    cand_path.write_bytes(plan.candidate_mp3)
    pristine = dirs["candidate"] / f"{fid}.orig.mp3"
    pristine.write_bytes(plan.candidate_mp3)
    cand_samples = audio_io.mp3_to_samples(cand_path)
    # Front-trim a previous_text leak / oversized lead: EL v2 occasionally voices a tail
    # of the prosody context BEFORE the phrase (docs/splice-end-cutoff-analysis.md; the
    # retry in generate_with_timestamps catches the blatant case — this catches what's
    # left). The char alignment places the phrase's first word AFTER the leak, so audio
    # before cand_words[0].start (minus a lead-in margin) is not the requested phrase —
    # drop it and SHIFT cand_words to match, so the span splice's candidate-side times
    # stay valid on the trimmed file. trim_candidate re-applies this same front cut when
    # it re-derives from the pristine copy.
    cw = plan.meta.get("cand_words") or []
    front_s = 0.0
    if cw and cw[0].get("start") is not None:
        lead = float(cw[0]["start"])
        if lead > _CAND_LEAD_MAX_S:
            front_s = lead - _CAND_LEAD_KEEP_S
            cand_samples = cand_samples[int(round(front_s * audio_io.SR)):]

            def _shift(w: dict) -> dict:
                out = {**w, "start": max(0.0, float(w["start"]) - front_s),
                       "end": max(0.0, float(w["end"]) - front_s)}
                if w.get("letter_end") is not None:
                    out["letter_end"] = max(0.0, float(w["letter_end"]) - front_s)
                return out

            cw = [_shift(w) for w in cw]
            plan.meta["cand_words"] = cw
    plan.meta["cand_front_trim_s"] = round(front_s, 3)
    # The trim's ms windows were tuned on 1.0x takes; a slowed CEFR take (A12 0.7,
    # B1 0.85) stretches every articulation by 1/speed, so hand it the speed the clip
    # was actually voiced at rather than letting it judge a 0.7x word-tail by 1.0x rules.
    gen_speed = _effective_speed(srow)
    trimmed = audio_io.trim_trailing_breath(cand_samples, audio_io.SR, speed=gen_speed)
    # The trim's energy heuristics are take-dependent (every threshold so far met a
    # take that beat it — docs/splice-end-cutoff-analysis.md), so FLOOR the cut at the
    # last word's aligned LETTER end (+pad). The final punctuation char's `end` absorbs
    # the clip's trailing silence (useless), but the last letter's end lands within
    # ~50 ms of the audible word end — the trim must never cut before it. The pad is a
    # natural release, so it stretches with the take like every other duration.
    # NB this floor is why plan_whole now asks for /with-timestamps: without cand_words
    # it silently does not apply, which is exactly how whole/Q&A regens lost their
    # final word.
    if cw and cw[-1].get("letter_end"):
        floor_n = int(round((float(cw[-1]["letter_end"])
                             + _CAND_TAIL_FLOOR_PAD_S / max(0.1, gen_speed))
                            * audio_io.SR))
        if len(trimmed) < floor_n:
            trimmed = cand_samples[:min(len(cand_samples), floor_n)]
    # Level-match the AUDITION copy to the retained working context — the same S1 gain
    # the combine applies (gated RMS, ±12 dB) — so what the reviewer hears pre-combine is
    # what the splice will sound like. Safe against double-apply: combine re-measures the
    # (now leveled) candidate against the same context, so its own gain lands ≈ 0 dB.
    cand_gain_db = 0.0
    if plan.meta.get("span_only") and frow["mp3_name"]:
        wpath = dirs["working"] / frow["mp3_name"]
        if wpath.exists():
            try:
                base = audio_io.mp3_to_samples(wpath)
                tL = float(plan.meta.get("tL") or 0.0)
                tR = (audio_io.duration_seconds(base, audio_io.SR)
                      if plan.meta.get("tR") is None else float(plan.meta["tR"]))
                bL = max(0, min(int(round(tL * audio_io.SR)), len(base)))
                bR = max(bL, min(int(round(tR * audio_io.SR)), len(base)))
                retained = (np.concatenate([base[:bL], base[bR:]])
                            if (bL or bR < len(base)) else base)
                ref_db = audio_io.gated_rms_db(retained, audio_io.SR)
                cnd_db = audio_io.gated_rms_db(trimmed, audio_io.SR)
                if ref_db > -119 and cnd_db > -119:
                    g = float(np.clip(ref_db - cnd_db, -12.0, 12.0))
                    if abs(g) > 0.25:      # skip inaudible corrections
                        trimmed = audio_io.limit_peak(
                            audio_io.apply_gain_db(trimmed, g),
                            audio_splice.TRUE_PEAK_CEILING_DB)
                        cand_gain_db = g
            except Exception:  # noqa: BLE001 — preview-only nicety, never block a regen
                cand_gain_db = 0.0
    plan.meta["cand_gain_db"] = round(cand_gain_db, 2)
    if front_s > 0.0 or cand_gain_db != 0.0 or len(trimmed) < len(cand_samples):
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
    resp = serialize_field(sid, _field_row(sid, fid))
    if cjk_fell_back:      # tell the FE the whole clip changed (not a surgical splice)
        resp["cjk_fallback"] = True
    return resp


def combine(sid: str, fid: int) -> dict:
    srow = _session_row(sid)
    frow = _field_row(sid, fid)
    if not frow["candidate_mp3_path"] or not Path(frow["candidate_mp3_path"]).exists():
        raise HTTPException(409, detail={"error": "no_candidate",
                                         "detail": "regenerate first"})
    meta = json.loads(frow["splice_meta_json"] or "{}")
    dirs = work_dirs(sid)
    # Every combined take ends on the trip's required trailing pause: the 3s beginner
    # pause is for the NARRATION only (SceneDesc); questions/options/titles keep a small
    # 0.4s. A whole TTS candidate lacks the beginner tail and a segment splice can disturb
    # it, so normalize here rather than relying on what the candidate happens to carry.
    tail_target = _target_tail_seconds(srow["trip_id"], frow["field_path"])

    if meta.get("mode") == "whole":
        # whole-block / Q&A: replace working with the candidate take.
        cand = audio_io.mp3_to_samples(frow["candidate_mp3_path"])
        cand = audio_io.set_trailing_silence(cand, audio_io.SR, tail_target)
        whash = _set_working(sid, frow, samples=cand, kind="splice")
        _r2_upload_working(srow["trip_id"], dirs, frow)
        patch = {"working_audio_hash": whash, "splice_confidence": None,
                 "candidate_mp3_path": None, "working_text": frow["current_text"]}
        patch.update(_clear_coverage_and_done(frow))
        patch.update(_zh_working_hans_patch(frow))   # re-baseline ZH OLD to this take
        db.update_fields(fid, **patch)
        db.touch_session(sid)
        warm_whisper_async(sid, fid)   # re-transcribe the new take in the background
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
    patch.update(_zh_working_hans_patch(frow))       # re-baseline ZH OLD to this take
    if result.edit_required:
        patch["flag"] = "edit_required"
        patch["comment"] = _append_note(
            frow["comment"],
            f"Low splice confidence ({result.confidence}); please verify or send to "
            f"manual edit.")
    db.update_fields(fid, **patch)
    db.touch_session(sid)
    warm_whisper_async(sid, fid)   # re-transcribe the new take in the background
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
    # Re-apply the front cut regenerate made (previous_text-leak / lead removal): the
    # pristine copy still carries the leak, and cand_words were shifted to the
    # front-trimmed timeline — rebuilding without it would put the leak back.
    front = float(meta.get("cand_front_trim_s") or 0.0)
    if front > 0.0:
        samples = samples[int(round(front * sr)):]
    n = len(samples)
    max_trim_ms = max(0.0, (n / sr - 0.15) * 1000.0)        # always keep ≥150 ms
    new_trim = min(max(0.0, float(meta.get("cand_trim_ms", 0.0)) + float(delta_ms)),
                   max_trim_ms)
    keep = max(0, n - int(round(new_trim / 1000.0 * sr)))
    out = samples[:keep]
    # Re-apply the audition level-match regenerate made (pristine is un-leveled).
    gain = float(meta.get("cand_gain_db") or 0.0)
    if gain:
        out = audio_io.limit_peak(audio_io.apply_gain_db(out, gain),
                                  audio_splice.TRUE_PEAK_CEILING_DB)
    audio_io.samples_to_mp3(out, Path(cand))
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
    warm_whisper_async(sid, fid)   # re-transcribe the imported take in the background
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


def _trim_noise_cjk(sid: str, srow, frow, cjk, start: int, end: int) -> dict:
    """CJK counterpart of the ``trim_noise`` body — same two cases (WORDS/GAP), same DSP,
    with the highlight located via the MMS aligner's per-char times instead of English
    Whisper. The selection is in the DISPLAYED text (Hans field / kana line) and is mapped
    to the OLD text the audio actually says before reading times."""
    lang, old, new = cjk
    s, e = _cjk_sel_range(frow, lang, new, start, end)
    oa, ob = cjk_splice.map_new_span_to_old(old, new, s, e)
    pos, _mean = _cjk_char_times(srow, frow, lang, old)
    dirs = work_dirs(sid)
    base = audio_io.mp3_to_samples(dirs["working"] / frow["mp3_name"])
    sr = audio_io.SR
    dur = audio_io.duration_seconds(base, sr)

    sel_placed = [p for p in range(oa, ob) if p in pos]   # spoken chars in the selection
    if sel_placed:
        # WORDS: strip non-speech blips inside the selected chars' window only, clamped to
        # the neighbouring placed chars so no adjacent speech is touched.
        tA = max(0.0, min(pos[p][0] for p in sel_placed) - 0.05)
        tB = min(dur, max(pos[p][1] for p in sel_placed) + 0.05)
        prev_p = max((p for p in pos if p < sel_placed[0]), default=None)
        next_p = min((p for p in pos if p > sel_placed[-1]), default=None)
        if prev_p is not None:
            tA = max(tA, pos[prev_p][1] + 0.02)
        if next_p is not None:
            tB = min(tB, pos[next_p][0] - 0.02)
        if tB <= tA:
            return serialize_field(sid, frow)
        new_samples = audio_io.trim_slivers(base, sr, tA, tB, sliver_max=0.35, sil_min=0.03)
        if len(new_samples) >= len(base) - int(0.01 * sr):   # nothing removed
            return serialize_field(sid, frow)
    else:
        # GAP: the selection covers only punctuation/unplaced chars — the artefact lives in
        # the pause. Blank it to clean silence between the neighbouring chars' release and
        # true onset (energy-detected, exactly as the English GAP case).
        prev_p = max((p for p in pos if p < oa), default=None)
        next_p = min((p for p in pos if p >= ob), default=None)
        pt = pos[prev_p] if prev_p is not None else None
        nt = pos[next_p] if next_p is not None else None
        g0 = pt[1] if pt else 0.0
        search_to = nt[1] if nt else dur
        q0 = audio_io.first_silence_after(base, sr, g0, search_to)
        g0 = q0 if q0 is not None else g0
        onset = audio_io.first_voice_onset(base, sr, g0, search_to)
        g1 = onset if onset is not None else (nt[0] if nt else dur)
        s0 = max(0, min(int(round(g0 * sr)), len(base)))
        s1 = max(s0, min(int(round(g1 * sr)), len(base)))
        gap_len = (s1 - s0) / sr
        sil = np.zeros(int(round(max(0.2, gap_len) * sr)), dtype=np.float32)
        new_samples = np.concatenate([base[:s0], sil, base[s1:]]).astype(np.float32)

    return _commit_working_edit(sid, srow, frow, new_samples, "noise_trim")


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
    # CJK (_ZH hanzi / _JP kana): char→time comes from the MMS aligner, not English
    # Whisper. Isolated branch, mirroring `regenerate`; the English body below is untouched.
    cjk = _cjk_spoken(srow, frow)
    if cjk is not None:
        return _trim_noise_cjk(sid, srow, frow, cjk, start, end)
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
    # Editing the working take invalidates any pending regenerate candidate — its tL/tR were
    # computed against the pre-edit audio, so combining it now would splice at stale offsets
    # (mirrors undo/redo/revert). Only reached when the take actually changed (no-ops above).
    patch = {"working_audio_hash": whash, "splice_confidence": None,
             "candidate_mp3_path": None}
    patch.update(_clear_coverage_and_done(frow))
    db.update_fields(fid, **patch)
    db.touch_session(sid)
    return serialize_field(sid, _field_row(sid, fid))


def trim_silence(sid: str, fid: int) -> dict:
    """Normalize the trailing pause on the working take to the clip's requirement:
    beginner-trip (A1-2 / N5 / HSK1-2) NARRATION (SceneDesc) keeps 3s of end silence;
    every other field or level has its excess trailing silence removed (small 0.4s tail).
    Only touches end-silence (never voiced audio); when
    nothing needs changing the working take is left untouched. Archives a version + resets
    the done gate; revertable."""
    srow = _session_row(sid)
    frow = _field_row(sid, fid)
    if not (frow["has_audio"] and frow["mp3_name"]):
        raise HTTPException(400, detail={"error": "no_audio", "detail": "text field"})
    dirs = work_dirs(sid)
    base = audio_io.mp3_to_samples(dirs["working"] / frow["mp3_name"])
    sr = audio_io.SR
    target = _target_tail_seconds(srow["trip_id"], frow["field_path"])
    new = audio_io.set_trailing_silence(base, sr, target)
    if abs(len(new) - len(base)) < int(0.02 * sr):       # <20 ms change → no-op
        return serialize_field(sid, frow)
    # Only END silence moved (set_trailing_silence never touches a voiced sample), so no
    # word time changes at all — model it as an edit at the very end of the OLD clip and
    # the cache survives with just its duration adjusted.
    end = len(base) / sr
    return _commit_working_edit(
        sid, srow, frow, new, "silence_trim",
        silence_edit=(end, end, (len(new) - len(base)) / sr))


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

    # CJK (_ZH hanzi / _JP kana): the caret maps to an audio time via the MMS aligner
    # (JP carets must sit in the kana line — 409 hint otherwise). Identical pause-only
    # insertion; the English mapping below is untouched.
    cjk = _cjk_spoken(srow, frow)
    if cjk is not None:
        t_at = _cjk_caret_seed(srow, frow, cjk, int(pos))
        run = audio_io.silence_run_nearest(base, sr, t_at, 0.4, 0.4)
        if run is None:
            raise HTTPException(409, detail={
                "error": "no_pause",
                "detail": "No pause at the cursor to extend — put the caret right after "
                          "the clause punctuation (。/、). This lengthens a pause, it "
                          "won't split a word."})
        run = audio_splice._skip_stop_closure(base, sr, run)
        mid = (run[0] + run[1]) / 2.0
        cut = max(0, min(int(round(mid * sr)), n))
        gap = np.zeros(int(round(seconds * sr)), dtype=np.float32)
        return _commit_working_edit(
            sid, srow, frow, np.concatenate([base[:cut], gap, base[cut:]]),
            "insert_silence", silence_edit=(cut / sr, cut / sr, seconds))

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
    # A caret after a stop-final word ("…to the right.| As") seeds at Whisper's word END,
    # which sits at the stop's CLOSURE silence — inserting there splits the word
    # ("righ…0.5s…t"). Skip past the burst to the real pause.
    run = audio_splice._skip_stop_closure(base, sr, run)
    mid = (run[0] + run[1]) / 2.0            # drop the gap inside the existing silence
    cut = max(0, min(int(round(mid * sr)), n))
    gap = np.zeros(int(round(seconds * sr)), dtype=np.float32)
    return _commit_working_edit(
        sid, srow, frow, np.concatenate([base[:cut], gap, base[cut:]]),
        "insert_silence", silence_edit=(cut / sr, cut / sr, seconds))


# The longest natural pause remove_silence always leaves in place: an inter-sentence gap
# below ~0.25s starts to sound clipped, and cutting further would risk butting two voiced
# regions together.
_REMOVE_PAUSE_KEEP = 0.25


def remove_silence(sid: str, fid: int, pos: int, seconds: float = 1.0) -> dict:
    """SHORTEN an existing pause by up to ``seconds`` at the TEXT caret ``pos`` — the
    inverse of ``insert_silence``, for ALL languages (English word timing / CJK aligner).
    The removal is taken from the MIDDLE of the genuine-silence run at the caret, so the
    preceding word's release and the next word's onset are untouched, and at least
    ``_REMOVE_PAUSE_KEEP`` of natural pause always remains. Refuses (409) when there is no
    pause at the cursor or none to spare. Archives a version + resets the done gate;
    revertable."""
    srow = _session_row(sid)
    frow = _field_row(sid, fid)
    if not (frow["has_audio"] and frow["mp3_name"]):
        raise HTTPException(400, detail={"error": "no_audio", "detail": "text field"})
    seconds = max(0.05, min(float(seconds), 10.0))
    dirs = work_dirs(sid)
    base = audio_io.mp3_to_samples(dirs["working"] / frow["mp3_name"])
    sr = audio_io.SR
    dur = audio_io.duration_seconds(base, sr)

    cjk = _cjk_spoken(srow, frow)
    if cjk is not None:
        t_at = _cjk_caret_seed(srow, frow, cjk, int(pos))
    else:
        # Caret → end-time of the last spoken word before it — the same mapping
        # insert_silence uses (duplicated so that validated body stays untouched).
        t_at = 0.0
        if int(pos) > 0:
            cleaned_working, _ = _cleaned_orig(srow, frow)
            cur = audio_core.strip_url_lines(frow["current_text"] or "")
            blo, bhi = audio_splice.highlight_span_in_cleaned(
                cur, cleaned_working, 0, int(pos))
            words = _whisper_orig(srow, frow)
            if not words:
                raise HTTPException(400, detail={"error": "no_audio",
                                                 "detail": "no word timing"})
            wmap = audio_splice._whisper_index_map(
                audio_splice.tokens(cleaned_working), words)
            t_at = dur                       # default to the end if nothing maps
            for t in range(bhi - 1, blo - 1, -1):
                if t in wmap:
                    t_at = wmap[t][1]
                    break

    run = audio_io.silence_run_nearest(base, sr, t_at, 0.4, 0.4)
    if run is None:
        raise HTTPException(409, detail={
            "error": "no_pause",
            "detail": "No pause at the cursor to shorten — put the caret right after a "
                      "full stop (or other gap)."})
    # Same stop-closure trap as insert_silence: a caret after "right." seeds at the
    # closure — shortening THAT would eat the word's own articulation. Skip first.
    run = audio_splice._skip_stop_closure(base, sr, run)
    # Re-measure the run's TRUE extent from its midpoint: the ±0.4s discovery window
    # above clips a long pause (e.g. one just extended by insert_silence to 1.4s reads
    # as ~1.1s), which made remove take LESS than insert added. 12s covers any pause
    # the insert tool can create (its cap is 10s).
    mid = (run[0] + run[1]) / 2.0
    run = audio_io.silence_run_nearest(base, sr, mid, 12.0, 12.0) or run
    run_len = run[1] - run[0]
    remove = min(seconds, run_len - _REMOVE_PAUSE_KEEP)
    if remove < 0.05:
        raise HTTPException(409, detail={
            "error": "no_excess_pause",
            "detail": f"That pause is only {run_len:.2f}s — already at a natural length, "
                      "nothing to remove."})
    c0 = run[0] + (run_len - remove) / 2.0   # centered cut: both edges stay in silence
    a = max(0, min(int(round(c0 * sr)), len(base)))
    b = max(a, min(int(round((c0 + remove) * sr)), len(base)))
    return _commit_working_edit(
        sid, srow, frow, np.concatenate([base[:a], base[b:]]), "remove_silence",
        silence_edit=(a / sr, b / sr, 0.0))


# --------------------------------------------------------------------------- #
# Waveform editor — TIME-addressed edits ("Edit waveform")
#
# Every tool above addresses the audio through the TEXT: a caret/selection in the
# narration box is mapped to an audio time via Whisper (English) or the MMS aligner
# (CJK). That is what makes them safe — and also what makes them coarse (you can only
# act at a word boundary the aligner agrees with) and slow (a mapping costs a
# transcription). These tools address the audio DIRECTLY: the reviewer sees the
# waveform and says exactly where. No text, no aligner, no Whisper — so they are also
# the fast path.
#
# The trade is that they are genuinely destructive: nothing here checks that a cut sits
# in silence, because the whole point is to let a human put it where the machine won't.
# The safety net is the one already in place for every audio op — each edit archives a
# version (undo/redo), drops any pending candidate, and clears the coverage + `done`
# gate, so the clip must be listened to end-to-end again before it can be signed off.
# --------------------------------------------------------------------------- #
_WAVEFORM_BUCKETS = 1600      # ~2 px per bucket on a wide screen; keeps the JSON small


def waveform(sid: str, fid: int, track: str = "working") -> dict:
    """Min/max envelope of a field's audio for the waveform editor.

    Returned as two int8-ish arrays (``peaks`` = interleaved min,max per bucket, each
    -128..127) rather than raw floats: a 60 s clip is 2.6 M samples, and the UI only
    ever draws ~1600 columns of it."""
    _session_row(sid)
    frow = _field_row(sid, fid)
    if not (frow["has_audio"] and frow["mp3_name"]):
        raise HTTPException(400, detail={"error": "no_audio", "detail": "text field"})
    path = _orig_path(sid, frow) if track == "original" else (
        work_dirs(sid)["working"] / frow["mp3_name"])
    if not path or not Path(path).exists():
        raise HTTPException(404, detail={"error": "no_audio", "detail": track})
    samples = audio_io.mp3_to_samples(path)
    sr = audio_io.SR
    n = len(samples)
    if n == 0:
        return {"duration": 0.0, "buckets": 0, "peaks": []}
    buckets = min(_WAVEFORM_BUCKETS, max(1, n // 16))
    # Pad to a whole number of buckets so the reshape is exact (cheaper + clearer than
    # a Python loop over 1600 slices).
    per = int(np.ceil(n / buckets))
    pad = per * buckets - n
    block = np.concatenate([samples, np.zeros(pad, dtype=np.float32)]).reshape(buckets, per)
    lo = np.clip(block.min(axis=1) * 127.0, -127, 127).astype(np.int8)
    hi = np.clip(block.max(axis=1) * 127.0, -127, 127).astype(np.int8)
    peaks: list[int] = np.stack([lo, hi], axis=1).reshape(-1).astype(int).tolist()
    return {"duration": round(n / sr, 3), "buckets": buckets, "peaks": peaks,
            "hash": (frow["working_audio_hash"] or "")[:8] if track == "working" else ""}


def _wave_base(sid: str, fid: int):
    """(srow, frow, samples, sr, duration) for a waveform edit, or 400 if the field has
    no audio."""
    srow = _session_row(sid)
    frow = _field_row(sid, fid)
    if not (frow["has_audio"] and frow["mp3_name"]):
        raise HTTPException(400, detail={"error": "no_audio", "detail": "text field"})
    base = audio_io.mp3_to_samples(work_dirs(sid)["working"] / frow["mp3_name"])
    sr = audio_io.SR
    return srow, frow, base, sr, audio_io.duration_seconds(base, sr)


def _span(dur: float, start: float, end: float, what: str) -> tuple[float, float]:
    """Validate + clamp a [start, end) selection against the clip, or 422."""
    a, b = max(0.0, float(start)), min(float(dur), float(end))
    if b - a < 0.01:
        raise HTTPException(422, detail={
            "error": "bad_range",
            "detail": f"Select at least 10 ms of audio to {what}."})
    return a, b


# A seam between two pieces of audio that were never adjacent will click unless it is
# ramped. 8 ms is short enough to be inaudible as a fade and long enough to kill the
# discontinuity — the same order as the splice engine's own seam fades.
_WAVE_SEAM_MS = 8.0


def wave_insert_silence(sid: str, fid: int, at: float, seconds: float) -> dict:
    """Insert ``seconds`` of silence at EXACTLY time ``at`` — no snapping to a pause.

    Unlike the caret-driven insert_silence (which refuses anywhere but a genuine
    silence run, because it can't see what the reviewer meant), this trusts the
    waveform: the reviewer is looking at the gap they clicked in."""
    srow, frow, base, sr, dur = _wave_base(sid, fid)
    seconds = max(0.05, min(float(seconds), 10.0))
    t = max(0.0, min(float(at), dur))
    cut = int(round(t * sr))
    gap = np.zeros(int(round(seconds * sr)), dtype=np.float32)
    return _commit_working_edit(
        sid, srow, frow, np.concatenate([base[:cut], gap, base[cut:]]),
        "wave_insert_silence", silence_edit=(cut / sr, cut / sr, seconds))


def wave_delete(sid: str, fid: int, start: float, end: float) -> dict:
    """Delete the selected span outright and butt the two sides together (an 8 ms
    equal-power seam so the join can't click). This is the 'cut' half of cut/paste and
    the one op here that can remove SPEECH — the word cache is deliberately NOT re-timed
    (it goes stale on the content hash and is honestly rebuilt), because after this the
    text and the audio genuinely no longer agree."""
    srow, frow, base, sr, dur = _wave_base(sid, fid)
    a, b = _span(dur, start, end, "delete")
    sa, sb = int(round(a * sr)), int(round(b * sr))
    kept = audio_io.crossfade_join([base[:sa], base[sb:]], sr, _WAVE_SEAM_MS)
    if len(kept) < int(0.05 * sr):
        raise HTTPException(422, detail={
            "error": "would_empty",
            "detail": "That would delete nearly the whole clip — regenerate it instead."})
    return _commit_working_edit(sid, srow, frow, kept, "wave_delete")


def wave_silence(sid: str, fid: int, start: float, end: float) -> dict:
    """Replace the selected span with silence, KEEPING the clip's length (so nothing
    after it shifts). The precise version of 'trim highlighted noise': for a cough or a
    click the reviewer can see, where the text-mapped tool has no word to hang off."""
    srow, frow, base, sr, dur = _wave_base(sid, fid)
    a, b = _span(dur, start, end, "silence")
    sa, sb = int(round(a * sr)), int(round(b * sr))
    out = base.copy()
    out[sa:sb] = 0.0
    # Ramp into and out of the blanked span so the surrounding audio doesn't click.
    f = min(int(sr * _WAVE_SEAM_MS / 1000), (sb - sa) // 2)
    if f > 0:
        ramp = np.linspace(1.0, 0.0, f, dtype=np.float32)
        out[sa:sa + f] = base[sa:sa + f] * ramp
        out[sb - f:sb] = base[sb - f:sb] * ramp[::-1]
    # The span may have contained speech, so the cached word timings are now a lie about
    # WHAT is said (though not about when) — let them rebuild.
    return _commit_working_edit(sid, srow, frow, out, "wave_silence")


def wave_move(sid: str, fid: int, start: float, end: float, to: float) -> dict:
    """Cut the selected span out and paste it back in at time ``to`` (measured on the
    CURRENT clip, i.e. before the cut is applied — that is what the reviewer is looking
    at). ``to`` inside the selection is a no-op and is refused rather than silently
    doing nothing."""
    srow, frow, base, sr, dur = _wave_base(sid, fid)
    a, b = _span(dur, start, end, "move")
    t = max(0.0, min(float(to), dur))
    if a <= t <= b:
        raise HTTPException(422, detail={
            "error": "paste_inside_selection",
            "detail": "Drop the selection somewhere outside itself."})
    sa, sb, st = (int(round(x * sr)) for x in (a, b, t))
    piece = base[sa:sb]
    rest_head, rest_tail = base[:sa], base[sb:]
    if st <= sa:                       # paste BEFORE the selection's old home
        pieces = [base[:st], piece, base[st:sa], rest_tail]
    else:                              # paste AFTER it (st >= sb)
        pieces = [rest_head, base[sb:st], piece, base[st:]]
    return _commit_working_edit(
        sid, srow, frow, audio_io.crossfade_join(pieces, sr, _WAVE_SEAM_MS), "wave_move")


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
        dur = _working_duration(sid, frow)
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
    """Put a field back exactly as it was seeded: text, the editable English sibling, the
    _ZH 4-script block, and the working audio (the pristine v0 master).

    ALL the reviewer-editable text surfaces must be reset, not just `current_text` — a
    field counts as edited (and is written to staging) if ANY of them differs from its
    original (`_field_has_edit`). Before 2026-07-13 this reset `current_text` only, so
    reverting a MANDARIN field was a no-op where it mattered: the reviewer edits
    `localization.cur`, never `current_text`, so their words stayed in the 4-script box,
    Gate-1 still blocked on the now-stale zhuyin, and submit still wrote the edit to
    staging. English/Japanese fields DO live in `current_text` and were reverted correctly
    — except for the editable English sibling (`source_text`), which is written back to
    staging too and was likewise never reset.
    """
    _session_row(sid)
    frow = _field_row(sid, fid)
    patch = {"current_text": frow["original_text"], "flag": "none",
             "candidate_mp3_path": None, "splice_confidence": None,
             "played_coverage_json": "{}", "working_text": frow["original_text"],
             "version_cursor": 0,   # working now == the pristine master (v0)
             # The editable English translation (the *En writeback). '' when the field has
             # no English sibling, so this is a no-op there. Both columns are NOT NULL.
             "source_text": frow["original_source"]}

    # _ZH: the 4-script block IS the reviewer's edit surface (and cur.Hans is what the
    # voice speaks). Restore cur -> orig, and re-baseline `working_hans` to the Hans the
    # v0 take actually says — it's the OLD text the next surgical splice diffs against, so
    # leaving it on the reverted-away wording would splice at cut times for audio that no
    # longer exists.
    loc_raw = _srow_get(frow, "localization_json")
    if loc_raw:
        try:
            loc = json.loads(loc_raw)
        except (ValueError, TypeError):
            loc = None
        if isinstance(loc, dict) and isinstance(loc.get("orig"), dict):
            orig_loc = loc["orig"]
            loc["cur"] = dict(orig_loc)
            hans = (orig_loc.get("Hans") or "").strip()
            if hans:
                loc["working_hans"] = hans
            patch["localization_json"] = json.dumps(loc, ensure_ascii=False)

    reverted_audio = False
    if frow["has_audio"] and frow["mp3_name"]:
        dirs = work_dirs(sid)
        name = frow["mp3_name"]
        orig = dirs["orig"] / name
        if orig.exists():
            audio_io.mp3_to_mp3_copy(orig, dirs["working"] / name)
            patch["working_audio_hash"] = _file_hash(dirs["working"] / name)
            reverted_audio = True
    db.update_fields(fid, **patch)
    # Re-mirror the restored take, like every other working-take mutator (combine, import,
    # undo/redo, trim, pause). Not cosmetic: `resolve_audio_dir` can SEED a new session from
    # the review-audio R2 cache on a host with no local masters, so a mirror left holding the
    # reverted-away take would hand that session the edited audio as its "pristine" master.
    # Best-effort — never raises, never fails the revert.
    if reverted_audio:
        _r2_upload_working(_trip_id_cached(sid), work_dirs(sid), _field_row(sid, fid))
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
    warm_whisper_async(sid, fid)   # re-transcribe the restored take in the background
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


def _leak_scan_targets(f) -> list[str]:
    """The edited text strings to leak-scan for a field. For a Latin field that's the
    edited ``current_text``; for a ``_ZH`` field the reviewer edits the 4-script block
    (``localization_json`` cur vs orig), NOT current_text, so scan every CHANGED script
    (Hans/Hant/zhuyin/en) — the leak patterns are ASCII/symbol artefacts (URLs, media
    filenames, ⚠, [source]) that can slip into any script. Empty when the field is
    unchanged. (The ``working_hans`` re-baseline key is a sibling of cur/orig, so iterating
    ``cur`` never picks it up.)"""
    loc_raw = _srow_get(f, "localization_json")
    if loc_raw:
        loc = json.loads(loc_raw)
        cur, orig = loc.get("cur") or {}, loc.get("orig") or {}
        return [v for k, v in cur.items()
                if isinstance(v, str) and v and v != orig.get(k)]
    if (f["current_text"] or "") != (f["original_text"] or ""):
        return [f["current_text"]]
    return []


def _field_has_edit(f) -> bool:
    """True if commit / zh_writeback would write this field back to staging — a changed
    localization block (_ZH), current_text, or the editable English sibling (source_text)."""
    loc_raw = _srow_get(f, "localization_json")
    if loc_raw and (json.loads(loc_raw).get("cur") or {}) != (json.loads(loc_raw).get("orig") or {}):
        return True
    return ((f["current_text"] or "") != (f["original_text"] or "")
            or (f["source_text"] or "") != (f["original_source"] or ""))


def validate(sid: str, mode: str = "submit") -> tuple[list[dict], list[dict]]:
    """PURE pre-submit validation — NO writes. Returns (hard, soft) issue lists.
    Reads the FRESH live staging trip for the final-360 check (a read, not a write).
    Shared by reviewer ``submit`` (gate) and admin ``approve`` (re-check vs live).

    ``mode``: at "submit" the Gate-1 auto-check blockers are DEMOTED to warnings —
    a reviewer fixing grammar in Hans shouldn't be locked out because the zhuyin
    now needs a matching edit (dave, 2026-07-08). At "approve" they stay hard:
    staging must never receive mismatched scripts."""
    srow = _session_row(sid)
    trip_id = srow["trip_id"]
    frows = db.query(
        "SELECT * FROM field_edits WHERE session_id=? ORDER BY id", (sid,))

    hard: list[dict] = []
    soft: list[dict] = []

    # Leak-scan every edited field. Keyed off the actual edit location (current_text for
    # Latin, the changed localization scripts for _ZH) so Mandarin edits — which never touch
    # current_text — are scanned too.
    for f in frows:
        targets = _leak_scan_targets(f)
        if not targets:
            continue
        fp = f["field_path"]
        out_fp = (f"questionOption[{f['option_index']}]"
                  if fp == "questionOption" else fp)
        for text in targets:
            hard += _validate_text(out_fp, f["scene_index"], text)
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

    # Staging-drift guard: an edit to a scene that no longer exists in the LIVE trip would be
    # SILENTLY dropped by commit / zh_writeback (their `si < len(qt_live)` / loc-index checks
    # just skip it, and approve still reports success). Block instead so the drift is visible.
    # _ZH needs BOTH the live Trip scene AND its TripLocalizations scene to write back.
    live_scene_ids = set(range(len(qt_live)))
    if _is_zh_session(sid):
        live_loc = _fetch_localization(trip_id)
        if live_loc is not None:
            live_scene_ids &= {sc.get("index") for sc in (live_loc.get("scenes") or [])
                               if sc.get("index") is not None}
    missing = sorted({f["scene_index"] for f in frows
                      if f["scene_index"] is not None and _field_has_edit(f)
                      and f["scene_index"] not in live_scene_ids})
    if missing:
        hard.append({"scene_index": None, "field_path": "*",
                     "issue": f"scene(s) {missing} in this review no longer exist in the live "
                              "staging trip — it changed since this review was seeded; "
                              "re-seed or resolve before approving",
                     "severity": "block"})

    # every field must be listened-to (the `done` flag only unlocks after full
    # playback) and explicitly marked done before the trip can be submitted
    not_done = sum(1 for f in frows if (f["flag"] or "none") != "done")
    if not_done:
        hard.append({"scene_index": None, "field_path": "*",
                     "issue": f"{not_done} section(s) not yet marked done — listen to "
                              "the audio and mark every section done before submitting",
                     "severity": "block"})

    # _ZH: a voice version must be picked before submit — until the pick promotes a take,
    # the A/B fields carry no working audio (current_mp3_path NULL) and nothing is editable.
    if _is_zh_session(sid) and any(
            f["has_audio"] and not f["current_mp3_path"] for f in frows):
        hard.append({"scene_index": None, "field_path": "*",
                     "issue": "pick a voice version (V2 or V3) before submitting — the "
                              "chosen take becomes the trip's working audio",
                     "severity": "block"})

    # Gate 1 of the auto-review pipeline: deterministic script-consistency / format checks
    # (docs/auto-review-proposal.md). Hard at APPROVE (staging protection); demoted to
    # loud warnings at SUBMIT so a reviewer is never locked out mid-handover.
    ac_hard, ac_soft = auto_checks.run_checks(frows, _is_zh_session(sid), trip_id)
    if mode == "approve":
        hard += ac_hard
    else:
        soft += [{**i, "severity": "warn",
                  "issue": "[will block approval] " + i["issue"]} for i in ac_hard]
    soft += ac_soft

    return hard, soft


# --------------------------------------------------------------------------- #
# Mandarin (_ZH) staging writeback — the reviewed 4-script text -> TripLocalizations
# (+ the derived Trip doc). THE RISKIEST CODE: guarded by dry_run, re-fetches live,
# writes ONLY changed fields, regenerates pinyin from the confirmed zhuyin.
# --------------------------------------------------------------------------- #
def zh_writeback(sid: str, *, dry_run: bool = True) -> dict:
    """Compute (and, only when ``dry_run`` is False, APPLY) the staging writes for an
    approved _ZH review:

      * TripLocalizations/{id}: for each CHANGED field, set
        scenes[i].{field}.target.{Hans,Hant,zhuyin} + home.en, and REGENERATE
        target.pinyin from the confirmed zhuyin (never authored). description gets
        target.{Hans,Hant} + home.en (no phonetics). status -> 'reviewed'.
      * Trip doc quickTrips[i]: SceneDesc/questionKey/questionOption = "Hans⏎pinyin"
        (line 1 hanzi drives the audio), titleKey = "Hans⏎en", and the *En siblings.

    Only CHANGED fields (localization cur != orig) are written; the live docs are
    re-fetched first so concurrent edits elsewhere are never clobbered. Returns the plan
    (always) — when dry_run it APPLIES NOTHING and is safe to call anywhere."""
    srow = _session_row(sid)
    trip_id = srow["trip_id"]

    live_loc = _fetch_localization(trip_id)
    if live_loc is None:
        raise HTTPException(404, detail={
            "error": "no_localization",
            "detail": f"TripLocalizations/{trip_id} not found — cannot write back"})
    try:
        trip_live = get_trip(trip_id)
    except SystemExit as e:
        raise HTTPException(404, detail=str(e))

    loc_scenes = live_loc.get("scenes") or []
    loc_by_index = {sc.get("index"): sc
                    for sc in loc_scenes if sc.get("index") is not None}
    qt_live = list(trip_live.get("quickTrips") or [])

    frows = db.query(
        "SELECT * FROM field_edits WHERE session_id=? ORDER BY id", (sid,))

    changed_out: list[dict] = []
    warnings: list[str] = []
    written: list[str] = []
    loc_scene_changed = False
    loc_desc_changed = False

    for f in frows:
        loc_raw = _srow_get(f, "localization_json")
        if not loc_raw:
            continue
        loc = json.loads(loc_raw)
        cur = loc.get("cur") or {}
        orig = loc.get("orig") or {}
        if cur == orig:
            continue   # ONLY changed fields

        si, fp, k = f["scene_index"], f["field_path"], f["option_index"]
        hans, hant = cur.get("Hans") or "", cur.get("Hant") or ""
        zh, en = cur.get("zhuyin") or "", cur.get("en") or ""
        entry: dict = {"scene_index": si, "field_path": fp, "option_index": k,
                       "cur": dict(cur)}

        # ---- trip description (no phonetics; localization.description only) ----
        if fp == "tripgroup_description":
            desc = live_loc.get("description") or {}
            desc.setdefault("target", {})
            desc.setdefault("home", {})
            desc["target"]["Hans"] = hans
            desc["target"]["Hant"] = hant
            desc["home"]["en"] = en
            live_loc["description"] = desc
            loc_desc_changed = True
            entry.update({"loc_path": "description",
                          "target": {"Hans": hans, "Hant": hant}, "home": {"en": en}})
            written.append("TripLocalizations.description")
            changed_out.append(entry)
            continue

        # ---- pinyin regenerated from the confirmed zhuyin (display line) ----
        pinyin, pw = _zh_regen_pinyin(zh, hans)
        entry["pinyin"] = pinyin
        entry["pinyin_warnings"] = pw
        if pw:
            warnings.extend([f"scene {si} {fp}"
                             + (f"[{k}]" if k is not None else "") + f": {w}" for w in pw])

        # ---- TripLocalizations mutation ----
        sc = loc_by_index.get(si)
        if sc is not None:
            node = {"target": {"Hans": hans, "Hant": hant, "zhuyin": zh, "pinyin": pinyin},
                    "home": {"en": en}}
            loc_path = None
            if fp == "questionOption":
                opts = list(sc.get("questionOptionKeys") or [])
                if k is not None and k < len(opts):
                    opts[k] = node
                    sc["questionOptionKeys"] = opts
                    loc_path = f"scenes[{si}].questionOptionKeys[{k}]"
            else:
                sc[fp] = node
                loc_path = f"scenes[{si}].{fp}"
            if loc_path:
                loc_scene_changed = True
                written.append("TripLocalizations." + loc_path)
                entry["loc_path"] = loc_path
                entry["target"] = node["target"]
                entry["home"] = node["home"]

        # ---- Trip doc quickTrips mutation ----
        if si is not None and si < len(qt_live):
            tsc = qt_live[si]
            if fp == "SceneDesc":
                tsc["SceneDesc"] = _zh_join2(hans, pinyin)
                tsc["SceneDescEn"] = en
                entry["trip_path"] = f"quickTrips[{si}].SceneDesc"
                entry["trip_value"] = tsc["SceneDesc"]
                entry["trip_value_en"] = en
            elif fp == "titleKey":
                tsc["titleKey"] = _zh_join2(hans, en)   # line 2 = English on titleKey
                tsc["titleKeyEn"] = ""
                entry["trip_path"] = f"quickTrips[{si}].titleKey"
                entry["trip_value"] = tsc["titleKey"]
            elif fp == "questionKey":
                tsc["questionKey"] = _zh_join2(hans, pinyin)
                tsc["questionKeyEn"] = en
                entry["trip_path"] = f"quickTrips[{si}].questionKey"
                entry["trip_value"] = tsc["questionKey"]
                entry["trip_value_en"] = en
            elif fp == "questionOption":
                opts = list(tsc.get("questionOptionKeys") or [])
                opts_en = list(tsc.get("questionOptionKeysEn") or [])
                while len(opts_en) <= (k or 0):
                    opts_en.append("")
                if k is not None and k < len(opts):
                    opts[k] = _zh_join2(hans, pinyin)
                    opts_en[k] = en
                    tsc["questionOptionKeys"] = opts
                    tsc["questionOptionKeysEn"] = opts_en
                    entry["trip_path"] = f"quickTrips[{si}].questionOptionKeys[{k}]"
                    entry["trip_value"] = opts[k]
                    entry["trip_value_en"] = en
            if entry.get("trip_path"):
                written.append("Trip." + entry["trip_path"])

        changed_out.append(entry)

    plan = {
        "trip_id": trip_id,
        "dry_run": dry_run,
        "localization_status": {"from": live_loc.get("status"), "to": "reviewed"},
        "changed": changed_out,
        "warnings": warnings,
        "written": written,
    }
    if dry_run:
        return plan

    # ---- HARD GATE: never APPLY with an empty regenerated pinyin (2026-07-08) ----
    # KaohsiungLotusPond_HSK3_ZH was approved on a host missing jieba: _zh_regen_pinyin
    # degraded to '' and the writeback stripped the pinyin display line from every edited
    # Trip-doc field + wrote empty target.pinyin. Warning-and-proceeding is fine for the
    # to_pinyin(Hans) FALLBACK (non-empty), but an EMPTY pinyin on a phonetics-bearing
    # field means the environment is broken — block the approve instead of corrupting
    # staging. (dry_run still returns the full plan + warnings for the UI.)
    _needs_pinyin = {"SceneDesc", "questionKey", "questionOption"}
    _broken = [e for e in changed_out
               if e.get("field_path") in _needs_pinyin
               and e.get("pinyin_warnings") and not e.get("pinyin")]
    if _broken:
        raise HTTPException(409, detail={
            "error": "pinyin_regen_failed",
            "detail": "pinyin could not be regenerated (hsk_lib/jieba unavailable?) — "
                      "approve blocked so the staging pinyin lines are not stripped",
            "fields": [f"scene {e['scene_index']} {e['field_path']}" for e in _broken],
            "warnings": warnings})

    # ---- APPLY (admin approve, _ZH branch) — SAFETY: only reached with dry_run=False ----
    loc_update: dict = {"status": "reviewed"}
    if loc_scene_changed:
        loc_update["scenes"] = loc_scenes
    if loc_desc_changed:
        loc_update["description"] = live_loc["description"]
    fb_db().collection("TripLocalizations").document(trip_id).update(loc_update)
    # Trip doc: reuse the shared one-.update() helper; rewrite quickTrips only when a
    # scene field actually changed (a description-only edit doesn't touch the Trip doc).
    trip_changed = any(e.get("trip_path") for e in changed_out)
    if trip_changed:
        update_trip_text(trip_id, qt_live, {})
    return plan


def commit(sid: str, user) -> dict:
    """Perform the STAGING WRITES + master promotion for an approved session. Assumes
    ``validate`` has already passed. Re-fetches the live trip so it writes onto the
    freshest quickTrips. Returns {"written": [...], "promoted_mp3": [...]}.

    INVARIANT (§5): the working->master promotion + versions/ archive happen ONLY here
    (admin approve), NEVER in reviewer submit."""
    srow = _session_row(sid)
    trip_id = srow["trip_id"]

    # _ZH: the reviewed text writes back to TripLocalizations (+ the Trip doc); there are
    # no in-app master mp3s to promote (the A/B version pick drives audio finalisation in
    # the HSK pipeline, not here).
    if _is_zh_session(sid):
        wb = zh_writeback(sid, dry_run=False)
        return {"written": wb["written"], "promoted_mp3": [],
                "zh_warnings": wb.get("warnings") or []}
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
        # Re-claim the pipeline's pad-idempotence tag on beginner narrations: our
        # numpy→WAV→mp3 encode strips ALL metadata, so a corrected A12/N5/HSK1-2
        # SceneDesc master would otherwise look unpadded to Stage 9's pad_dirs and be
        # padded a SECOND time (one scene with ~6 s dead air). Tag only when the tail
        # is measurably present (see _PAD_TAG_MIN_TAIL_S); the tagged copy is what
        # gets promoted AND mirrored to R2 — R2 is the bytes the pipeline syncs back,
        # so tagging only the local master copy would not survive the round-trip.
        # Best-effort: on any failure promote the untagged working take (a double pad
        # is cosmetic; a failed approve is not).
        src = working
        tagged_tmp = None
        try:
            if (_target_tail_seconds(trip_id, f["field_path"]) == _BEGINNER_TAIL_SECONDS
                    and audio_io.trailing_silence_seconds(
                        audio_io.mp3_to_samples(working)) >= _PAD_TAG_MIN_TAIL_S):
                tagged_tmp = dirs["candidate"] / f"promote_tag_{name}"
                audio_io.tag_pad_marker(working, tagged_tmp)
                src = tagged_tmp
        except Exception as e:  # noqa: BLE001
            print(f"[commit] pad-marker tag failed for {name}: {e} — promoting untagged")
            src, tagged_tmp = working, None
        if master.exists():
            ver_dir.mkdir(parents=True, exist_ok=True)
            stem = name[:-4]
            n = _next_version_suffix(ver_dir, stem)
            audio_io.mp3_to_mp3_copy(master, ver_dir / f"{stem}v{n}.mp3")
        audio_io.mp3_to_mp3_copy(src, master)
        # Mirror the promoted master to R2 SYNCHRONOUSLY, here, at the moment the
        # correction becomes the deliverable.
        #
        # This is not belt-and-braces on the combine-time mirror — on a host with no
        # local master trees (the live Ubuntu laptop) `resolve_audio_dir` hands back an
        # ephemeral seed cache, so the `master` we just wrote above lives in a CACHE
        # DIRECTORY. The R2 key is the corrected take's only durable home, and it must
        # not be sitting in a background queue that a restart could drop. A master can be
        # regenerated by the pipeline; a confirmed take is hours of human work.
        # Best-effort (never fails an approve) but loud, and it also self-heals a
        # combine-time mirror that failed earlier.
        if not review_audio.upload_master(trip_id, src, name):
            print(f"[commit] !! R2 mirror FAILED for the promoted master {trip_id}/{name} "
                  f"— the corrected take is on disk at {src} but NOT on R2. "
                  "Re-run the approve or re-upload before this host is rebuilt.")
        if tagged_tmp is not None:
            tagged_tmp.unlink(missing_ok=True)
        promoted.append(name)

    return {"written": written, "promoted_mp3": promoted}


def submit(sid: str, user) -> dict:
    """Reviewer/admin (own language): VALIDATE ONLY. On success flip to `submitted`
    (locked read-only, awaiting admin). NO staging writes, NO master promotion.

    From `ai_review` this is the RE-submit that hands the trip back to the admin — and it
    is blocked until every Gate-2 finding has been answered (dave, 2026-07-13: the whole
    point of bouncing it back is that the reviewer responds to each one)."""
    srow = _session_row(sid)
    if srow["status"] not in _EDITABLE_STATUSES:
        raise HTTPException(403, detail={
            "error": "locked",
            "detail": f"session is '{srow['status']}' — cannot submit"})
    open_n = _open_findings_count(sid)
    if open_n:
        raise HTTPException(409, detail={
            "error": "findings_open",
            "open": open_n,
            "detail": f"{open_n} AI-review item(s) still need a response — resolve, reject "
                      "(with a reason), or hand each to the admin before re-submitting"})
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
        hard, soft = validate(sid, mode="approve")
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
        tid = trip_id_for_session(sid)
        db.execute(
            "INSERT INTO approvals(session_id,trip_id,approved_by,approved_at,written_json)"
            " VALUES(?,?,?,?,?)",
            (sid, tid, getattr(user, "username", None), now,
             json.dumps(result["written"])))
        # Approving a trip also COMPLETES it (leaves the active queue). Upsert so a
        # re-approve (e.g. after un-complete + re-review) refreshes the row.
        db.execute(
            "INSERT INTO completed_trips"
            "(trip_id,completed_by,completed_at,method,session_id,note) "
            "VALUES(?,?,?,'approved',?,'') "
            "ON CONFLICT(trip_id) DO UPDATE SET "
            "completed_by=excluded.completed_by, completed_at=excluded.completed_at, "
            "method='approved', session_id=excluded.session_id",
            (tid, getattr(user, "username", None), now, sid))
        export_completed_trips()   # Stage-9 handshake file (best-effort)
        trello.notify(tid, move=True, comment=(
            f"Approved in review-app by {getattr(user, 'username', 'admin')} — "
            f"corrected masters promoted + text written to staging. Moved to lane 9."))
        return {"ok": True, "validation": soft, "written": result["written"],
                "promoted_mp3": result["promoted_mp3"], "awaiting_stage9": True,
                "zh_warnings": result.get("zh_warnings") or []}
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


# --------------------------------------------------------------------------- #
# Presence (who's live on which session) + recall (reviewer takes a submission back)
# --------------------------------------------------------------------------- #
PRESENCE_LIVE_SECONDS = 120       # heartbeat within this window = "live on the session"
_PRESENCE_PRUNE_SECONDS = 3600    # rows older than this are deleted opportunistically


def heartbeat(sid: str, user, context: str = "") -> dict:
    """Upsert the caller's presence on a session (FE pings every ~30s with what they're
    looking at). Allowed in ANY session state — an admin heartbeats on a submitted trip
    while reviewing it, which is exactly what blocks a silent recall."""
    _session_row(sid)   # 404 if unknown
    now = time.time()
    db.execute(
        "INSERT INTO presence(username,session_id,role,context,updated_at) "
        "VALUES(?,?,?,?,?) "
        "ON CONFLICT(username,session_id) DO UPDATE SET "
        "role=excluded.role, context=excluded.context, updated_at=excluded.updated_at",
        (user.username, sid, user.role, (context or "")[:200], now))
    db.execute("DELETE FROM presence WHERE updated_at<?",
               (now - _PRESENCE_PRUNE_SECONDS,))
    return {"ok": True}


def presence_list(user) -> list[dict]:
    """Everyone live right now (heartbeat within PRESENCE_LIVE_SECONDS), joined to their
    session's trip. Reviewers are filtered to their languages (admins see all) — same
    visibility rule as the trip list itself."""
    from . import auth   # lazy (auth imports sessions) — no module-load cycle
    cutoff = time.time() - PRESENCE_LIVE_SECONDS
    rows = db.query(
        "SELECT p.username, p.role, p.context, p.updated_at, p.session_id, "
        "s.trip_id, s.status FROM presence p JOIN sessions s ON s.id=p.session_id "
        "WHERE p.updated_at>=? ORDER BY p.updated_at DESC", (cutoff,))
    out: list[dict] = []
    for r in rows:
        if not auth.language_allowed(user, r["trip_id"]):
            continue
        out.append({
            "username": r["username"], "role": r["role"], "context": r["context"],
            "updated_at": r["updated_at"], "sid": r["session_id"],
            "trip_id": r["trip_id"], "session_status": r["status"],
        })
    return out


def _admin_live_on(sid: str, exclude_username: str | None = None):
    """The most recent live ADMIN presence row on a session (excluding the caller), or
    None. This is the recall "admin is mid-review" check."""
    cutoff = time.time() - PRESENCE_LIVE_SECONDS
    rows = db.query(
        "SELECT username, context, updated_at FROM presence "
        "WHERE session_id=? AND role='admin' AND updated_at>=? "
        "ORDER BY updated_at DESC", (sid, cutoff))
    for r in rows:
        if r["username"] != exclude_username:
            return r
    return None


def _recall_request_dict(row) -> dict:
    return {
        "id": row["id"], "sid": row["session_id"], "trip_id": row["trip_id"],
        "requested_by": row["requested_by"], "reason": row["reason"],
        "status": row["status"], "created_at": row["created_at"],
        "resolved_by": row["resolved_by"], "resolved_at": row["resolved_at"],
        "resolution_note": row["resolution_note"] or "",
    }


def recall_state(sid: str, user) -> dict:
    """What the Recall button should offer right now: whether this user may recall,
    whether it would auto-grant, what blocks it otherwise, and the latest request (so
    the FE can show 'waiting for admin' / 'declined: <note>' banners)."""
    srow = _session_row(sid)
    status = srow["status"]
    submitter = _srow_get(srow, "submitted_by")
    is_submitter = user.is_admin or (submitter and user.username == submitter)
    can_recall = bool(status in ("submitted", "approving", "approved") and is_submitter)
    blocker = None
    if status in ("approving", "approved"):
        blocker = "approved" if status == "approved" else "admin_reviewing"
    elif status == "submitted" and not user.is_admin:
        live = _admin_live_on(sid, exclude_username=user.username)
        if live:
            blocker = "admin_reviewing"
    req = db.query_one(
        "SELECT * FROM recall_requests WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
        (sid,))
    return {"status": status, "can_recall": can_recall,
            "auto": can_recall and blocker is None, "blocker": blocker,
            "request": _recall_request_dict(req) if req else None}


def recall(sid: str, user, reason: str = "") -> dict:
    """Reviewer takes a SUBMITTED trip back ("Recall submission").
    Auto-grant (submitter or admin; no live admin on the session): CAS back to
    in_review — the approve CAS makes the race safe (one side 409s cleanly).
    Otherwise (admin mid-review / approving / already approved): a reason is required
    and an open recall_requests row is created for the admin queue."""
    srow = _session_row(sid)
    status = srow["status"]
    if status in _EDITABLE_STATUSES:
        raise HTTPException(409, detail={
            "error": "bad_state",
            "detail": f"session is '{status}' — already editable, nothing to recall"})
    if status not in ("submitted", "approving", "approved"):
        raise HTTPException(409, detail={
            "error": "bad_state", "detail": f"cannot recall from '{status}'"})
    submitter = _srow_get(srow, "submitted_by")
    if not (user.is_admin or (submitter and user.username == submitter)):
        raise HTTPException(403, detail={
            "error": "forbidden",
            "detail": "only the submitter (or an admin) can recall this submission"})

    if status == "submitted":
        live = None if user.is_admin else _admin_live_on(sid, exclude_username=user.username)
        if live is None:
            claimed = db.execute_rowcount(
                "UPDATE sessions SET status='in_review', updated_at=? "
                "WHERE id=? AND status='submitted'", (time.time(), sid))
            if claimed:
                # any open request for this session is now moot — close it as granted
                db.execute(
                    "UPDATE recall_requests SET status='granted', resolved_by=?, "
                    "resolved_at=?, resolution_note='auto-granted (recall)' "
                    "WHERE session_id=? AND status='open'",
                    (user.username, time.time(), sid))
                return {"ok": True, "recalled": True, "status": "in_review"}
            # lost a race (approve claimed it / admin sent it back) — re-read and fall
            # through to the request path on the new state
            srow = _session_row(sid)
            status = srow["status"]
            if status in _EDITABLE_STATUSES:
                return {"ok": True, "recalled": True, "status": status}

    reason = (reason or "").strip()
    if not reason:
        blocker = ("it is already approved (staging written, masters promoted)"
                   if status == "approved"
                   else "an admin is currently reviewing it")
        raise HTTPException(409, detail={
            "error": "reason_required",
            "detail": f"cannot auto-recall — {blocker}; state the reason for the request"})
    existing = db.query_one(
        "SELECT id FROM recall_requests WHERE session_id=? AND status='open'", (sid,))
    if existing:
        return {"ok": True, "recalled": False, "request_id": existing["id"],
                "existing": True}
    rid = db.execute(
        "INSERT INTO recall_requests(session_id,trip_id,requested_by,reason,created_at) "
        "VALUES(?,?,?,?,?)",
        (sid, srow["trip_id"], user.username, reason, time.time()))
    return {"ok": True, "recalled": False, "request_id": rid, "existing": False}


def recall_requests_list(status: str = "open") -> list[dict]:
    """ADMIN: recall requests (default: the open ones, pinned atop the review queue).
    Each carries the session's current status + the trip's completed method so the FE
    can warn how far downstream an approved trip got before granting."""
    rows = db.query(
        "SELECT * FROM recall_requests WHERE status=? ORDER BY created_at", (status,))
    out: list[dict] = []
    for r in rows:
        d = _recall_request_dict(r)
        srow = db.query_one("SELECT status FROM sessions WHERE id=?", (r["session_id"],))
        crow = db.query_one("SELECT method FROM completed_trips WHERE trip_id=?",
                            (r["trip_id"],))
        meta = _trip_meta(r["trip_id"])
        d["session_status"] = srow["status"] if srow else None
        d["completed_method"] = crow["method"] if crow else None
        d["title"] = meta.get("title") or r["trip_id"]
        d["language"] = audio_core.language_of(r["trip_id"])
        out.append(d)
    return out


def recall_counts() -> dict:
    """ADMIN: open recall-request count for the nav badge (bug-reports pattern)."""
    row = db.query_one("SELECT COUNT(*) AS n FROM recall_requests WHERE status='open'")
    return {"open": int(row["n"] if row else 0)}


def resolve_recall(rid: int, admin, action: str, note: str = "") -> dict:
    """ADMIN resolves a pinned recall request.
    grant  — send the trip back to the reviewer: `changes_requested` with the reason
             (or the admin's note) as the review_note banner; an APPROVED trip is
             un-completed first (Stage 9 stops seeing it; re-approval later re-writes).
    decline — keep the submission; the note is shown to the requester."""
    row = db.query_one("SELECT * FROM recall_requests WHERE id=?", (rid,))
    if not row:
        raise HTTPException(404, detail={"error": "no_request", "detail": str(rid)})
    if row["status"] != "open":
        raise HTTPException(409, detail={
            "error": "bad_state", "detail": f"request is already '{row['status']}'"})
    sid = row["session_id"]
    session_status = None
    if action == "grant":
        srow = _session_row(sid)
        status = srow["status"]
        if status == "approving":
            # An approve() is mid-flight. BOTH of its exits (→approved, or the
            # revert-to-submitted on failure) write the status unconditionally, so a
            # grant now would be silently clobbered — worse, commit() may already be
            # half-written to staging. Make the admin wait for the approve to settle.
            raise HTTPException(409, detail={
                "error": "approve_in_progress",
                "detail": "an approve is running on this session — retry once it "
                          "finishes (it settles to approved or submitted)"})
        msg = (note or "").strip() or f"Recall granted: {row['reason']}"
        if status in ("submitted", "approved"):
            if status == "approved":
                uncomplete_trip(admin, srow["trip_id"])
            # CAS on the status we just read: if approve claimed the session between
            # the read and here, don't stomp its transient 'approving' state.
            changed = db.execute_rowcount(
                "UPDATE sessions SET status='changes_requested', review_note=?, "
                "updated_at=? WHERE id=? AND status=?",
                (msg, time.time(), sid, status))
            if not changed:
                raise HTTPException(409, detail={
                    "error": "state_changed",
                    "detail": "the session state changed underneath — reload and retry"})
            session_status = "changes_requested"
        else:
            # Already editable (auto-recalled / sent back concurrently) — nothing to do
            # to the session; just close the request.
            session_status = status
    now = time.time()
    db.execute(
        "UPDATE recall_requests SET status=?, resolved_by=?, resolved_at=?, "
        "resolution_note=? WHERE id=?",
        ("granted" if action == "grant" else "declined",
         getattr(admin, "username", None), now, note or "", rid))
    return {"ok": True, "session_status": session_status}


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
# Completed queue
# --------------------------------------------------------------------------- #
# Machine-readable mirror of the completed_trips table, next to trips_to_review.json,
# so the Scripts/Stage-9 side can see which trips are finished WITHOUT touching
# review.db. CURRENT-STATE snapshot, not an event log: an un-complete removes the row.
#
# Written to TWO places, and the R2 one is the contract:
#   * this local file — only useful to a Stage 9 running on the SAME machine as the app;
#   * review-audio/_bus/completed_trips.json (review_bus.COMPLETED_KEY) — the
#     cross-machine surface, and the one Stage 9 should read first.
# The local-only design broke the moment the server moved to the Ubuntu laptop: the app
# went on writing this file on the laptop while Stage 9 read a Windows copy frozen at
# 2026-07-08. Anything that writes one must write the other.
COMPLETED_EXPORT_PATH = Path(os.environ.get(
    "REVIEW_APP_COMPLETED_EXPORT",
    str(config.REVIEW_APP_ROOT / "completed_trips.json")))


def _iso(ts: float | None) -> str | None:
    return (datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")
            if ts is not None else None)


def completed_export_payload(rows) -> dict:
    """Build the completed_trips.json payload from completed_trips row mappings —
    shared by the live server hook and scripts/export_completed.py (which reads the
    db read-only). ``method`` is load-bearing downstream: 'approved' = the corrected
    <i>.mp3 masters were promoted in place AND the text went to staging; 'manual' =
    marker only, NOTHING was written. Never collapse the two."""
    trips = []
    for r in rows:
        _lvl, fam = _level_family(r["trip_id"])
        trips.append({
            "trip_id": r["trip_id"],
            "method": r["method"],
            "completed_by": r["completed_by"],
            "completed_at": _iso(r["completed_at"]),
            "session_id": r["session_id"],
            "note": r["note"] or "",
            "language": audio_core.language_of(r["trip_id"]),
            "family": fam or "",
        })
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "review.db completed_trips",
        "trips": trips,
    }


def write_completed_export(payload: dict,
                           path: Path = COMPLETED_EXPORT_PATH) -> None:
    """Atomic write (tmp + os.replace) so Stage 9 never reads a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def export_completed_trips() -> None:
    """Refresh the completed-trips snapshot — the LOCAL file and the R2 mirror
    (review_bus.COMPLETED_KEY), which is the surface Stage 9 actually reads now that the
    app and the pipeline live on different machines.

    Called on approve, manual complete, AND un-complete: the snapshot is current-state,
    so a trip disappearing from it is how an un-complete is communicated — skip the push
    on that path and the consumer would keep finalising a withdrawn trip.

    BEST-EFFORT throughout: an export hiccup must never fail the API operation that
    triggered it (an approve is worth more than its mirror, and the one-shot script
    scripts/export_completed.py can always rebuild both)."""
    try:
        rows = db.query(
            "SELECT trip_id, completed_by, completed_at, method, session_id, note "
            "FROM completed_trips ORDER BY completed_at")
        payload = completed_export_payload(rows)
    except Exception as e:  # noqa: BLE001 — see docstring
        print(f"[completed-export] skipped (could not read the table): {e}")
        return
    try:
        write_completed_export(payload)
    except Exception as e:  # noqa: BLE001
        print(f"[completed-export] local file skipped: {e}")
    review_bus.put_completed_snapshot(payload)     # loud on failure, never raises


def _parse_bus_ts(s) -> float | None:
    """Tolerant ISO-8601 → epoch seconds for finalised-bus timestamps (values may
    carry a Z or an offset; treat naive as UTC). None on anything unparseable."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            from datetime import timezone
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _finalised_state(fin: dict | None, completed_at: float) -> tuple[str | None, float | None]:
    """Cross-reference a completed trip against its Stage-9 finalised-bus entry.

    Returns (state, finalised_at_epoch): 'shipped' = the CURRENT approval was
    finalised + uploaded; 'restale' = it was shipped once but the trip has been
    re-approved since (Scripts' pending() calls this restale too — re-finalise
    pending); None = never finalised. The join key is the entry's completed_at —
    the approval timestamp Stage 9 finalised against — with a 1 s slop because it
    round-trips through ISO seconds."""
    if not fin:
        return None, None
    f_at = _parse_bus_ts(fin.get("finalised_at"))
    fc_at = _parse_bus_ts(fin.get("completed_at"))
    if fc_at is not None and completed_at > fc_at + 1.0:
        return "restale", f_at
    if f_at is not None and f_at + 1.0 >= completed_at:
        return "shipped", f_at
    return "restale", f_at


def completed(user) -> list[dict]:
    """The completed queue (approved + admin-marked-complete). BOTH roles; reviewers are
    filtered to their languages (admins see all). Newest first, except SHIPPED trips
    (Stage-9-finalised, per the read-only finalised bus) sink to the bottom — they need
    no further attention. View-only — an admin un-completes to return a trip to the
    active list. The bus is re-fetched on every load (no caching) so a just-shipped
    trip flips to Published without a hard reload."""
    from . import auth   # lazy (auth imports sessions) — no module-load cycle
    rows = db.query(
        "SELECT trip_id, completed_by, completed_at, method, session_id "
        "FROM completed_trips ORDER BY completed_at DESC")
    fin_map = review_bus.get_finalised_snapshot()   # best-effort; {} = none finalised
    out: list[dict] = []
    for r in rows:
        tid = r["trip_id"]
        if not auth.language_allowed(user, tid):
            continue
        meta = _trip_meta(tid)
        fin_state, fin_at = _finalised_state(fin_map.get(tid), r["completed_at"])
        out.append({
            "trip_id": tid,
            "title": meta.get("title") or tid,
            "language": audio_core.language_of(tid),
            "method": r["method"],
            "completed_by": r["completed_by"],
            "completed_at": r["completed_at"],
            "session_id": r["session_id"],
            "finalised": fin_state,        # None | 'shipped' | 'restale'
            "finalised_at": fin_at,        # epoch seconds | None
        })
    # Stable partition: unshipped (newest-first) above, shipped sunk to the bottom.
    out.sort(key=lambda it: it["finalised"] == "shipped")
    return out


def complete_trip(user, trip_id: str, note: str = "") -> dict:
    """ADMIN manual complete (bypass): upsert a completed_trips row (method='manual',
    session_id=NULL). NO session / mark-done / submit / approve required and it writes
    NOTHING to staging or masters — it's purely a workflow marker (work done elsewhere).
    Idempotent; 200 even when the trip has no session."""
    now = time.time()
    db.execute(
        "INSERT INTO completed_trips"
        "(trip_id,completed_by,completed_at,method,session_id,note) "
        "VALUES(?,?,?,'manual',NULL,?) "
        "ON CONFLICT(trip_id) DO UPDATE SET "
        "completed_by=excluded.completed_by, completed_at=excluded.completed_at, "
        "method='manual', session_id=NULL, note=excluded.note",
        (trip_id, getattr(user, "username", None), now, note or ""))
    export_completed_trips()   # Stage-9 handshake file (best-effort)
    trello.notify(trip_id, move=False, comment=(
        f"Marked complete (manual) in review-app by "
        f"{getattr(user, 'username', 'admin')} — workflow marker only, nothing "
        f"written; card left in place." + (f" Note: {note}" if note else "")))
    return {"ok": True}


def uncomplete_trip(user, trip_id: str) -> dict:
    """ADMIN un-complete: return the trip to the main list AND reopen its approved session
    for editing. Idempotent (no-op if not completed).

    Why reopen (dave, 2026-07-15): `approved` is the only terminal status, so a fresh open
    re-seeds a BLANK session from the promoted masters — which, being newest, then shadows
    the real approved session in the trip list, so the trip looks unreviewed and its edit
    history is hidden. Flipping the backing session to `changes_requested` (an
    EDITABLE_STATUS) means the next open RESUMES it — edits, diffs and flags intact —
    instead of seeding that blank shadow. A manual-only completion (session_id NULL) just
    drops the row. This is the same shadow-session shape statuses.py was created to kill."""
    row = db.query_one(
        "SELECT session_id FROM completed_trips WHERE trip_id=?", (trip_id,))
    sid = row["session_id"] if row else None
    # REOPEN BEFORE DELETE (order matters for crash-recovery): db.execute autocommits per
    # statement, so if the process dies between these two writes we want the survivable
    # state. Reopen-then-delete: a crash after the reopen leaves the trip still completed
    # (row present) but its session already editable — re-running uncomplete is a clean
    # no-op reopen (the status guard) + the delete, fully recovered. The reverse order
    # would strand the trip un-completed with a still-`approved` session AND no row left
    # to find the session through, reviving the blank-shadow bug with no API remedy.
    # Only an `approved` session is reopened — a manual completion points at NULL, and the
    # `status='approved'` guard means a non-approved session is never disturbed.
    if sid:
        db.execute(
            "UPDATE sessions SET status='changes_requested', "
            "review_note='Un-completed by admin — reopened for further edits.', "
            "updated_at=? WHERE id=? AND status='approved'", (time.time(), sid))
    db.execute("DELETE FROM completed_trips WHERE trip_id=?", (trip_id,))
    export_completed_trips()   # Stage-9 handshake file (best-effort; row removed)
    trello.notify(trip_id, move=False, comment=(
        f"Un-completed in review-app by {getattr(user, 'username', 'admin')} — trip "
        f"reopened for further edits. Card NOT moved back automatically."))
    return {"ok": True}


def pin_trip(user, trip_id: str) -> dict:
    """ADMIN: pin a trip to the top of the reviewer list (above the Trello base order).
    Idempotent; re-pinning refreshes pinned_at, moving it back to the top."""
    db.execute(
        "INSERT INTO trip_priority(trip_id,pinned_by,pinned_at) VALUES(?,?,?) "
        "ON CONFLICT(trip_id) DO UPDATE SET pinned_by=excluded.pinned_by, "
        "pinned_at=excluded.pinned_at",
        (trip_id, getattr(user, "username", None), time.time()))
    return {"ok": True}


def unpin_trip(user, trip_id: str) -> dict:
    """ADMIN: remove a trip's pin — it returns to the Trello base order."""
    db.execute("DELETE FROM trip_priority WHERE trip_id=?", (trip_id,))
    return {"ok": True}


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


# Leveled/target-language trip id → the BASE trip ids whose display images it shares.
# KaohsiungLotusPond_HSK3_ZH → KaohsiungLotusPond_EN; Tokyo_03_Beg_N4_JP → Tokyo_03_Beg_JP
# (the N5 sibling, whose Day Series OGG folder holds the {i}.jpg stills) then Tokyo_03_EN.
# Display-only heuristics — never used to resolve audio or write targets.
_IMAGE_BASE_RES = [
    (re.compile(r"^(?P<b>.+)_HSK\d+_ZH$"), ("{b}_EN",)),
    (re.compile(r"^(?P<b>.+)_Beg_N4_JP$"), ("{b}_Beg_JP", "{b}_EN")),
    (re.compile(r"^(?P<b>.+)_Beg_N5_JP$"), ("{b}_Beg_JP", "{b}_EN")),
    (re.compile(r"^(?P<b>.+)_Beg_JP$"), ("{b}_EN",)),
    (re.compile(r"^(?P<b>.+)_(?:A12|B1|B2)_EN$"), ("{b}_EN",)),
]


def _image_base_ids(trip_id: str) -> list[str]:
    ids = [trip_id]
    for rx, outs in _IMAGE_BASE_RES:
        m = rx.match(trip_id)
        if m:
            ids += [o.format(b=m.group("b")) for o in outs]
            break
    return ids


def _resolve_overlay_file(trip_id: str, mp3_dir: Path | None, ogg_dir: Path | None,
                          filename: str, folder_name: str = "",
                          static_indices: set[int] | None = None) -> Path | None:
    """Pure resolver (no DB) for an overlay / static-360 image, display only. Static-360
    stills ({i}.jpg) live in the OGG folder; flat overlays live under the trip's data
    cache — for leveled/target-language trips both live under the BASE trip's folders
    (Audio Generation/ogg/<base>/, the Day Series OGG tree, or the base's
    data/<base>/static_images/). Returns None if nothing is found (caller nulls the
    URL). ``folder_name`` (e.g. "Japan/Tokyo/Tokyo_03_Beg_N4_JP") supplies the location
    segment the Day Series tree is split by."""
    from .config import (AUDIO_GENERATION_OGG, EXTRA_IMAGE_OGG_ROOTS,
                         OVERLAY_SEARCH_DIRS, RW_DATA_ROOTS)
    safe = Path(filename).name
    # `{i}-4k.jpg` is not in any of the trees below — it is the 4K panorama re-encode,
    # which lives in its own tree and is index-matched (static360). Resolve it there or
    # not at all; falling through would silently miss.
    m4k = static360.NAME_RE.match(safe)
    if m4k:
        return static360.resolve(trip_id, _image_base_ids(trip_id),
                                 static_indices or set(), int(m4k.group(1)))
    candidates = [
        OVERLAY_SEARCH_DIRS[0] / trip_id / "static_images" / safe,
        OVERLAY_SEARCH_DIRS[0] / trip_id / safe,
    ]
    if ogg_dir is not None:
        candidates.append(ogg_dir / safe)
    if mp3_dir is not None:
        candidates.append(mp3_dir / safe)
    parts = (folder_name or "").replace("\\", "/").strip("/").split("/")
    location = parts[1] if len(parts) > 2 else ""
    for bid in _image_base_ids(trip_id):
        candidates.append(AUDIO_GENERATION_OGG / bid / safe)
        for root in RW_DATA_ROOTS:
            candidates.append(root / bid / "static_images" / safe)
        for root in EXTRA_IMAGE_OGG_ROOTS:
            if location:
                candidates.append(root / location / bid / safe)
            candidates.append(root / bid / safe)
    candidates += [d / safe for d in OVERLAY_SEARCH_DIRS]
    for c in candidates:
        if c.exists():
            return c
    return None


def _overlay_base(trip_id: str) -> str:
    """Canonical R2 base for a trip's overlay/static images: the most-reduced base id
    (``<base>_EN``) — every level/language sibling of a group reduces to it, so one R2
    copy is shared. `_image_base_ids` always includes trip_id itself, so [-1] is safe."""
    return _image_base_ids(trip_id)[-1]


def overlay_path(sid: str, filename: str) -> Path | None:
    """Best-effort overlay/static-360 image resolution for display only. When the local
    file resolves it is ALSO mirrored to R2 (best-effort) so hosts without the source
    trees (the Ubuntu laptop) can serve it via the R2 fallback."""
    srow = _session_row(sid)
    trip_id = srow["trip_id"]
    trip = json.loads(srow["loaded_trip_json"])
    try:
        _p = paths_for(trip, trip_id)
        mp3_dir, ogg_dir = _p["mp3_dir"], _p["ogg_dir"]
    except SystemExit:
        mp3_dir = ogg_dir = None
    static_idx = {i for i, s in enumerate(trip.get("quickTrips") or [])
                  if (s or {}).get("isStaticImage")}
    local = _resolve_overlay_file(trip_id, mp3_dir, ogg_dir, filename,
                                  srow["folder_name"] or "", static_idx)
    if local is not None:
        images_r2.ensure_uploaded(_overlay_base(trip_id), filename, local)
    return local


def overlay_r2_url(sid: str, filename: str) -> str | None:
    """Public R2 URL for an overlay/static image when it isn't resolvable locally (the
    hosted/laptop case). Returns None unless the object is present on R2."""
    srow = _session_row(sid)          # 404s on an unknown session, like overlay_path
    base = _overlay_base(srow["trip_id"])
    return images_r2.public_url(base, filename) if images_r2.exists(base, filename) else None
