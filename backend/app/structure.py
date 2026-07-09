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
keeps the id (registry gains the videoId); a genuinely different scene re-keys — but a
videoId that is ALREADY registered to an atom REUSES that atom (one id per capture),
else ``ensure_unique(derive_from_stem(...) or mint_opaque())`` where the stem comes
from the VideoIds mapping (real staged videoUrls are bare Vimeo ids, not filenames);
every op keeps the ``Scenes/{sceneId}`` registry current (usedBy / videoIds /
currentVideoId — existing currentVideoId/kind are PRESERVED when a trip merely gains
a use of an existing atom; some atoms deliberately pin an older currentVideoId via
``remap``).

Concurrency: each structural op re-reads the Trip **inside a Firestore transaction**,
re-checks the FE's ``base`` fingerprint there, and writes quickTrips + the renumbered
TripLocalizations doc in that same transaction — so two racing ops cannot interleave
between check and write, and the loc doc can never desync from the Trip on a partial
failure. Scenes-registry updates happen after the commit and are best-effort: a
registry failure is surfaced as a warning + recorded in the audit row, never a 500
that hides an already-applied structural change.

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
from typing import Any, Callable

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

# mutate(qt) -> (new_qt, index_map | None, ...) — index_map: old -> new (None=removed)
_Mutate = Callable[[list[dict]], tuple[list[dict], "dict[int, int | None] | None"]]


def _audit(trip_id: str, op: str, payload: dict, user) -> None:
    db.execute(
        "INSERT INTO structure_ops(trip_id,op,payload_json,performed_by,performed_at) "
        "VALUES(?,?,?,?,?)",
        (trip_id, op, json.dumps(payload, ensure_ascii=False, default=str),
         getattr(user, "username", None) or "", time.time()))


def _active_session_row(trip_id: str):
    ph = ",".join("?" * len(_ACTIVE_STATUSES))
    return db.query_one(
        f"SELECT id, status FROM sessions WHERE trip_id=? AND status IN ({ph}) "
        "ORDER BY created_at DESC LIMIT 1", (trip_id, *_ACTIVE_STATUSES))


def _assert_no_active_session(trip_id: str) -> None:
    row = _active_session_row(trip_id)
    if row:
        raise HTTPException(409, detail={
            "error": "active_session",
            "detail": f"session {row['id']} is '{row['status']}' on this trip — "
                      "approve, send back + discard, or complete it before editing "
                      "structure (structural edits would desync its scene indexes)"})


def _warn_if_session_appeared(trip_id: str, warnings: list[str]) -> None:
    """The active-session guard runs before the write; a session opened in that
    window may have seeded from the PRE-op structure. We can't prevent it (the guard
    is SQLite, the write is Firestore), so detect + shout after the commit."""
    row = _active_session_row(trip_id)
    if row:
        warnings.append(
            f"Session {row['id']} was opened on this trip WHILE the structural edit "
            "was being written — if it seeded before the write its scene indexes are "
            "stale; discard and reopen it.")


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


def _renumbered(scenes: list[dict], index_map: dict[int, int | None]) -> list[dict]:
    """Apply old-index -> new-index (None = scene removed) to a TripLocalizations
    ``scenes`` list. Entries with an unknown/absent index are kept untouched."""
    new_scenes = []
    for sc in scenes:
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
    return new_scenes


