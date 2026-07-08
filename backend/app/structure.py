"""Direct scene-STRUCTURE editor (WS4 phases 2–3): add / remove / reorder / swap
scenes, per-scene videoUrl + static-image edits, and direct tripCategories editing —
all as immediate targeted STAGING writes, deliberately OUTSIDE the session model.

Why direct (Dave, 2026-07-08): every session artefact (field_edits.scene_index, the
_ZH localization mapping, positional mp3 names) is index-addressed. Buffering
structural ops inside a session would require transactionally renumbering all of it;
instead structure edits are rare, admin-only, immediate ops that are simply REFUSED
(409) while any active session exists on the trip.

SceneId discipline (SCENEID_AND_VIDEO_MAPPING_DECISION_2026-07-04): this module is the
SINGLE re-key writer. It imports the shared ruleset ``scene_ids`` from the Scripts repo
(same sys.path mechanism as stage9.common) — mint/preserve logic is never reimplemented
here. Rules applied: reorder CARRIES ids (never re-mints); same-footage video swap
keeps the id (registry gains the videoId); a genuinely different scene re-keys via
``ensure_unique(derive_from_stem(...) or mint_opaque())``; every op keeps the
``Scenes/{sceneId}`` registry current (usedBy / videoIds / currentVideoId).

TripLocalizations stays INDEX-keyed for now (decision: renumber-now, sceneId-key
later with the wire compiler) — structural ops renumber/drop its ``scenes[].index``
entries in the same operation so the _ZH readers never desync.

⚠ Positional media: mp3/ogg/srt artefacts are keyed by scene POSITION and are NOT
rewritten here — any add/remove/reorder means the trip's audio must be re-staged
(Stage 9 / the lane's audio step) before publish. Every structural op returns that
warning and is recorded in ``structure_ops`` for the audit trail.
"""

from __future__ import annotations

import json
import re
import time

from fastapi import HTTPException

from . import config  # noqa: F401  (side effect: SCRIPTS_ROOT on sys.path) — keep first
import scene_ids  # Scripts repo shared ruleset — NEVER reimplement mint/preserve here

from . import db, thumbs
from .staging import db as fb_db, get_trip, get_tripgroup, tripgroup_id_for

_POSITIONAL_MEDIA_WARNING = (
    "Scene media (mp3/ogg/subtitles) is positional — after an add/remove/reorder the "
    "trip's audio artefacts no longer line up and the audio stage / Stage 9 must be "
    "re-run before this trip is finalised or published.")

# Text fields every quickTrips entry carries; a new scene gets them empty so the
# session editor (and Stage 9) see the usual shape.
_SCENE_TEXT_FIELDS = ("titleKey", "titleKeyEn", "SceneDesc", "SceneDescEn",
                      "questionKey", "questionKeyEn")

_ACTIVE_STATUSES = ("in_review", "submitted", "approving", "changes_requested")


def _audit(trip_id: str, op: str, payload: dict, user) -> None:
    db.execute(
        "INSERT INTO structure_ops(trip_id,op,payload_json,performed_by,performed_at) "
        "VALUES(?,?,?,?,?)",
        (trip_id, op, json.dumps(payload, ensure_ascii=False, default=str),
         getattr(user, "username", None) or "", time.time()))


def _assert_no_active_session(trip_id: str) -> None:
    row = db.query_one(
        "SELECT id, status FROM sessions WHERE trip_id=? AND status IN "
        "('in_review','submitted','approving','changes_requested') "
        "ORDER BY created_at DESC LIMIT 1", (trip_id,))
    if row:
        raise HTTPException(409, detail={
            "error": "active_session",
            "detail": f"session {row['id']} is '{row['status']}' on this trip — "
                      "approve, send back + discard, or complete it before editing "
                      "structure (structural edits would desync its scene indexes)"})


def _fingerprint(scenes: list[dict]) -> list[str]:
    """Stable per-scene identity the FE echoes back so a concurrent structural change
    is detected before writing (sceneId when present, else index+videoUrl)."""
    out = []
    for i, s in enumerate(scenes or []):
        out.append(str((s or {}).get("sceneId") or f"i{i}:{(s or {}).get('videoUrl') or ''}"))
    return out


def _fetch(trip_id: str) -> tuple[dict, list[dict]]:
    try:
        trip = get_trip(trip_id)
    except SystemExit as e:
        raise HTTPException(404, detail={"error": "no_staging_trip", "detail": str(e)})
    return trip, list(trip.get("quickTrips") or [])


