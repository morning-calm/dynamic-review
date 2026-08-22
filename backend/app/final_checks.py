"""Final-check workflow (docs/post-approval-admin-spec.md §2) — admin-only.

One checklist row PER TRIP, appearing when its Trello card reaches lane 10 (the
manifest's `final_check` entries, exported alongside lanes 6/7 by
Trello/export_review_trips.py) or when an admin adds it by hand (final_check_extra).
Checks are stored at the level they are true at (final_checks: scope trip/group/
location), so ticking the family-level checks on one rung greens them on every
sibling — a single trip can be released without waiting for its family.

All seven checks have in-app tooling (spec phases 1-5, all built 2026-08-21):
description re-read, categories + ContentEnrichment country search, contentTitleKey
with prod drift, the TripLocation/pin editor, static-image timing + credits, the
keyword mic check, and the thumbnail panel. TripGroup staging writes are targeted
`.update()`s via tripdesc._write_tripgroup.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

from fastapi import HTTPException

from . import config, db, review_bus, tripdesc

log = logging.getLogger("uvicorn.error")

# (key, scope, label, tooling) — tooling=False would mean "tick by hand, no in-app
# tooling yet" (all seven have panels since the phase-2..5 build, so all True today;
# the flag stays for any future check that ships list-first).
# Order = display order = the lane-10 checklist order.
CHECKS: list[tuple[str, str, str, bool]] = [
    ("desc_reread",   "group",    "Description re-read", True),
    ("categories",    "group",    "Categories confirmed (+ cross-trip search)", True),
    ("title_key",     "group",    "TripGroup title key", True),
    ("trip_location", "location", "TripLocation + map pin", True),
    ("static_images", "trip",     "Static image timing + credits", True),
    ("keywords",      "trip",     "Keyword check", True),
    ("thumbnail",     "group",    "Thumbnail", True),
]
CHECK_KEYS = {c[0] for c in CHECKS}


# --------------------------------------------------------------------------- #
# Work list — manifest final_check entries + manual extras
# --------------------------------------------------------------------------- #
def _manifest() -> dict:
    try:
        return json.loads(config.MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("final_checks: manifest unreadable: %s", e)
        return {}


def _manifest_final(data: dict) -> list[dict]:
    """The lane-10/10b/11 entries. Empty until the export runs with final-lane
    support — the page then shows only the audit + manual additions."""
    return [t for t in (data.get("final_check") or []) if t.get("trip_id")]


def _language_of(tid: str) -> str:
    from . import audio_core   # lazy: heavy import
    try:
        return audio_core.language_of(tid)
    except Exception:  # noqa: BLE001
        return ""


def _resolve_tg(tid: str, by_trip: dict) -> tuple[str | None, bool]:
    """(tg_id, resolved) — the TripGroup listing this trip. Falls back to the naive
    strip-_EN contract when the index has no entry (doc may not exist yet)."""
    tg = by_trip.get(tid)
    if tg:
        return tg, True
    from .staging import tripgroup_id_for
    return tripgroup_id_for(tid), False


def _location_of(tg_id: str) -> str:
    """scope_id for 'location' checks: the group's first TripLocation name, or the
    tg_id itself when the group is in no location yet (still shared family-wide)."""
    locs = tripdesc._triplocations_index().get(tg_id) or []
    return locs[0][0] if locs else tg_id


def _scope_id(check_key: str, scope: str, tid: str, tg_id: str) -> str:
    if scope == "trip":
        return tid
    if scope == "location":
        return _location_of(tg_id)
    return tg_id


def _states() -> dict[tuple[str, str, str], dict]:
    out: dict[tuple[str, str, str], dict] = {}
    for r in db.query("SELECT * FROM final_checks"):
        out[(r["scope"], r["scope_id"], r["check_key"])] = {
            "state": r["state"], "by": r["by"], "at": r["at"], "note": r["note"]}
    return out


def _checks_for(tid: str, tg_id: str, states: dict) -> list[dict]:
    out = []
    for key, scope, label, tooling in CHECKS:
        sid = _scope_id(key, scope, tid, tg_id)
        st = states.get((scope, sid, key)) or {
            "state": "open", "by": "", "at": None, "note": ""}
        out.append({"key": key, "scope": scope, "scope_id": sid, "label": label,
                    "tooling": tooling, **st})
    return out


# --------------------------------------------------------------------------- #
# Release-readiness probes (the "ready in EVERY WAY" columns — all AUTOMATED,
# dave 2026-08-22; no human checkboxes).
# --------------------------------------------------------------------------- #
_GB_COUNTRIES = {"Scotland", "England", "Wales", "GreatBritain", "NorthernIreland"}
# Leveled-rung shapes that stage10b gives a recall quiz (native _EN / _Trip /
# bare native ids never; English leveled rungs only for UK families).
_QUIZ_EN_RE = re.compile(r"_(A12|B1)_EN$")
_QUIZ_TL_RE = re.compile(r"_(Beg(_N4)?|N3|N4|HSK12|HSK3|HSK4|TPK\d|A12|B1)_(JP|ZH|KO|FR|DE|IT|ES)$")

_quiz_cache: dict = {"at": 0.0, "have": set()}
_QUIZ_TTL_S = 600
_quiz_lock = threading.Lock()


def _recall_have() -> set[str]:
    """Trip ids with a non-empty staging recallQuiz (field-only sweep, TTL).
    Freshness is judged by the sweep CLOCK, not by a non-empty result — a
    legitimately-empty sweep must cache too, or every board load re-sweeps the
    whole Trips collection. A failed sweep leaves `at` alone (serve stale,
    retry on the next call). Single-flight: two admins landing on a cold board
    must not run two full collection sweeps."""
    with _quiz_lock:
        now = time.time()
        if now - _quiz_cache["at"] < _QUIZ_TTL_S:
            return _quiz_cache["have"]
        from .staging import db as fb_db
        have: set[str] = set()
        try:
            for snap in fb_db().collection("Trips").select(["recallQuiz"]).stream():
                if (snap.to_dict() or {}).get("recallQuiz"):
                    have.add(snap.id)
        except Exception as e:  # noqa: BLE001 — readiness must never break the board
            log.warning("final_checks: recallQuiz sweep failed: %s", e)
            return _quiz_cache["have"]
        _quiz_cache["have"], _quiz_cache["at"] = have, now
        return have


def invalidate_recall_sweep() -> None:
    """Reset the recallQuiz sweep clock so the next board load re-sweeps NOW —
    called when a stage-10b job applies (it just wrote the quizzes the sweep
    asserts are absent)."""
    _quiz_cache["at"] = 0.0


def _recall_eligible(tid: str, country: str) -> bool:
    """Would stage10b give this rung a recall quiz? EN leveled rungs only for UK
    families; TL leveled rungs always; native/_Trip/bare ids never. The ONE copy
    of the predicate — _recall_state judges with it and releases() pass 1 uses it
    to decide which rungs need a keyword probe."""
    if _QUIZ_EN_RE.search(tid):
        return country in _GB_COUNTRIES
    return bool(_QUIZ_TL_RE.search(tid))


# ---- per-rung probe cache (release_probe_cache table) ----------------------- #
# The board used to issue these single-doc Firestore gets SERIALLY inside the
# request — hundreds of round trips per load, re-done from cold every time the
# sweep TTL flushed the in-memory dict (the exact pathology trip_list_cache was
# built to kill on the trip list). Now: SQLite-materialised, served stale, missing
# rungs fetched in a thread pool, stale rungs re-probed off-request (single-flight),
# and stage-10b/4K tool jobs invalidate explicitly so their chips turn promptly.
_PROBE_TTL_S = 600
_probe_lock = threading.Lock()
_probe_mem: dict[str, dict] = {}
_probe_loaded = False
_PROBE_REFRESH_ACTIVE = False


def _probe_rows() -> dict[str, dict]:
    global _probe_loaded
    with _probe_lock:
        if not _probe_loaded:
            for r in db.query("SELECT * FROM release_probe_cache"):
                try:
                    kk = {int(k): v for k, v in
                          json.loads(r["keyword_keys"] or "{}").items()}
                except Exception:  # noqa: BLE001
                    kk = {}
                _probe_mem[r["trip_id"]] = {
                    "has_keyword": bool(r["has_keyword"]),
                    "static_scenes": r["static_scenes"],
                    "keyword_keys": kk,
                    "fetched_at": r["fetched_at"] or 0.0}
            _probe_loaded = True
        return _probe_mem


def _probe_fetch(tid: str) -> dict:
    """The actual staging read (network; call off the hot path where possible):
    has_keyword (≥1 keyword scene — quiz eligibility), static_scenes (count of
    isStaticImage — 4K eligibility), keyword_keys ({scene_index: questionKey}
    for keyword scenes — the A12→EN copy predicate)."""
    from .staging import db as fb_db
    out = {"has_keyword": False, "static_scenes": 0, "keyword_keys": {}}
    d = fb_db().collection("Trips").document(tid).get().to_dict() or {}
    for i, s in enumerate(d.get("quickTrips") or []):
        if s.get("isStaticImage"):
            out["static_scenes"] += 1
        if s.get("hasQuestion") and not [
                o for o in (s.get("questionOptionKeys") or []) if o]:
            out["has_keyword"] = True
            out["keyword_keys"][i] = (s.get("questionKey") or "").strip()
    return out


def _store_probe(tid: str, facts: dict) -> None:
    now = time.time()
    db.execute(
        "INSERT INTO release_probe_cache(trip_id,has_keyword,static_scenes,"
        "keyword_keys,fetched_at) VALUES(?,?,?,?,?) ON CONFLICT(trip_id) DO UPDATE "
        "SET has_keyword=excluded.has_keyword, static_scenes=excluded.static_scenes, "
        "keyword_keys=excluded.keyword_keys, fetched_at=excluded.fetched_at",
        (tid, int(facts["has_keyword"]), facts["static_scenes"],
         json.dumps(facts["keyword_keys"]), now))
    with _probe_lock:
        _probe_mem[tid] = {**facts, "fetched_at": now}


def invalidate_probes(trip_ids: list[str] | None = None) -> None:
    """Drop cached probe rows so the next board load re-probes NOW (rather than at
    TTL expiry). None = everything. Called when a stage-10b / 4K / publish tool job
    APPLIES — the job just changed exactly what these probes assert."""
    with _probe_lock:
        if trip_ids is None:
            _probe_mem.clear()
        else:
            for tid in trip_ids:
                _probe_mem.pop(tid, None)
    if trip_ids is None:
        db.execute("DELETE FROM release_probe_cache")
    else:
        for tid in trip_ids:
            db.execute("DELETE FROM release_probe_cache WHERE trip_id=?", (tid,))


def _refresh_probes_async(tids: list[str]) -> None:
    """Background single-flight re-probe of stale rungs (the trip-list refresher
    pattern): the requester serves stale immediately, freshness lands off-request."""
    global _PROBE_REFRESH_ACTIVE
    if not tids:
        return
    with _probe_lock:
        if _PROBE_REFRESH_ACTIVE:
            return
        _PROBE_REFRESH_ACTIVE = True

    def run() -> None:
        global _PROBE_REFRESH_ACTIVE
        t0 = time.time()
        try:
            _fetch_probes_parallel(tids)
            log.info("final_checks: probe refresh: %d rungs in %.1fs",
                     len(tids), time.time() - t0)
        except Exception as e:  # noqa: BLE001 — background; never crash the server
            log.warning("final_checks: probe refresh failed: %s", e)
        finally:
            with _probe_lock:
                _PROBE_REFRESH_ACTIVE = False

    threading.Thread(target=run, name="release-probe-refresh", daemon=True).start()


def _fetch_probes_parallel(tids: list[str]) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from .staging import db as fb_db
    try:
        fb_db()   # unguarded lazy singleton — init alone before concurrent gets
    except Exception:  # noqa: BLE001 — per-probe fetches surface their own failures
        pass

    def one(tid: str) -> None:
        try:
            _store_probe(tid, _probe_fetch(tid))
        except Exception as e:  # noqa: BLE001 — a miss stays a miss, retried later
            log.warning("final_checks: probe fetch failed for %s: %s", tid, e)

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(one, tids))


def _prewarm_probes(tids: set[str]) -> None:
    """Serve-stale contract for a board load: never-seen rungs are fetched NOW in
    parallel (they'd otherwise render wrong), stale rungs serve cached and re-probe
    in the background."""
    rows = _probe_rows()
    now = time.time()
    missing = [t for t in tids if t not in rows]
    if missing:
        _fetch_probes_parallel(missing)
    stale = [t for t in tids
             if (r := rows.get(t)) and now - r["fetched_at"] >= _PROBE_TTL_S]
    _refresh_probes_async(stale)


def _doc_probe(tid: str) -> dict:
    """Cached scene facts for the readiness columns (see _probe_fetch). Serves any
    cached row regardless of age — staleness is the prewarm/refresh machinery's
    job; a full miss (rung never prewarmed) fetches synchronously once."""
    rows = _probe_rows()
    hit = rows.get(tid)
    if hit is not None:
        return hit
    facts = {"has_keyword": False, "static_scenes": 0, "keyword_keys": {}}
    try:
        facts = _probe_fetch(tid)
    except Exception:  # noqa: BLE001 — degrade like the old probe: na/0, uncached
        return facts
    _store_probe(tid, facts)
    return facts


def _has_keyword_scene(tid: str) -> bool:
    return _doc_probe(tid)["has_keyword"]


def _four_k_state(tid: str, ledger: dict) -> str:
    """'built' (on the static4k ledger) | 'missing' (has isStaticImage scenes,
    no ledger entry) | 'na' (no static scenes — which is also what a failed
    probe degrades to: static_scenes=0, uncached, retried next sweep)."""
    if tid in ledger:
        return "built"
    return "missing" if _doc_probe(tid)["static_scenes"] else "na"


def _keyword_copy_state(en_tid: str, leveled_tid: str | None) -> str:
    """A12→EN keyword-copy readiness for a TL family's native _EN rung:
    'copied' when every leveled-rung keyword scene's questionKey matches the EN
    rung at the same index (what CopyKeywordsfromBegtoEn writes), 'missing'
    otherwise, 'na' when there is no leveled sibling / no keywords."""
    if not leveled_tid:
        return "na"
    src = _doc_probe(leveled_tid)["keyword_keys"]
    if not src:
        return "na"
    dst = _doc_probe(en_tid)["keyword_keys"]
    return "copied" if all(dst.get(i) == k for i, k in src.items() if k) else "missing"


def _finalised_of(fin: dict | None, completed_at: float | None) -> str | None:
    """'shipped' | 'restale' (re-approved since the last finalise) | None.
    Thin wrapper over sessions._finalised_state for rungs with a completion."""
    if not fin or completed_at is None:
        return None
    from . import sessions
    state, _at = sessions._finalised_state(fin, completed_at)
    return state


def _recall_state(tid: str, country: str, have: set[str]) -> str:
    """'present' | 'missing' (eligible, no quiz yet — run stage 10b) | 'na'."""
    if tid in have:
        return "present"
    if _recall_eligible(tid, country) and _has_keyword_scene(tid):
        return "missing"
    return "na"


_loc_full_cache: dict = {"at": 0.0, "by_tg": {}}
_loc_full_lock = threading.Lock()


def _loc_full_index() -> dict[str, list[tuple[str, str, str]]]:
    """tg_id -> [(loc_DOC_ID, locationName, locationCountry), …], TTL-cached.

    Exists because the release wizard's publish_pin bus job must carry the
    TripLocation DOC ID (`publish_trips_cli.py --pin LOCID`), while
    tripdesc._triplocations_index deliberately stores only the display
    name+country — and ~1 in 6 staging TripLocations has locationName != id
    (Ainsa -> "Aragon", Alps_DE/FR/IT -> "Alps"), so the name is NOT a usable
    pin target. Same best-effort contract as the tripdesc index: a failed
    stream serves the stale map. Single-flight (lock) like the tripdesc indexes."""
    with _loc_full_lock:
        if (time.time() - _loc_full_cache["at"] < _QUIZ_TTL_S
                and _loc_full_cache["by_tg"]):
            return _loc_full_cache["by_tg"]
        try:
            from .staging import db as fb_db
            by_tg: dict[str, list[tuple[str, str, str]]] = {}
            for snap in fb_db().collection("TripLocations").select(
                    ["locationName", "locationCountry", "trips"]).stream():
                d = snap.to_dict() or {}
                entry = (snap.id, d.get("locationName") or snap.id,
                         d.get("locationCountry") or "")
                for tg_id in (d.get("trips") or []):
                    if isinstance(tg_id, str) and tg_id:
                        pairs = by_tg.setdefault(tg_id, [])
                        if entry not in pairs:
                            pairs.append(entry)
            _loc_full_cache["by_tg"] = by_tg
            _loc_full_cache["at"] = time.time()
        except Exception as e:  # noqa: BLE001 — readiness must never break the board
            log.warning("final_checks: TripLocations id index failed: %s", e)
        return _loc_full_cache["by_tg"]


def _pending_deltas() -> set[str]:
    """Trips with an unconsumed ``_delta/<cid>.json`` — changed clips awaiting a
    reviewer. Such a trip is BACK IN REVIEW: its final check must not proceed (and
    it is not publishable) until the delta session is approved, which deletes the
    manifest. Best-effort ({} on R2 trouble — the list stays usable)."""
    from . import deltas
    try:
        return {e["trip_id"] for e in deltas.list_all()}
    except Exception:  # noqa: BLE001
        return set()


def list_items() -> dict:
    data = _manifest()
    entries = _manifest_final(data)
    listed = {t["trip_id"] for t in entries}
    for r in db.query("SELECT * FROM final_check_extra ORDER BY added_at"):
        if r["trip_id"] not in listed:
            listed.add(r["trip_id"])
            entries.append({"trip_id": r["trip_id"], "lane": "manual",
                            "added_by": r["added_by"]})
    by_trip, _docs = tripdesc._tripgroup_index()
    states = _states()
    pending = _pending_deltas()
    rows: list[dict] = []
    for t in entries:
        tid = t["trip_id"]
        tg_id, tg_ok = _resolve_tg(tid, by_trip)
        checks = _checks_for(tid, tg_id or "", states)
        rows.append({
            "pending_delta": tid in pending,
            "trip_id": tid,
            "lane": t.get("lane") or "manual",
            "family": t.get("family") or "",
            "language": _language_of(tid),
            "tg_id": tg_id,
            "tg_resolved": tg_ok,
            "card_url": t.get("card_url") or "",
            "added_by": t.get("added_by") or "",
            "done": sum(1 for c in checks if c["state"] == "done"),
            "total": len(checks),
            "checks": {c["key"]: c["state"] for c in checks},
        })
    return {"items": rows,
            "audit": _audit(listed, data),
            "manifest_has_final": "final_check" in data}


def releases() -> dict:
    """Group-centric release board for the Publisher (dave, 2026-08-22): every
    family with release activity (a final-check row, a manual addition, or a
    completed review), each staging rung's pipeline position, and whether the
    production TripGroup exists (publish UPDATES it) or not (publish CREATES it,
    partial — only released rungs in trips[]). Live-rung membership comes from
    the committed prod_tripgroups.json snapshot (export_review_trips)."""
    data = _manifest()
    fc_by_trip = {t["trip_id"]: t for t in _manifest_final(data)}
    extras = {r["trip_id"] for r in db.query("SELECT trip_id FROM final_check_extra")}
    review_lane = {t["trip_id"]: str(t.get("lane") or "")
                   for t in (data.get("trips") or []) if t.get("trip_id")}
    completed: dict[str, str] = {}
    completed_at: dict[str, float] = {}
    for r in db.query("SELECT trip_id, method, completed_at FROM completed_trips"):
        completed[r["trip_id"]] = r["method"]
        completed_at[r["trip_id"]] = r["completed_at"]
    fin_map = review_bus.get_finalised_snapshot()   # best-effort; {} = none
    recall_have = _recall_have()
    four_k_ledger = review_bus.get_static4k_snapshot()
    try:
        prod = json.loads(config.PROD_TRIPGROUPS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        prod = {}
    prod_by_group = prod.get("trips_by_group") or {}
    prod_ids = set(prod.get("tripgroup_ids") or [])
    by_trip, docs = tripdesc._tripgroup_index()
    states = _states()
    loc_index = tripdesc._triplocations_index()
    loc_full = _loc_full_index()   # adds the TripLocation DOC ID (pin target)
    pending = _pending_deltas()

    # Pass 1 — resolve every family and work out exactly which rungs the readiness
    # columns will probe, so the probes run through the cache machinery in one
    # parallel prewarm instead of serially inside the rung loop (the board's old
    # dominant cost: hundreds of sequential Firestore gets per load).
    interest = set(fc_by_trip) | extras | set(completed)
    fams: list[dict] = []
    seen_tg: set[str] = set()
    need_probe: set[str] = set()
    for tid in interest:
        tg_id, _ok = _resolve_tg(tid, by_trip)
        if not tg_id or tg_id in seen_tg:
            continue
        seen_tg.add(tg_id)
        rung_ids = (docs.get(tg_id) or {}).get("trips") or [tid]
        locs = loc_index.get(tg_id) or []
        country = locs[0][1] if locs else ""
        # A12→EN keyword-copy predicate (TL families only): the native _EN rung
        # vs the family's first leveled rung.
        is_gb = country in _GB_COUNTRIES
        en_rung = next((r for r in rung_ids if r.endswith("_EN")
                        and not _QUIZ_EN_RE.search(r)), None)
        leveled = next((r for r in rung_ids if _QUIZ_TL_RE.search(r)), None)
        fams.append({"tg_id": tg_id, "rung_ids": rung_ids, "country": country,
                     "is_gb": is_gb, "en_rung": en_rung, "leveled": leveled})
        for rid in rung_ids:
            if rid not in four_k_ledger:
                need_probe.add(rid)                       # _four_k_state
            if rid not in recall_have and _recall_eligible(rid, country):
                need_probe.add(rid)                       # _recall_state
            if not is_gb and rid == en_rung and leveled:
                need_probe.update((rid, leveled))         # _keyword_copy_state
    _prewarm_probes(need_probe)

    # Stage-10b / tool / publish jobs bucketed per family, so the board can show
    # queued/running/done chips inline (dave, 2026-08-22). Best-effort — the board
    # must render with the job inbox unreachable.
    try:
        recent_jobs = review_bus.list_jobs()
    except Exception:  # noqa: BLE001
        recent_jobs = []

    groups: dict[str, dict] = {}
    for fam in fams:
        tg_id = fam["tg_id"]
        rung_ids = fam["rung_ids"]
        country, is_gb = fam["country"], fam["is_gb"]
        en_rung, leveled = fam["en_rung"], fam["leveled"]
        live = set(prod_by_group.get(tg_id) or [])
        rungs = []
        for rid in rung_ids:
            checks = _checks_for(rid, tg_id, states)
            done = sum(1 for c in checks if c["state"] == "done")
            in_fc = rid in fc_by_trip or rid in extras or done > 0
            if rid in live:
                status = "live"
            elif rid in pending:
                # changed clips await a delta re-review — NOT publishable, and the
                # final check is on hold until the reviewer approves.
                status = "re_review"
            elif in_fc and done == len(checks):
                status = "ready"
            elif in_fc:
                status = "final_check"
            elif rid in completed:
                status = "reviewed"
            elif rid in review_lane:
                status = "in_review"
            else:
                status = "not_started"
            rungs.append({
                "trip_id": rid,
                "status": status,
                # Kept separate from status: a LIVE rung with changed clips awaiting
                # delta re-review stays LIVE (players see the old audio) but the FE
                # marks the hold on the badge (dave, 2026-08-22).
                "pending_delta": rid in pending,
                "checks_done": done,
                "checks_total": len(checks),
                "review_lane": review_lane.get(rid) or "",
                "completed_method": completed.get(rid) or "",
                "card_url": (fc_by_trip.get(rid) or {}).get("card_url") or "",
                # Readiness columns (all machine-derived): Stage-9 finalise state
                # from the bus ledger (subtitles/ogg/S3/re-encode/enrich covered),
                # recall-quiz state per the three-part eligibility rule.
                "finalised": _finalised_of(fin_map.get(rid),
                                           completed_at.get(rid)),
                "recall_quiz": _recall_state(rid, country, recall_have),
                "four_k": _four_k_state(rid, four_k_ledger),
                "keyword_copy": (_keyword_copy_state(rid, leveled)
                                 if (not is_gb and rid == en_rung) else "na"),
            })
        # tg_id: stage-10b tool / add_to_location jobs; rung cids: publish /
        # publish_docs; TripLocation doc ids: publish_pin.
        fam_ids = ({tg_id} | set(rung_ids)
                   | {i for i, _n, _c in (loc_full.get(tg_id) or [])})
        groups[tg_id] = {
            "tg_id": tg_id,
            "in_prod": tg_id in prod_ids,
            "live_count": sum(1 for r in rungs if r["status"] == "live"),
            "ready_count": sum(1 for r in rungs if r["status"] == "ready"),
            # Recent bus jobs targeting this family (the tg_id for stage-10b tool
            # jobs / add_to_location, a rung cid for publish/publish_docs) — the
            # inline chips; the full inbox stays at the bottom of the page.
            "jobs": [{"id": j.get("id"), "kind": j.get("kind"),
                      "trip_id": j.get("trip_id"), "status": j.get("status"),
                      "note": j.get("note") or "",
                      "requested_at": j.get("requested_at")}
                     for j in recent_jobs if j.get("trip_id") in fam_ids],
            # `id` = the TripLocation DOC ID — the publish_pin job target (the
            # display name diverges from it on ~1 in 6 docs). Falls back to the
            # name-only tripdesc index when the id sweep has never succeeded.
            "locations": ([{"id": i, "name": n, "country": c}
                           for i, n, c in (loc_full.get(tg_id) or [])]
                          or [{"id": n, "name": n, "country": c}
                              for n, c in (loc_index.get(tg_id) or [])]),
            "rungs": rungs,
        }
    ordered = sorted(groups.values(),
                     key=lambda g: (-g["ready_count"], g["tg_id"].lower()))
    return {"groups": ordered,
            "prod_snapshot_at": prod.get("generated_at") or "",
            "prod_snapshot_has_rungs": bool(prod_by_group)}


def _short(v, n: int = 220) -> str:
    s = json.dumps(v, ensure_ascii=False, default=str) if not isinstance(v, str) else v
    return s if len(s) <= n else s[: n - 1] + "…"


def group_diff(tg_id: str) -> dict:
    """Staging→prod field diff for one TripGroup card on the Releases board.
    Prod state comes from the workstation-exported per-trip snapshot
    (`publish_inbox.py snapshot` — the app never holds prod creds), so the diff is
    as fresh as the newest rung snapshot; the payload says which and when."""
    from .staging import db as fb_db
    snap_doc = fb_db().collection("TripGroups").document(tg_id).get()
    if not snap_doc.exists:
        raise HTTPException(404, detail={"error": "no_staging_group", "detail": tg_id})
    stg = snap_doc.to_dict() or {}
    # A group ABSENT from the committed prod export needs no per-trip snapshot at
    # all — publish_inbox writes NO snapshot for a not-in-prod rung, so demanding
    # one here was an unsatisfiable hint (dave hit it on the Milne A12 release).
    try:
        exp = json.loads(config.PROD_TRIPGROUPS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        exp = {}
    if exp.get("tripgroup_ids") and tg_id not in exp["tripgroup_ids"]:
        return {"tg_id": tg_id, "snapshot_trip": None, "snapshot_at": None,
                "prod_missing": True, "changed": [],
                "hint": "TripGroup not in production — the first rung publish "
                        "CREATES it (partial: just that rung)"}
    # Newest rung snapshot that carries a trip_group. A WITHHELD rung's snapshot has
    # trip_group=None (the prod group doesn't list that rung), so prefer a
    # group-bearing snapshot over a newer group-less one — but keep the newest
    # group-less snapshot as a fallback so an all-withheld / brand-new family
    # reports prod_missing instead of "no snapshot".
    best: dict | None = None
    best_tid = ""
    newest: dict | None = None
    newest_tid = ""
    for entry in (stg.get("trips") or []):
        tid = entry.get("tripId") if isinstance(entry, dict) else None
        if not tid:
            continue
        s = review_bus.prod_snapshot(tid)
        if not s:
            continue
        if newest is None or (s.get("fetched_at") or 0) > (newest.get("fetched_at") or 0):
            newest, newest_tid = s, tid
        if s.get("trip_group") is not None and (
                best is None or (s.get("fetched_at") or 0) > (best.get("fetched_at") or 0)):
            best, best_tid = s, tid
    if best is None and newest is not None:
        best, best_tid = newest, newest_tid
    if best is None:
        # Self-serve on the workstation: the group IS in prod but no rung has a
        # snapshot yet — fetch one ourselves (publish_inbox.py holds the prod key)
        # instead of telling the admin to run a tool by hand. Target a LIVE rung:
        # _snapshot_one writes nothing for a not-in-prod trip.
        live = (exp.get("trips_by_group") or {}).get(tg_id) or []
        if live and os.environ.get("REVIEW_APP_PUBLISHER") == "1":
            import subprocess
            import sys as _sys
            try:
                subprocess.run(
                    [_sys.executable, str(config.SCRIPTS_ROOT / "publish_inbox.py"),
                     "snapshot", live[0]],
                    cwd=str(config.SCRIPTS_ROOT), capture_output=True,
                    timeout=120, check=False)
            except Exception as e:  # noqa: BLE001 — fall through to the hint
                log.warning("group_diff: auto-snapshot of %s failed: %s", live[0], e)
            s = review_bus.prod_snapshot(live[0])
            if s and s.get("trip_group") is not None:
                best, best_tid = s, live[0]
    if best is None:
        return {"tg_id": tg_id, "snapshot_trip": None, "snapshot_at": None,
                "prod_missing": None, "changed": [],
                "hint": "no prod snapshot for any rung (and this instance can't "
                        "fetch one — workstation Publisher only): run the tool "
                        "'Refresh prod snapshot' on a live rung"}
    prod = best.get("trip_group")
    changed: list[dict] = []
    if prod is None:
        return {"tg_id": tg_id, "snapshot_trip": best_tid,
                "snapshot_at": best.get("fetched_at"), "prod_missing": True,
                "changed": [], "hint": "TripGroup not in production at snapshot "
                "time — the first rung publish CREATES it (partial)"}
    # The snapshot's trip_group is the REDUCED entry publish_inbox exports (its id +
    # the drift fields, + trips[] since 2026-08-22), NOT the full doc — compare only
    # the fields it actually carries, or every unsnapshotted staging field (levels,
    # tooltip, thumbnailTextureId, …) would read as drift.
    for k in sorted(set(prod) - {"id", "trips"}):
        sv, pv = stg.get(k), prod.get(k)
        if sv is None and isinstance(pv, (str, list)):
            sv = type(pv)()   # the snapshot normalises a missing field to ''/[]
        if sv == pv:
            continue
        changed.append({"field": k, "staging": _short(stg.get(k)),
                        "prod": _short(pv)})
    # trips[]: per-rung membership + text. A pre-2026-08-22 snapshot has no trips[]
    # — fall back to the committed prod export for MEMBERSHIP (text drift then needs
    # a re-snapshot).
    hint = ""
    stg_by = {t.get("tripId"): t for t in (stg.get("trips") or []) if isinstance(t, dict)}
    prod_trips = prod.get("trips")
    if isinstance(prod_trips, list):
        prod_by = {t.get("tripId"): t for t in prod_trips if isinstance(t, dict)}
    else:
        prod_by = {tid: None
                   for tid in ((exp.get("trips_by_group") or {}).get(tg_id) or [])}
        hint = ("snapshot predates the trips[] export — membership from "
                "prod_tripgroups.json; re-run 'Refresh prod snapshot' for "
                "per-rung text drift")
    for tid in stg_by:
        if tid not in prod_by:
            changed.append({"field": f"trips[{tid}]", "staging": "listed (rung button)",
                            "prod": "NOT in production trips[] (unreleased or withheld)"})
            continue
        if prod_by[tid] is None:
            continue   # membership-only fallback — no text to compare
        for f in ("buttonText", "tooltipText"):
            if stg_by[tid].get(f) != prod_by[tid].get(f):
                changed.append({"field": f"trips[{tid}].{f}",
                                "staging": _short(stg_by[tid].get(f)),
                                "prod": _short(prod_by[tid].get(f))})
    for tid in prod_by:
        if tid not in stg_by:
            changed.append({"field": f"trips[{tid}]", "staging": "NOT in staging (retired?)",
                            "prod": "listed"})
    return {"tg_id": tg_id, "snapshot_trip": best_tid,
            "snapshot_at": best.get("fetched_at"), "prod_missing": False,
            "changed": changed, "hint": hint}


def _audit(listed: set[str], data: dict) -> list[dict]:
    """Completed trips that sit on NO lane-10+ card (and aren't manually added) —
    the belt-and-braces list, mirroring the export's lane-6/7 audit. A trip whose
    card already reached lane 12 (live) is shown with that context, not hidden:
    completion without a final check is exactly what this surfaces."""
    lanes = data.get("card_lanes") or {}
    out = []
    for r in db.query("SELECT trip_id, method, completed_at FROM completed_trips "
                      "ORDER BY completed_at DESC"):
        tid = r["trip_id"]
        if tid in listed:
            continue
        out.append({"trip_id": tid, "method": r["method"],
                    "completed_at": r["completed_at"],
                    "card_lane": lanes.get(tid) or ""})
    return out


def count_open() -> int:
    data = _manifest()
    entries = _manifest_final(data)
    listed = {t["trip_id"] for t in entries}
    extra = [r["trip_id"] for r in db.query("SELECT trip_id FROM final_check_extra")]
    tids = listed | set(extra)
    if not tids:
        return 0
    by_trip, _docs = tripdesc._tripgroup_index()
    states = _states()
    open_n = 0
    for tid in tids:
        tg_id, _ok = _resolve_tg(tid, by_trip)
        checks = _checks_for(tid, tg_id or "", states)
        if any(c["state"] != "done" for c in checks):
            open_n += 1
    return open_n


def start(trip_id: str, admin) -> dict:
    """Manual 'start final check' for a trip on no lane-10+ card."""
    tid = (trip_id or "").strip()
    if not tid:
        raise HTTPException(422, detail={"error": "empty_trip_id"})
    db.execute("INSERT OR IGNORE INTO final_check_extra(trip_id, added_by, added_at) "
               "VALUES(?,?,?)", (tid, admin.username, time.time()))
    return {"trip_id": tid, "started": True}


# --------------------------------------------------------------------------- #
# Item detail — the per-trip checklist with each check's data
# --------------------------------------------------------------------------- #
def _tripgroup_doc(tg_id: str) -> dict | None:
    """Fresh read of the fields the checks edit (the TTL index may be 10 min old)."""
    try:
        from .staging import db as fb_db
        snap = fb_db().collection("TripGroups").document(tg_id).get()
        return (snap.to_dict() or {}) if snap.exists else None
    except Exception as e:  # noqa: BLE001
        log.warning("final_checks: TripGroup read failed for %s: %s", tg_id, e)
        return None


def get_item(trip_id: str) -> dict:
    by_trip, _docs = tripdesc._tripgroup_index()
    tg_id, tg_ok = _resolve_tg(trip_id, by_trip)
    doc = _tripgroup_doc(tg_id) if tg_id else None
    states = _states()
    checks = _checks_for(trip_id, tg_id or "", states)

    desc_row = db.query_one(
        "SELECT status FROM tripgroup_reviews WHERE tg_id=?", (tg_id,)) if tg_id else None

    # Prod comparison for the title key: per-trip snapshot; trip_group is present once
    # the workstation's publish_inbox.py snapshot includes it (2026-08-21+ exports).
    snap = review_bus.prod_snapshot(trip_id)
    prod_group = (snap or {}).get("trip_group") or None
    prod_trip = (snap or {}).get("trip") or {}

    locations = [{"name": n, "country": c}
                 for n, c in (tripdesc._triplocations_index().get(tg_id) or [])]

    return {
        "trip_id": trip_id,
        "tg_id": tg_id,
        "tg_resolved": tg_ok,
        "tg_exists": doc is not None,
        "pending_delta": trip_id in _pending_deltas(),
        "language": _language_of(trip_id),
        "locations": locations,
        "checks": checks,
        "description": {
            "home": (doc or {}).get("descriptionHome") or "",
            "target": (doc or {}).get("descriptionTarget") or "",
            "tripdesc_status": desc_row["status"] if desc_row else None,
        },
        "categories": (doc or {}).get("tripCategories") or [],
        "title_key": {
            "staging": (doc or {}).get("contentTitleKey") or "",
            "prod_group": (prod_group or {}).get("contentTitleKey"),
            "prod_trip": prod_trip.get("contentTitleKey"),
            "snapshot_at": (snap or {}).get("fetched_at"),
        },
    }


def set_check(trip_id: str, check_key: str, state: str, note: str, admin) -> dict:
    if check_key not in CHECK_KEYS:
        raise HTTPException(422, detail={"error": "bad_check_key", "detail": check_key})
    if state not in ("open", "done"):
        raise HTTPException(422, detail={"error": "bad_state", "detail": state})
    by_trip, _docs = tripdesc._tripgroup_index()
    tg_id, _ok = _resolve_tg(trip_id, by_trip)
    scope = next(c[1] for c in CHECKS if c[0] == check_key)
    sid = _scope_id(check_key, scope, trip_id, tg_id or "")
    db.execute(
        "INSERT INTO final_checks(scope, scope_id, check_key, state, by, at, note) "
        "VALUES(?,?,?,?,?,?,?) ON CONFLICT(scope, scope_id, check_key) DO UPDATE SET "
        "state=excluded.state, by=excluded.by, at=excluded.at, note=excluded.note",
        (scope, sid, check_key, state, admin.username, time.time(), note or ""))
    return {"key": check_key, "scope": scope, "scope_id": sid, "state": state,
            "by": admin.username, "note": note or ""}


# --------------------------------------------------------------------------- #
# Check 2/3 writes — targeted TripGroup updates (same discipline as tripdesc)
# --------------------------------------------------------------------------- #
def _require_tg(trip_id: str) -> str:
    by_trip, _docs = tripdesc._tripgroup_index()
    tg_id, _ok = _resolve_tg(trip_id, by_trip)
    if not tg_id or _tripgroup_doc(tg_id) is None:
        raise HTTPException(409, detail={
            "error": "tripgroup_missing",
            "detail": f"no staging TripGroup found for {trip_id}"})
    return tg_id


def save_title_key(trip_id: str, value: str, admin) -> dict:
    v = (value or "").strip()
    if not v:
        raise HTTPException(422, detail={"error": "empty_title_key"})
    tg_id = _require_tg(trip_id)
    tripdesc._write_tripgroup(tg_id, {"contentTitleKey": v})
    log.info("final_checks: %s contentTitleKey -> %r (by %s)", tg_id, v, admin.username)
    return {"tg_id": tg_id, "contentTitleKey": v}


def save_categories(trip_id: str, categories: list[str], admin) -> dict:
    clean = [c.strip() for c in (categories or []) if c and c.strip()]
    tg_id = _require_tg(trip_id)
    tripdesc._write_tripgroup(tg_id, {"tripCategories": clean})
    log.info("final_checks: %s tripCategories -> %s (by %s)",
             tg_id, clean, admin.username)
    return {"tg_id": tg_id, "categories": clean}


def reopen_description(trip_id: str, admin) -> dict:
    """The check-1 Edit escape hatch: make sure a tripdesc item exists for this
    family and put it back to pending_en, returning the tg_id to navigate to."""
    by_trip, _docs = tripdesc._tripgroup_index()
    tg_id = by_trip.get(trip_id)
    if tg_id is None:
        # seed_trips resolves via the same index; a miss means no TripGroup lists it.
        raise HTTPException(409, detail={
            "error": "tripgroup_missing",
            "detail": f"no staging TripGroup lists {trip_id}"})
    if not db.query_one("SELECT tg_id FROM tripgroup_reviews WHERE tg_id=?", (tg_id,)):
        tripdesc.seed_trips([trip_id])
    tripdesc.reopen(tg_id, admin)
    return {"tg_id": tg_id}


# --------------------------------------------------------------------------- #
# ContentEnrichment country index + cross-trip category search (spec §1)
# --------------------------------------------------------------------------- #
_ENR_TTL_S = 600
_enr_lock = threading.Lock()
_enr_index: dict = {"at": 0.0, "docs": []}

_ENR_FIELDS = ("applicableCategories", "newCategorySuggestions", "themes",
               "keyVocabulary", "namedEntities")


def _enrichment_index(force: bool = False) -> list[dict]:
    """One ContentEnrichment sweep, TTL-cached: [{id, tg_id, countries, <fields>}].
    Country comes from the doc's TripGroup via the TripLocations index (enrichment
    docs carry no country of their own — deliberately not denormalised, spec §1).
    Best-effort: a failed stream serves the stale list."""
    with _enr_lock:
        if (not force and _enr_index["docs"]
                and time.time() - _enr_index["at"] < _ENR_TTL_S):
            return _enr_index["docs"]
        try:
            from .staging import db as fb_db
            by_trip, tg_docs = tripdesc._tripgroup_index()
            by_tg_loc = tripdesc._triplocations_index()
            docs: list[dict] = []
            for snap in fb_db().collection("ContentEnrichment").select(
                    list(_ENR_FIELDS)).stream():
                d = snap.to_dict() or {}
                tg_id = by_trip.get(snap.id) or (snap.id if snap.id in tg_docs else None)
                countries = sorted({c for _n, c in (by_tg_loc.get(tg_id) or []) if c})
                docs.append({
                    "id": snap.id, "tg_id": tg_id, "countries": countries,
                    **{f: [v for v in (d.get(f) or []) if isinstance(v, str)]
                       for f in _ENR_FIELDS}})
            _enr_index["docs"] = docs
            _enr_index["at"] = time.time()
        except Exception as e:  # noqa: BLE001 — suggestions only
            log.warning("final_checks: ContentEnrichment index failed: %s", e)
        return _enr_index["docs"]


def _enrichment_matches(tg_id: str, category: str, cap: int = 30) -> list[dict]:
    """Country-mates whose enrichment signals mention `category` — the richer arm of
    the cross-trip search (the description arm is tripdesc.category_check)."""
    needle = category.strip().lower()
    if len(needle) < 3:
        return []
    # Same naive-singular probe as tripdesc._mention_snippet ("Castles" should hit
    # a "castle history" theme).
    probes = {p for p in (needle, needle.rstrip("s")) if len(p) >= 3}
    countries = {c for _n, c in (tripdesc._triplocations_index().get(tg_id) or []) if c}
    out: list[dict] = []
    for doc in _enrichment_index():
        if doc["tg_id"] == tg_id:
            continue
        if countries and not (countries & set(doc["countries"])):
            continue
        if not countries and doc["countries"]:
            continue   # scoped group ⇒ only unscoped docs match an unscoped group
        hits = []
        for f in _ENR_FIELDS:
            for v in doc[f]:
                lv = v.lower()
                if any(p in lv for p in probes) or lv in needle:
                    hits.append({"field": f, "value": v})
        if hits:
            out.append({"doc_id": doc["id"], "tg_id": doc["tg_id"],
                        "countries": doc["countries"], "hits": hits[:5]})
        if len(out) >= cap:
            break
    return out


def category_check(trip_id: str, category: str) -> dict:
    """The check-2 panel: the description-based sibling check PLUS the
    ContentEnrichment country matches. Read-only, informational."""
    tg_id = _require_tg(trip_id)
    base = tripdesc.category_check(tg_id, category)
    base["enrichment_matches"] = _enrichment_matches(tg_id, category)
    return base


# --------------------------------------------------------------------------- #
# Check 4 — TripLocation editor + map pin (spec §2.4, decided flow: pin placed in
# the UI writes x,y to STAGING CustomizableMenus; production only at publish via
# publish_trips_cli.py --pin).
# --------------------------------------------------------------------------- #
_LOCATION_FIELDS = ("locationTitleKey", "skyboxTextureId", "trips")


def _menu_docs() -> list[dict]:
    """Every staging `*_Trip_Menu` doc: id, MapName, Pins, ExtraMapButtons."""
    from .staging import db as fb_db
    out = []
    for snap in fb_db().collection("CustomizableMenus").stream():
        if not snap.id.endswith("_Trip_Menu"):
            continue
        d = snap.to_dict() or {}
        out.append({"id": snap.id,
                    "map_name": d.get("MapName") or "",
                    "pins": [p for p in (d.get("Pins") or []) if isinstance(p, dict)],
                    "extra_buttons": [p for p in (d.get("ExtraMapButtons") or [])
                                      if isinstance(p, dict)]})
    out.sort(key=lambda m: m["id"])
    return out


def _menu_for_location(menus: list[dict], loc_id: str) -> tuple[str | None, str | None, dict | None]:
    """(menu_id, array_field, entry) for the menu already holding loc_id's pin."""
    for m in menus:
        for field in ("pins", "extra_buttons"):
            for entry in m[field]:
                if entry.get("LocationId") == loc_id:
                    fs_field = "Pins" if field == "pins" else "ExtraMapButtons"
                    return m["id"], fs_field, entry
    return None, None, None


def _skybox_options() -> dict:
    """The in-use ids (with counts) + the committed shipped-skybox manifest."""
    from .staging import db as fb_db
    used: dict[str, int] = {}
    try:
        for snap in fb_db().collection("TripLocations").select(
                ["skyboxTextureId"]).stream():
            sid = (snap.to_dict() or {}).get("skyboxTextureId") or ""
            if sid.strip():
                used[sid.strip()] = used.get(sid.strip(), 0) + 1
    except Exception as e:  # noqa: BLE001 — options only
        log.warning("final_checks: skybox usage sweep failed: %s", e)
    manifest_ids: list[str] = []
    generated_at = None
    try:
        m = json.loads((config.REVIEW_APP_ROOT / "skybox_manifest.json")
                       .read_text(encoding="utf-8"))
        manifest_ids = [s["id"] for s in (m.get("skyboxes") or []) if s.get("id")]
        generated_at = m.get("generated_at")
    except Exception as e:  # noqa: BLE001 — a missing manifest only disables the warning
        log.warning("final_checks: skybox manifest unreadable: %s", e)
    return {"used": sorted(({"id": k, "count": n} for k, n in used.items()),
                           key=lambda s: (-s["count"], s["id"].lower())),
            "manifest": sorted(manifest_ids, key=str.lower),
            "manifest_generated_at": generated_at}


def get_location(trip_id: str) -> dict:
    """The check-4 read model: the staging TripLocation doc(s) listing this trip's
    family, each with its menu/pin state, plus the skybox vocabulary and the map
    image name the FE renders (/maps/<MapName>.png, committed from Unity)."""
    tg_id = _require_tg(trip_id)
    from .staging import db as fb_db
    fs = fb_db()
    menus = _menu_docs()
    _by_trip, tg_docs = tripdesc._tripgroup_index()
    locations = []
    for snap in fs.collection("TripLocations").stream():
        d = snap.to_dict() or {}
        trips = [t for t in (d.get("trips") or []) if isinstance(t, str)]
        if tg_id not in trips:
            continue
        menu_id, field, entry = _menu_for_location(menus, snap.id)
        locations.append({
            "id": snap.id,
            "contentId": d.get("contentId") or "",
            "locationName": d.get("locationName") or "",
            "locationTitleKey": d.get("locationTitleKey") or "",
            "locationCountry": d.get("locationCountry") or "",
            "skyboxTextureId": d.get("skyboxTextureId") or "",
            "trips": trips,
            "groups": [{"tg_id": g, "exists": g in tg_docs,
                        "is_this_family": g == tg_id} for g in trips],
            "pin": ({"menu_id": menu_id, "field": field,
                     "x": entry.get("xPos"), "y": entry.get("yPos")}
                    if entry else None),
        })
    return {
        "trip_id": trip_id,
        "tg_id": tg_id,
        "locations": locations,
        "menus": [{"id": m["id"], "map_name": m["map_name"],
                   "pins": m["pins"], "extra_buttons": m["extra_buttons"]}
                  for m in menus],
        "skyboxes": _skybox_options(),
    }


def _require_location(loc_id: str, tg_id: str) -> dict:
    from .staging import db as fb_db
    snap = fb_db().collection("TripLocations").document(loc_id).get()
    if not snap.exists:
        raise HTTPException(409, detail={"error": "location_missing",
                                         "detail": f"TripLocations/{loc_id}"})
    d = snap.to_dict() or {}
    if tg_id not in (d.get("trips") or []):
        raise HTTPException(409, detail={
            "error": "location_mismatch",
            "detail": f"TripLocations/{loc_id} does not list {tg_id}"})
    return d


def save_location(trip_id: str, loc_id: str, fields: dict, admin) -> dict:
    """Targeted STAGING update of a TripLocation, mirroring the 09c discipline:
    only the named fields, never a doc rewrite. `trips` must be a permutation of
    the live array (reorder only — membership changes ride the pipeline/publish
    paths, where the cascade is understood)."""
    tg_id = _require_tg(trip_id)
    live = _require_location(loc_id, tg_id)
    sets: dict = {}
    for k in _LOCATION_FIELDS:
        if k not in fields or fields[k] is None:
            continue
        v = fields[k]
        if k == "trips":
            if not isinstance(v, list) or sorted(v) != sorted(live.get("trips") or []):
                raise HTTPException(422, detail={
                    "error": "trips_not_permutation",
                    "detail": "trips[] may only be REORDERED here"})
            sets[k] = v
        else:
            sets[k] = str(v).strip()
    if not sets:
        raise HTTPException(422, detail={"error": "nothing_to_save"})
    from .staging import db as fb_db
    fb_db().collection("TripLocations").document(loc_id).update(sets)
    log.info("final_checks: TripLocations/%s <- %s (by %s)",
             loc_id, sets, admin.username)
    return {"loc_id": loc_id, "updated": sorted(sets)}


def save_pin(trip_id: str, loc_id: str, menu_id: str, x: float, y: float,
             admin) -> dict:
    """Upsert the STAGING map pin for `loc_id` in `menu_id` (read-modify-update of
    the one array; an entry already living in ExtraMapButtons is updated there).
    Production is untouched — the Publisher's publish_pin job mirrors it at
    publish time."""
    tg_id = _require_tg(trip_id)
    _require_location(loc_id, tg_id)
    from .staging import db as fb_db
    ref = fb_db().collection("CustomizableMenus").document(menu_id)
    snap = ref.get()
    if not snap.exists or not menu_id.endswith("_Trip_Menu"):
        raise HTTPException(409, detail={"error": "menu_missing",
                                         "detail": f"CustomizableMenus/{menu_id}"})
    d = snap.to_dict() or {}
    x, y = round(float(x), 3), round(float(y), 3)
    field = "Pins"
    if any((p or {}).get("LocationId") == loc_id
           for p in d.get("ExtraMapButtons") or []):
        field = "ExtraMapButtons"
    arr = list(d.get(field) or [])
    idx = next((i for i, p in enumerate(arr)
                if (p or {}).get("LocationId") == loc_id), None)
    if idx is None:
        arr.append({"LocationId": loc_id, "xPos": x, "yPos": y})
    else:
        arr[idx] = {**arr[idx], "xPos": x, "yPos": y}
    ref.update({field: arr})
    log.info("final_checks: %s.%s pin %s -> (%s, %s) (by %s)",
             menu_id, field, loc_id, x, y, admin.username)
    return {"menu_id": menu_id, "field": field, "loc_id": loc_id, "x": x, "y": y}


# --------------------------------------------------------------------------- #
# Check 5 — static-image timing + replace + credits (spec §2.5).
# Timing writes mirror stage10_static_check.py `set-timing` exactly (fresh read →
# int fields on the one staticImages entry → whole-quickTrips update). The house
# rules (stage9/image_timing.py + stage10's DISPLAY_MIN/MAX) surface as WARNINGS,
# never blocks — the reviewer is looking at the actual scene.
# --------------------------------------------------------------------------- #
TIMING_RULES = {"min_appear": 1, "min_display": 6, "max_display": 15, "gap": 1}


def _get_trip(trip_id: str) -> dict:
    from .staging import get_trip
    try:
        return get_trip(trip_id)
    except SystemExit as e:
        raise HTTPException(404, detail={"error": "no_staging_trip", "detail": str(e)})


def get_static_images(trip_id: str) -> dict:
    trip = _get_trip(trip_id)
    scenes = []
    for i, s in enumerate(trip.get("quickTrips") or []):
        sis = [si for si in (s.get("staticImages") or []) if isinstance(si, dict)]
        if not sis:
            continue
        scenes.append({
            "scene_index": i,
            "narration": s.get("SceneDesc") or "",
            "audio_url": f"/api/final/{trip_id}/scenes/{i}/audio",
            "overlays": [{
                "filename": si.get("filename") or "",
                "appear": si.get("appearAtSeconds"),
                "disappear": si.get("disappearAtSeconds"),
                "url": f"/api/final/{trip_id}/overlays/{si.get('filename') or ''}",
            } for si in sis],
        })
    return {"trip_id": trip_id, "scenes": scenes, "rules": TIMING_RULES}


def overlay_image(trip_id: str, filename: str):
    """(local_path, redirect_url): the overlay from the local trees, else the R2
    review-overlays mirror. 404 when neither has it."""
    from . import images_r2, sessions
    trip = _get_trip(trip_id)
    try:
        mp3_dir = sessions.resolve_audio_dir(trip_id, trip)
    except Exception:  # noqa: BLE001 — image can still resolve via base dirs / R2
        mp3_dir = None
    local = sessions._resolve_overlay_file(trip_id, mp3_dir, None, filename,
                                           trip.get("folderName") or "")
    base = sessions._overlay_base(trip_id)
    if local is not None:
        # A REPLACED overlay lands on R2 first (the canonical local distribution
        # is a later workstation job), so a newer R2 copy must win over the stale
        # local file — otherwise "Replace image…" appears to do nothing on hosts
        # with the local trees (dave, 2026-08-22). The v= busts browser caches.
        r2_at = images_r2.last_modified(base, filename)
        try:
            local_at = local.stat().st_mtime
        except OSError:
            local_at = 0.0
        if r2_at is not None and r2_at > local_at:
            return None, images_r2.public_url(base, filename) + f"?v={int(r2_at)}"
        return local, None
    if images_r2.exists(base, filename):
        return None, images_r2.public_url(base, filename)
    raise HTTPException(404, detail={"error": "no_overlay", "detail": filename})


def _secs(v: float) -> str:
    """5 -> '5', 5.5 -> '5.5' — decimal timings without float noise in messages."""
    return f"{round(float(v), 1):g}"


def _timing_warnings(appear: float, disappear: float, others: list[dict]) -> list[str]:
    w = []
    if appear < TIMING_RULES["min_appear"]:
        w.append(f"appears before {TIMING_RULES['min_appear']}s — the opening second "
                 "always shows the video")
    shown = disappear - appear
    if shown < TIMING_RULES["min_display"]:
        w.append(f"on screen only {_secs(shown)}s (house rule ≥{TIMING_RULES['min_display']}s)")
    if shown > TIMING_RULES["max_display"]:
        w.append(f"on screen {_secs(shown)}s (house rule ≤{TIMING_RULES['max_display']}s)")
    for o in others:
        oa, od = o.get("appearAtSeconds"), o.get("disappearAtSeconds")
        if oa is None or od is None:
            continue
        if appear < od + TIMING_RULES["gap"] and od <= appear:
            w.append(f"only {_secs(appear - od)}s after {o.get('filename')} clears "
                     f"(house rule ≥{TIMING_RULES['gap']}s gap)")
        if oa < disappear and appear < od:
            w.append(f"overlaps {o.get('filename')} ({_secs(oa)}–{_secs(od)}s)")
    return w


def set_image_timing(trip_id: str, scene_index: int, filename: str,
                     appear: float, disappear: float, admin) -> dict:
    # Sub-second timing (0.1s steps) — the headset field is a Unity float
    # (QuickTripScene.appearAtSeconds), so decimals round-trip fine.
    appear, disappear = round(float(appear), 1), round(float(disappear), 1)
    if appear < 0 or appear >= disappear:
        raise HTTPException(422, detail={"error": "bad_timing",
                                         "detail": f"need 0 ≤ appear < disappear "
                                                   f"(got {appear}, {disappear})"})
    trip = _get_trip(trip_id)
    qt = trip.get("quickTrips") or []
    if not 0 <= scene_index < len(qt):
        raise HTTPException(422, detail={"error": "bad_scene",
                                         "detail": f"scene {scene_index} of {len(qt)}"})
    sis = qt[scene_index].get("staticImages") or []
    hit = [si for si in sis if isinstance(si, dict) and si.get("filename") == filename]
    if not hit:
        raise HTTPException(404, detail={
            "error": "no_overlay",
            "detail": f"scene {scene_index} has no overlay {filename!r}"})
    for si in hit:
        si["appearAtSeconds"] = appear
        si["disappearAtSeconds"] = disappear
    from .staging import db as fb_db
    fb_db().collection("Trips").document(trip_id).update({"quickTrips": qt})
    others = [si for si in sis
              if isinstance(si, dict) and si.get("filename") != filename]
    log.info("final_checks: %s scene %d %s timing -> %s..%ss (by %s)",
             trip_id, scene_index, filename, appear, disappear, admin.username)
    return {"scene_index": scene_index, "filename": filename,
            "appear": appear, "disappear": disappear,
            "warnings": _timing_warnings(appear, disappear, others)}


def replace_overlay(trip_id: str, filename: str, data: bytes, admin) -> dict:
    """Laptop half of an overlay replacement: stage the new image to R2
    review-overlays/<base>/ (so this app and other hosts display it at once) and
    queue a `replace_overlay` bus job — the workstation runs
    stage10_static_check.py `replace`, which does the CANONICAL distribution:
    the country folders under D:\\Dynamic Languages (+ Drive + every rung's OGG
    folder + S3). No silent divergence: the canonical copy only ever comes from
    that script."""
    is_jpg = data[:3] == b"\xff\xd8\xff"
    is_png = data[:8] == b"\x89PNG\r\n\x1a\n"
    if not (is_jpg or is_png):
        raise HTTPException(422, detail={"error": "bad_image",
                                         "detail": "JPEG or PNG only"})
    if len(data) > 12 * 1024 * 1024:
        raise HTTPException(422, detail={"error": "too_large", "detail": "max 12 MB"})
    from . import images_r2, sessions
    trip = _get_trip(trip_id)
    known = {si.get("filename") for s in (trip.get("quickTrips") or [])
             for si in (s.get("staticImages") or []) if isinstance(si, dict)}
    if filename not in known:
        raise HTTPException(404, detail={"error": "no_overlay",
                                         "detail": f"{filename} not on any scene"})
    base = sessions._overlay_base(trip_id)
    s3 = images_r2._r2()
    if s3 is None:
        raise HTTPException(502, detail={"error": "r2_unavailable",
                                         "detail": "no R2 credentials on this host"})
    key = images_r2._key(base, filename)
    # One-level undo: keep the object we're about to overwrite as <key>.prev so
    # "Revert" can restore it. First-ever replace of a local-only overlay has no
    # R2 object to back up — revert then DELETES the key so the local original
    # serves again (see revert_overlay).
    try:
        s3.copy_object(Bucket=config.THUMB_BUCKET, Key=key + ".prev",
                       CopySource={"Bucket": config.THUMB_BUCKET, "Key": key})
    except Exception:  # noqa: BLE001 — no existing object (or copy hiccup)
        pass
    # replaced=1 marks this object as a REPLACEMENT (vs the ensure_uploaded mirror
    # of the original, or a producer's hand upload) — revert_overlay's delete
    # branch requires it, so a revert can never delete the only hosted copy of a
    # never-replaced image. copy_object defaults to MetadataDirective=COPY, so the
    # .prev backup and a restored previous keep their own (un)marked state.
    s3.put_object(Bucket=config.THUMB_BUCKET, Key=key, Body=data,
                  ContentType="image/png" if is_png else "image/jpeg",
                  Metadata={"replaced": "1"})
    images_r2._LISTING.pop(base, None)   # bust the cached listing (we overwrote)
    _lvl, family = sessions._level_family(trip_id)
    job_id = None
    try:
        job = review_bus.queue_job("replace_overlay", trip_id, admin,
                                   note=f"family={family};file={filename}")
        job_id = job["id"]
    except Exception as e:  # noqa: BLE001 — R2 copy is live; canonical copy owed
        log.warning("final_checks: replace_overlay queue failed: %s", e)
    log.info("final_checks: overlay %s/%s replaced on R2 (%d bytes, by %s; job %s)",
             base, filename, len(data), admin.username, job_id)
    return {"filename": filename, "r2_key": key, "family": family,
            "replace_job": job_id}


def revert_overlay(trip_id: str, filename: str, admin) -> dict:
    """Undo the last replace of one overlay: restore <key>.prev over <key>
    (one-level undo); when no .prev exists (first-ever replace of a local-only
    image), DELETE the R2 copy so the untouched local original serves again.
    If a canonical-distribution job for the old replace already RAN on the
    workstation, re-run it after this so the country folders/S3 revert too."""
    from . import images_r2, sessions
    base = sessions._overlay_base(trip_id)
    s3 = images_r2._r2()
    if s3 is None:
        raise HTTPException(502, detail={"error": "r2_unavailable",
                                         "detail": "no R2 credentials on this host"})
    key = images_r2._key(base, filename)
    prev = key + ".prev"

    def _head(k: str) -> dict | None:
        try:
            return s3.head_object(Bucket=config.THUMB_BUCKET, Key=k)
        except Exception:  # noqa: BLE001 — 404/network: treat as absent
            return None

    cur = _head(key)
    if _head(prev) is not None:
        s3.copy_object(Bucket=config.THUMB_BUCKET, Key=key,
                       CopySource={"Bucket": config.THUMB_BUCKET, "Key": prev})
        s3.delete_object(Bucket=config.THUMB_BUCKET, Key=prev)
        mode = "restored_previous"
    elif cur is not None and (cur.get("Metadata") or {}).get("replaced"):
        # Only delete a copy that replace_overlay itself wrote (replaced=1). An
        # UNMARKED object is the mirror/hand-upload of the ORIGINAL — for a trip
        # with no local trees (the laptop, producer-uploaded gallery stills) it
        # can be the only copy anywhere, so deleting it on a stray Revert click
        # (or a second click after restored_previous) would lose the image.
        s3.delete_object(Bucket=config.THUMB_BUCKET, Key=key)
        images_r2._UPLOADED.discard(key)   # let ensure_uploaded re-mirror later
        mode = "deleted_r2_copy_local_serves"
    else:
        raise HTTPException(404, detail={"error": "nothing_to_revert",
                                         "detail": f"{filename} has no replaced copy"})
    images_r2._LISTING.pop(base, None)
    _lvl, family = sessions._level_family(trip_id)
    job_id = None
    if mode == "restored_previous":
        # The restored image is a content change too — the canonical distribution
        # must follow it, same as a replace.
        try:
            job = review_bus.queue_job("replace_overlay", trip_id, admin,
                                       note=f"family={family};file={filename};revert=1")
            job_id = job["id"]
        except Exception as e:  # noqa: BLE001
            log.warning("final_checks: revert_overlay queue failed: %s", e)
    log.info("final_checks: overlay %s/%s revert (%s, by %s; job %s)",
             base, filename, mode, admin.username, job_id)
    return {"filename": filename, "mode": mode, "replace_job": job_id}


# --------------------------------------------------------------------------- #
# Credits — the VR app's SINGLE CustomizableMenus/Credits doc (one button showing
# all credits), format fixed by CreditsPanel.cs:
#   { "credits": [ { "header": str, "entries": [str, ...] }, ... ] }
# Keep that format exactly; only ever ADD entries (dave, 2026-08-21).
# --------------------------------------------------------------------------- #
CREDITS_DOC = "Credits"


# Country (TripLocations.locationCountry) -> the Credits header's place name
# (convention on the live doc: "Trips to Japan Images").
_CREDIT_COUNTRY = {"GreatBritain": "the UK", "SouthKorea": "South Korea"}


def _parse_attribution(text: str) -> dict:
    """The drafting webfetch sidecar (<image>.attribution.txt):
    Image/Source/Author/Licence/Credit lines -> dict (missing keys '')."""
    out = {"image": "", "source": "", "source_url": "", "author": "",
           "licence": "", "credit": ""}
    for line in text.splitlines():
        m = re.match(r"(Image|Source|Author|Licence|License|Credit):\s*(.*)", line.strip())
        if not m:
            continue
        key = {"License": "licence"}.get(m.group(1), m.group(1).lower())
        out[key] = m.group(2).strip()
    u = re.search(r"https?://\S+", out["source"])
    out["source_url"] = u.group(0) if u else ""
    return out


def credit_proposals(trip_id: str) -> dict:
    """Auto-fill for check 5's credit lines: for every overlay on this trip, find
    the drafting webfetch sidecar (`<RW data root>/<base id>/static_images/
    <filename>.attribution.txt`), format an entry in the live Credits doc's house
    style, and say which are already present / unusable. ADD is still a human
    click — nothing is written here."""
    from . import sessions
    trip = _get_trip(trip_id)
    # header from the trip's location country
    by_trip, _docs = tripdesc._tripgroup_index()
    tg_id, _ok = _resolve_tg(trip_id, by_trip)
    countries = [c for _n, c in (tripdesc._triplocations_index().get(tg_id) or []) if c]
    place = _CREDIT_COUNTRY.get(countries[0], countries[0]) if countries else ""
    header = f"Trips to {place} Images" if place else "Trip Images"
    existing: set[str] = set()
    for b in (get_credits().get("credits") or []):
        for e in (b.get("entries") or []):
            existing.add(" ".join(str(e).split()).lower())

    bases = sessions._image_base_ids(trip_id)
    rows: list[dict] = []
    seen: set[str] = set()
    for s in (trip.get("quickTrips") or []):
        for si in (s.get("staticImages") or []):
            fn = (si or {}).get("filename")
            if not fn or fn in seen:
                continue
            seen.add(fn)
            sidecar = None
            for bid in bases:
                for root in config.RW_DATA_ROOTS:
                    p = root / bid / "static_images" / f"{fn}.attribution.txt"
                    if p.is_file():
                        sidecar = p
                        break
                if sidecar:
                    break
            if sidecar is None:
                rows.append({"filename": fn, "status": "no_attribution",
                             "entry": "", "detail": "no drafting sidecar found — "
                             "credit by hand if the image needs one"})
                continue
            a = _parse_attribution(sidecar.read_text(encoding="utf-8", errors="replace"))
            # Unusable when the source url or author is missing, or when the
            # sidecar itself says "unknown source" AND carries no licence (with a
            # licence the [url] [By author, licence] entry is complete anyway).
            bad = (not a["source_url"] or not a["author"]
                   or ("unknown source" in a["credit"].lower() and not a["licence"]))
            parts = [f"By {a['author']}" if a["author"] else "", a["licence"]]
            extra = a["credit"] if a["credit"].startswith("http") \
                and a["credit"] != a["source_url"] else ""
            if extra:
                parts.append(extra)
            entry = (f"[{a['source_url']}] "
                     f"[{', '.join(p for p in parts if p)}]").strip()
            status = ("already_added" if " ".join(entry.split()).lower() in existing
                      else "needs_hand_edit" if bad else "proposed")
            rows.append({"filename": fn, "status": status, "entry": entry,
                         "detail": a["image"] or ""})
    return {"trip_id": trip_id, "header": header, "proposals": rows}


def get_credits() -> dict:
    from .staging import db as fb_db
    snap = fb_db().collection("CustomizableMenus").document(CREDITS_DOC).get()
    d = (snap.to_dict() or {}) if snap.exists else {}
    blocks = [b for b in (d.get("credits") or []) if isinstance(b, dict)]
    return {"exists": snap.exists,
            "credits": [{"header": b.get("header") or "",
                         "entries": [e for e in (b.get("entries") or [])
                                     if isinstance(e, str)]} for b in blocks]}


def add_credit(header: str, entry: str, admin) -> dict:
    header, entry = (header or "").strip(), (entry or "").strip()
    if not header or not entry:
        raise HTTPException(422, detail={"error": "empty_credit"})
    from .staging import db as fb_db
    ref = fb_db().collection("CustomizableMenus").document(CREDITS_DOC)
    snap = ref.get()
    d = (snap.to_dict() or {}) if snap.exists else {}
    blocks = [b for b in (d.get("credits") or []) if isinstance(b, dict)]
    block = next((b for b in blocks
                  if (b.get("header") or "").strip().lower() == header.lower()), None)
    if block is None:
        blocks.append({"header": header, "entries": [entry]})
    else:
        entries = [e for e in (block.get("entries") or []) if isinstance(e, str)]
        if any(e.strip().lower() == entry.lower() for e in entries):
            raise HTTPException(409, detail={"error": "duplicate_credit",
                                             "detail": entry})
        block["entries"] = entries + [entry]
    if snap.exists:
        ref.update({"credits": blocks})
    else:
        ref.set({"credits": blocks})
    log.info("final_checks: credit added under %r: %r (by %s)",
             header, entry, admin.username)
    return get_credits()


# --------------------------------------------------------------------------- #
# Check 6 — keyword check (spec §2.6). The admin SPEAKS each answer; Azure's
# N-best is scored in the browser by the ported library-app engine, and a heard
# variant that fails the current keys can be added to additionalAnswerKeys —
# ADD-ONLY, collision-checked against the question's other options (the same
# safety rule as stage9/answer_keys.py, which stays the automated first pass).
# --------------------------------------------------------------------------- #
_AUDIO_KINDS = {"narration": "{i}.mp3", "question": "{i}_q.mp3",
                "answer": "{i}_a.mp3", "option": "{i}_a{n}.mp3"}


def scene_audio_file(trip_id: str, scene_index: int, kind: str, opt: int = 0):
    """A scene clip from the trip's master dir: narration {i}.mp3, question
    {i}_q.mp3, answer {i}_a.mp3, options {i}_a{n}.mp3 (1-based, sessions.mp3_name)."""
    if kind not in _AUDIO_KINDS:
        raise HTTPException(422, detail={"error": "bad_kind", "detail": kind})
    from . import sessions
    trip = _get_trip(trip_id)
    try:
        d = sessions.resolve_audio_dir(trip_id, trip)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(404, detail={"error": "no_audio_dir", "detail": str(e)})
    p = d / _AUDIO_KINDS[kind].format(i=scene_index, n=opt)
    if not p.is_file():
        raise HTTPException(404, detail={"error": "no_scene_audio",
                                         "detail": p.name})
    return p


def get_keywords(trip_id: str) -> dict:
    """Per Q&A/keyword scene: the accepted set + audio urls for the check-6 UI."""
    trip = _get_trip(trip_id)
    from . import audio_core
    scenes = []
    for i, s in enumerate(trip.get("quickTrips") or []):
        opts = [o for o in (s.get("questionOptionKeys") or []) if isinstance(o, str)]
        qk = (s.get("questionKey") or "").strip()
        if not opts and not qk:
            continue
        correct = opts[0] if opts else qk
        au = f"/api/final/{trip_id}/scenes/{i}/audio"
        scenes.append({
            "scene_index": i,
            "question": qk,
            "question_en": s.get("questionKeyEn") or "",
            "options": opts,
            "correct": correct,
            "additional": [a for a in (s.get("additionalAnswerKeys") or [])
                           if isinstance(a, str)],
            "is_keyword": not opts,       # speak-and-repeat word, no options
            "question_audio": f"{au}?kind=question",
            # Keyword scenes have NO {i}_a.mp3 — the spoken word IS the question
            # clip ({i}_q.mp3), so point the row's player there or it 404s.
            "answer_audio": f"{au}?kind={'answer' if opts else 'question'}",
        })
    return {"trip_id": trip_id, "language": audio_core.language_of(trip_id),
            "scenes": scenes}


def add_answer_key(trip_id: str, scene_index: int, key: str, admin) -> dict:
    key = (key or "").strip()
    if not key:
        raise HTTPException(422, detail={"error": "empty_key"})
    trip = _get_trip(trip_id)
    qt = trip.get("quickTrips") or []
    if not 0 <= scene_index < len(qt):
        raise HTTPException(422, detail={"error": "bad_scene",
                                         "detail": f"scene {scene_index} of {len(qt)}"})
    s = qt[scene_index]
    opts = [o for o in (s.get("questionOptionKeys") or []) if isinstance(o, str)]
    low = key.lower()
    # answer_keys.py safety rule: a variant equal to another (wrong) option would
    # make the wrong option match — refuse.
    if any(o.strip().lower() == low for o in opts[1:]):
        raise HTTPException(409, detail={
            "error": "collides_with_option",
            "detail": f"{key!r} equals another option of this question"})
    existing = [a for a in (s.get("additionalAnswerKeys") or []) if isinstance(a, str)]
    correct = (opts[0] if opts else (s.get("questionKey") or "")).strip()
    if low == correct.lower() or any(a.strip().lower() == low for a in existing):
        raise HTTPException(409, detail={"error": "duplicate_key", "detail": key})
    s["additionalAnswerKeys"] = existing + [key]
    from .staging import db as fb_db
    fb_db().collection("Trips").document(trip_id).update({"quickTrips": qt})
    log.info("final_checks: %s scene %d additionalAnswerKeys += %r (by %s)",
             trip_id, scene_index, key, admin.username)
    return {"scene_index": scene_index, "additional": s["additionalAnswerKeys"]}


def delete_answer_key(trip_id: str, scene_index: int, key: str, admin) -> dict:
    """Remove one additionalAnswerKeys entry (check 6 — a mis-added or test
    variant). Only the ADDITIONAL keys are deletable here; the question's options
    and the correct answer stay pipeline-owned."""
    key = (key or "").strip()
    if not key:
        raise HTTPException(422, detail={"error": "empty_key"})
    trip = _get_trip(trip_id)
    qt = trip.get("quickTrips") or []
    if not 0 <= scene_index < len(qt):
        raise HTTPException(422, detail={"error": "bad_scene",
                                         "detail": f"scene {scene_index} of {len(qt)}"})
    s = qt[scene_index]
    existing = [a for a in (s.get("additionalAnswerKeys") or []) if isinstance(a, str)]
    low = key.lower()
    remaining = [a for a in existing if a.strip().lower() != low]
    if len(remaining) == len(existing):
        raise HTTPException(404, detail={"error": "no_such_key", "detail": key})
    s["additionalAnswerKeys"] = remaining
    from .staging import db as fb_db
    fb_db().collection("Trips").document(trip_id).update({"quickTrips": qt})
    log.info("final_checks: %s scene %d additionalAnswerKeys -= %r (by %s)",
             trip_id, scene_index, key, admin.username)
    return {"scene_index": scene_index, "additional": remaining}


_speech_token_cache: dict = {"token": "", "region": "", "at": 0.0}


def speech_token() -> dict:
    """A short-lived Azure Speech token for the browser (the subscription key never
    leaves the backend; it lives in the Scripts .env — `Azure_Key1` (dave's name,
    2026-08-21; `AZURE_SPEECH_KEY` also accepted) + `AZURE_SPEECH_REGION`, loaded
    at config import). Tokens last 10 min; cached 8."""
    import os
    import urllib.request

    def env(*names: str) -> str:
        for n in names:
            v = os.environ.get(n, "").strip()
            if v:
                return v
        return ""

    key = env("Azure_Key1", "AZURE_SPEECH_KEY")
    region = env("AZURE_SPEECH_REGION", "Azure_Region") or "uksouth"
    if not key:
        raise HTTPException(503, detail={
            "error": "azure_not_configured",
            "detail": "Azure_Key1 / AZURE_SPEECH_KEY missing from the Scripts .env "
                      "on this host"})
    now = time.time()
    if _speech_token_cache["token"] and now - _speech_token_cache["at"] < 8 * 60 \
            and _speech_token_cache["region"] == region:
        return {"token": _speech_token_cache["token"], "region": region}
    req = urllib.request.Request(
        f"https://{region}.api.cognitive.microsoft.com/sts/v1.0/issueToken",
        method="POST", data=b"",
        headers={"Ocp-Apim-Subscription-Key": key, "Content-Length": "0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            token = resp.read().decode("utf-8")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, detail={"error": "azure_token_failed",
                                         "detail": str(e)[:300]})
    _speech_token_cache.update({"token": token, "region": region, "at": now})
    return {"token": token, "region": region}


# --------------------------------------------------------------------------- #
# Ready to publish (spec §2 intro + §3): all seven checks green on a TRIP unlocks
# queueing its release — a publish_docs job (single-trip PARTIAL publish via
# publish_trips_cli). A bus job the WORKSTATION executes; the laptop holds no
# Trello/prod creds. The family Trello card is stamped/moved by the publish
# apply hook, not here.
# --------------------------------------------------------------------------- #
def ready_to_publish(trip_id: str, admin) -> dict:
    by_trip, _docs = tripdesc._tripgroup_index()
    tg_id, _ok = _resolve_tg(trip_id, by_trip)
    checks = _checks_for(trip_id, tg_id or "", _states())
    open_checks = [c["key"] for c in checks if c["state"] != "done"]
    if open_checks:
        raise HTTPException(409, detail={
            "error": "checks_open",
            "detail": f"still open: {', '.join(open_checks)}"})
    if trip_id in _pending_deltas():
        # Server-side twin of the FE's hidden button (red-fable finding): changed
        # clips await a delta re-review — publishing now would ship unreviewed audio.
        raise HTTPException(409, detail={
            "error": "pending_delta",
            "detail": "changed clips await a delta re-review — approve it first"})
    # NO Trello job here (dave, 2026-08-22): the board is one card per FAMILY, so a
    # single rung passing final checks must not move the card. The card moves to
    # lane 12 automatically when its LAST rung is published (mark_published.py
    # --move-complete-to, run by the publish apply hook).
    publish_job = review_bus.queue_job("publish_docs", trip_id, admin,
                                       note="final-check: ready to publish")
    log.info("final_checks: %s READY TO PUBLISH (by %s) — job %s",
             trip_id, admin.username, publish_job["id"])
    return {"trip_id": trip_id, "publish_job": publish_job["id"],
            "trello_job": None}


# --------------------------------------------------------------------------- #
# Check 7 — TripGroup thumbnail (spec §2.7). thumbnailTextureId is the filename
# STEM; the headset loads it from R2 `dynamic-languages-thumbs` at the BUCKET
# ROOT `<stem>.jpg` (upload_thumbnails_r2.py — unlike scene thumbs, which sit
# under scene-thumbs/). The canonical LOCAL tree (Images\App thumbnails) is
# workstation-only, so an upload here also queues a thumbnail_local_copy bus job
# the Publisher runs.
# --------------------------------------------------------------------------- #
def _thumb_key(stem: str) -> str:
    return f"{stem}.jpg"


def get_thumbnail(trip_id: str) -> dict:
    tg_id = _require_tg(trip_id)
    doc = _tripgroup_doc(tg_id) or {}
    stem = (doc.get("thumbnailTextureId") or "").strip()
    on_r2 = None
    if stem:
        try:
            from . import images_r2
            s3 = images_r2._r2()
            if s3 is not None:
                s3.head_object(Bucket=config.THUMB_BUCKET, Key=_thumb_key(stem))
                on_r2 = True
        except Exception:  # noqa: BLE001 — 404 or no creds both read as "not verified"
            on_r2 = False
    from urllib.parse import quote
    return {
        "tg_id": tg_id,
        "thumbnailTextureId": stem,
        "url": (config.THUMB_PUBLIC_BASE + quote(_thumb_key(stem))) if stem else None,
        "on_r2": on_r2,
    }


def upload_thumbnail(trip_id: str, data: bytes, admin) -> dict:
    """Replace the family thumbnail: R2 `<stem>.jpg` + staging thumbnailTextureId,
    then queue the Publisher's local-tree copy (best-effort — the R2+staging write
    is the durable part). Keeps the existing stem when one is set (same key ⇒ the
    headset cache-busts on Last-Modified); a first thumbnail gets the local-tree
    naming convention so upload_thumbnails_r2.py --all still matches it."""
    if not data[:3] == b"\xff\xd8\xff":
        raise HTTPException(422, detail={"error": "not_jpeg",
                                         "detail": "thumbnail must be a JPEG"})
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(422, detail={"error": "too_large", "detail": "max 8 MB"})
    tg_id = _require_tg(trip_id)
    doc = _tripgroup_doc(tg_id) or {}
    stem = (doc.get("thumbnailTextureId") or "").strip() or (
        f"{tg_id} - EN Trip app thumb")
    from . import images_r2
    s3 = images_r2._r2()
    if s3 is None:
        raise HTTPException(502, detail={"error": "r2_unavailable",
                                         "detail": "no R2 credentials on this host"})
    s3.put_object(Bucket=config.THUMB_BUCKET, Key=_thumb_key(stem), Body=data,
                  ContentType="image/jpeg")
    tripdesc._write_tripgroup(tg_id, {"thumbnailTextureId": stem})
    log.info("final_checks: thumbnail %s (%d bytes) -> r2:%s/%s (by %s)",
             tg_id, len(data), config.THUMB_BUCKET, _thumb_key(stem), admin.username)
    job_id = None
    try:
        job = review_bus.queue_job("thumbnail_local_copy", tg_id, admin,
                                   note=f"stem={stem}")
        job_id = job["id"]
    except Exception as e:  # noqa: BLE001 — the copy is a workstation nicety
        log.warning("final_checks: thumbnail_local_copy queue failed: %s", e)
    return {**get_thumbnail(trip_id), "local_copy_job": job_id}
