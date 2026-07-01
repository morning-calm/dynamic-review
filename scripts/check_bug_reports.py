"""Print open/unresolved bug reports from review.db — the "so I notice them" check.

Run it at the start of a Claude session (or on a schedule) to surface new reports:
    PYTHONIOENCODING=utf-8 py -3.12 scripts/check_bug_reports.py

Reads the local DB directly (no server needed). Exit code 1 if there are open reports, 0 if
none — so a scheduled task/CI step can branch on "are there new bug reports to triage".
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app import bug_reports, db  # noqa: E402


def _ago(ts: float) -> str:
    s = max(0, int(time.time() - ts))
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def main() -> int:
    db.init()
    rows = bug_reports.open_reports_digest()
    if not rows:
        print("No open bug reports.")
        return 0
    print(f"{len(rows)} open bug report(s):\n")
    for r in rows:
        loc = f"{r['trip_id']} · {r['field_path']}"
        if r["scene_index"] is not None:
            loc += f" (scene {r['scene_index']})"
        print(f"  #{r['id']} [{r['status']}] {loc}")
        print(f"     by {r['reporter']} · {_ago(r['created_at'])}")
        body = r["body"].strip().replace("\n", " ")
        print(f"     {body[:200]}{'…' if len(body) > 200 else ''}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