def _structural_write(trip_id: str, base: list[str], mutate: _Mutate) -> bool:
    """Run one structural op ATOMICALLY: inside a Firestore transaction, re-read the
    Trip, re-check ``base`` against the FRESH quickTrips, apply ``mutate`` and write
    the new quickTrips plus the renumbered TripLocalizations doc together. Retries on
    transaction contention re-run mutate against a fresh read, so two racing ops
    serialize instead of interleaving between check and write. Returns whether a
    localization doc was renumbered.

    NB: the ``@firestore.transactional`` decorator only retries Aborted raised at
    COMMIT time; cross-transaction contention detected at READ time propagates raw
    (verified against google-cloud-firestore 2.21) — so we retry the whole
    transaction here. A retry re-reads and re-checks ``base``, so it either applies
    cleanly (the competing write didn't change the structure fingerprint) or 409s."""
    from google.api_core import exceptions as gexc
    from google.cloud import firestore

    client = fb_db()
    trip_ref = client.collection("Trips").document(trip_id)
    loc_ref = client.collection("TripLocalizations").document(trip_id)

    @firestore.transactional
    def _txn(txn) -> bool:
        snap = trip_ref.get(transaction=txn)
        if not snap.exists:
            raise HTTPException(404, detail={
                "error": "no_staging_trip",
                "detail": f"staging Trips/{trip_id} not found"})
        loc_snap = loc_ref.get(transaction=txn)   # all reads before any write
        qt = list((snap.to_dict() or {}).get("quickTrips") or [])
        _check_base(qt, base)
        new_qt, index_map = mutate(qt)
        txn.update(trip_ref, {"quickTrips": new_qt})
        if index_map is not None and loc_snap.exists:
            doc = loc_snap.to_dict() or {}
            txn.update(loc_ref,
                       {"scenes": _renumbered(doc.get("scenes") or [], index_map)})
            return True
        return False

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            return _txn(client.transaction())
        except gexc.Aborted as e:
            last_exc = e
            time.sleep(0.2 * (attempt + 1))
    raise HTTPException(409, detail={
        "error": "state_changed",
        "detail": "another staging write is racing this trip — reload and retry"}) \
        from last_exc


# --------------------------------------------------------------------------- #
# Scenes registry maintenance (single writer, per the decision memo).
# All post-commit + best-effort: the Trip/loc write has already been applied, so a
# registry hiccup is surfaced as a warning (+ audit) instead of a misleading 500.
# --------------------------------------------------------------------------- #
def _reg_safe(warnings: list[str], what: str, fn, *args) -> str | None:
    try:
        fn(*args)
        return None
    except Exception as e:  # noqa: BLE001 — registry is repairable metadata
        msg = f"Scenes-registry update failed ({what}): {e}"
        warnings.append(msg + " — the structural change WAS applied; re-run the op "
                              "or fix the registry doc manually.")
        return msg


def _registry_add_use(scene_id: str, trip_id: str, video_url: str | None,
                      is_static: bool) -> None:
    """usedBy gains the trip; videoIds gains the videoId. An EXISTING atom's
    currentVideoId and kind are preserved (some atoms deliberately pin an older
    currentVideoId via ``remap``; GE flats are kind='flat' even though they carry a
    videoId) — only a doc that lacks them gets defaults."""
    from google.cloud import firestore
    ref = fb_db().collection("Scenes").document(scene_id)
    existing = ref.get().to_dict() or {}
    doc: dict = {"sceneId": scene_id,
                 "usedBy": firestore.ArrayUnion([trip_id])}
    if video_url:
        doc["videoIds"] = firestore.ArrayUnion([video_url])
        if not existing.get("currentVideoId"):
            doc["currentVideoId"] = video_url
        if not existing.get("kind"):
            doc["kind"] = "video360"
    elif is_static and not existing.get("kind"):
        doc["kind"] = "photo360"
    ref.set(doc, merge=True)


def _registry_drop_use(scene_id: str, trip_id: str, qt_after: list[dict]) -> None:
    """Remove trip from usedBy ONLY when the atom no longer appears anywhere in the
    trip (the same 360 atom can legitimately be used by two scenes)."""
    from google.cloud import firestore
    if any((s or {}).get("sceneId") == scene_id for s in qt_after):
        return
    fb_db().collection("Scenes").document(scene_id).set(
        {"usedBy": firestore.ArrayRemove([trip_id])}, merge=True)


