"""Family-level TripGroup description review (docs/tripgroup-description-review-proposal.md).

Three stages, one row per TripGroup in `tripgroup_reviews`:
  pending_en   — admin (English check) edits the EN description + confirms categories,
                 with the family's scenes (thumbs/titles/descs) as context.
  translating  — machine translation EN→TL on a background thread via the `claude` CLI
                 (same headless transport as Gate 2 — no API key in this repo). Failures
                 park the item with `last_error` + a Retry button; nothing is blocked.
  pending_tl   — the language's reviewer confirms/edits the TL text against the EN.
  done         — written to staging.

Staging writes are targeted `.update()`s on the TripGroup doc:
  approve EN → descriptionHome + tripCategories (+ tooltip)
  approve TL → descriptionTarget (+ tooltip)
English-target families (Scotland/UK) skip B/C: approve EN also writes
descriptionTarget = the approved EN text and completes the item.

The `tooltip` is DERIVED and kept in sync on every description write — the rule is
ported from Scripts\\TripGroupDescFromTrip-Select.py (first sentence of target, blank
line, first sentence of home; single part when one side is empty).

Queue population: lazily, from every family in the current review manifest
(trips_to_review.json). Historical/completed families are deliberately NOT backfilled
yet (dave, 2026-08-14 — pending his call on which ones need it).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time

from fastapi import HTTPException

from . import config, db, thumbs

log = logging.getLogger("uvicorn.error")

STATUSES = ("pending_en", "translating", "pending_tl", "done")

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
CLAUDE_MODEL = os.environ.get("REVIEW_CLAUDE_MODEL", "sonnet")
CLAUDE_TIMEOUT_S = 600


# --------------------------------------------------------------------------- #
# Tooltip derivation — ported verbatim-in-spirit from
# Scripts\TripGroupDescFromTrip-Select.py (extract_first_sentence + build tooltip).
# --------------------------------------------------------------------------- #
def first_sentence(text: str) -> str:
    """Everything up to and including the first '.'; the whole text if none."""
    text = (text or "").strip()
    if "." in text:
        return text[: text.find(".") + 1].strip()
    return text


def build_tooltip(description_target: str, description_home: str) -> str:
    parts = [p for p in (first_sentence(description_target),
                         first_sentence(description_home)) if p]
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# TripGroup resolution — reverse index tripId → (tg_id, doc), TTL-cached (the
# TripGroup id is NOT derivable from a rung id: EU families key on the family sid).
# --------------------------------------------------------------------------- #
_TG_TTL_S = 600
_tg_lock = threading.Lock()
_tg_index: dict = {"at": 0.0, "by_trip": {}, "docs": {}}


def _tripgroup_index(force: bool = False) -> tuple[dict, dict]:
    """(tripId → tg_id, tg_id → {descriptionHome, descriptionTarget, tripCategories})."""
    with _tg_lock:
        if (not force and _tg_index["by_trip"]
                and time.time() - _tg_index["at"] < _TG_TTL_S):
            return _tg_index["by_trip"], _tg_index["docs"]
        from .staging import db as fb_db   # lazy: Firestore init on first use
        fs = fb_db()
        by_trip: dict[str, str] = {}
        docs: dict[str, dict] = {}
        for snap in fs.collection("TripGroups").select(
                ["trips", "descriptionHome", "descriptionTarget",
                 "tripCategories"]).stream():
            d = snap.to_dict() or {}
            docs[snap.id] = {
                "descriptionHome": d.get("descriptionHome") or "",
                "descriptionTarget": d.get("descriptionTarget") or "",
                "tripCategories": d.get("tripCategories") or [],
            }
            for entry in (d.get("trips") or []):
                tid = entry.get("tripId") if isinstance(entry, dict) else (
                    entry if isinstance(entry, str) else None)
                if tid:
                    by_trip.setdefault(tid, snap.id)
        _tg_index["by_trip"] = by_trip
        _tg_index["docs"] = docs
        _tg_index["at"] = time.time()
        return by_trip, docs


# --------------------------------------------------------------------------- #
# Category vocabulary + sibling check (country/playlist = TripLocations)
# --------------------------------------------------------------------------- #
_loc_lock = threading.Lock()
_loc_index: dict = {"at": 0.0, "by_tg": {}}


def _triplocations_index(force: bool = False) -> dict[str, list[tuple[str, str]]]:
    """tg_id → [(locationName, locationCountry), …] from staging TripLocations,
    TTL-cached like the TripGroup index. Best-effort: a failed stream returns the
    stale map (or empty) rather than raising — this only powers suggestions."""
    with _loc_lock:
        if (not force and _loc_index["by_tg"]
                and time.time() - _loc_index["at"] < _TG_TTL_S):
            return _loc_index["by_tg"]
        try:
            from .staging import db as fb_db
            fs = fb_db()
            by_tg: dict[str, list[tuple[str, str]]] = {}
            for snap in fs.collection("TripLocations").stream():
                d = snap.to_dict() or {}
                loc = (d.get("locationName") or snap.id,
                       d.get("locationCountry") or "")
                for tg_id in (d.get("trips") or []):
                    if isinstance(tg_id, str) and tg_id:
                        pairs = by_tg.setdefault(tg_id, [])
                        if loc not in pairs:
                            pairs.append(loc)
            _loc_index["by_tg"] = by_tg
            _loc_index["at"] = time.time()
        except Exception as e:  # noqa: BLE001 - suggestions only
            log.warning("tripdesc: TripLocations index failed: %s", e)
        return _loc_index["by_tg"]


def used_categories(tg_id: str | None = None) -> dict:
    """The live category vocabulary, with how many TripGroups carry each entry.

    With `tg_id`, the vocabulary is scoped to that group's COUNTRY (via its
    TripLocations): a Japanese trip must only offer categories that other Japanese
    trips already use, never Spain's (dave, 2026-08-21). A group in no TripLocation
    (or one whose locations carry no country) falls back to the global list —
    showing everything beats showing nothing while the location doc is pending."""
    _by_trip, docs = _tripgroup_index()
    scope = "all"
    if tg_id:
        by_tg = _triplocations_index()
        countries = {c for _n, c in by_tg.get(tg_id, []) if c}
        if countries:
            in_country = {sid for sid, locs in by_tg.items()
                          if any(c in countries for _n, c in locs)}
            docs = {sid: d for sid, d in docs.items() if sid in in_country}
            scope = "country:" + ",".join(sorted(countries))
    counts: dict[str, int] = {}
    canon: dict[str, str] = {}   # lower → first-seen spelling
    for d in docs.values():
        for c in d.get("tripCategories") or []:
            if not isinstance(c, str) or not c.strip():
                continue
            k = c.strip().lower()
            canon.setdefault(k, c.strip())
            counts[k] = counts.get(k, 0) + 1
    items = [{"name": canon[k], "count": n} for k, n in counts.items()]
    items.sort(key=lambda i: (-i["count"], i["name"].lower()))
    return {"categories": items, "scope": scope}


def _mention_snippet(text: str, needle: str) -> str | None:
    """The sentence (well, ±80 chars trimmed to word bounds) around the first
    case-insensitive mention of `needle` (or its naive singular) in `text`."""
    low = (text or "").lower()
    for probe in (needle.lower(), needle.lower().rstrip("s")):
        if len(probe) < 3:
            continue
        pos = low.find(probe)
        if pos < 0:
            continue
        start, end = max(0, pos - 80), min(len(text), pos + len(probe) + 80)
        snippet = text[start:end].strip()
        return ("…" if start > 0 else "") + snippet + ("…" if end < len(text) else "")
    return None


def category_check(tg_id: str, category: str) -> dict:
    """When the admin picks a category for `tg_id`: is it new to the vocabulary,
    and do any OTHER TripGroups in the same TripLocations (country/playlist)
    look like they should carry it too? A sibling 'fits' when its live EN/TL
    description mentions the category word but the category isn't applied.
    Read-only and informational — nothing is written anywhere."""
    cat = (category or "").strip()
    if not cat:
        raise HTTPException(422, detail={"error": "empty_category"})
    _by_trip, docs = _tripgroup_index()
    # 'New' is judged against the same country-scoped vocabulary the chips offer.
    used = {i["name"].lower() for i in used_categories(tg_id)["categories"]}
    by_tg = _triplocations_index()
    my_locs = by_tg.get(tg_id, [])
    sibling_ids = sorted({sid for sid, locs in by_tg.items()
                          if sid != tg_id and any(loc in my_locs for loc in locs)})
    siblings: list[dict] = []
    for sid in sibling_ids:
        d = docs.get(sid)
        if d is None:
            continue
        has = any(isinstance(c, str) and c.strip().lower() == cat.lower()
                  for c in d.get("tripCategories") or [])
        snippet = _mention_snippet(
            (d.get("descriptionHome") or "") + "\n" + (d.get("descriptionTarget") or ""),
            cat)
        if snippet is None:
            continue   # description never mentions it — nothing to flag
        siblings.append({"tg_id": sid, "has_category": has,
                         "mentions": snippet is not None, "snippet": snippet})
    # Likely fits first: mentions the word but doesn't carry the category.
    siblings.sort(key=lambda s: (not (s["mentions"] and not s["has_category"]),
                                 s["tg_id"].lower()))
    return {
        "category": cat,
        "is_new": cat.lower() not in used,
        "locations": [{"name": n, "country": c} for n, c in my_locs],
        "siblings": siblings,
    }


# --------------------------------------------------------------------------- #
# Seeding — every family in the current review manifest (NO historical backfill)
#
# Since 2026-08-21 a family whose TripGroup is already on PRODUCTION Firebase is
# NOT seeded: shipped ⇒ its description was checked when the group went live (dave).
# The signal is the committed snapshot prod_tripgroups.json, exported by
# Trello/export_review_trips.py on the WORKSTATION (the prod key never goes to the
# laptop). Missing/unreadable snapshot degrades to seed-everything, with a warning —
# a broken export must never silently suppress reviews. The explicit seed_trips()
# backfill path deliberately IGNORES the snapshot ("review this one anyway").
# --------------------------------------------------------------------------- #
_prod_warned = False


def _prod_tripgroup_ids() -> frozenset[str]:
    global _prod_warned
    try:
        data = json.loads(config.PROD_TRIPGROUPS_PATH.read_text(encoding="utf-8"))
        ids = frozenset(data.get("tripgroup_ids") or [])
        _prod_warned = False
        return ids
    except Exception as e:  # noqa: BLE001
        if not _prod_warned:
            log.warning("tripdesc: prod TripGroups snapshot unreadable (%s) — "
                        "seeding ALL manifest families: %s",
                        config.PROD_TRIPGROUPS_PATH, e)
            _prod_warned = True
        return frozenset()



def _seed_trip(tid: str, language: str, family_hint: str, by_trip: dict,
               docs: dict, now: float) -> str | None:
    """Insert a tripgroup_reviews row for the TripGroup that lists `tid`, unless one
    already exists. Returns the tg_id inserted, None if skipped (no TripGroup /
    already seeded). Existing rows are never touched."""
    tg_id = by_trip.get(tid)
    if tg_id is None:
        log.info("tripdesc: no TripGroup lists %s — skipped", tid)
        return None
    if db.query_one("SELECT tg_id FROM tripgroup_reviews WHERE tg_id=?", (tg_id,)):
        return None
    doc = docs.get(tg_id) or {}
    from .sessions import _level_family   # lazy: sessions is heavy at import
    _lvl, fam = _level_family(tid)
    if not language:
        from . import audio_core
        language = audio_core.language_of(tid)
    # UK/Scotland TripGroups (target = EN) hold their description in descriptionTarget
    # ONLY — descriptionHome is empty there, so an EN-target family seeds its English
    # text from the target field (found on the 5-family backfill, 2026-08-14).
    en = doc.get("descriptionHome") or (
        doc.get("descriptionTarget") if language == "English" else "") or ""
    db.execute(
        "INSERT INTO tripgroup_reviews(tg_id, language, family, rep_trip_id, "
        "status, en_text, en_original, tl_text, tl_original, categories_json, "
        "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (tg_id, language, family_hint or fam, tid, "pending_en",
         en, en,
         doc.get("descriptionTarget") or "", doc.get("descriptionTarget") or "",
         json.dumps(doc.get("tripCategories") or [], ensure_ascii=False),
         now, now))
    return tg_id


def seed_trips(trip_ids: list[str]) -> dict:
    """Explicit backfill: seed the TripGroups listing the given trips (used by
    scripts/backfill_tripdesc.py for the completed families dave confirmed —
    NOT called from any route). Deliberately ignores the production snapshot:
    this is the "review it anyway" escape hatch."""
    by_trip, docs = _tripgroup_index()
    now = time.time()
    seeded, skipped = [], []
    for tid in trip_ids:
        tg_id = _seed_trip(tid, "", "", by_trip, docs, now)
        (seeded if tg_id else skipped).append(tg_id or tid)
    return {"seeded": seeded, "skipped": skipped}


def seed_from_manifest() -> dict:
    """Ensure a tripgroup_reviews row exists for every TripGroup reachable from the
    manifest's trips. Existing rows are never touched (staging may have moved on, but
    a review-in-progress must not be clobbered). Returns counts; never raises on a
    single family — a manifest entry with no TripGroup is skipped with a log line."""
    try:
        data = json.loads(config.MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("tripdesc: manifest unreadable: %s", e)
        return {"seeded": 0, "skipped": 0}
    entries = data if isinstance(data, list) else (data.get("trips") or [])
    entries = [t for t in entries if t.get("trip_id")]
    by_trip, docs = _tripgroup_index()
    prod_ids = _prod_tripgroup_ids()
    seeded = skipped = skipped_prod = 0
    now = time.time()
    seen: set[str] = set()
    for t in entries:
        tid = t["trip_id"]
        tg_id = by_trip.get(tid)
        if tg_id is None:
            skipped += 1
            log.info("tripdesc: no TripGroup lists %s — skipped", tid)
            continue
        if tg_id in seen:
            continue
        seen.add(tg_id)
        if tg_id in prod_ids:
            skipped_prod += 1
            log.info("tripdesc: %s is on production — description already checked, "
                     "not seeded (explicit backfill_tripdesc.py overrides)", tg_id)
            continue
        if _seed_trip(tid, t.get("language") or "", t.get("family") or "",
                      by_trip, docs, now):
            seeded += 1
    return {"seeded": seeded, "skipped": skipped, "skipped_prod": skipped_prod}


# --------------------------------------------------------------------------- #
# Read model
# --------------------------------------------------------------------------- #
def _row_out(r) -> dict:
    return {
        "tg_id": r["tg_id"],
        "language": r["language"],
        "family": r["family"],
        "rep_trip_id": r["rep_trip_id"],
        "status": r["status"],
        "en_text": r["en_text"],
        "en_original": r["en_original"],
        "tl_text": r["tl_text"],
        "tl_original": r["tl_original"],
        "categories": json.loads(r["categories_json"] or "[]"),
        "last_error": r["last_error"],
        "en_by": r["en_by"], "en_at": r["en_at"],
        "tl_by": r["tl_by"], "tl_at": r["tl_at"],
        "updated_at": r["updated_at"],
        "en_target": r["language"] == "English",
    }


def _get_row(tg_id: str):
    row = db.query_one("SELECT * FROM tripgroup_reviews WHERE tg_id=?", (tg_id,))
    if row is None:
        raise HTTPException(404, detail={"error": "not_found", "detail": tg_id})
    return row


def _visible_to(user, r) -> bool:
    if user.is_admin:
        return True
    # Reviewers see ONLY their translation work: pending_tl items in their languages.
    return r["status"] == "pending_tl" and r["language"] in (user.languages or [])


def list_items(user) -> dict:
    rows = db.query("SELECT * FROM tripgroup_reviews ORDER BY "
                    "CASE status WHEN 'pending_en' THEN 0 WHEN 'translating' THEN 1 "
                    "WHEN 'pending_tl' THEN 2 ELSE 3 END, family, tg_id")
    items = [_row_out(r) for r in rows if _visible_to(user, r)]
    return {"items": items,
            "counts": {s: sum(1 for i in items if i["status"] == s) for s in STATUSES}}


def counts_for(user) -> dict:
    """Nav badge: what needs THIS user's action right now."""
    if user.is_admin:
        row = db.query_one(
            "SELECT COUNT(*) AS n FROM tripgroup_reviews WHERE status='pending_en'")
        return {"open": row["n"]}
    langs = user.languages or []
    if not langs:
        return {"open": 0}
    q = ",".join("?" for _ in langs)
    row = db.query_one(
        f"SELECT COUNT(*) AS n FROM tripgroup_reviews WHERE status='pending_tl' "
        f"AND language IN ({q})", tuple(langs))
    return {"open": row["n"]}


