#!/usr/bin/env python3
"""Gate 2 of the auto-review pipeline (docs/auto-review-proposal.md): a Claude review of
every SUBMITTED session that doesn't yet have a report.

SHADOW MODE: this script only WRITES REPORTS (auto_reviews table). It never changes a
session's status, never edits text, never touches staging. The admin reads the report on
the Changes-summary page (and gets a one-line verdict in the notifier email) and still
approves manually.

What Claude is asked to judge (per changed field): meaning equivalence across the scripts
and the English translation, grammar/naturalness, and Q&A logic. Deterministic
script-consistency AND vocabulary level are Gate 1's job (backend/app/auto_checks.py +
zh_level.py) and run at submit — this pass is judgment only. (LEVEL was removed from the
prompt on 2026-07-13: on a six-script audit the model's level calls were wrong as often as
right, while the pipeline's own HSK wordlist gets it exactly right. Don't add it back.)

NO LONGER SHADOW-ONLY: every 'warning'/'needs_human' verdict becomes a triage item the
SUBMITTING REVIEWER must answer (resolve / reject-with-reason / defer-to-admin), and the
session bounces from 'submitted' back to 'ai_review' until they do — see
backend/app/auto_review_ingest.py. Reports still never edit text or touch staging.

INCREMENTAL SINCE 2026-08-10 (the French reviewer's endless cycle): each successful report
snapshots the diff it judged (auto_reviews.input_json), and a later run re-judges ONLY the
fields whose text changed since that snapshot — unchanged fields keep their previous
verdicts AND the reviewer's answers to them. A re-submit that changed nothing writes a
carried-forward report with NO Claude call and NO bounce, so answer-and-resubmit is a
guaranteed exit from the loop. Before this, every re-submit re-judged the reviewer's
ENTIRE diff, and the model's nondeterministic verdicts re-flagged a different subset each
round (Reims2_A12_FR 2026-08-09: 1 warn -> 1 warn -> 0 -> 1 warn on identical text).

Suggested fixes for _ZH fields are POST-VERIFIED with hsk_lib (Hant↔Hans via Gate 1's
`auto_checks._hant_correspondence`, zhuyin_to_pinyin full-confirm) and carry
verified:true/false — an unverified suggestion is shown but flagged. Nothing is ever
auto-applied.

Runs from cron on the live host (*/5, like the notifier). Uses the `claude` CLI headless
(`claude -p --output-format json`) — no API key in this repo.

Usage:
  python scripts/claude_review.py               # review any submitted session lacking a report
  python scripts/claude_review.py --sid SID     # force one session (even if reported)
  python scripts/claude_review.py --dry-run     # print the prompt + Claude's report, write nothing
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
from app import auto_checks, auto_review_ingest  # noqa: E402  (shared with the API — see their docstrings)

DB_PATH = REPO / "backend" / "review.db"
STATE_PATH = REPO / "backend" / "autoreview_state.json"
LOCK_PATH = REPO / "backend" / "autoreview.lock"
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
MODEL = os.environ.get("REVIEW_CLAUDE_MODEL", "sonnet")
TIMEOUT_S = 600
LIMIT_BACKOFF_S = 3600      # usage-limit hit: try again hourly until the limit resets
ERROR_RETRY_S = 6 * 3600    # non-limit failure: re-review that session after 6h

# Substrings that mean "the Claude subscription/API is out of quota RIGHT NOW" — the
# session must stay QUEUED (no report row) and be retried after the limit resets.
_LIMIT_MARKERS = ("usage limit", "rate limit", "limit reached", "out of extra usage",
                  "quota", "overloaded", "429", "exceeded")


class UsageLimitError(RuntimeError):
    pass

SCRIPTS_ROOT = Path(os.environ.get("REVIEW_APP_SCRIPTS_ROOT", r"D:\Dynamic Languages\Scripts"))


def _hsk():
    try:
        hsk_dir = str(SCRIPTS_ROOT / "Research and Writing" / "HSK Mandarin" / "stages")
        if hsk_dir not in sys.path:
            sys.path.insert(0, hsk_dir)
        import hsk_lib
        return hsk_lib
    except Exception:  # noqa: BLE001
        return None


def trip_level(trip_id: str) -> str:
    m = re.search(r"_(HSK12|HSK3|A12|B1|B2|Beg_N4|N4|N5)_", trip_id + "_")
    return m.group(1) if m else "unknown"


# ------------------------------------------------------------------ diff dump
def session_diff(con, sid: str) -> dict:
    s = con.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    fields = []
    for f in con.execute("SELECT * FROM field_edits WHERE session_id=? ORDER BY id", (sid,)):
        entry = {"scene": f["scene_index"], "field": f["field_path"],
                 "option": f["option_index"]}
        loc_raw = f["localization_json"] if "localization_json" in f.keys() else None
        if loc_raw:
            loc = json.loads(loc_raw)
            cur, orig = loc.get("cur") or {}, loc.get("orig") or {}
            if cur == orig:
                continue
            entry["orig"], entry["cur"] = orig, cur
        else:
            cur_t, orig_t = f["current_text"] or "", f["original_text"] or ""
            src, orig_src = f["source_text"] or "", f["original_source"] or ""
            if cur_t == orig_t and src == orig_src:
                continue
            entry["orig"] = {"text": orig_t, "en": orig_src}
            entry["cur"] = {"text": cur_t, "en": src}
        fields.append(entry)
    return {"session_id": sid, "trip_id": s["trip_id"], "level": trip_level(s["trip_id"]),
            "submitted_by": s["submitted_by"], "fields": fields}


# ------------------------------------------------------------------ Claude call
PROMPT = """You are the review gate for a VR language-learning content pipeline. A human
reviewer edited fields of the trip below; you judge ONLY the changed fields (orig -> cur).
Deterministic script-consistency (Hant<->Hans conversion, zhuyin alignment) has ALREADY
been machine-checked — do not re-check mechanics. Judge:

