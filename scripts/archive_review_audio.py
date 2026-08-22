r"""archive_review_audio.py — retire a PUBLISHED trip's R2 `review-audio/<cid>/` folder
to a local backup, and put it back when a delta re-review needs it.

WHY THIS EXISTS. `review-audio/<cid>/` is where a trip's reviewer-CORRECTED masters live
(on the masters-less live laptop it is their only durable home — see
backend/app/review_audio.py). Once the trip is live on production those bytes no longer
need to sit in the bucket, but they must not be thrown away: this tool downloads the whole
prefix, VERIFIES the copy, and only then deletes the R2 objects. The local backup folder
becomes the sole copy of that audio, so treat it like the DB backup — it is not in git.

    py -3.12 scripts/archive_review_audio.py audit [CID ...]
    py -3.12 scripts/archive_review_audio.py archive CID [CID ...] [--apply]
    py -3.12 scripts/archive_review_audio.py archive --all-published [--apply]
    py -3.12 scripts/archive_review_audio.py restore CID [--apply]

DRY-RUN BY DEFAULT. Without --apply nothing is downloaded, uploaded or deleted — you get
the plan and the refusals only.

TWO HARD GATES before anything is deleted, checked per trip:
  1. the trip's doc EXISTS ON PRODUCTION Firebase (`Trips/<cid>`), read with the
     workstation-only production credential `Scripts/firebase_production_key2.json`
     (the same key publish_inbox.py / PublishTrips-Select.py use; override with
     --prod-key). "Published" is never inferred from Trello lanes or review.db.
  2. the cid is NOT in the current review manifest `trips_to_review.json` (either the
     `trips` review lanes or the `final_check` lanes) — a trip someone may still open
     keeps its audio.
Neither gate can be skipped. A cid that fails one is reported and left completely alone.

RESTORE EXISTS BECAUSE DELTAS PULL FRESH FROM R2. A delta re-review of a completed trip
downloads its clips straight from `review-audio/<cid>/` every time, so if a delta manifest
turns up for an archived trip, run `restore <cid> --apply` first. Restore NEVER overwrites
an object that is already on R2 (the standing rule: corrected audio is never overwritten or
resurrected over) — an existing key is skipped, and a size mismatch is reported, not fixed.

Backup root: %REVIEW_APP_R2_BACKUP_DIR% (default C:\Users\david\Documents\R2 Backup),
one folder per cid, R2 subpaths (originals/, versions/) preserved, plus a `_manifest.json`.

Workstation tool: it needs the Scripts .env (`Cloudfare_*` R2 creds) and the production
Firebase key, both of which live only here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "backend" / "review.db"
MANIFEST = ROOT / "trips_to_review.json"
BUCKET = "review-audio"
MANIFEST_NAME = "_manifest.json"

# Bucket-level prefixes that are NOT trips — nothing here may ever address them.
RESERVED_PREFIXES = ("_bus", "_db-backups", "_delta", "_publish-jobs", "_snapshots")

DEFAULT_BACKUP_DIR = r"C:\Users\david\Documents\R2 Backup"
PROD_KEY_NAME = "firebase_production_key2.json"


def backup_root() -> Path:
    return Path(os.environ.get("REVIEW_APP_R2_BACKUP_DIR", DEFAULT_BACKUP_DIR))


def scripts_root() -> Path:
    """The dynamic-content checkout (holds .env + the Firebase keys)."""
    try:
        sys.path.insert(0, str(ROOT / "backend"))
        from app import config  # noqa: PLC0415 — optional, only for its path bootstrap
        return Path(config.SCRIPTS_ROOT)
    except Exception:  # noqa: BLE001 — the app package is not required for this tool
        return Path(os.environ.get("REVIEW_APP_SCRIPTS_ROOT", r"D:\Dynamic Languages\Scripts"))


SCRIPTS = scripts_root()


# ── env / R2 ─────────────────────────────────────────────────────────────────
def load_env() -> None:
    """Read the Scripts .env into os.environ (no dotenv dependency). Existing
    environment values win, so an exported credential always overrides the file."""
    path = SCRIPTS / ".env"
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception as e:  # noqa: BLE001
        print(f"WARN could not read {path}: {e}")


def r2():
    """S3 client for the R2 `review-audio` bucket, or a clear SystemExit."""
    load_env()
    try:
        import boto3  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"boto3 is not installed ({e}) — py -3.12 -m pip install boto3")

    def envget(*names):
        for n in names:
            if os.environ.get(n):
                return os.environ[n]
        return None

    ak = envget("Cloudfare_Access_Key_ID", "R2_ACCESS_KEY_ID")
    sk = envget("Cloudfare_Secret_Access_Key", "R2_SECRET_ACCESS_KEY")
    ep = envget("Cloudfare_S3_API_Endpoint", "R2_ENDPOINT")
    if not (ak and sk and ep):
        raise SystemExit(
            f"R2 creds missing — set Cloudfare_Access_Key_ID / Cloudfare_Secret_Access_Key /"
            f" Cloudfare_S3_API_Endpoint in {SCRIPTS / '.env'} (or R2_* in the environment)")
    return boto3.client("s3", endpoint_url=ep,
                        aws_access_key_id=ak, aws_secret_access_key=sk)


def list_prefix(s3, cid: str) -> list[dict]:
    """Every object under `<cid>/`, as {key, name, size, etag}."""
    prefix = f"{cid}/"
    out: list[dict] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.startswith(prefix) or key.endswith("/"):
                continue
            out.append({
                "key": key,
                "name": key[len(prefix):],
                "size": int(obj["Size"]),
                "etag": (obj.get("ETag") or "").strip('"'),
            })
    out.sort(key=lambda o: o["key"])
    return out


# ── production Firebase (the only acceptable proof a trip is published) ───────
_PROD_DB = None


def prod_db(prod_key: str | None):
    """Firestore client for PRODUCTION. Raises SystemExit with a plain message when the
    credential or firebase-admin is unavailable — never a traceback."""
    global _PROD_DB
    if _PROD_DB is not None:
        return _PROD_DB
    keyfile = Path(prod_key) if prod_key else (SCRIPTS / PROD_KEY_NAME)
    if not keyfile.exists():
        raise SystemExit(
            f"Production Firebase key not found: {keyfile}\n"
            f"  This tool must confirm each trip really is on production before deleting\n"
            f"  anything. Pass --prod-key <path> (the same {PROD_KEY_NAME} the Scripts\n"
            f"  publisher uses — workstation only).")
    try:
        import firebase_admin  # noqa: PLC0415
        from firebase_admin import credentials, firestore  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"firebase-admin is not installed ({e}) — "
                         f"py -3.12 -m pip install firebase-admin")
    try:
        app = firebase_admin.get_app("archive-review-audio-prod")
    except ValueError:
        app = firebase_admin.initialize_app(
            credentials.Certificate(str(keyfile)), name="archive-review-audio-prod")
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"Could not initialise production Firebase: {e}")
    try:
        _PROD_DB = firestore.client(app)
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"Could not open production Firestore: {e}")
    return _PROD_DB


def on_production(cid: str, prod_key: str | None) -> bool:
    db = prod_db(prod_key)
    try:
        return db.collection("Trips").document(cid).get().exists
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"Production Firestore read failed for {cid}: {e}")


# ── local state ──────────────────────────────────────────────────────────────
def published_cids() -> list[str]:
    """trip_ids in review.db's published_trips (oldest publish first)."""
    if not DB.exists():
        print(f"No review.db at {DB} — nothing to audit.")
        return []
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    except Exception as e:  # noqa: BLE001
        print(f"Could not open {DB} read-only: {e}")
        return []
    try:
        rows = con.execute(
            "SELECT trip_id FROM published_trips ORDER BY published_at").fetchall()
    except sqlite3.Error as e:
        print(f"published_trips unavailable in {DB}: {e}")
        return []
    finally:
        con.close()
    return [r[0] for r in rows]


