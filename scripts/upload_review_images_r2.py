r"""upload_review_images_r2.py — mirror static-360 stills + flat overlays to R2.

Scene THUMBNAILS already go to R2 (thumbs.py), but static-360 stills ({i}.jpg) and
flat overlays are served from the LOCAL source-audio trees, which don't exist on the
hosted / Ubuntu-laptop deployment — so those images 404 there. The backend now serves
them from an R2 mirror when the local file is absent (app/images_r2.py); this script
POPULATES that mirror from a host that HAS the source trees (the workstation).

For each trip in trips_to_review.json (or one ``--trip``): read the staging Trip doc,
enumerate its static-360 stills + overlay filenames, resolve each locally (the same
resolver the app uses), and upload to
    review-overlays/<canonical_base>/<filename>
in the thumbs bucket (served at thumbs.dynamiclanguages.org). Idempotent — an object
already on R2 is skipped. DRY-RUN by default; pass --apply to upload. Run on the
workstation (needs the local image trees + the Cloudfare_* creds in the Scripts .env).

    py -3.12 scripts/upload_review_images_r2.py                 # dry-run, all trips
    py -3.12 scripts/upload_review_images_r2.py --apply         # upload, all trips
    py -3.12 scripts/upload_review_images_r2.py --trip Tokyo_03_EN --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app import config  # noqa: E402  (bootstraps the Scripts sys.path / .env)
from app import images_r2, sessions, staging  # noqa: E402

MANIFEST = ROOT / "trips_to_review.json"


def _image_names(trip: dict) -> list[str]:
    """Every display image filename for a trip: static-360 panoramas + each scene's flat
    overlay filenames.

    A static scene contributes BOTH names: ``{i}-4k.jpg`` (the 4096×2048 re-encode the
    app now prefers) and ``{i}.jpg`` (the 7680×7680 VR master it falls back to when a
    trip has no 4K copy). Both are mirrored — the live host has neither tree locally, so
    whichever the app asks for has to be there."""
    names: list[str] = []
    for i, s in enumerate(trip.get("quickTrips") or []):
        if s.get("isStaticImage"):
            names.append(f"{i}-4k.jpg")
            names.append(f"{i}.jpg")
        for si in (s.get("staticImages") or []):
            fn = si.get("filename")
            if fn:
                names.append(fn)
    # de-dupe, preserve order
    seen: set[str] = set()
    return [n for n in names if not (n in seen or seen.add(n))]


def process(trip_id: str, apply: bool) -> tuple[int, int, int]:
    try:
        # get_trip (stage9.common) calls sys.exit() on a missing trip — SystemExit is
        # NOT an Exception, so catch it too or one no-audio trip aborts the whole batch.
        trip = staging.get_trip(trip_id)
    except (Exception, SystemExit) as e:  # noqa: BLE001
        print(f"  ! {trip_id}: staging read failed/absent: {e}")
        return (0, 0, 0)
    folder_name = (trip.get("folderName") or "").replace("\\", "/").strip("/")
    try:
        p = staging.paths_for(trip, trip_id)
        mp3_dir, ogg_dir = p["mp3_dir"], p["ogg_dir"]
    except SystemExit:
        mp3_dir = ogg_dir = None
    base = sessions._overlay_base(trip_id)
    # The 4K resolver refuses a folder that doesn't cover every static scene of this trip
    # (two folders for one location can number their scenes differently) — so it needs
    # the trip's full static-index set, exactly as the app passes it.
    static_idx = {i for i, s in enumerate(trip.get("quickTrips") or [])
                  if (s or {}).get("isStaticImage")}

    found = uploaded = missing = 0
    for fn in _image_names(trip):
        local = sessions._resolve_overlay_file(trip_id, mp3_dir, ogg_dir, fn, folder_name,
                                               static_idx)
        if local is None:
            missing += 1
            continue
        found += 1
        if apply:
            if images_r2.ensure_uploaded(base, fn, local):
                uploaded += 1
        else:
            print(f"    would upload  review-overlays/{base}/{fn}   <-  {local}")
    print(f"  {trip_id}: {found} resolved / {uploaded} uploaded / {missing} missing "
          f"(base {base})")
    return (found, uploaded, missing)


def audit_4k(ids: list[str]) -> None:
    """Which queued trips still serve the 7680×7680 master because no 4K re-encode of
    their panoramas exists? Printed by name so the gap is actionable (re-encode them
    into STATIC_4K_ROOT) instead of silently costing every reviewer 15 MB a scene."""
    from app import static360

    total = covered = 0
    gaps: list[tuple[str, list[int]]] = []
    for tid in ids:
        try:
            trip = staging.get_trip(tid)
        except (Exception, SystemExit):  # noqa: BLE001
            continue
        want = {i for i, s in enumerate(trip.get("quickTrips") or [])
                if (s or {}).get("isStaticImage")}
        if not want:
            continue
        base_ids = sessions._image_base_ids(tid)
        missing = [i for i in sorted(want)
                   if static360.resolve(tid, base_ids, want, i) is None]
        total += len(want)
        covered += len(want) - len(missing)
        if missing:
            gaps.append((tid, missing))

    print(f"\n4K static-360 coverage: {covered}/{total} panoramas "
          f"({total - covered} still on the full-size master)")
    if gaps:
        print(f"\nTrips with NO 4K re-encode for one or more static scenes ({len(gaps)}):")
        for tid, missing in gaps:
            print(f"    {tid:<38} scenes {missing}")
        print("\nThese fall back to the 7680×7680 master (~15 MB each). Re-encode them "
              f"into {config.STATIC_4K_ROOT} as <sceneIndex>-4k.jpg to close the gap.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trip", help="one trip id (default: every trip in trips_to_review.json)")
    ap.add_argument("--apply", action="store_true", help="actually upload (default: dry-run)")
    ap.add_argument("--audit-4k", action="store_true",
                    help="also report which trips have no 4K panorama re-encode")
    args = ap.parse_args()

    if args.trip:
        ids = [args.trip]
    else:
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        ids = [t["trip_id"] for t in (data.get("trips") or []) if t.get("trip_id")]

    print(f"{'UPLOADING' if args.apply else 'DRY-RUN'} overlay/still images for "
          f"{len(ids)} trip(s) → review-overlays/ in {config.THUMB_BUCKET}\n")
    tf = tu = tm = 0
    for tid in ids:
        f, u, m = process(tid, args.apply)
        tf += f
        tu += u
        tm += m
    print(f"\nTotal: {tf} resolved / {tu} uploaded / {tm} missing across {len(ids)} trip(s).")
    if args.audit_4k:
        audit_4k(ids)
    if not args.apply:
        print("Dry-run — re-run with --apply to upload.")


if __name__ == "__main__":
    main()