def _check_base(scenes: list[dict], base: list[str]) -> None:
    if base != _fingerprint(scenes):
        raise HTTPException(409, detail={
            "error": "state_changed",
            "detail": "the trip's scene structure changed since you loaded it — reload"})


def _write_quicktrips(trip_id: str, qt: list[dict]) -> None:
    fb_db().collection("Trips").document(trip_id).update({"quickTrips": qt})


# --------------------------------------------------------------------------- #
# Scenes registry maintenance (single writer, per the decision memo)
# --------------------------------------------------------------------------- #
def _registry_add_use(scene_id: str, trip_id: str, video_url: str | None,
                      is_static: bool) -> None:
    from google.cloud import firestore
    doc: dict = {"sceneId": scene_id,
                 "usedBy": firestore.ArrayUnion([trip_id])}
    if video_url:
        doc["videoIds"] = firestore.ArrayUnion([video_url])
        doc.setdefault("currentVideoId", video_url)
        doc["kind"] = "video360"
    elif is_static:
        doc["kind"] = "photo360"
    fb_db().collection("Scenes").document(scene_id).set(doc, merge=True)


def _registry_drop_use(scene_id: str, trip_id: str, qt_after: list[dict]) -> None:
    """Remove trip from usedBy ONLY when the atom no longer appears anywhere in the
    trip (the same 360 atom can legitimately be used by two scenes)."""
    from google.cloud import firestore
    if any((s or {}).get("sceneId") == scene_id for s in qt_after):
        return
    fb_db().collection("Scenes").document(scene_id).set(
        {"usedBy": firestore.ArrayRemove([trip_id])}, merge=True)


def _registry_add_video(scene_id: str, video_url: str) -> None:
    from google.cloud import firestore
    fb_db().collection("Scenes").document(scene_id).set(
        {"videoIds": firestore.ArrayUnion([video_url]),
         "currentVideoId": video_url}, merge=True)


# --------------------------------------------------------------------------- #
# TripLocalizations renumbering (index-keyed for now — renumber-now decision)
# --------------------------------------------------------------------------- #
def _renumber_localization(trip_id: str, index_map: dict[int, int | None]) -> bool:
    """Apply old-index -> new-index (None = scene removed) to the TripLocalizations
    doc's scenes[].index, in the same operation as the Trip structural write. Returns
    whether a loc doc existed. Missing doc (non-_ZH trips) is a clean no-op."""
    ref = fb_db().collection("TripLocalizations").document(trip_id)
    snap = ref.get()
    if not snap.exists:
        return False
    doc = snap.to_dict() or {}
    new_scenes = []
    for sc in doc.get("scenes") or []:
        old = sc.get("index")
        if old is None or old not in index_map:
            new_scenes.append(sc)          # unknown entry — keep untouched
            continue
        new = index_map[old]
        if new is None:
            continue                        # scene removed → entry dropped
        sc = dict(sc)
        sc["index"] = new
        new_scenes.append(sc)
    new_scenes.sort(key=lambda sc: (sc.get("index") is None, sc.get("index")))
    ref.update({"scenes": new_scenes})
    return True


# --------------------------------------------------------------------------- #
# Read model
# --------------------------------------------------------------------------- #
def get_structure(trip_id: str) -> dict:
    trip, qt = _fetch(trip_id)
    tg_id, tg = get_tripgroup(trip_id)
    scenes = []
    for i, s in enumerate(qt):
        s = s or {}
        scenes.append({
            "index": i,
            "scene_id": s.get("sceneId"),
            "video_url": s.get("videoUrl"),
            "is_static_image": bool(s.get("isStaticImage")),
            "has_audio": bool(s.get("hasAudio")),
            "title": (s.get("titleKey") or "").split("\n")[0][:80],
            "desc_snippet": (s.get("SceneDesc") or "").split("\n")[0][:120],
            "thumb_url": thumbs.thumb_url_for_scene(s),
            "static_images": [si.get("filename") for si in (s.get("staticImages") or [])
                              if isinstance(si, dict) and si.get("filename")],
        })
    return {
        "trip_id": trip_id,
        "title": trip.get("contentTitleKey") or trip_id,
        "tripgroup_id": tg_id,
        "categories": (tg or {}).get("tripCategories")
                      or trip.get("tripCategories") or [],
        "scenes": scenes,
        "base": _fingerprint(qt),
        "localization_doc": bool(
            fb_db().collection("TripLocalizations").document(trip_id).get().exists),
        "recent_ops": [
            {"op": r["op"], "by": r["performed_by"], "at": r["performed_at"],
             "payload": json.loads(r["payload_json"])}
            for r in db.query(
                "SELECT op,payload_json,performed_by,performed_at FROM structure_ops "
                "WHERE trip_id=? ORDER BY performed_at DESC LIMIT 10", (trip_id,))],
    }