def manifest_cids() -> set[str]:
    """Every trip id the current review manifest still lists (review + final-check)."""
    if not MANIFEST.exists():
        raise SystemExit(f"Review manifest not found: {MANIFEST}\n"
                         f"  Refusing to archive without it — it is the 'still in the queue'"
                         f" gate.")
    try:
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"Review manifest unreadable ({MANIFEST}): {e}")
    out: set[str] = set()
    for lane in ("trips", "final_check"):
        for row in data.get(lane) or []:
            tid = (row or {}).get("trip_id")
            if tid:
                out.add(tid)
    return out


def valid_cid(cid: str) -> bool:
    """Reject anything that isn't a plain trip folder name."""
    if not cid or cid.strip() != cid:
        return False
    if "/" in cid or "\\" in cid or cid.startswith(".") or ".." in cid:
        return False
    return cid not in RESERVED_PREFIXES and not cid.startswith("_")


def safe_relpath(name: str) -> Path | None:
    """`originals/3.mp3` → a relative Path; anything odd → None."""
    if not name or name.startswith("/") or "\\" in name or ".." in name.split("/"):
        return None
    return Path(*name.split("/"))


def local_dir(cid: str) -> Path:
    return backup_root() / cid


def local_manifest(cid: str) -> dict | None:
    p = local_dir(cid) / MANIFEST_NAME
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"  WARN unreadable {p}: {e}")
        return None


