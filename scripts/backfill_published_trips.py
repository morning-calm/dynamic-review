"""One-time backfill of the `published_trips` ledger from Trello `published=` stamps.

The ledger (review.db, drives the Publisher's "Recently published" list) is stamped
going forward by the publish job's apply path (routes_admin). History predating the
ledger lives only in the Trello cards' [review] blocks — the per-rung
`published=YYYY-MM-DD` annotations mark_published.py writes. This walks every card,
parses the blocks with Scripts' own review_block.parse_block (never a re-port), and
inserts rows it doesn't already have (source='trello_backfill'; a 'publisher'-stamped
row always wins — it has the exact time and user).

Run from the repo root on a machine with the Scripts checkout + Trello creds:
    py -3.12 scripts/backfill_published_trips.py [--apply]
Dry-run by default. Idempotent.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))
from app import config  # noqa: E402  (paths only; no server import)

sys.path.insert(0, str(config.SCRIPTS_ROOT / "Trello"))
import review_block  # noqa: E402
import trello_common as tc  # noqa: E402

DB_PATH = REPO / "backend" / "review.db"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write to review.db")
    args = ap.parse_args()

    ids = tc.load_ids()
    cards = tc.get_cards(ids["board_id"])
    stamped: dict[str, str] = {}
    for card in cards:
        for row in review_block.parse_block(card.get("desc") or ""):
            cid, pub = row.get("cid") or row.get("id"), row.get("published")
            if cid and pub:
                stamped[cid] = pub

    print(f"{len(stamped)} rung(s) carry a published= stamp on Trello")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    have = {r["trip_id"] for r in conn.execute("SELECT trip_id FROM published_trips")}
    new = 0
    for cid, date in sorted(stamped.items()):
        if cid in have:
            continue
        try:
            at = datetime.strptime(date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc).timestamp()
        except ValueError:
            print(f"  !! {cid}: unparseable date {date!r} — skipped")
            continue
        new += 1
        print(f"  + {cid}  published {date}")
        if args.apply:
            conn.execute(
                "INSERT INTO published_trips(trip_id, published_at, published_by,"
                " source, note) VALUES(?,?,?,?,?)",
                (cid, at, "", "trello_backfill", "backfill from Trello published="))
    if args.apply:
        conn.commit()
        print(f"APPLIED: {new} row(s) inserted ({len(have)} already present)")
    else:
        print(f"DRY RUN: would insert {new} row(s) ({len(have)} already present) — "
              "re-run with --apply")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
