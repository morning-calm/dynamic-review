r"""refresh_trips.py — make the app serve a trip's NEW content after the pipeline
re-publishes it (staging text) and re-uploads it (R2 `review-audio/<cid>/`).

WHY THIS EXISTS. Uploading to R2 and publishing to staging is NOT enough for a trip the
review app has already seen. `sessions.resolve_audio_dir` mirrors `review-audio/<cid>/`
into `work/_r2_seed_cache/<cid>` and returns that folder on every later call — it never
re-downloads. And the cache is filled by the trip LISTING (the `reviewable` probe), not
just by opening a trip, so on the live laptop practically every queued trip is cached.
Without this refresh a reviewer gets the NEW text against the OLD audio. A session that
was seeded before the change additionally froze `original_text` and must be re-seeded.

    py -3.12 scripts/refresh_trips.py audit  --file cids.txt
    py -3.12 scripts/refresh_trips.py clear  --file cids.txt
    py -3.12 scripts/refresh_trips.py warm   --file cids.txt      # re-pull now, not lazily
    py -3.12 scripts/refresh_trips.py verify --file cids.txt [--changed changed.txt]
    py -3.12 scripts/refresh_trips.py reseed --file cids.txt [--dry-run]
    py -3.12 scripts/refresh_trips.py run    --file cids.txt [--changed changed.txt]

`--changed` takes the producer's changed-scene list, one `cid: 4,7` per line, and adds the
assertion that actually matters: for each changed scene the quiz clips on R2 must be NEWER
than that trip's narration (i.e. the regen really landed), and the narration itself must
NOT have moved when the batch claimed to touch questions only.

ORDER MATTERS. Run this AFTER the producer's uploads have landed, never before —
clearing early just re-caches the OLD audio. And "cache is gone" is not the success
condition: the cache refills from R2 the next time anyone loads the trip list (observed
within minutes). `verify` is what actually proves the refresh took, by comparing every
cached mp3 against the R2 object (MD5 via ETag, no download).

`audit`, `verify` are read-only. `clear` only removes cache folders for the cids you
name. `reseed` DELETES sessions, and refuses unless every guard passes:
  * the session's trip is one you named  (a typo can't reach another reviewer's trip)
  * status is `in_review`                (never an approved/submitted/ai_review session)
  * no presence heartbeat in 15 minutes  (never yank someone mid-edit)
  * no text edits, no flags, no corrected takes — coverage alone is fine
Back up review.db first: `py -3.12 scripts/backup_review_db.py backup`.

Full procedure + producer-side twin: `docs/adding-trips-to-review.md` § 5b and
`Scripts/Trello/REVIEW_QUEUE_HANDOFF.md` § 5.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app import config  # noqa: E402  (bootstraps the Scripts sys.path / .env)
from app import review_audio  # noqa: E402

DB = ROOT / "backend" / "review.db"
CACHE_ROOT = config.WORK_ROOT / "_r2_seed_cache"
PRESENCE_WINDOW_S = 15 * 60
RESEEDABLE_STATUS = "in_review"
CHILD_TABLES = ("field_edits", "audio_versions", "manual_clips", "presence",
                "recall_requests", "auto_review_findings", "auto_reviews",
                "structure_ops", "external_reports", "approvals")


# ── helpers ──────────────────────────────────────────────────────────────────
def _con(write: bool = False) -> sqlite3.Connection:
    con = sqlite3.connect(str(DB) if write else f"file:{DB}?mode=ro", uri=not write)
    con.row_factory = sqlite3.Row
    return con


def _sha(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def corrected_takes(sid: str) -> list[str]:
    """Takes the reviewer changed — working differs from the pristine seed."""
    w, o = config.WORK_ROOT / sid / "working", config.WORK_ROOT / sid / "orig"
    if not w.is_dir():
        return []
    return [p.name for p in sorted(w.glob("*.mp3"))
            if not (o / p.name).exists() or _sha(p) != _sha(o / p.name)]


def load_trips(args) -> list[str]:
    trips: list[str] = []
    if args.file:
        trips += [ln.rstrip("\n").strip() for ln in
                  Path(args.file).read_text(encoding="utf-8").splitlines() if ln.strip()]
    for chunk in args.trips or []:
        trips += [t.strip() for t in chunk.split(",") if t.strip()]
    if not trips:
        sys.exit("no trips given — use --trips or --file (one contentID per line)")
    seen, out = set(), []
    for t in trips:                       # de-dupe, keep order
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def session_state(con, cid: str, live: dict) -> list[dict]:
    out = []
    for s in con.execute("select * from sessions where trip_id=?", (cid,)):
        sid = s["id"]
        q = ("select count(*) from field_edits where session_id=? and %s")
        edits = con.execute(q % "ifnull(current_text,'')<>ifnull(original_text,'')",
                            (sid,)).fetchone()[0]
        flags = con.execute(q % "flag is not null and flag<>'none'", (sid,)).fetchone()[0]
        cov = con.execute(q % "ifnull(played_coverage_json,'')<>''", (sid,)).fetchone()[0]
        who = sorted({r[0] for r in con.execute(
            "select distinct edited_by from field_edits "
            "where session_id=? and edited_by is not null", (sid,))})
        takes = corrected_takes(sid)
        out.append({"sid": sid, "status": s["status"], "idle_d": (time.time() - s["updated_at"]) / 86400,
                    "edits": edits, "flags": flags, "coverage": cov, "takes": takes,
                    "edited_by": who, "live": live.get(sid)})
    return out


def verdict(cid: str, sessions: list[dict], completed: set) -> tuple[str, str]:
    if cid in completed:
        return "HANDS OFF", "completed trip — changes belong in a DELTA manifest, not a re-upload"
    if not sessions:
        return "CLEAR", "no session — cache clear is all it needs"
    reasons = []
    for s in sessions:
        if s["live"]:
            reasons.append(f"{s['sid']} has {s['live']} LIVE in it")
        if s["status"] != RESEEDABLE_STATUS:
            reasons.append(f"{s['sid']} is {s['status']}, not {RESEEDABLE_STATUS}")
        if s["edits"] or s["flags"] or s["takes"]:
            reasons.append(f"{s['sid']} holds work (edits={s['edits']} flags={s['flags']} "
                           f"takes={len(s['takes'])})")
    if reasons:
        return "HANDS OFF", "; ".join(reasons)
    return "RESEED", "session has no work product — safe to delete and re-seed"


# ── commands ─────────────────────────────────────────────────────────────────
def cmd_audit(trips: list[str]) -> dict[str, str]:
    con = _con()
    now = time.time()
    completed = {r[0] for r in con.execute("select trip_id from completed_trips")}
    live = {r["session_id"]: r["username"] for r in
            con.execute("select * from presence where ?-updated_at < ?", (now, PRESENCE_WINDOW_S))}
    verdicts = {}
    for cid in trips:
        cache = CACHE_ROOT / cid
        files = list(cache.glob("*.mp3")) if cache.is_dir() else []
        age = (f"{(now - max(p.stat().st_mtime for p in files)) / 86400:.1f}d"
               if files else "-")
        sess = session_state(con, cid, live)
        v, why = verdict(cid, sess, completed)
        verdicts[cid] = v
        print(f"[{v:<9}] {cid}")
        print(f"            cache {len(files):>3} files (newest {age}) | {why}")
        for s in sess:
            print(f"            {s['sid']} {s['status']} idle {s['idle_d']:.1f}d | "
                  f"edits {s['edits']} flags {s['flags']} coverage {s['coverage']} "
                  f"takes {s['takes'] or '-'} | edited_by {s['edited_by'] or '-'}"
                  + (f" | LIVE: {s['live']}" if s["live"] else ""))
    n = lambda v: sum(1 for x in verdicts.values() if x == v)  # noqa: E731
    print(f"\n{len(trips)} trips: {n('CLEAR')} clear · {n('RESEED')} need a re-seed · "
          f"{n('HANDS OFF')} HANDS OFF")
    if n("HANDS OFF"):
        print("!! resolve every HANDS OFF trip by hand before running clear/reseed")
    return verdicts


def cmd_clear(trips: list[str]) -> None:
    protected = [d.name for d in CACHE_ROOT.iterdir() if d.is_dir()] if CACHE_ROOT.is_dir() else []
    protected = [c for c in protected if c not in trips]
    before = {c: (CACHE_ROOT / c).is_dir() for c in protected}
    cleared = 0
    for cid in trips:
        d = CACHE_ROOT / cid
        if d.is_dir():
            n = len(list(d.glob("*.mp3")))
            shutil.rmtree(d)
            cleared += 1
            print(f"   cleared {cid}  ({n} files)")
        else:
            print(f"   (no cache) {cid}")
    for cid in trips:
        assert not (CACHE_ROOT / cid).exists(), f"{cid} still cached"
    for cid in protected:                       # nothing else may have moved
        assert (CACHE_ROOT / cid).is_dir() == before[cid], f"GUARD VIOLATED: {cid}"
    print(f"\ncleared {cleared}/{len(trips)} | {len(protected)} other trip caches untouched")
    print("NOTE: the cache refills on the next trip-list load — run `verify` to prove the "
          "refilled bytes match R2 (that, not absence, is the success condition).")


def _is_reviewer_correction(s3, cid: str, name: str) -> bool:
    """True when R2's copy is a reviewer's CONFIRMED take rather than pipeline output.

    The app archives the as-delivered master to ``review-audio/<cid>/originals/<name>``
    the first time a correction overwrites a scene, so that key's presence marks
    ``<cid>/<name>`` as human work (the same marker `upload_review_audio_r2.py` uses to
    refuse to clobber it). A cache↔R2 difference on such a clip is EXPECTED — the cache
    holds the as-delivered master, R2 holds the correction — and is not a failed refresh.
    """
    try:
        s3.head_object(Bucket=config.REVIEW_AUDIO_BUCKET, Key=f"{cid}/originals/{name}")
        return True
    except Exception:                                            # noqa: BLE001
        return False


def cmd_verify(trips: list[str]) -> int:
    """Compare every cached mp3 against its R2 object by MD5 (ETag) — no downloads."""
    s3 = review_audio._r2()
    if s3 is None:
        sys.exit("R2 unavailable — cannot verify")
    bad = 0
    for cid in trips:
        cache = CACHE_ROOT / cid
        files = sorted(cache.glob("*.mp3")) if cache.is_dir() else []
        if not files:
            print(f"{cid}: cache empty — nothing seeded yet (open the trip list, then re-run)")
            continue
        mism, miss, corr, multi = [], [], [], 0
        for p in files:
            try:
                etag = s3.head_object(Bucket=config.REVIEW_AUDIO_BUCKET,
                                      Key=f"{cid}/{p.name}")["ETag"].strip('"')
            except Exception:                                    # noqa: BLE001
                miss.append(p.name)
                continue
            if "-" in etag:                  # multipart upload: ETag isn't a plain MD5
                multi += 1
                continue
            if etag != _md5(p):
                (corr if _is_reviewer_correction(s3, cid, p.name) else mism).append(p.name)
        state = "OK  " if not (mism or miss) else "BAD "
        print(f"{state}{cid:<34} {len(files):>3} cached · matched "
              f"{len(files) - len(mism) - len(miss) - len(corr) - multi}"
              + (f" · MISMATCH {mism}" if mism else "")
              + (f" · not on R2 {miss}" if miss else "")
              + (f" · {len(corr)} reviewer-corrected on R2 (expected): {corr}" if corr else "")
              + (f" · {multi} multipart (skipped)" if multi else ""))
        bad += len(mism) + len(miss)
    print("\nAll cached audio matches R2 (reviewer corrections aside)." if not bad
          else f"\n!! {bad} file(s) differ from R2 with no `originals/` marker — "
               "the refresh did NOT take")
    return bad


def cmd_warm(trips: list[str]) -> None:
    """Re-pull each trip's masters from R2 into the seed cache now.

    Exactly what `resolve_audio_dir` does on a cache miss — doing it here means the next
    reviewer doesn't pay the download, and `verify` can run immediately instead of waiting
    for someone to load the trip list.
    """
    for cid in trips:
        dest = CACHE_ROOT / cid
        ok = review_audio.download_dir(cid, dest)
        n = len(list(dest.glob("*.mp3"))) if dest.is_dir() else 0
        print(f"   {'warmed ' if ok else 'FAILED '} {cid:<34} {n:>3} files")
    print("\nnow run `verify` to compare the cache against R2.")


def _load_changed(path: str) -> dict[str, list[int]]:
    """Parse the producer's changed-scene list: one `cid: 4,7` per line."""
    out: dict[str, list[int]] = {}
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        cid, _, scenes = ln.rpartition(":")
        if not cid:
            sys.exit(f"bad --changed line (expected `cid: 4,7`): {ln!r}")
        out[cid.strip()] = [int(s) for s in scenes.replace(" ", "").split(",") if s]
    return out