def md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def size_str(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.1f}{unit}"
        f /= 1024
    return f"{f:.1f}B"


# ── audit ────────────────────────────────────────────────────────────────────
def cmd_audit(cids: list[str], prod_key: str | None) -> int:
    if not cids:
        cids = published_cids()
        if not cids:
            print("published_trips is empty (and no cids given) — nothing to audit.")
            return 0
        print(f"{len(cids)} published trip(s) from {DB}")
    s3 = r2()
    in_queue = manifest_cids()
    print(f"backup root: {backup_root()}")
    print()
    for cid in cids:
        if not valid_cid(cid):
            print(f"{cid}: INVALID trip id — skipped")
            continue
        objs = list_prefix(s3, cid)
        total = sum(o["size"] for o in objs)
        r2_state = f"R2 {len(objs)} files / {size_str(total)}" if objs else "R2 EMPTY"
        lm = local_manifest(cid)
        if lm:
            loc = f"archived locally {len(lm.get('files') or [])} files @ {lm.get('archived_at', '?')}"
        elif local_dir(cid).exists():
            loc = "local folder EXISTS (no _manifest.json)"
        else:
            loc = "not archived"
        try:
            prod = "PROD yes" if on_production(cid, prod_key) else "PROD NO"
        except SystemExit as e:
            print(f"\n{e}")
            return 1
        queued = " IN REVIEW QUEUE" if cid in in_queue else ""
        print(f"{cid}: {r2_state} | {prod} | {loc}{queued}")
    return 0


