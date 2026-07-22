"""
Delta reviews — R2-published change manifests for already-approved trips.

When the pipeline regenerates a handful of clips on a trip that has ALREADY passed
its review (e.g. the 2026-07-22 quiz-variety remediation), the Scripts repo uploads
a manifest to ``review-audio/_delta/<contentId>.json`` describing exactly which
scenes/clips changed:

    {"contentId": "...", "created": "2026-07-22", "reason": "...",
     "scenes": [{"index": 6, "clips": ["6_q", "6_a1", ...],
                 "questionKey": "...", "questionOptionKeys": [...]}]}

The app surfaces each manifest as a compact "delta card" (sessions.delta_cards) for
trips in ``completed_trips`` ONLY — a trip mid-full-review is never in that table, so
an open full review supersedes its delta automatically. Opening a card seeds a normal
session restricted to the changed fields (sessions.create_or_resume with ``delta=``);
approving it deletes the manifest here, which is the consumed signal the Scripts side
polls for (docs/delta-review.md).

The manifest's questionKey/questionOptionKeys mirror what was written to staging and
are DISPLAY-side hints only — the session seeds its text from live staging (source of
truth), and this module reads just ``scenes[].index`` + ``scenes[].clips``.

This module is the pure R2/data layer (list, fetch, parse, delete) — it must not
import sessions (sessions imports it). Every entry point is best-effort: R2 trouble
logs a WARN and degrades to "no deltas", never a 500.
"""

from __future__ import annotations

import json
import re
import threading
import time

from . import review_audio

PREFIX = "_delta/"

# The trip list fetches the card list on every load; a short TTL keeps that from
# hammering R2 (one LIST + one GET per manifest per refresh) while still surfacing
# a freshly-uploaded manifest within a minute.
_CACHE_TTL_S = 60.0
_LOCK = threading.Lock()
_CACHE: tuple[float, list[dict]] | None = None


# Clip stems mirror mp3_name: "6"=SceneDesc, "6_q"=questionKey, "6_a1"=questionOption[0].
# "6_a" (the spoken ANSWER take) has no reviewable field and parses as unrecognised.
_CLIP_RE = re.compile(r"^(\d+)(?:_(q|a([1-9]\d*)))?$")


def clip_field(clip: str) -> tuple[int, str, int | None] | None:
    """(scene_index, field_path, option_index) for a delta clip stem, or None when the
    stem names nothing the app reviews. Scene indexes are 0-based, matching both the
    mp3 stems and quickTrips[] (verified against the 2026-07-22 manifests)."""
    m = _CLIP_RE.match(str(clip or "").strip())
    if not m:
        return None
    idx = int(m.group(1))
    if m.group(2) is None:
        return (idx, "SceneDesc", None)
    if m.group(2) == "q":
        return (idx, "questionKey", None)
    return (idx, "questionOption", int(m.group(3)) - 1)


def field_keys(doc: dict) -> set[tuple[int, str, int | None]]:
    """Every reviewable (scene_index, field_path, option_index) the manifest names.
    Unrecognised clip stems are skipped LOUDLY — a silent skip would make a delta
    look fully covered when part of it never reached the reviewer."""
    keys: set[tuple[int, str, int | None]] = set()
    for sc in doc.get("scenes") or []:
        for c in sc.get("clips") or []:
            k = clip_field(c)
            if k is not None:
                keys.add(k)
            else:
                print(f"[deltas] WARN unrecognised clip {c!r} in "
                      f"{doc.get('contentId')} — not reviewable in-app, skipped")
    return keys


def clip_stems(doc: dict) -> list[str]:
    """The manifest's clip stems (deduped, order kept) — the files to pull from
    ``review-audio/<cid>/`` at seed time."""
    seen: list[str] = []
    for sc in doc.get("scenes") or []:
        for c in sc.get("clips") or []:
            c = str(c or "").strip()
            if c and c not in seen and clip_field(c) is not None:
                seen.append(c)
    return seen


def _key(trip_id: str) -> str:
    return f"{PREFIX}{trip_id}.json"


def _parse(raw: bytes, trip_id: str) -> dict | None:
    try:
        doc = json.loads(raw)
    except ValueError as e:
        print(f"[deltas] WARN {_key(trip_id)} is not valid JSON: {e}")
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("scenes"), list):
        print(f"[deltas] WARN {_key(trip_id)} has no scenes[] — ignored")
        return None
    cid = doc.get("contentId")
    if cid and cid != trip_id:
        # The filename is what keys the audio/session — a mismatched body is a
        # producer bug we must not paper over by trusting either side.
        print(f"[deltas] WARN {_key(trip_id)} carries contentId={cid!r} — ignored")
        return None
    if not field_keys(doc):
        print(f"[deltas] WARN {_key(trip_id)} names no reviewable clips — ignored")
        return None
    return doc


def fetch(trip_id: str) -> dict | None:
    """The live manifest for one trip (no cache — open-time must be fresh), or None
    when absent/unreadable/invalid."""
    try:
        s3 = review_audio._r2()
        if s3 is None:
            return None
        body = s3.get_object(Bucket=review_audio.BUCKET,
                             Key=_key(trip_id))["Body"].read()
    except Exception:  # noqa: BLE001 — 404 is the normal "no delta" answer
        return None
    return _parse(body, trip_id)


def list_all(force: bool = False) -> list[dict]:
    """Every valid manifest under ``_delta/``:
    [{"trip_id", "doc", "last_modified" (epoch seconds)}]. TTL-cached; [] on any R2
    trouble (loud, never raises)."""
    global _CACHE
    with _LOCK:
        if not force and _CACHE is not None and time.time() - _CACHE[0] < _CACHE_TTL_S:
            return _CACHE[1]
    entries: list[dict] = []
    try:
        s3 = review_audio._r2()
        if s3 is None:
            return []
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=review_audio.BUCKET, Prefix=PREFIX):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(".json") or key == PREFIX:
                    continue
                tid = key[len(PREFIX):-len(".json")]
                body = s3.get_object(Bucket=review_audio.BUCKET, Key=key)["Body"].read()
                doc = _parse(body, tid)
                if doc is None:
                    continue
                entries.append({"trip_id": tid, "doc": doc,
                                "last_modified": obj["LastModified"].timestamp()})
    except Exception as e:  # noqa: BLE001 — the trip list must load without R2
        print(f"[deltas] WARN could not list {PREFIX}: {e}")
        return []
    with _LOCK:
        _CACHE = (time.time(), entries)
    return entries


def invalidate() -> None:
    global _CACHE
    with _LOCK:
        _CACHE = None


def delete_object(trip_id: str) -> bool:
    """Consume a delta: remove its manifest. Object-gone is how the Scripts side
    verifies consumption, so a failure here is loud — the caller decides what else
    to say. Idempotent (deleting an absent key succeeds)."""
    try:
        s3 = review_audio._r2()
        if s3 is None:
            print(f"[deltas] WARN cannot delete {_key(trip_id)} — R2 unavailable")
            return False
        s3.delete_object(Bucket=review_audio.BUCKET, Key=_key(trip_id))
        invalidate()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[deltas] WARN could not delete {_key(trip_id)}: {e}")
        return False