def get_item(tg_id: str, user) -> dict:
    row = _get_row(tg_id)
    if not _visible_to(user, row):
        raise HTTPException(403, detail={
            "error": "forbidden",
            "detail": "this description item is not assigned to you"})
    out = _row_out(row)
    if user.is_admin:
        out["scenes"] = _scene_context(row["rep_trip_id"])
    return out


def _scene_context(rep_trip_id: str) -> list[dict]:
    """The admin's checking context: per scene, thumbnail + ENGLISH title/description.
    Uses the family's representative trip doc — its scenes/videos are shared across the
    family's rungs, and non-EN trips carry the English source in the *En siblings.
    Best-effort: a missing staging doc yields an empty list, never a failed GET."""
    try:
        from .staging import get_trip
        trip = get_trip(rep_trip_id)
    except Exception as e:  # noqa: BLE001 (incl. stage9.common's SystemExit on a miss)
        log.warning("tripdesc: scene context unavailable for %s: %s", rep_trip_id, e)
        return []
    scenes: list[dict] = []
    for i, s in enumerate(trip.get("quickTrips") or []):
        try:
            thumb = thumbs.thumb_url_for_scene(s)
        except Exception:  # noqa: BLE001
            thumb = None
        scenes.append({
            "index": i,
            "thumb_url": thumb,
            "title": s.get("titleKeyEn") or s.get("titleKey") or "",
            "description": s.get("SceneDescEn") or s.get("SceneDesc") or "",
        })
    return scenes