# ── archive ──────────────────────────────────────────────────────────────────
def archive_one(s3, cid: str, in_queue: set[str], prod_key: str | None,
                apply: bool, force_redownload: bool) -> str:
    """Returns a one-word outcome: archived | planned | skipped | refused | failed."""
    print(f"\n=== {cid}")
    if not valid_cid(cid):
        print("  REFUSED: not a valid trip folder name")
        return "refused"
    if cid in in_queue:
        print("  REFUSED: still listed in trips_to_review.json (review / final-check lane)")
        return "refused"
    if not on_production(cid, prod_key):
        print("  REFUSED: no Trips/<cid> doc on PRODUCTION Firebase — not published")
        return "refused"

    objs = list_prefix(s3, cid)
    if not objs:
        print("  nothing on R2 under this prefix — already archived or never uploaded")
        return "skipped"
    total = sum(o["size"] for o in objs)
    print(f"  production: yes | R2: {len(objs)} files, {size_str(total)}")

    dest = local_dir(cid)
    if dest.exists() and any(dest.iterdir()) and not force_redownload:
        print(f"  REFUSED: {dest} already exists — pass --force-redownload to replace it")
        return "refused"

    for o in objs[:200]:
        print(f"    {o['name']}  {size_str(o['size'])}")
    if len(objs) > 200:
        print(f"    … {len(objs) - 200} more")

    if not apply:
        print(f"  DRY-RUN: would download to {dest}, verify, then delete {len(objs)} "
              f"R2 object(s). Re-run with --apply.")
        return "planned"

    # 1. download
    dest.mkdir(parents=True, exist_ok=True)
    downloaded: list[dict] = []
    for o in objs:
        rel = safe_relpath(o["name"])
        if rel is None:
            print(f"  ABORT: refusing suspicious key {o['key']!r} — nothing deleted")
            return "failed"
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            s3.download_file(BUCKET, o["key"], str(target))
        except Exception as e:  # noqa: BLE001
            print(f"  ABORT: download failed for {o['key']}: {e} — nothing deleted")
            return "failed"
        downloaded.append(o)
    print(f"  downloaded {len(downloaded)} file(s) -> {dest}")

    # 2. verify against a FRESH listing (sizes, count, and md5 where the ETag is one)
    fresh = list_prefix(s3, cid)
    if len(fresh) != len(objs):
        print(f"  ABORT: R2 changed during the copy ({len(objs)} -> {len(fresh)} objects)"
              f" — nothing deleted")
        return "failed"
    by_key = {o["key"]: o for o in objs}
    for o in fresh:
        was = by_key.get(o["key"])
        if was is None or was["size"] != o["size"] or (was["etag"] and o["etag"]
                                                      and was["etag"] != o["etag"]):
            print(f"  ABORT: {o['key']} changed during the copy — nothing deleted")
            return "failed"
        local = dest / safe_relpath(o["name"])
        if not local.exists():
            print(f"  ABORT: missing local copy of {o['key']} — nothing deleted")
            return "failed"
        if local.stat().st_size != o["size"]:
            print(f"  ABORT: size mismatch {o['key']} "
                  f"(R2 {o['size']} vs local {local.stat().st_size}) — nothing deleted")
            return "failed"
        # A non-multipart R2 ETag IS the md5 — free extra proof when it's available.
        if o["etag"] and "-" not in o["etag"] and md5(local) != o["etag"]:
            print(f"  ABORT: checksum mismatch {o['key']} — nothing deleted")
            return "failed"
    print(f"  verified {len(fresh)} file(s): count, sizes"
          f"{' and checksums' if any('-' not in o['etag'] for o in fresh if o['etag']) else ''} match")

    # 3. manifest (written BEFORE the delete, so the backup is self-describing even if
    #    the delete is interrupted). UNION with any existing manifest: after a partial
    #    delete + --force-redownload re-run, `fresh` holds only the objects still on R2
    #    — overwriting the manifest would drop the already-deleted files from restore's
    #    view even though their bytes sit right here in the backup. A stale union entry
    #    whose local file is gone surfaces loudly at restore ("MISSING local file"),
    #    which is the safe direction.
    files_by_key: dict[str, dict] = {}
    prior = local_manifest(cid)
    for f in (prior or {}).get("files") or []:
        if f.get("key"):
            files_by_key[f["key"]] = f
    for o in fresh:
        files_by_key[o["key"]] = {"key": o["key"], "name": o["name"],
                                  "size": o["size"], "etag": o["etag"]}
    man = {
        "cid": cid,
        "archived_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bucket": BUCKET,
        "files": sorted(files_by_key.values(), key=lambda f: f["key"]),
    }
    (dest / MANIFEST_NAME).write_text(json.dumps(man, indent=2), encoding="utf-8")

    # 4. delete — only keys we listed, verified, and that live under this cid
    prefix = f"{cid}/"
    keys = [o["key"] for o in fresh if o["key"].startswith(prefix)]
    if len(keys) != len(fresh):
        print("  ABORT: a listed key escaped the trip prefix — nothing deleted")
        return "failed"
    deleted = 0
    for i in range(0, len(keys), 900):
        batch = keys[i:i + 900]
        try:
            resp = s3.delete_objects(
                Bucket=BUCKET,
                Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True})
        except Exception as e:  # noqa: BLE001
            print(f"  PARTIAL: delete failed after {deleted} object(s): {e}")
            print(f"  The local backup at {dest} is complete and verified; re-run to finish.")
            return "failed"
        for err in resp.get("Errors") or []:
            print(f"  WARN delete error {err.get('Key')}: {err.get('Message')}")
        deleted += len(batch) - len(resp.get("Errors") or [])
    print(f"  deleted {deleted}/{len(keys)} R2 object(s) under {BUCKET}/{prefix}")
    return "archived" if deleted == len(keys) else "failed"