def cmd_check_changed(changed: dict[str, list[int]]) -> int:
    """Assert the producer's claim: quiz clips moved, narration did not."""
    s3 = review_audio._r2()
    bad = 0
    for cid, scenes in changed.items():
        for i in scenes:
            def when(name):
                try:
                    return s3.head_object(Bucket=config.REVIEW_AUDIO_BUCKET,
                                          Key=f"{cid}/{name}")["LastModified"]
                except Exception:                                # noqa: BLE001
                    return None
            q, narr = when(f"{i}_q.mp3"), when(f"{i}.mp3")
            if q is None:
                print(f"BAD  {cid} s{i}: {i}_q.mp3 is not on R2")
                bad += 1
                continue
            note = ""
            if narr is not None and q <= narr:
                note = "  !! quiz clip is NOT newer than the narration — regen did not land"
                bad += 1
            print(f"{'BAD ' if note else 'OK  '} {cid:<30} s{i:<3} "
                  f"q {q:%Y-%m-%d %H:%M} · narration "
                  f"{narr.strftime('%Y-%m-%d %H:%M') if narr else '—'}{note}")
    return bad


def cmd_reseed(trips: list[str], dry_run: bool) -> None:
    con = _con(write=not dry_run)
    now = time.time()
    completed = {r[0] for r in con.execute("select trip_id from completed_trips")}
    live = {r["session_id"]: r["username"] for r in
            con.execute("select * from presence where ?-updated_at < ?", (now, PRESENCE_WINDOW_S))}
    plan = []
    for cid in trips:
        sess = session_state(con, cid, live)
        v, why = verdict(cid, sess, completed)
        if v == "HANDS OFF":
            sys.exit(f"ABORT: {cid} — {why}")
        for s in sess:
            plan.append((s["sid"], cid))
            print(f"guards ok: {s['sid']}  {cid}")
    if not plan:
        print("nothing to re-seed (no sessions on these trips)")
        return
    if dry_run:
        print(f"\n--dry-run: would delete {len(plan)} session(s) and their work dirs")
        return
    print()
    for sid, cid in plan:
        print(f"== {sid}  {cid}")
        for tbl in CHILD_TABLES:
            try:
                n = con.execute(f"delete from {tbl} where session_id=?", (sid,)).rowcount
            except sqlite3.OperationalError:
                continue                      # table has no session_id column
            if n:
                print(f"   -{n:>4} {tbl}")
        print(f"   -{con.execute('delete from sessions where id=?', (sid,)).rowcount:>4} sessions")
    con.commit()
    for sid, _ in plan:
        d = config.WORK_ROOT / sid
        if d.is_dir():
            shutil.rmtree(d)
            print(f"   removed work dir {d}")
    orphans = con.execute("select count(*) from field_edits "
                          "where session_id not in (select id from sessions)").fetchone()[0]
    print(f"\nsessions remaining: {con.execute('select count(*) from sessions').fetchone()[0]} "
          f"| orphaned field_edits: {orphans}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("audit", "clear", "warm", "verify", "reseed", "run"))
    ap.add_argument("--trips", action="append", help="contentIDs, comma-separated (repeatable)")
    ap.add_argument("--file", help="file with one contentID per line (use this for long lists "
                                   "— cids may contain spaces)")
    ap.add_argument("--changed", help="producer's changed-scene list, one `cid: 4,7` per line")
    ap.add_argument("--dry-run", action="store_true", help="reseed: show the plan, write nothing")
    args = ap.parse_args()
    trips = load_trips(args)
    changed = _load_changed(args.changed) if args.changed else {}
    if changed and set(changed) - set(trips):
        sys.exit(f"--changed names trips not in the list: {sorted(set(changed) - set(trips))}")

    if args.command == "audit":
        cmd_audit(trips)
    elif args.command == "clear":
        cmd_clear(trips)
    elif args.command == "warm":
        cmd_warm(trips)
    elif args.command == "verify":
        bad = cmd_verify(trips)
        if changed:
            print("\n── producer's changed scenes ─────────────────────")
            bad += cmd_check_changed(changed)
        sys.exit(1 if bad else 0)
    elif args.command == "reseed":
        cmd_reseed(trips, args.dry_run)
    elif args.command == "run":
        print("── audit ─────────────────────────────────────────")
        v = cmd_audit(trips)
        if any(x == "HANDS OFF" for x in v.values()):
            sys.exit("\nstopping: resolve the HANDS OFF trips first")
        print("\n── clear ─────────────────────────────────────────")
        cmd_clear(trips)
        print("\n── warm ──────────────────────────────────────────")
        cmd_warm(trips)
        print("\n── verify ────────────────────────────────────────")
        bad = cmd_verify(trips)
        if changed:
            print("\n── producer's changed scenes ─────────────────────")
            bad += cmd_check_changed(changed)
        if any(x == "RESEED" for x in v.values()):
            print("\nSTILL TO DO — re-seed the stale sessions (dave runs this):\n"
                  "   py -3.12 scripts/refresh_trips.py reseed --file <same list>")
        sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
