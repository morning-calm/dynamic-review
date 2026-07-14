r"""backfill_r2_originals.py — archive the as-delivered masters R2 no longer has a copy of.

WHY THIS EXISTS
``review-audio/<cid>/<i>.mp3`` holds a trip's LATEST ACCEPTED audio, and the app
overwrites it on every combine/import — correctly: on a masters-less host (the live
Ubuntu laptop) that key is the only durable home a reviewer's corrected take has.

What was missing is a copy of what was there BEFORE the first review touched it. For any
trip already reviewed on such a host, the as-delivered master exists nowhere on R2, so
there is no way back if a review goes wrong or a take is corrupted.
`review_audio.preserve_original` now archives it on the first overwrite — but only from
now on. This backfills the ones already lost.

WHAT IT DOES
Every session keeps the true pristine v0 on disk at ``work/<sid>/orig/<i>.mp3`` — the
copy taken at seed, before any edit, never mutated. This uploads it to
``review-audio/<cid>/originals/<i>.mp3`` wherever that key is missing.

⚠ It writes ONLY the ``originals/`` keys. It NEVER touches ``<cid>/<i>.mp3``, so it
cannot overwrite corrected audio — the point is to add a safety net under it, not to
roll anything back. (Nothing seeds from originals/ either; it is a recovery archive.
See the warning on review_audio.download_dir.)

Run it ON THE HOST THAT HOLDS THE WORK DIRS — the live Ubuntu laptop. Idempotent: an
originals/ key that already exists is never overwritten (it is, by definition, closer to
the delivered master than anything we could put over it). DRY-RUN by default.

    python3 scripts/backfill_r2_originals.py            # dry-run
    python3 scripts/backfill_r2_originals.py --apply
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app import config  # noqa: E402  (bootstraps the Scripts sys.path / .env)
from app import review_audio  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="actually upload (default: dry-run)")
    args = ap.parse_args()

    s3 = review_audio._r2()
    if s3 is None:
        sys.exit("R2 unavailable (Cloudfare_* creds missing) — nothing to do.")

    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    # EARLIEST session first: a trip reviewed more than once may have a later session
    # that was seeded FROM the corrected canonical key (masters-less host), so that
    # session's orig/ is the corrected take, not the delivered master. The first
    # session's orig/ is by construction the closest thing to as-delivered that exists.
    rows = con.execute(
        "SELECT DISTINCT s.id AS sid, s.trip_id AS trip_id, f.mp3_name AS mp3_name, "
        "       s.created_at AS created_at "
        "FROM sessions s JOIN field_edits f ON f.session_id = s.id "
        "WHERE f.has_audio = 1 AND f.mp3_name IS NOT NULL AND f.mp3_name != '' "
        "ORDER BY s.trip_id, f.mp3_name, s.created_at").fetchall()
    con.close()

    # One candidate list per (trip, file), earliest session's copy first — the seed-time
    # orig/ is pristine by construction (sessions never write to it), and the earliest
    # session's is the closest to as-delivered. Later sessions are only a fallback for
    # when that work dir has been cleaned.
    candidates: dict[tuple[str, str], list[Path]] = {}
    for r in rows:
        candidates.setdefault((r["trip_id"], r["mp3_name"]), []).append(
            config.WORK_ROOT / r["sid"] / "orig" / r["mp3_name"])

    uploaded = present = absent = 0
    for (trip, name), origs in sorted(candidates.items()):
        orig = next((p for p in origs if p.exists()), None)
        if orig is None:
            absent += 1
            continue
        key = f"{trip}/{review_audio.ORIGINALS_PREFIX}{name}"
        try:
            s3.head_object(Bucket=review_audio.BUCKET, Key=key)
            present += 1
            continue                       # already preserved — leave it alone
        except Exception:                  # noqa: BLE001 — 404 is the expected path
            pass
        if args.apply:
            if review_audio.upload(trip, orig,
                                   f"{review_audio.ORIGINALS_PREFIX}{name}"):
                uploaded += 1
                print(f"  restored  {review_audio.BUCKET}/{key}")
        else:
            print(f"  would restore  {review_audio.BUCKET}/{key}   <-  {orig}")
            uploaded += 1

    print(f"\n{'restored' if args.apply else 'would restore'}: {uploaded}   "
          f"already preserved: {present}   no local orig/ copy: {absent}")
    if absent:
        print("NB: sessions whose work/ dir has been cleaned have no pristine copy left "
              "to restore from — for those, R2's canonical key is all there is.")
    if not args.apply:
        print("Dry-run — re-run with --apply to upload.")


if __name__ == "__main__":
    main()