def _registry_add_video(scene_id: str, video_url: str) -> None:
    """Same-footage swap: the atom gains the videoId and it BECOMES current (the
    admin explicitly said this encode/URL supersedes — unlike _registry_add_use)."""
    from google.cloud import firestore
    fb_db().collection("Scenes").document(scene_id).set(
        {"videoIds": firestore.ArrayUnion([video_url]),
         "currentVideoId": video_url}, merge=True)


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
# Ops (each: guard → transactional fetch+check+mutate+write → registry → audit)
# --------------------------------------------------------------------------- #
def reorder(trip_id: str, order: list[int], base: list[str], user) -> dict:
    """Memo rule 1: a reorder CARRIES each scene's sceneId with it — ids travel inside
    the scene dicts; nothing is re-minted."""
    _assert_no_active_session(trip_id)

    def mutate(qt: list[dict]):
        if sorted(order) != list(range(len(qt))):
            raise HTTPException(422, detail={
                "error": "bad_order",
                "detail": f"order must be a permutation of 0..{len(qt) - 1}"})
        return ([qt[old] for old in order],
                {old: new for new, old in enumerate(order)})

    loc = _structural_write(trip_id, base, mutate)
    warnings = [_POSITIONAL_MEDIA_WARNING]
    _warn_if_session_appeared(trip_id, warnings)
    _audit(trip_id, "reorder", {"order": order, "loc_renumbered": loc}, user)
    return {"ok": True, "warnings": warnings,
            "structure": get_structure(trip_id)}


def remove(trip_id: str, index: int, base: list[str], user) -> dict:
    _assert_no_active_session(trip_id)
    state: dict[str, Any] = {}

    def mutate(qt: list[dict]):
        if not (0 <= index < len(qt)):
            raise HTTPException(422, detail={"error": "bad_index", "detail": str(index)})
        if len(qt) == 1:
            raise HTTPException(422, detail={
                "error": "last_scene",
                "detail": "cannot remove the trip's only remaining scene — an empty "
                          "quickTrips breaks readers; swap its video instead"})
        removed = qt.pop(index)
        index_map: dict[int, int | None] = {index: None}
        for old in range(index + 1, len(qt) + 1):
            index_map[old] = old - 1
        state.update(removed=removed, qt_after=qt)
        return qt, index_map

    loc = _structural_write(trip_id, base, mutate)
    removed = state["removed"] or {}
    sid = removed.get("sceneId")
    warnings = [_POSITIONAL_MEDIA_WARNING]
    reg_err = None
    if sid:
        reg_err = _reg_safe(warnings, f"release usedBy of {sid}",
                            _registry_drop_use, sid, trip_id, state["qt_after"])
    _warn_if_session_appeared(trip_id, warnings)
    _audit(trip_id, "remove",
           {"index": index, "scene_id": sid, "video_url": removed.get("videoUrl"),
            "loc_renumbered": loc, "registry_error": reg_err}, user)
    return {"ok": True, "warnings": warnings,
            "structure": get_structure(trip_id)}


def _new_scene_id(qt: list[dict], video_url: str | None,
                  requested: str | None) -> tuple[str, str]:
    """Resolve the atom id for a new/re-keyed scene, per the memo (one id per
    capture, single shared ruleset). Returns (scene_id, how). Order:
    1. an explicitly requested EXISTING atom id (typo-guarded against the registry);
    2. a videoId already registered to an atom → REUSE that atom;
    3. derive from the capture timestamp — real staged videoUrls are bare Vimeo ids,
       so the stem comes from the VideoIds mapping (falling back to a filename-ish
       URL) — via scene_ids.derive_from_stem;
    4. scene_ids.mint_opaque(). Never a local re-implementation of mint/derive."""
    taken = {str(s.get("sceneId")) for s in qt if (s or {}).get("sceneId")}
    if requested:
        if not scene_ids.is_scene_id(requested):
            raise HTTPException(422, detail={
                "error": "bad_scene_id", "detail": requested})
        if requested not in taken and not \
                fb_db().collection("Scenes").document(requested).get().exists:
            raise HTTPException(422, detail={
                "error": "unknown_scene_id",
                "detail": f"{requested} is not in the Scenes registry — reuse needs "
                          "an existing atom; leave blank to derive/mint a new id"})
        return requested, "requested"   # existing atom, may repeat across trips
    vid = thumbs._vimeo_id(video_url) if video_url else None
    stem = None
    if vid:
        try:
            from google.cloud.firestore_v1 import FieldFilter
            hits = (fb_db().collection("Scenes")
                    .where(filter=FieldFilter("videoIds", "array_contains", vid))
                    .limit(1).get())
            if hits:
                return hits[0].id, "reused-atom"    # same capture ⇒ same atom
        except Exception:  # noqa: BLE001 — lookup is an optimisation, not a gate
            pass
        stem = thumbs.stem_for_video_id(vid)
    if not stem and video_url:
        m = re.search(r"([^/\\]+?)(?:_\d{3,4})?\.(?:mp4|mov|m4v)$",
                      video_url, re.IGNORECASE) or \
            re.search(r"([^/\\]+)$", video_url)
        stem = m.group(1) if m else None
    cand = scene_ids.derive_from_stem(stem) if stem else None
    sid = scene_ids.ensure_unique(cand or scene_ids.mint_opaque(), taken)
    return sid, ("derived" if cand else "minted")


