r"""export_completed.py — one-shot rebuild of completed_trips.json from review.db.

The server keeps the file fresh on approve / manual-complete / un-complete
(sessions.export_completed_trips); this exists for backfill, disaster recovery
(rebuild from a restored review.db), and running while the server is DOWN.

Read-only: review.db is opened with mode=ro (no WAL side effects), so it is also
safe to run while the server is up.

    py -3.12 scripts/export_completed.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app import config  # noqa: E402  (bootstraps the Scripts sys.path / .env)
from app import sessions  # noqa: E402


def main() -> None:
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


if __name__ == "__main__":
    main()
