r"""export_completed.py — one-shot rebuild of the completed-trips snapshot from review.db.

The server keeps the snapshot fresh on approve / manual-complete / un-complete
(sessions.export_completed_trips); this exists for backfill, disaster recovery
(rebuild from a restored review.db), running while the server is DOWN, and — the
common case now — REPAIRING THE R2 MIRROR after a push failed (the server's push is
best-effort so it can never fail a reviewer's approve, which means a failure leaves the
mirror stale until something re-pushes; that something is this).

Writes BOTH surfaces, and the R2 one is what Stage 9 reads across machines:
    local : <repo>/completed_trips.json          (same-machine consumers only)
    R2    : review-audio/_bus/completed_trips.json

Read-only on review.db (mode=ro, no WAL side effects), so it is safe to run while the
server is up.

    py -3.12 scripts/export_completed.py              # rebuild + push to R2
    py -3.12 scripts/export_completed.py --no-push    # local file only
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app import config  # noqa: E402  (bootstraps the Scripts sys.path / .env)
from app import review_bus, sessions  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-push", action="store_true",
                    help="skip the R2 mirror (local file only)")
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT trip_id, completed_by, completed_at, method, session_id, note "
        "FROM completed_trips ORDER BY completed_at").fetchall()
    con.close()
    payload = sessions.completed_export_payload(rows)
    sessions.write_completed_export(payload)
    per = {}
    for t in payload["trips"]:
        per[t["method"]] = per.get(t["method"], 0) + 1
    print(f"wrote {sessions.COMPLETED_EXPORT_PATH}")
    print(f"  trips: {len(payload['trips'])} "
          f"({', '.join(f'{k}={v}' for k, v in sorted(per.items())) or 'none'})")
    print(f"  generated_at: {payload['generated_at']}")

    if args.no_push:
        print("  R2 mirror: SKIPPED (--no-push) — cross-machine consumers stay stale")
        return
    if review_bus.put_completed_snapshot(payload):
        print(f"  R2 mirror: {review_bus.BUCKET}/{review_bus.COMPLETED_KEY}  ✓")
    else:
        # put_completed_snapshot already logged why. Exit non-zero: unlike the server's
        # fire-and-forget hook, this script's ONLY job is to make the mirror current, so
        # a failure here must be visible to whoever/whatever ran it.
        sys.exit("  R2 mirror: FAILED — see the error above")


if __name__ == "__main__":
    main()
