#!/usr/bin/env python3
"""Remove tripgroup_reviews rows that were seeded for TripGroups already live on
PRODUCTION Firebase — shipped means the description was checked, so those families
never needed review (the seed guard added 2026-08-21 stops NEW ones; this cleans up
the rows created before it).

Only UNTOUCHED rows are deleted:
  status = 'pending_en'  AND  en_by IS NULL  AND  en_text = en_original
Anything an admin has edited/approved, anything translating/pending_tl/done, is
left alone — re-run `backfill_tripdesc.py <trip_id>` later if one of those should
in fact be reviewed.

Run ON THE LIVE HOST (the laptop — review.db lives there), from backend/:

  python3 scripts/cleanup_tripdesc_prod.py            # dry-run: print what would go
  python3 scripts/cleanup_tripdesc_prod.py --apply    # delete

Requires prod_tripgroups.json at the repo root (git pull first). Refuses to run
without it — an absent/empty snapshot must never look like "nothing to clean".
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry-run)")
    args = ap.parse_args()

    from app import config, db
    db.init()

    try:
        data = json.loads(config.PROD_TRIPGROUPS_PATH.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        sys.exit(f"prod snapshot unreadable ({config.PROD_TRIPGROUPS_PATH}): {e}\n"
                 f"→ run Trello/export_review_trips.py on the workstation, git pull here.")
    prod_ids = set(data.get("tripgroup_ids") or [])
    if not prod_ids:
        sys.exit("prod snapshot holds no ids — refusing to run.")
    print(f"prod snapshot: {len(prod_ids)} TripGroups "
          f"(generated {data.get('generated_at', '?')})")

    rows = db.query("SELECT tg_id, language, family, status, en_by, "
                    "en_text = en_original AS untouched FROM tripgroup_reviews")
    doomed, kept = [], []
    for r in rows:
        if r["tg_id"] not in prod_ids:
            continue
        if r["status"] == "pending_en" and r["en_by"] is None and r["untouched"]:
            doomed.append(r)
        else:
            kept.append(r)

    for r in doomed:
        print(f"  DELETE  {r['tg_id']:<30} ({r['language']}, {r['family']})")
    for r in kept:
        print(f"  keep    {r['tg_id']:<30} (on prod but {r['status']}"
              f"{', edited' if not r['untouched'] or r['en_by'] else ''} — not touched)")
    if not doomed:
        print("nothing to delete.")
        return
    if not args.apply:
        print(f"\ndry-run: {len(doomed)} row(s) would be deleted — re-run with --apply")
        return
    for r in doomed:
        db.execute("DELETE FROM tripgroup_reviews WHERE tg_id=? AND status='pending_en' "
                   "AND en_by IS NULL AND en_text = en_original", (r["tg_id"],))
    print(f"\ndeleted {len(doomed)} row(s).")


if __name__ == "__main__":
    main()
