"""Hot-backup review.db (and optionally work/) to Cloudflare R2 — disaster recovery.

review.db is the ONLY copy of in-progress review state (edits, flags, comments,
coverage, version history, users, bug threads). It lives on the server machine and is
not in git. This snapshots it to R2 so a lost disk doesn't lose reviewer work.

    py -3.12 scripts/backup_review_db.py            # snapshot review.db -> R2
    py -3.12 scripts/backup_review_db.py --work     # also zip+upload backend/work/
    py -3.12 scripts/backup_review_db.py restore     # pull latest DB back to a temp file

"Hot" = uses SQLite's online backup API, so it produces a *consistent* snapshot even
while uvicorn holds the DB open (WAL frames included). Safe to run on a schedule while
the app is live. R2 is a backup TARGET, not a live store — the app always reads a local
review.db on disk, so recovery means downloading the snapshot back to backend/review.db
and restarting uvicorn (see `restore`).

Bucket/prefix: reuses the existing `review-audio` bucket (same Cloudfare_* creds as the
app) under a `_db-backups/` key prefix, override with REVIEW_BACKUP_BUCKET. Each run
writes a timestamped key AND overwrites `_db-backups/review-latest.db` for easy restore.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app import config, review_audio  # noqa: E402  (imports config -> loads .env, sys.path)

BACKUP_BUCKET = os.environ.get("REVIEW_BACKUP_BUCKET", review_audio.BUCKET)
DB_PREFIX = "_db-backups"
WORK_PREFIX = "_work-backups"
LATEST_DB_KEY = f"{DB_PREFIX}/review-latest.db"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _consistent_db_copy(dest: Path) -> None:
    """Snapshot review.db to *dest* via the SQLite online-backup API (WAL-safe)."""
    # Read-only source connection; .backup() copies a committed, consistent image even
    # with concurrent writers (uvicorn) — never a torn half-written file.
    src = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _upload(local: Path, key: str, content_type: str) -> bool:
    s3 = review_audio._r2()
    if s3 is None:
        print("[backup] R2 creds missing (Cloudfare_* in the Scripts .env) — aborting.")
        return False
    s3.upload_file(str(local), BACKUP_BUCKET, key,
                   ExtraArgs={"ContentType": content_type})
    print(f"[backup] uploaded s3://{BACKUP_BUCKET}/{key}  ({local.stat().st_size:,} bytes)")
    return True


def backup(include_work: bool) -> int:
    if not config.DB_PATH.exists():
        print(f"[backup] no DB at {config.DB_PATH} — nothing to back up.")
        return 1

    stamp = _stamp()
    with tempfile.TemporaryDirectory() as td:
        snap = Path(td) / "review.db"
        _consistent_db_copy(snap)
        ok = _upload(snap, f"{DB_PREFIX}/review-{stamp}.db", "application/x-sqlite3")
        if not ok:
            return 1
        # Overwrite the fixed "latest" key so `restore` needs no timestamp lookup.
        _upload(snap, LATEST_DB_KEY, "application/x-sqlite3")

        if include_work:
            work = config.WORK_ROOT
            if not any(work.iterdir()) if work.exists() else True:
                print("[backup] work/ empty or missing — skipping work archive.")
            else:
                zpath = Path(td) / "work.zip"
                _zip_dir(work, zpath)
                _upload(zpath, f"{WORK_PREFIX}/work-{stamp}.zip", "application/zip")
    print("[backup] done.")
    return 0


def _zip_dir(root: Path, dest_zip: Path) -> None:
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in root.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(root))


def restore() -> int:
    """Download the latest DB snapshot to a temp file (never clobbers the live DB)."""
    s3 = review_audio._r2()
    if s3 is None:
        print("[restore] R2 creds missing — aborting.")
        return 1
    out = config.BACKEND_ROOT / "review.restored.db"
    s3.download_file(BACKUP_BUCKET, LATEST_DB_KEY, str(out))
    print(f"[restore] wrote {out}")
    print("[restore] To go live: stop uvicorn, then replace backend/review.db with it:")
    print(f"          move-item -force '{out}' '{config.DB_PATH}'   # remove -wal/-shm first")
    print("          then restart uvicorn.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Hot-backup review.db to Cloudflare R2.")
    ap.add_argument("mode", nargs="?", default="backup", choices=["backup", "restore"])
    ap.add_argument("--work", action="store_true",
                    help="also zip and upload backend/work/ (can be large)")
    args = ap.parse_args()
    return restore() if args.mode == "restore" else backup(args.work)


if __name__ == "__main__":
    raise SystemExit(main())
