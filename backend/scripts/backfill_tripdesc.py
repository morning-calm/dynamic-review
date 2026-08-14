#!/usr/bin/env python3
"""One-off backfill of tripgroup_reviews for COMPLETED families no longer in the
review manifest (the lazy seeding only covers manifest families).

Dave confirmed EXACTLY these five (2026-08-14) — all English-target Scotland/UK,
so each completes in one admin approval, no translation stage:
  Jedburgh1_TownAbbey, Jedburgh2_CastleJail, HadriansWall, Melrose, Abbotsford

Run ON THE LIVE HOST (the laptop — review.db lives there), with the backend stopped
or running (SQLite WAL; the app's writes are serialized through its own connection,
ours is a short independent one):

  cd ~/Desktop/Server/review-app/backend && python3 scripts/backfill_tripdesc.py

Idempotent: already-seeded TripGroups are skipped. Pass trip ids as argv to backfill
a different set (one representative trip id per family is enough).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# One representative (completed) trip id per confirmed family — the TripGroup is
# resolved via the staging TripGroups.trips[] reverse index, same as the lazy seed.
CONFIRMED = [
    "Jedburgh1_TownAbbey_EN",
    "Jedburgh2_CastleJail_EN",
    "HadriansWall_EN",
    "Melrose_EN",
    "Abbotsford_EN",
]


def main() -> None:
    from app import db, tripdesc
    db.init()
    trip_ids = sys.argv[1:] or CONFIRMED
    out = tripdesc.seed_trips(trip_ids)
    for tg in out["seeded"]:
        print(f"seeded   {tg}")
    for t in out["skipped"]:
        print(f"skipped  {t} (already seeded, or no TripGroup lists it)")
    print(f"\n{len(out['seeded'])} seeded, {len(out['skipped'])} skipped")


if __name__ == "__main__":
    main()