# --------------------------------------------------------------------------- #
# Edits + workflow transitions
# --------------------------------------------------------------------------- #
def save(tg_id: str, user, en_text: str | None = None,
         categories: list[str] | None = None, tl_text: str | None = None) -> dict:
    row = _get_row(tg_id)
    status = row["status"]
    sets: dict = {}
    if en_text is not None or categories is not None:
        if not user.is_admin:
            raise HTTPException(403, detail={"error": "forbidden",
                                             "detail": "admin only"})
        if status not in ("pending_en",):
            raise HTTPException(409, detail={
                "error": "wrong_status",
                "detail": f"EN edits only while pending_en (now {status})"})
        if en_text is not None:
            sets["en_text"] = en_text
        if categories is not None:
            clean = [c.strip() for c in categories if c and c.strip()]
            sets["categories_json"] = json.dumps(clean, ensure_ascii=False)
    if tl_text is not None:
        if not _visible_to(user, row):
            raise HTTPException(403, detail={
                "error": "forbidden",
                "detail": "this description item is not assigned to you"})
        if status != "pending_tl":
            raise HTTPException(409, detail={
                "error": "wrong_status",
                "detail": f"TL edits only while pending_tl (now {status})"})
        sets["tl_text"] = tl_text
    if sets:
        cols = ", ".join(f"{k}=?" for k in sets)
        db.execute(f"UPDATE tripgroup_reviews SET {cols}, updated_at=? WHERE tg_id=?",
                   tuple(sets.values()) + (time.time(), tg_id))
    return _row_out(_get_row(tg_id))