1. MEANING: after the edit, do all forms still say the same thing (Hans/Hant/zhuyin are one
   fact; `en` is its translation)? Flag meaning drift, e.g. target says "typhoon" but en
   says "wind".
2. LANGUAGE QUALITY: is the edited text natural, grammatical, correctly punctuated for its
   language? (zh trips: Simplified is the spoken/display line.)
3. Q&A LOGIC: for questionKey/questionOption fields, is the question still answerable from
   the scene description, and is the phrasing consistent with its options?

DO NOT judge VOCABULARY LEVEL (whether a word is above the trip's HSK/CEFR band). That is
now a deterministic lookup against the real HSK wordlist in Gate 1 (backend/app/zh_level.py),
and your guesses at it were wrong as often as they were right (2026-07-13: you flagged 离开
(HSK2) and 保持 (HSK3) as above-level, invented band numbers, and cleared 旧 as "HSK2" when
it is HSK3). Say nothing about level; the machine has it.

Verdicts per field: "ok" (publishable as-is), "warning" (works, but a human should glance),
"needs_human" (do not publish without a human decision).
For zh fields you may include a suggested_fix object with any of Hans/Hant/zhuyin/en; for
other trips with any of text/en. Only suggest a fix you are confident in; otherwise omit.

Respond with STRICT JSON only — no markdown fence, no commentary:
{{"summary": "<one-line overall verdict>",
  "fields": [{{"scene": <int>, "field": "<field_path>", "option": <int|null>,
              "verdict": "ok|warning|needs_human", "reasons": ["<short reason>", ...],
              "suggested_fix": {{...}} | null}}, ...]}}
Include EVERY field from the input, in order.

INPUT:
{diff}
"""


def _looks_like_limit(*texts: str) -> bool:
    blob = " ".join(t or "" for t in texts).lower()
    return any(m in blob for m in _LIMIT_MARKERS)


def call_claude(diff: dict) -> dict:
    prompt = PROMPT.format(diff=json.dumps(diff, ensure_ascii=False, indent=1))
    proc = subprocess.run(
        [CLAUDE_BIN, "-p", prompt, "--output-format", "json", "--model", MODEL],
        capture_output=True, text=True, timeout=TIMEOUT_S)
    if proc.returncode != 0:
        if _looks_like_limit(proc.stdout, proc.stderr):
            raise UsageLimitError(f"claude usage limit: {(proc.stderr or proc.stdout)[:200]}")
        # The real reason usually rides in the JSON envelope on STDOUT (e.g. "Not logged
        # in · Please run /login" after a laptop re-auth lapse — 2026-08-04/05, five
        # silent "claude exited 1:" retries), so surface stdout too, preferring the
        # envelope's own result text.
        detail = proc.stderr.strip() or ""
        try:
            env = json.loads(proc.stdout)
            detail = detail or str(env.get("result") or env)[:400]
        except (ValueError, TypeError):
            detail = detail or proc.stdout.strip()[:400]
        if "not logged in" in detail.lower():
            detail += "  → ssh to the host and run `claude /login` (interactive)."
        raise RuntimeError(f"claude exited {proc.returncode}: {detail[:400]}")
    envelope = json.loads(proc.stdout)
    if isinstance(envelope, dict) and envelope.get("is_error"):
        msg = str(envelope.get("result") or envelope)[:400]
        if _looks_like_limit(msg, str(envelope.get("api_error_status") or "")):
            raise UsageLimitError(f"claude usage limit: {msg[:200]}")
        if "not logged in" in msg.lower():
            msg += "  → ssh to the host and run `claude /login` (interactive)."
        raise RuntimeError(f"claude error envelope: {msg}")
    text = envelope.get("result") if isinstance(envelope, dict) else None
    if not text:
        raise RuntimeError(f"no result in claude output: {proc.stdout[:400]}")
    # strict JSON expected; strip an accidental fence defensively
    text = re.sub(r"^```(json)?\s*|\s*```$", "", text.strip())
    return json.loads(text)


# ------------------------------------------------------------------ fix verification
def verify_fixes(report: dict, diff: dict, is_zh: bool) -> None:
    hsk = _hsk() if is_zh else None
    by_key = {(f["scene"], f["field"], f.get("option")): f for f in diff["fields"]}
    for fr in report.get("fields", []):
        fix = fr.get("suggested_fix")
        if not fix:
            continue
        if not is_zh or hsk is None:
            fr["suggested_fix_verified"] = None   # nothing mechanical to verify
            continue
        src = by_key.get((fr.get("scene"), fr.get("field"), fr.get("option")))
        cur = (src or {}).get("cur") or {}
        hans = fix.get("Hans", cur.get("Hans") or "")
        hant = fix.get("Hant", cur.get("Hant") or "")
        zhuyin = fix.get("zhuyin", cur.get("zhuyin") or "")
        ok = True
        try:
            # Shares Gate 1's comparison rather than re-rolling it: comparing only via
            # to_simplified false-positives on the durative 着 (s2tw writes it 著, which is
            # also valid Simplified), which would brand a CORRECT suggestion "failed machine
            # check" and make apply_suggested_fix 409. See auto_checks._hant_correspondence.
            # Anything but a clean 'ok' still fails CLOSED, exactly as before — a badge that
            # merely withholds "verified" is the safe side, unlike Gate 1's hard block.
            if hant and auto_checks._hant_correspondence(hsk, hans, hant) != "ok":
                ok = False
            if zhuyin and hans:
                _, warns = hsk.zhuyin_to_pinyin(zhuyin, hsk.to_simplified(hans))
                if warns:
                    ok = False
        except Exception:  # noqa: BLE001
            ok = False
        fr["suggested_fix_verified"] = ok


# ------------------------------------------------------------------ main
def _acquire_lock():
    """One runner at a time. The */5 cron has no overlap guard of its own, and a sweep of
    N sessions × a slow Claude call easily outlives 5 minutes — on 2026-08-09 two
    overlapping runs each reviewed the same pending list, writing duplicate reports
    43–60 s apart and (via ingest's delete-and-recreate) clobbering findings the reviewer
    had answered minutes earlier. Returns an open, locked file handle to hold for the
    process lifetime, or None if another run holds it (caller exits quietly)."""
    fh = open(LOCK_PATH, "w")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def _fkey(f: dict) -> tuple:
    return (f.get("scene"), f.get("field"), f.get("option"))


def _baseline(con, sid: str):
    """The newest successful report that recorded what it judged (input_json) — the
    incremental-review baseline. Returns (summary, {key: input_field}, {key: report_field})
    or (None, None, None) when there is nothing to diff against: a pre-migration report has
    no snapshot and so forces one last FULL review, which then writes one."""
    row = con.execute(
        "SELECT report_json, input_json FROM auto_reviews WHERE session_id=? "
        "AND status='ok' AND input_json IS NOT NULL "
        "ORDER BY created_at DESC LIMIT 1", (sid,)).fetchone()
    if not row:
        return None, None, None
    try:
        prev_in = {_fkey(f): f for f in json.loads(row["input_json"]).get("fields") or []}
        rep = json.loads(row["report_json"])
        prev_rep = {_fkey(f): f for f in rep.get("fields") or []}
    except (ValueError, TypeError, AttributeError):
        return None, None, None
    return rep.get("summary") or "", prev_in, prev_rep


def pending_sessions(con):
    """Submitted sessions needing a review: no report newer than the last update, OR the
    latest report is an ERROR older than ERROR_RETRY_S (errors are retried, not final).
    Usage-limit failures write NO row at all, so those sessions simply stay pending here
    and are reviewed automatically once the limit resets."""
    now = time.time()
    rows = con.execute(
        "SELECT s.id, s.trip_id, s.updated_at, r.created_at last_report, r.status last_status "
        "FROM sessions s LEFT JOIN auto_reviews r ON r.id = "
        " (SELECT id FROM auto_reviews WHERE session_id = s.id ORDER BY created_at DESC LIMIT 1) "
        "WHERE s.status='submitted'").fetchall()
    out = []
    for r in rows:
        if r["last_report"] is None or r["last_report"] < r["updated_at"]:
            out.append(r)
        elif r["last_status"] == "error" and (now - r["last_report"]) > ERROR_RETRY_S:
            out.append(r)
    return out


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def ensure_table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS auto_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
        trip_id TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
        model TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'ok',
        ok_count INTEGER NOT NULL DEFAULT 0, warn_count INTEGER NOT NULL DEFAULT 0,
        flag_count INTEGER NOT NULL DEFAULT 0, report_json TEXT NOT NULL DEFAULT '{}')""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_autoreviews_session "
                "ON auto_reviews(session_id, created_at)")
    # input_json = the diff this report judged, the incremental-review baseline
    # (2026-08-10). CREATE IF NOT EXISTS won't add it to an existing table.
    cols = {r[1] for r in con.execute("PRAGMA table_info(auto_reviews)")}
    if "input_json" not in cols:
        con.execute("ALTER TABLE auto_reviews ADD COLUMN input_json TEXT")
    # The runner can reach a DB whose backend hasn't restarted onto the new schema yet.
    con.executescript(auto_review_ingest.FINDINGS_DDL)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sid", help="review this session even if already reported")
    ap.add_argument("--dry-run", action="store_true", help="print report, write nothing")
    args = ap.parse_args()

    lock = _acquire_lock()   # held (open) until the process exits
    if lock is None:
        print("[auto-review] another run is still active — skipping this tick.")
        return

    # Usage-limit backoff: after a limit hit we only re-attempt hourly (each attempt is
    # one cheap failed call). Sessions stay queued; nothing is lost while limited.
    state = _load_state()
    now = time.time()
    if not args.sid and state.get("backoff_until", 0) > now:
        mins = int((state["backoff_until"] - now) / 60)
        print(f"[auto-review] usage-limit backoff — next attempt in ~{mins} min.")
        return

    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    ensure_table(con)

    if args.sid:
        targets = con.execute("SELECT id, trip_id, updated_at FROM sessions WHERE id=?",
                              (args.sid,)).fetchall()
    else:
        targets = pending_sessions(con)
    if not targets:
        print("[auto-review] nothing to review.")
        return

    for s in targets:
        sid, trip_id = s["id"], s["trip_id"]
        diff = session_diff(con, sid)
        if not diff["fields"]:
            print(f"[auto-review] {trip_id}: no changed fields — skipping.")
            continue

        # Incremental re-review: judge ONLY the fields whose text changed since the last
        # successful report; everything else keeps its previous verdict (and, in ingest,
        # the reviewer's previous answer). --sid stays a forced FULL review.
        prev_summary = prev_in = prev_rep = None
        if not args.sid:
            prev_summary, prev_in, prev_rep = _baseline(con, sid)
        changed_keys = None
        carried: list[dict] = []
        to_review = diff["fields"]
        if prev_in is not None:
            fresh = []
            for f in diff["fields"]:
                k = _fkey(f)
                pf = prev_in.get(k)
                if pf is not None and pf.get("cur") == f.get("cur") and k in prev_rep:
                    carried.append(prev_rep[k])
                else:
                    fresh.append(f)
            to_review = fresh
            changed_keys = {_fkey(f) for f in fresh}

        if prev_in is not None and not to_review:
            # Nothing changed since the last AI pass (typically: the reviewer answered the
            # findings and re-submitted). Re-file the previous verdicts so the session
            # leaves the pending queue, call no model, touch no findings, bounce nothing —
            # the trip goes straight to the admin. THIS is the loop's cut-off point.
            report = {"summary": (prev_summary or "").strip() or "carried forward",
                      "fields": carried}
            report["summary"] += "  [no text changes since the last review — verdicts " \
                                 "carried forward, no new AI pass]"
            verdicts = [f.get("verdict") for f in carried]
            print(f"[auto-review] {trip_id} ({sid}): unchanged since last report — "
                  f"carrying {len(carried)} verdict(s) forward, no claude call.")
            if args.dry_run:
                continue
            con.execute(
                "INSERT INTO auto_reviews(session_id, trip_id, created_at, model, status, "
                "ok_count, warn_count, flag_count, report_json, input_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (sid, trip_id, time.time(), MODEL, "ok",
                 verdicts.count("ok"), verdicts.count("warning"),
                 verdicts.count("needs_human"),
                 json.dumps(report, ensure_ascii=False),
                 json.dumps(diff, ensure_ascii=False)))
            con.commit()
            continue

        sub_diff = dict(diff, fields=to_review)
        print(f"[auto-review] {trip_id} ({sid}): {len(to_review)} field(s) to judge "
              f"({len(carried)} unchanged, carried), calling claude ({MODEL})…")
        started = time.time()
        try:
            report = call_claude(sub_diff)
            verify_fixes(report, sub_diff, trip_id.endswith("_ZH"))
            if carried:
                # Merge: previous verdicts for unchanged fields + the fresh ones, in the
                # full diff's order, so the stored report still covers every edited field
                # (apply_suggested_fix and the FE read the LATEST report only).
                new_by_key = {_fkey(f): f for f in report.get("fields") or []}
                merged = []
                for f in diff["fields"]:
                    k = _fkey(f)
                    if k in changed_keys:
                        if k in new_by_key:
                            merged.append(new_by_key.pop(k))
                    else:
                        merged.append(prev_rep[k])
                merged.extend(new_by_key.values())   # defensive: off-key model entries
                report["fields"] = merged
            status = "ok"
            if state.get("backoff_until"):
                state.pop("backoff_until", None)
                _save_state(state)
        except UsageLimitError as e:
            # No report row (session stays queued for after the reset), hourly backoff,
            # stop the whole run — every other session would hit the same wall.
            state["backoff_until"] = time.time() + LIMIT_BACKOFF_S
            state["last_limit_error"] = str(e)[:300]
            _save_state(state)
            print(f"[auto-review] {trip_id}: USAGE LIMIT — backing off 60 min, "
                  f"session stays queued. ({e})")
            break
        except Exception as e:  # noqa: BLE001 — fail open to the manual queue
            report = {"summary": f"auto-review FAILED: {e}", "fields": []}
            status = "error"
        verdicts = [f.get("verdict") for f in report.get("fields", [])]
        ok_n = verdicts.count("ok")
        warn_n = verdicts.count("warning")
        flag_n = verdicts.count("needs_human")
        print(f"[auto-review] {trip_id}: {status} in {time.time()-started:.0f}s — "
              f"{ok_n} ok, {warn_n} warning, {flag_n} needs_human. {report.get('summary','')}")
        if args.dry_run:
            print(json.dumps(report, ensure_ascii=False, indent=1))
            continue
        cur = con.execute(
            "INSERT INTO auto_reviews(session_id, trip_id, created_at, model, status, "
            "ok_count, warn_count, flag_count, report_json, input_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (sid, trip_id, time.time(), MODEL, status, ok_n, warn_n, flag_n,
             json.dumps(report, ensure_ascii=False),
             json.dumps(diff, ensure_ascii=False) if status == "ok" else None))
        con.commit()
        # Turn the non-clean verdicts into triage items for the SUBMITTING REVIEWER and
        # bounce the session back to them ('submitted' -> 'ai_review'). A clean report
        # creates nothing and leaves it for the admin. An ERRORED report must not reach
        # ingest at all: its empty field list used to run the delete-and-recreate anyway,
        # WIPING every answered finding (and the notes the admin is owed) for the session.
        if status == "ok":
            n = auto_review_ingest.ingest(con, sid, trip_id, cur.lastrowid, report,
                                          changed_keys=changed_keys)
            if n:
                print(f"[auto-review] {trip_id}: {n} finding(s) sent back to the reviewer "
                      f"— session is now 'ai_review'.")
    con.close()


if __name__ == "__main__":
    main()
