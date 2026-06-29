"""
Firebase staging access — read the live Trip / TripGroup, and the targeted writes
performed at /submit. Thin wrapper over the reused ``stage9.common`` singletons so
there is exactly ONE Firestore client in the process.

Staging is the source of truth (the GDoc is out of the loop). We re-fetch live on
submit so concurrent edits made elsewhere are never clobbered.
"""

from __future__ import annotations

import re

from . import config  # noqa: F401  (ensures SCRIPTS_ROOT on sys.path) — keep first
from stage9.common import db, get_trip, paths_for  # reused, READ-ONLY import

# PORTED (not imported) from Research and Writing/stages/09_gdoc_to_firebase.py — that
# module pulls google_auth/trip_config at import time (OAuth side effects), so we copy
# the 5-line category parser verbatim instead.
TRIP_TYPE_RE = re.compile(r"^Trip Type:\s*(.+)$", re.MULTILINE)


def trip_categories_from_description(desc: str) -> list:
    """Parse the `Trip Type: A, B, C` line into a list."""
    m = TRIP_TYPE_RE.search(desc or "")
    if not m:
        return []
    return [c.strip() for c in m.group(1).split(",") if c.strip()]

__all__ = [
    "db",
    "get_trip",
    "paths_for",
    "trip_categories_from_description",
    "tripgroup_id_for",
    "get_tripgroup",
    "update_trip_text",
    "update_tripgroup",
]


def tripgroup_id_for(trip_id: str) -> str:
    """TripGroup id = trip id without the trailing _EN (per the contract)."""
    return trip_id[:-3] if trip_id.endswith("_EN") else trip_id


def get_tripgroup(trip_id: str) -> tuple[str, dict | None]:
    tg_id = tripgroup_id_for(trip_id)
    snap = db().collection("TripGroups").document(tg_id).get()
    return tg_id, (snap.to_dict() if snap.exists else None)


def update_trip_text(trip_id: str, quick_trips: list | None, top_level: dict) -> None:
    """ONE .update() with top-level text + (only when a scene field changed) the
    freshly-merged quickTrips. S5: never rewrite the whole array for a title-only /
    description-only edit — that needlessly widens the clobber window vs concurrent
    edits to other scenes."""
    payload = dict(top_level or {})
    if quick_trips is not None:
        payload["quickTrips"] = quick_trips
    if payload:
        db().collection("Trips").document(trip_id).update(payload)


def update_tripgroup(tg_id: str, description: str, categories: list) -> None:
    db().collection("TripGroups").document(tg_id).update(
        {"descriptionTarget": description, "tripCategories": categories}
    )


def merge_categories(old_desc: str, new_desc: str, live_categories: list) -> list:
    """Re-derive semantic categories from the edited 'Trip Type:' line WITHOUT
    clobbering non-semantic tags (the CEFR level tags 'Beginner (A1)' etc. are added
    by Stage-9 enrichment and are NOT in the Trip Type line). We keep every existing
    category that did not come from the OLD Trip Type line, then prepend the new
    semantic set."""
    old_semantic = trip_categories_from_description(old_desc or "")
    new_semantic = trip_categories_from_description(new_desc or "")
    preserved = [c for c in (live_categories or []) if c not in old_semantic]
    out = list(new_semantic)
    for c in preserved:
        if c not in out:
            out.append(c)
    return out