def _write_tripgroup(tg_id: str, fields: dict) -> None:
    from .staging import db as fb_db
    from google.api_core.exceptions import NotFound
    try:
        fb_db().collection("TripGroups").document(tg_id).update(fields)
    except NotFound:
        raise HTTPException(409, detail={
            "error": "tripgroup_gone",
            "detail": f"TripGroups/{tg_id} no longer exists in staging"})


def approve_en(tg_id: str, admin) -> dict:
    row = _get_row(tg_id)
    if row["status"] != "pending_en":
        raise HTTPException(409, detail={
            "error": "wrong_status",
            "detail": f"approve-en requires pending_en (now {row['status']})"})
    en = (row["en_text"] or "").strip()
    if not en:
        raise HTTPException(422, detail={"error": "empty_description",
                                         "detail": "EN description is empty"})
    cats = json.loads(row["categories_json"] or "[]")
    en_target = row["language"] == "English"
    fields: dict = {"descriptionHome": en, "tripCategories": cats}
    if en_target:
        # Scotland/UK: target IS English — one approval writes both + finishes.
        fields["descriptionTarget"] = en
        fields["tooltip"] = build_tooltip(en, en)
    else:
        # TL not confirmed yet: tooltip from the still-live target + the new EN.
        fields["tooltip"] = build_tooltip(row["tl_text"] or row["tl_original"], en)
    _write_tripgroup(tg_id, fields)
    now = time.time()
    new_status = "done" if en_target else "translating"
    db.execute(
        "UPDATE tripgroup_reviews SET status=?, en_by=?, en_at=?, last_error='', "
        "updated_at=? WHERE tg_id=?",
        (new_status, admin.username, now, now, tg_id))
    if not en_target:
        _spawn_translation(tg_id)
    return _row_out(_get_row(tg_id))


