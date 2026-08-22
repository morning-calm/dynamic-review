"""Release batches — the named unit a release ships as (dave, 2026-08-22).

A batch is a set of trips / TripGroups / TripLocations released together. It is
authored in the app (the Publishing Queue multi-select → "Save as release batch"),
optionally SEEDED from a card on the Trello "TG Release Schedule" lane — the card
is a plan, the app copy is the contract, so imports are always editable after.
Batches drive: the Publishing Queue's group-by-batch view, the wizard's scope, the
social-post drafting (which needs to know exactly what's in the release), and the
post-publish steps.

Social-post readiness is probed from the Comms folder (workstation paths — the
publisher-mode instance; other hosts report 'unknown', never 'missing').
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path

from fastapi import HTTPException

from . import config, db, tripdesc

log = logging.getLogger("uvicorn.error")

MEMBER_KINDS = ("trip", "group", "location")

# Where the drafted social posts + the in-app news feed live (Scripts social/README
# § output; dave moved the drafts home to Comms\Social Posts, 2026-08-22).
SOCIAL_DIR = Path(os.environ.get(
    "REVIEW_APP_SOCIAL_DIR", r"D:\Dynamic Languages\Comms\Social Posts"))
NEWS_JSON = Path(os.environ.get(
    "REVIEW_APP_NEWS_JSON", r"D:\Dynamic Languages\Comms\news.json"))


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
def _clean_members(members: list[dict]) -> list[dict]:
    out, seen = [], set()
    for m in members or []:
        kind, mid = (m.get("kind") or "").strip(), (m.get("id") or "").strip()
        if kind not in MEMBER_KINDS or not mid:
            raise HTTPException(422, detail={"error": "bad_member", "detail": m})
        if (kind, mid) not in seen:
            seen.add((kind, mid))
            out.append({"kind": kind, "id": mid})
    return out


def _resolve(members: list[dict]) -> dict:
    """Expand a batch's members to concrete ids: every rung trip_id the batch
    covers, plus the group/location ids themselves (the FE groups by these)."""
    from . import final_checks
    _by_trip, docs = tripdesc._tripgroup_index()
    loc_full = final_checks._loc_full_index()
    group_ids: set[str] = set()
    trip_ids: set[str] = set()
    location_ids: set[str] = set()
    for m in members:
        if m["kind"] == "trip":
            trip_ids.add(m["id"])
        elif m["kind"] == "group":
            group_ids.add(m["id"])
        elif m["kind"] == "location":
            location_ids.add(m["id"])
            for tg_id, locs in loc_full.items():
                if any(m["id"] in (i, n) for i, n, _c in locs):
                    group_ids.add(tg_id)
    for tg_id in group_ids:
        trip_ids.update((docs.get(tg_id) or {}).get("trips") or [])
    return {"trip_ids": sorted(trip_ids), "group_ids": sorted(group_ids),
            "location_ids": sorted(location_ids)}


def _row_out(r: dict, resolve: bool = True) -> dict:
    members = json.loads(r["members_json"] or "[]")
    out = {"id": r["id"], "name": r["name"], "status": r["status"],
           "source": r["source"], "trello_card": r["trello_card"],
           "members": members, "created_by": r["created_by"],
           "created_at": r["created_at"], "updated_at": r["updated_at"]}
    if resolve:
        out["resolved"] = _resolve(members)
        out["social"] = social_state(r["name"])
    return out


def list_batches() -> dict:
    rows = [dict(r) for r in db.query(
        "SELECT * FROM release_batches ORDER BY status='published', created_at DESC")]
    return {"batches": [_row_out(r) for r in rows],
            "social_probe": "local" if SOCIAL_DIR.is_dir() else "unavailable"}


def save_batch(name: str, members: list[dict], admin,
               batch_id: int | None = None, status: str | None = None,
               source: str = "manual", trello_card: str = "") -> dict:
    name = (name or "").strip()
    if not name:
        raise HTTPException(422, detail={"error": "empty_name"})
    if status is not None and status not in ("planned", "published", "archived"):
        raise HTTPException(422, detail={"error": "bad_status", "detail": status})
    clean = _clean_members(members)
    now = time.time()
    if batch_id is None:
        existing = db.query_one("SELECT id FROM release_batches WHERE name=?", (name,))
        if existing:
            batch_id = existing["id"]
    if batch_id is None:
        db.execute(
            "INSERT INTO release_batches(name,status,members_json,source,trello_card,"
            "created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (name, status or "planned", json.dumps(clean), source, trello_card,
             admin.username, now, now))
        row = db.query_one("SELECT * FROM release_batches WHERE name=?", (name,))
    else:
        if not db.query_one("SELECT id FROM release_batches WHERE id=?", (batch_id,)):
            raise HTTPException(404, detail={"error": "no_batch", "detail": batch_id})
        db.execute(
            "UPDATE release_batches SET name=?, members_json=?, updated_at=?"
            + (", status=?" if status else "") + " WHERE id=?",
            (name, json.dumps(clean), now, *((status,) if status else ()), batch_id))
        row = db.query_one("SELECT * FROM release_batches WHERE id=?", (batch_id,))
    return _row_out(dict(row))


def delete_batch(batch_id: int) -> dict:
    n = db.execute_rowcount("DELETE FROM release_batches WHERE id=?", (batch_id,))
    if not n:
        raise HTTPException(404, detail={"error": "no_batch", "detail": batch_id})
    return {"deleted": batch_id}


# --------------------------------------------------------------------------- #
# Trello "TG Release Schedule" import (seed only — always editable after)
# --------------------------------------------------------------------------- #
_CID_ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|", re.MULTILINE)


def _match_tg(token: str, tg_docs: dict) -> str | None:
    """Family token from a card ('Takayama') → the staging TripGroup id, trying
    the id itself, the prod-style `<id>_Trip`, then a unique prefix match."""
    for cand in (token, f"{token}_Trip"):
        if cand in tg_docs:
            return cand
    pref = [g for g in tg_docs if g.lower().startswith(token.lower() + "_")
            or g.lower() == token.lower()]
    return pref[0] if len(pref) == 1 else None


def import_trello(admin) -> dict:
    """One batch per card on the 'TG Release Schedule' lane. Structured cards
    (a families table with `contentID` rows) resolve to group members; bare cards
    import empty for the admin to fill. Existing trello-sourced batches update in
    place (matched by card id); manual batches are never touched."""
    trello_dir = config.SCRIPTS_ROOT / "Trello"
    if str(trello_dir) not in sys.path:
        sys.path.insert(0, str(trello_dir))
    try:
        import trello_common as tc
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, detail={
            "error": "trello_unavailable",
            "detail": f"could not import Scripts/Trello/trello_common: {e}"})
    try:
        ids = tc.load_ids()
        lists = tc.get_lists(ids["board_id"])
        sched = next((ln for ln in lists
                      if "release schedule" in ln["name"].lower()), None)
        if sched is None:
            raise HTTPException(404, detail={"error": "no_schedule_lane"})
        cards = [c for c in tc.get_cards(ids["board_id"])
                 if c.get("idList") == sched["id"]]
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, detail={"error": "trello_error", "detail": str(e)})

    _by_trip, tg_docs = tripdesc._tripgroup_index()
    imported, updated, unmatched = [], [], []
    for c in cards:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        members: list[dict] = []
        for token in _CID_ROW_RE.findall(c.get("desc") or ""):
            token = token.strip()
            if token.lower() in ("contentid", "content id"):
                continue
            tg = _match_tg(token, tg_docs)
            if tg:
                members.append({"kind": "group", "id": tg})
            else:
                unmatched.append({"card": name, "token": token})
        existing = db.query_one(
            "SELECT * FROM release_batches WHERE trello_card=? AND source='trello'",
            (c.get("id"),))
        if existing:
            # Update the name; keep the (possibly hand-edited) membership unless
            # the card carries a table and the batch is still empty.
            keep = json.loads(existing["members_json"] or "[]") or members
            save_batch(name, keep, admin, batch_id=existing["id"],
                       source="trello", trello_card=c.get("id") or "")
            updated.append(name)
        else:
            save_batch(name, members, admin, source="trello",
                       trello_card=c.get("id") or "")
            imported.append(name)
    return {"imported": imported, "updated": updated, "unmatched": unmatched}


# --------------------------------------------------------------------------- #
# Social-post readiness (release-prep chip)
# --------------------------------------------------------------------------- #
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def social_state(batch_name: str) -> dict:
    """Are the launch posts + news entry drafted for this batch? Files live in
    SOCIAL_DIR as `<Release>_meta.txt` / `<Release>_linkedin.txt` (Scripts
    social/README naming); matched loosely (normalised containment) because batch
    names carry spaces/punctuation the filenames don't. 'unknown' on hosts
    without the Comms tree — never a false 'missing'."""
    if not SOCIAL_DIR.is_dir():
        return {"state": "unknown", "meta": None, "linkedin": None, "news": None}
    key = _norm(batch_name)
    found = {"meta": None, "linkedin": None}
    try:
        for p in SOCIAL_DIR.iterdir():
            m = re.fullmatch(r"(.+)_(meta|linkedin)\.txt", p.name, re.IGNORECASE)
            if not m:
                continue
            stem_key = _norm(m.group(1))
            if stem_key and (stem_key in key or key in stem_key):
                found[m.group(2).lower()] = p.name
    except OSError as e:
        log.warning("release_batches: social dir unreadable: %s", e)
        return {"state": "unknown", "meta": None, "linkedin": None, "news": None}
    news = None
    try:
        if NEWS_JSON.is_file():
            news = key in _norm(NEWS_JSON.read_text(encoding="utf-8"))
    except OSError:
        news = None
    state = ("ready" if found["meta"] and found["linkedin"]
             else "partial" if (found["meta"] or found["linkedin"])
             else "missing")
    return {"state": state, "meta": found["meta"], "linkedin": found["linkedin"],
            "news": news}


# --------------------------------------------------------------------------- #
# Recently published (published_trips ledger)
# --------------------------------------------------------------------------- #
def recently_published(months: int = 12) -> dict:
    cutoff = time.time() - months * 30.44 * 86400
    titles = {r["trip_id"]: r["title"]
              for r in db.query("SELECT trip_id, title FROM trip_list_cache")}
    rows = []
    for r in db.query(
            "SELECT trip_id, published_at, published_by, batch_id, source, note "
            "FROM published_trips WHERE published_at >= ? "
            "ORDER BY published_at DESC", (cutoff,)):
        rows.append({**dict(r), "title": titles.get(r["trip_id"]) or r["trip_id"]})
    return {"months": months, "trips": rows}