def cmd_archive(cids: list[str], all_published: bool, prod_key: str | None,
                apply: bool, force_redownload: bool) -> int:
    if all_published:
        cids = list(dict.fromkeys(published_cids() + cids))
    if not cids:
        print("No trips given (and published_trips is empty) — nothing to archive.")
        return 0
    s3 = r2()
    in_queue = manifest_cids()
    print(f"{'APPLY' if apply else 'DRY-RUN'} — {len(cids)} trip(s); "
          f"backup root {backup_root()}")
    counts: dict[str, int] = {}
    for cid in cids:
        try:
            outcome = archive_one(s3, cid, in_queue, prod_key, apply, force_redownload)
        except SystemExit as e:
            print(f"\n{e}")
            return 1
        counts[outcome] = counts.get(outcome, 0) + 1
    print("\n── summary ──")
    for k in ("archived", "planned", "skipped", "refused", "failed"):
        if counts.get(k):
            print(f"  {k}: {counts[k]}")
    if not apply and counts.get("planned"):
        print("  (dry run — nothing downloaded or deleted; re-run with --apply)")
    return 1 if counts.get("failed") else 0


# ── restore ──────────────────────────────────────────────────────────────────
def cmd_restore(cid: str, prod_key: str | None, apply: bool) -> int:
    if not valid_cid(cid):
        print(f"{cid}: invalid trip id")
        return 1
    dest = local_dir(cid)
    man = local_manifest(cid)
    if man is None:
        print(f"No backup manifest at {dest / MANIFEST_NAME} — nothing to restore.")
        return 1
    files = man.get("files") or []
    if not files:
        print(f"{dest / MANIFEST_NAME} lists no files — nothing to restore.")
        return 1
    s3 = r2()
    existing = {o["key"]: o for o in list_prefix(s3, cid)}
    print(f"{'APPLY' if apply else 'DRY-RUN'} — restoring {len(files)} file(s) for {cid} "
          f"from {dest}")
    put = skipped = missing = mismatched = 0
    for f in files:
        name = f.get("name") or ""
        rel = safe_relpath(name)
        key = f"{cid}/{name}"
        if rel is None or not name:
            print(f"  WARN skipping odd manifest entry {name!r}")
            mismatched += 1
            continue
        local = dest / rel
        if not local.exists():
            print(f"  MISSING local file for {key} ({local})")
            missing += 1
            continue
        cur = existing.get(key)
        if cur is not None:
            if cur["size"] == local.stat().st_size:
                skipped += 1
            else:
                print(f"  ON R2 ALREADY, DIFFERENT SIZE — left untouched: {key} "
                      f"(R2 {cur['size']} vs backup {local.stat().st_size})")
                mismatched += 1
            continue
        if not apply:
            print(f"  would upload {key} ({size_str(local.stat().st_size)})")
            put += 1
            continue
        try:
            s3.upload_file(str(local), BUCKET, key,
                           ExtraArgs={"ContentType": "audio/mpeg"}
                           if local.suffix.lower() == ".mp3" else None)
        except Exception as e:  # noqa: BLE001
            print(f"  FAILED upload {key}: {e}")
            return 1
        put += 1
    print(f"\n── summary ── {'uploaded' if apply else 'to upload'}: {put} | "
          f"already on R2 (untouched): {skipped} | missing locally: {missing} | "
          f"mismatched/odd: {mismatched}")
    if not apply and put:
        print("  (dry run — re-run with --apply)")
    return 1 if (missing or mismatched) else 0


# ── cli ──────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prod-key", default=None,
                    help=f"path to the production Firebase key "
                         f"(default {SCRIPTS / PROD_KEY_NAME})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("audit", help="report R2 / production / local state per trip")
    p.add_argument("cids", nargs="*", help="trip ids (default: all published_trips)")

    p = sub.add_parser("archive", help="download + verify + delete a trip's R2 folder")
    p.add_argument("cids", nargs="*")
    p.add_argument("--all-published", action="store_true",
                   help="every trip in review.db published_trips")
    p.add_argument("--apply", action="store_true", help="actually download and delete")
    p.add_argument("--force-redownload", action="store_true",
                   help="replace an existing local backup folder")

    p = sub.add_parser("restore", help="re-upload a backup to review-audio/<cid>/")
    p.add_argument("cid")
    p.add_argument("--apply", action="store_true", help="actually upload")

    args = ap.parse_args()
    t0 = time.time()
    if args.cmd == "audit":
        rc = cmd_audit(args.cids, args.prod_key)
    elif args.cmd == "archive":
        rc = cmd_archive(args.cids, args.all_published, args.prod_key,
                         args.apply, args.force_redownload)
    else:
        rc = cmd_restore(args.cid, args.prod_key, args.apply)
    print(f"\ndone in {time.time() - t0:.1f}s")
    return rc


if __name__ == "__main__":
    sys.exit(main())