def approve_tl(tg_id: str, user) -> dict:
    row = _get_row(tg_id)
    if row["status"] != "pending_tl":
        raise HTTPException(409, detail={
            "error": "wrong_status",
            "detail": f"approve-tl requires pending_tl (now {row['status']})"})
    if not _visible_to(user, row):
        raise HTTPException(403, detail={
            "error": "forbidden",
            "detail": "this description item is not assigned to you"})
    tl = (row["tl_text"] or "").strip()
    if not tl:
        raise HTTPException(422, detail={"error": "empty_description",
                                         "detail": "TL description is empty"})
    _write_tripgroup(tg_id, {
        "descriptionTarget": tl,
        "tooltip": build_tooltip(tl, row["en_text"] or ""),
    })
    now = time.time()
    db.execute(
        "UPDATE tripgroup_reviews SET status='done', tl_by=?, tl_at=?, updated_at=? "
        "WHERE tg_id=?", (user.username, now, now, tg_id))
    return _row_out(_get_row(tg_id))


def reopen(tg_id: str, admin) -> dict:
    """Admin escape hatch: put any item back to the EN stage (e.g. after a wrong
    approval or to force a re-translate). The staged writes already made are live —
    reopening only restarts the review, it does not revert staging."""
    _get_row(tg_id)
    db.execute("UPDATE tripgroup_reviews SET status='pending_en', last_error='', "
               "updated_at=? WHERE tg_id=?", (time.time(), tg_id))
    return _row_out(_get_row(tg_id))