# --------------------------------------------------------------------------- #
# Ops (each: guard → fresh fetch → base check → mutate → write → registry → loc)
# --------------------------------------------------------------------------- #
def reorder(trip_id: str, order: list[int], base: list[str], user) -> dict:
    """Memo rule 1: a reorder CARRIES each scene's sceneId with it — ids travel inside
    the scene dicts; nothing is re-minted."""
    _assert_no_active_session(trip_id)
    _trip, qt = _fetch(trip_id)
    _check_base(qt, base)
    if sorted(order) != list(range(len(qt))):
        raise HTTPException(422, detail={
            "error": "bad_order",
            "detail": f"order must be a permutation of 0..{len(qt) - 1}"})
    new_qt = [qt[old] for old in order]
    _write_quicktrips(trip_id, new_qt)
    loc = _renumber_localization(
        trip_id, {old: new for new, old in enumerate(order)})
    _audit(trip_id, "reorder", {"order": order, "loc_renumbered": loc}, user)
    return {"ok": True, "warnings": [_POSITIONAL_MEDIA_WARNING],
            "structure": get_structure(trip_id)}


def remove(trip_id: str, index: int, base: list[str], user) -> dict:
    _assert_no_active_session(trip_id)
    _trip, qt = _fetch(trip_id)
    _check_base(qt, base)
    if not (0 <= index < len(qt)):
        raise HTTPException(422, detail={"error": "bad_index", "detail": str(index)})
    removed = qt.pop(index)
    _write_quicktrips(trip_id, qt)
    sid = (removed or {}).get("sceneId")
    if sid:
        _registry_drop_use(sid, trip_id, qt)
    index_map: dict[int, int | None] = {index: None}
    for old in range(index + 1, len(qt) + 1):
        index_map[old] = old - 1
    loc = _renumber_localization(trip_id, index_map)
    _audit(trip_id, "remove",
           {"index": index, "scene_id": sid,
            "video_url": (removed or {}).get("videoUrl"), "loc_renumbered": loc}, user)
    return {"ok": True, "warnings": [_POSITIONAL_MEDIA_WARNING],
            "structure": get_structure(trip_id)}


def _new_scene_id(qt: list[dict], video_url: str | None,
                  requested: str | None) -> str:
    """Reuse a requested existing atom id, else derive-from-stem / mint via the shared
    ruleset — never a local re-implementation."""
    taken = {str(s.get("sceneId")) for s in qt if (s or {}).get("sceneId")}
    if requested:
        if not scene_ids.is_scene_id(requested):
            raise HTTPException(422, detail={
                "error": "bad_scene_id", "detail": requested})
        return requested   # existing atom, may legitimately repeat across trips
    stem = None
    if video_url:
        m = re.search(r"([^/\\]+?)(?:_\d{3,4})?\.mp4?$", video_url) or \
            re.search(r"([^/\\]+)$", video_url)
        stem = m.group(1) if m else None
    cand = scene_ids.derive_from_stem(stem) if stem else None
    return scene_ids.ensure_unique(cand or scene_ids.mint_opaque(), taken)


def add(trip_id: str, position: int, base: list[str], user,
        video_url: str | None = None, is_static: bool = False,
        scene_id: str | None = None) -> dict:
    """Insert a new scene at `position`. sceneId: an existing atom's id may be supplied
    (reuse — registry usedBy gains this trip); otherwise derived/minted via scene_ids.
    Text fields are created empty — author them in a normal session afterwards."""
    _assert_no_active_session(trip_id)
    _trip, qt = _fetch(trip_id)
    _check_base(qt, base)
    if not video_url and not is_static:
        raise HTTPException(422, detail={
            "error": "bad_scene", "detail": "a videoUrl (or is_static) is required"})
    position = max(0, min(position, len(qt)))
    sid = _new_scene_id(qt, video_url, scene_id)
    scene: dict = {"sceneId": sid,
                   "videoUrl": video_url or "",
                   "isStaticImage": bool(is_static),
                   "hasAudio": True,
                   "staticImages": [],
                   "questionOptionKeys": [], "questionOptionKeysEn": []}
    for f in _SCENE_TEXT_FIELDS:
        scene[f] = ""
    qt.insert(position, scene)
    _write_quicktrips(trip_id, qt)
    _registry_add_use(sid, trip_id, video_url, is_static)
    index_map = {old: (old if old < position else old + 1)
                 for old in range(len(qt) - 1)}
    loc = _renumber_localization(trip_id, index_map)
    _audit(trip_id, "add", {"position": position, "scene_id": sid,
                            "video_url": video_url, "loc_renumbered": loc}, user)
    return {"ok": True, "warnings": [
        _POSITIONAL_MEDIA_WARNING,
        "New scene has no text or audio yet — open the trip in a session to author it."],
        "structure": get_structure(trip_id)}


