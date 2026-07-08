#!/usr/bin/env python3
"""One-off repair for KaohsiungLotusPond_HSK3_ZH (approved 2026-07-08 on a host missing
jieba): the _ZH writeback stripped the pinyin display line from every edited Trip-doc
field and wrote empty TripLocalizations target.pinyin.

For each changed field (loc target.pinyin == '' with Hans+zhuyin present) this regenerates
pinyin from the confirmed zhuyin (same code path as approve: sessions._zh_regen_pinyin),
then writes:
  * TripLocalizations.scenes[i].<fp>.target.pinyin = <pinyin>
  * Trips.quickTrips[i].<fp> = "Hans\npinyin"   (only when the live line == Hans exactly,
    i.e. still the broken hanzi-only value — never clobbers a since-edited line)

Run from the workstation (needs jieba/pypinyin + the Scripts Firebase key):
  py -3.12 scripts/repair_kaohsiung_pinyin.py            # dry run — print the plan
  py -3.12 scripts/repair_kaohsiung_pinyin.py --apply    # write to staging
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.staging import db, get_trip, update_trip_text          # noqa: E402
from app.sessions import _zh_regen_pinyin, _zh_join2            # noqa: E402

TRIP_ID = "KaohsiungLotusPond_HSK3_ZH"
FIELDS = ["SceneDesc", "questionKey"]   # questionOption included via the scan below


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write to staging (default: dry run)")
    args = ap.parse_args()

    loc_ref = db().collection("TripLocalizations").document(TRIP_ID)
    loc = loc_ref.get().to_dict()
    trip = get_trip(TRIP_ID)
    qt = list(trip.get("quickTrips") or [])
    scenes = loc.get("scenes") or []

    loc_changed = trip_changed = False
    for sc in scenes:
        si = sc.get("index")
        for fp in FIELDS:
            node = sc.get(fp)
            if not isinstance(node, dict):
                continue
            tgt = node.get("target") or {}
            hans, zh = tgt.get("Hans") or "", tgt.get("zhuyin") or ""
            if tgt.get("pinyin") or not hans:
                continue                       # healthy, or nothing to phoneticise
            if "pinyin" not in tgt:
                continue                       # field never carried pinyin — untouched by the approval
            py, warns = _zh_regen_pinyin(zh, hans)
            if not py:
                print(f"!! scene {si} {fp}: regen FAILED {warns} — skipped")
                continue
            print(f"scene {si} {fp}:")
            print(f"   pinyin -> {py}")
            if warns:
                print(f"   (fallback path: {warns})")
            tgt["pinyin"] = py
            loc_changed = True
            # Trip doc line: only repair if it is still the broken hanzi-only value
            if si is not None and si < len(qt):
                cur_line = qt[si].get(fp) or ""
                if cur_line == hans:
                    qt[si][fp] = _zh_join2(hans, py)
                    trip_changed = True
                    print(f"   Trip.quickTrips[{si}].{fp} -> Hans + pinyin line restored")
                elif cur_line.split("\n")[0] == hans and len(cur_line.split("\n")) == 2:
                    print(f"   Trip line already has 2 lines — left alone")
                else:
                    print(f"   !! Trip line diverged from Hans — NOT touched: {cur_line!r}")

    if not (loc_changed or trip_changed):
        print("nothing to repair.")
        return
    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to write the above.")
        return
    if loc_changed:
        loc_ref.update({"scenes": scenes})
        print("TripLocalizations.scenes updated.")
    if trip_changed:
        update_trip_text(TRIP_ID, qt, {})
        print("Trips.quickTrips updated.")


if __name__ == "__main__":
    main()