# --------------------------------------------------------------------------- #
# Stage B — machine translation via the `claude` CLI (background thread)
# --------------------------------------------------------------------------- #
_TRANSLATE_PROMPT = """You are translating a VR language-learning trip description \
from English into {language}.

The description has a fixed structure: free prose, then metadata lines (a "Trip Type:" \
line listing categories, and usually a guide/duration line). Below are {n_examples} \
EXISTING published descriptions in {language} with their English sources — your \
translation MUST reuse EXACTLY the same translated phrasing these use for the \
metadata lines (the words for "Trip Type", the category names, the guide line, the \
duration phrasing). Translate the prose naturally in the same register.

{examples}

Translate the following English description into {language}. Reply with ONLY the \
translated description text — no preamble, no fences, no notes.

{text}"""


def _exemplars(language: str, limit: int = 3) -> list[tuple[str, str]]:
    """(en, tl) pairs from live TripGroups of the same language — the style/glossary
    anchors. Language identity comes from the seeded rows (a bare TripGroup doc does
    not say its language, so an unseeded doc is never used — anchoring a French
    translation on a Japanese description would be worse than no anchor)."""
    _by_trip, docs = _tripgroup_index()
    lang_rows = db.query(
        "SELECT tg_id FROM tripgroup_reviews WHERE language=?", (language,))
    out: list[tuple[str, str]] = []
    for r in lang_rows:
        d = docs.get(r["tg_id"]) or {}
        en, tl = d.get("descriptionHome") or "", d.get("descriptionTarget") or ""
        if en.strip() and tl.strip() and en.strip() != tl.strip():
            out.append((en, tl))
        if len(out) >= limit:
            break
    return out