def swap_video(trip_id: str, index: int, video_url: str, rekey: bool,
               base: list[str], user, scene_id: str | None = None) -> dict:
    """Two distinct intents (memo rules 2 vs 3):
    rekey=False — same footage, new encode/URL fix: the sceneId is KEPT and the
    registry gains the videoId. rekey=True — a genuinely DIFFERENT scene now sits at
    this position: a new atom id is assigned (supplied / derived / minted); the old
    atom's registry use is dropped. Text is kept for the admin to rewrite; the
    localization entry stays index-keyed (renumber-now decision) so translations for
    the old atom are superseded when the text is re-authored."""
    _assert_no_active_session(trip_id)
    _trip, qt = _fetch(trip_id)
    _check_base(qt, base)
    if not (0 <= index < len(qt)):
        raise HTTPException(422, detail={"error": "bad_index", "detail": str(index)})
    if not (video_url or "").strip():
        raise HTTPException(422, detail={"error": "bad_url", "detail": "empty videoUrl"})
    scene = dict(qt[index] or {})
    old_sid = scene.get("sceneId")
    warnings: list[str] = []
    if rekey:
        new_sid = _new_scene_id(qt, video_url, scene_id)
        scene["sceneId"] = new_sid
        scene["videoUrl"] = video_url
        qt[index] = scene
        _write_quicktrips(trip_id, qt)
        if old_sid:
            _registry_drop_use(old_sid, trip_id, qt)
        _registry_add_use(new_sid, trip_id, video_url, bool(scene.get("isStaticImage")))
        warnings.append(
            "Re-keyed: translations/glosses keyed to the OLD sceneId will fall back "
            "to English until the new scene's text is authored + recompiled.")
        _audit(trip_id, "swap_rekey", {"index": index, "old_scene_id": old_sid,
                                       "new_scene_id": new_sid,
                                       "video_url": video_url}, user)
    else:
        scene["videoUrl"] = video_url
        qt[index] = scene
        _write_quicktrips(trip_id, qt)
        if old_sid:
            _registry_add_video(old_sid, video_url)
        _audit(trip_id, "set_video", {"index": index, "scene_id": old_sid,
                                      "video_url": video_url}, user)
    return {"ok": True, "warnings": warnings, "structure": get_structure(trip_id)}


def set_static_images(trip_id: str, index: int, filenames: list[str],
                      base: list[str], user) -> dict:
    """Edit a scene's flat-overlay list (filename refs — the JPGs themselves live in
    the Audio Generation / data trees and must exist there to render)."""
    _assert_no_active_session(trip_id)
    _trip, qt = _fetch(trip_id)
    _check_base(qt, base)
    if not (0 <= index < len(qt)):
        raise HTTPException(422, detail={"error": "bad_index", "detail": str(index)})
    clean = [f.strip() for f in filenames if f and f.strip()]
    scene = dict(qt[index] or {})
    scene["staticImages"] = [{"filename": f} for f in clean]
    qt[index] = scene
    _write_quicktrips(trip_id, qt)
    _audit(trip_id, "set_static_images", {"index": index, "filenames": clean}, user)
    return {"ok": True, "warnings": [], "structure": get_structure(trip_id)}


def set_categories(trip_id: str, categories: list[str], user) -> dict:
    """Direct tripCategories edit on the TripGroup (and the Trip doc when it carries
    its own copy). Unlike the description-derived path this sets the list verbatim —
    including the non-semantic level tags, so the FE shows the full live list."""
    clean = [c.strip() for c in categories if c and c.strip()]
    tg_id = tripgroup_id_for(trip_id)
    tg_ref = fb_db().collection("TripGroups").document(tg_id)
    if tg_ref.get().exists:
        tg_ref.update({"tripCategories": clean})
    trip, _qt = _fetch(trip_id)
    if "tripCategories" in trip:
        fb_db().collection("Trips").document(trip_id).update(
            {"tripCategories": clean})
    _audit(trip_id, "set_categories", {"categories": clean}, user)
    return {"ok": True, "warnings": [], "structure": get_structure(trip_id)}