def add(trip_id: str, position: int, base: list[str], user,
        video_url: str | None = None, is_static: bool = False,
        scene_id: str | None = None) -> dict:
    """Insert a new scene at `position`. sceneId: an existing atom's id may be supplied
    (reuse — registry usedBy gains this trip); otherwise reused-by-videoId / derived /
    minted via scene_ids. Text fields are created empty — author them in a normal
    session afterwards."""
    _assert_no_active_session(trip_id)
    if not video_url and not is_static:
        raise HTTPException(422, detail={
            "error": "bad_scene", "detail": "a videoUrl (or is_static) is required"})
    state: dict[str, Any] = {}

    def mutate(qt: list[dict]):
        pos = max(0, min(position, len(qt)))
        sid, how = _new_scene_id(qt, video_url, scene_id)
        scene: dict = {"sceneId": sid,
                       "videoUrl": video_url or "",
                       "isStaticImage": bool(is_static),
                       "hasAudio": True,
                       "staticImages": [],
                       "questionOptionKeys": [], "questionOptionKeysEn": []}
        for f in _SCENE_TEXT_FIELDS:
            scene[f] = ""
        index_map = {old: (old if old < pos else old + 1) for old in range(len(qt))}
        qt.insert(pos, scene)
        state.update(sid=sid, how=how, pos=pos)
        return qt, index_map

    loc = _structural_write(trip_id, base, mutate)
    warnings = [
        _POSITIONAL_MEDIA_WARNING,
        "New scene has no text or audio yet — open the trip in a session to author it."]
    reg_err = _reg_safe(warnings, f"register use of {state['sid']}",
                        _registry_add_use, state["sid"], trip_id, video_url, is_static)
    _warn_if_session_appeared(trip_id, warnings)
    _audit(trip_id, "add", {"position": state["pos"], "scene_id": state["sid"],
                            "scene_id_how": state["how"], "video_url": video_url,
                            "loc_renumbered": loc, "registry_error": reg_err}, user)
    return {"ok": True, "warnings": warnings, "structure": get_structure(trip_id)}