def call_claude_translate(text: str, language: str) -> str:
    """Headless `claude -p` translation (Gate 2's transport — see
    scripts/claude_review.py:call_claude for the failure-mode history: the real error
    rides in the JSON envelope on STDOUT, not stderr)."""
    ex = _exemplars(language)
    examples = "\n\n".join(
        f"ENGLISH:\n{en}\n\n{language.upper()}:\n{tl}" for en, tl in ex) or "(none available)"
    prompt = _TRANSLATE_PROMPT.format(language=language, n_examples=len(ex),
                                      examples=examples, text=text)
    proc = subprocess.run(
        [CLAUDE_BIN, "-p", prompt, "--output-format", "json", "--model", CLAUDE_MODEL],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=CLAUDE_TIMEOUT_S)
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()
        try:
            env = json.loads(proc.stdout)
            detail = detail or str(env.get("result") or env)[:400]
        except (ValueError, TypeError):
            detail = detail or (proc.stdout or "").strip()[:400]
        if "not logged in" in detail.lower():
            detail += "  → run `claude /login` on this host (interactive)."
        raise RuntimeError(f"claude exited {proc.returncode}: {detail[:400]}")
    envelope = json.loads(proc.stdout)
    if isinstance(envelope, dict) and envelope.get("is_error"):
        msg = str(envelope.get("result") or envelope)[:400]
        if "not logged in" in msg.lower():
            msg += "  → run `claude /login` on this host (interactive)."
        raise RuntimeError(f"claude error envelope: {msg}")
    text_out = envelope.get("result") if isinstance(envelope, dict) else None
    if not text_out or not str(text_out).strip():
        raise RuntimeError(f"no result in claude output: {proc.stdout[:400]}")
    return re.sub(r"^```\w*\s*|\s*```$", "", str(text_out).strip())


def _run_translation(tg_id: str) -> None:
    row = db.query_one("SELECT * FROM tripgroup_reviews WHERE tg_id=?", (tg_id,))
    if row is None or row["status"] != "translating":
        return
    try:
        tl = call_claude_translate(row["en_text"], row["language"])
        db.execute(
            "UPDATE tripgroup_reviews SET tl_text=?, status='pending_tl', "
            "last_error='', updated_at=? WHERE tg_id=? AND status='translating'",
            (tl, time.time(), tg_id))
        log.info("tripdesc: translated %s → %s (%d chars)",
                 tg_id, row["language"], len(tl))
    except Exception as e:  # noqa: BLE001 - parked with the reason, retryable
        log.warning("tripdesc: translation failed for %s: %s", tg_id, e)
        # Same CAS as the success path: a reopen while the run was in flight moved the
        # row to pending_en (and cleared last_error) — a late failure must not stamp
        # "Translation failed" onto an item that is no longer translating.
        db.execute(
            "UPDATE tripgroup_reviews SET last_error=?, updated_at=? "
            "WHERE tg_id=? AND status='translating'",
            (str(e)[:800], time.time(), tg_id))


def _spawn_translation(tg_id: str) -> None:
    threading.Thread(target=_run_translation, args=(tg_id,), daemon=True,
                     name=f"tripdesc-translate-{tg_id}").start()


def retry_translate(tg_id: str, admin) -> dict:
    row = _get_row(tg_id)
    if row["status"] != "translating":
        raise HTTPException(409, detail={
            "error": "wrong_status",
            "detail": f"retry requires translating (now {row['status']})"})
    db.execute("UPDATE tripgroup_reviews SET last_error='', updated_at=? WHERE tg_id=?",
               (time.time(), tg_id))
    _spawn_translation(tg_id)
    return _row_out(_get_row(tg_id))