def swap_video(trip_id: str, index: int, video_url: str, rekey: bool,
               base: list[str], user, scene_id: str | None = None) -> dict:
    """Two distinct intents (memo rules 2 vs 3):
    rekey=False — same footage, new encode/URL fix: the sceneId is KEPT and the
    registry gains the videoId. rekey=True — a genuinely DIFFERENT scene now sits at
    this position: its atom id is assigned (supplied / reused-by-videoId / derived /
    minted); the old atom's registry use is dropped. Text is kept for the admin to
    rewrite; the localization entry stays index-keyed (renumber-now decision) so
    translations for the old atom are superseded when the text is re-authored."""
    _assert_no_active_session(trip_id)
    if not (video_url or "").strip():
        raise HTTPException(422, detail={"error": "bad_url", "detail": "empty videoUrl"})
    state: dict[str, Any] = {}

    def mutate(qt: list[dict]):
        if not (0 <= index < len(qt)):
            raise HTTPException(422, detail={"error": "bad_index", "detail": str(index)})
        scene = dict(qt[index] or {})
        old_sid = scene.get("sceneId")
        new_sid = how = None
        if rekey:
            new_sid, how = _new_scene_id(qt, video_url, scene_id)
            scene["sceneId"] = new_sid
        scene["videoUrl"] = video_url
        qt[index] = scene
        state.update(old_sid=old_sid, new_sid=new_sid, how=how,
                     is_static=bool(scene.get("isStaticImage")), qt_after=qt)
        return qt, None

    _structural_write(trip_id, base, mutate)
    old_sid, new_sid = state["old_sid"], state["new_sid"]
    warnings: list[str] = []
    if rekey:
        reg_errs = []
        if old_sid:
            reg_errs.append(_reg_safe(warnings, f"release usedBy of {old_sid}",
                                      _registry_drop_use, old_sid, trip_id,
                                      state["qt_after"]))
        reg_errs.append(_reg_safe(warnings, f"register use of {new_sid}",
                                  _registry_add_use, new_sid, trip_id, video_url,
                                  state["is_static"]))
        warnings.append(
            "Re-keyed: translations/glosses keyed to the OLD sceneId will fall back "
            "to English until the new scene's text is authored + recompiled.")
        _warn_if_session_appeared(trip_id, warnings)
        _audit(trip_id, "swap_rekey",
               {"index": index, "old_scene_id": old_sid, "new_scene_id": new_sid,
                "scene_id_how": state["how"], "video_url": video_url,
                "registry_errors": [e for e in reg_errs if e]}, user)
    else:
        reg_err = None
        if old_sid:
            reg_err = _reg_safe(warnings, f"add videoId to {old_sid}",
                                _registry_add_video, old_sid, video_url)
        _warn_if_session_appeared(trip_id, warnings)
        _audit(trip_id, "set_video", {"index": index, "scene_id": old_sid,
                                      "video_url": video_url,
                                      "registry_error": reg_err}, user)
    return {"ok": True, "warnings": warnings, "structure": get_structure(trip_id)}


def set_static_images(trip_id: str, index: int, filenames: list[str],
                      base: list[str], user) -> dict:
    """Edit a scene's flat-overlay list (filename refs — the JPGs themselves live in
    the Audio Generation / data trees and must exist there to render)."""
    _assert_no_active_session(trip_id)
    clean = [f.strip() for f in filenames if f and f.strip()]

    def mutate(qt: list[dict]):
        if not (0 <= index < len(qt)):
            raise HTTPException(422, detail={"error": "bad_index", "detail": str(index)})
        scene = dict(qt[index] or {})
        scene["staticImages"] = [{"filename": f} for f in clean]
        qt[index] = scene
        return qt, None

    _structural_write(trip_id, base, mutate)
    _audit(trip_id, "set_static_images", {"index": index, "filenames": clean}, user)
    return {"ok": True, "warnings": [], "structure": get_structure(trip_id)}


def set_categories(trip_id: str, categories: list[str], user) -> dict:
    """Direct tripCategories edit on the TripGroup (and the Trip doc when it carries
    its own copy). Unlike the description-derived path this sets the list verbatim —
    including the non-semantic level tags, so the FE shows the full live list."""
    clean = [c.strip() for c in categories if c and c.strip()]
    trip, _qt = _fetch(trip_id)       # 404 for a bogus trip BEFORE any write
    warnings: list[str] = []
    row = _active_session_row(trip_id)
    if row:
        # Not a desync risk (categories aren't index-addressed), so warn instead of
        # 409: that session's submit re-derives tripCategories from the description
        # and may overwrite this verbatim edit.
        warnings.append(
            f"Session {row['id']} is '{row['status']}' on this trip — its submit "
            "re-derives tripCategories from the description and may overwrite this "
            "edit.")
    client = fb_db()
    tg_id = tripgroup_id_for(trip_id)
    tg_ref = client.collection("TripGroups").document(tg_id)
    batch = client.batch()            # TG + Trip land atomically (or not at all)
    dirty = False
    if tg_ref.get().exists:
        batch.update(tg_ref, {"tripCategories": clean})
        dirty = True
    if "tripCategories" in trip:
        batch.update(client.collection("Trips").document(trip_id),
                     {"tripCategories": clean})
        dirty = True
    if dirty:
        batch.commit()
    _audit(trip_id, "set_categories", {"categories": clean}, user)
    return {"ok": True, "warnings": warnings, "structure": get_structure(trip_id)}
