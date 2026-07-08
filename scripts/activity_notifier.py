#!/usr/bin/env python3
"""Activity notifier — emails dave@dynamiclanguages.org a short digest of reviewer/admin
work in the review app.

WHAT IT REPORTS (nothing else — individual field autosaves are "minor" and dropped):
  * reviewer LOGIN — a reviewer-role user logged in (exact, from auth_sessions;
                     admin logins are NOT reported — that's usually dave himself)
  * trip STARTED   — a reviewer/admin begins (or resumes after a 90m+ break) a session
  * trip FINISHED  — session submitted / approved / marked complete (exact user attribution)
  * BREAK 90m+     — someone who was active has gone quiet for >=90 min (also the
                     "done for the day" signal)

RATE LIMITS: login / start / finish are IMMEDIATE — they bypass the 1/hour gate so dave
knows exactly when a reviewer starts and finishes (per request 2026-07-08). Breaks are
batched behind the 1/hour gate as before. A hard <= 40 emails/day cap backstops runaways;
held events ride out with the next allowed email, so nothing is lost.

SAFETY: opens review.db READ-ONLY and keeps its own state in backend/notifier_state.json.
It never writes the DB and never touches the running app — safe to run while a review is live.

Designed to run every ~15 min from Windows Task Scheduler (task: ReviewAppActivityNotify),
mirroring the ReviewAppDbBackup pattern. First run seeds a silent baseline (no historical
blast); only activity after that generates emails.

Usage:
  py -3.12 scripts/activity_notifier.py            # detect + send if allowed
  py -3.12 scripts/activity_notifier.py --dry-run  # detect + PRINT the email, send nothing
  py -3.12 scripts/activity_notifier.py --force     # ignore the 1/hour gate (still <=40/day)
  py -3.12 scripts/activity_notifier.py --reset-baseline   # re-seed baseline, emit nothing
"""
from __future__ import annotations
import argparse, base64, json, sqlite3, time, urllib.parse, urllib.request, urllib.error
import datetime as dt
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
DB_PATH = BACKEND / "review.db"
STATE_PATH = BACKEND / "notifier_state.json"
CONFIG_PATH = Path(__file__).resolve().parent / "notifier_config.json"

BREAK_SECONDS = 90 * 60
MAX_PER_HOUR_SECONDS = 60 * 60
MAX_PER_DAY = 40
# Event kinds that must reach dave IMMEDIATELY (skip the 1/hour gate, not the daily cap).
IMMEDIATE_KINDS = {"login", "start", "finish", "auto_review"}

# ---------------------------------------------------------------- attribution
# field_edits has no user_id. Finishes use the exact submitted_by/approved_by/
# completed_by column. Starts/breaks are attributed to the language-capable user who
# logged in most recently before the activity (falling back to the language default).
LANG_DEFAULT = {"Mandarin": "ted", "Japanese": "toshifumi", "English": "admin"}
# A session in a reviewer's specialty language is attributed to that reviewer when they
# have logged in within this window before the activity (admin login noise notwithstanding).
SPECIALIST = {"Mandarin": "ted", "Japanese": "toshifumi"}
SPECIALIST_LOGIN_WINDOW = 12 * 3600


def session_language(row) -> str:
    if row["is_zh"]:
        return "Mandarin"
    folder = (row["folder_name"] or "")
    trip = (row["trip_id"] or "")
    if folder.startswith("Japan/") or trip.endswith("_JP"):
        return "Japanese"
    return "English"


def load_users(con):
    users = []
    for r in con.execute("SELECT id, username, role, languages_json FROM users WHERE active=1"):
        langs = json.loads(r["languages_json"] or "[]")
        users.append({"id": r["id"], "username": r["username"], "role": r["role"], "langs": langs})
    return users


def logins_by_user(con):
    out = {}
    for r in con.execute("SELECT user_id, created_at FROM auth_sessions ORDER BY created_at"):
        out.setdefault(r["user_id"], []).append(r["created_at"])
    return out


def live_tokens_by_user(con, at_ts):
    """user_id -> True if the user holds a token VALID at at_ts. THE reliable 'is this
    person using the app' signal: tokens persist for days, so 'logged in recently' misses
    a returning reviewer (the 13:12 2026-07-08 email blamed 'admin' for Ted's session —
    Ted's last login ROW was 2 days old but his token was live)."""
    out = {}
    for r in con.execute(
            "SELECT user_id FROM auth_sessions WHERE created_at <= ? AND expires_at > ?",
            (at_ts + 1, at_ts)):
        out[r["user_id"]] = True
    return out


def detect_logins(state, users, logins, now):
    """Exact login events for REVIEWER-role users: any auth_sessions row created after
    the stored watermark. Admin logins are skipped (that's normally dave). Rows deleted
    by logout/expiry before we see them are simply missed — acceptable."""
    wm = state.get("login_watermark")
    if wm is None:
        # First run after upgrade (or fresh baseline): silently start from now.
        state["login_watermark"] = now
        return []
    events = []
    for u in users:
        if u["role"] == "admin":
            continue
        for t in logins.get(u["id"], []):
            if t > wm:
                events.append({"ts": t, "kind": "login", "user": u["username"]})
    state["login_watermark"] = now
    events.sort(key=lambda e: e["ts"])
    state["pending"].extend(events)
    return events


def detect_auto_reviews(state, con, now):
    """One event per NEW auto_reviews row (Gate-2 Claude verdict, written by
    scripts/claude_review.py). Watermark on the row id; table may not exist yet."""
    wm = state.get("auto_review_watermark")
    if wm is None:
        try:
            row = con.execute("SELECT MAX(id) m FROM auto_reviews").fetchone()
            state["auto_review_watermark"] = row["m"] or 0
        except sqlite3.OperationalError:
            pass
        return []
    events = []
    try:
        rows = con.execute(
            "SELECT id, trip_id, created_at, status, ok_count, warn_count, flag_count "
            "FROM auto_reviews WHERE id > ? ORDER BY id", (wm,)).fetchall()
    except sqlite3.OperationalError:
        return []
    for r in rows:
        events.append({"ts": r["created_at"], "kind": "auto_review", "user": "auto-review",
                       "trip": r["trip_id"], "status": r["status"], "ok": r["ok_count"],
                       "warn": r["warn_count"], "flag": r["flag_count"]})
        state["auto_review_watermark"] = r["id"]
    state["pending"].extend(events)
    return events


def attribute(lang, at_ts, users, logins, live_tokens=None):
    """Best-effort username for a start/break at time at_ts on a session of `lang`."""
    by_name = {u["username"]: u for u in users}
    # 1) specialty language + the specialist holds a LIVE token (or logged in recently)
    #    -> the specialist. Token presence beats login recency: reviewers stay logged in
    #    for days, so a fresh login row is the exception, not the rule.
    spec = SPECIALIST.get(lang)
    if spec and spec in by_name:
        uid = by_name[spec]["id"]
        if live_tokens and live_tokens.get(uid):
            return spec
        prior = [t for t in logins.get(uid, []) if t <= at_ts + 1]
        if prior and (at_ts - max(prior)) <= SPECIALIST_LOGIN_WINDOW:
            return spec
    # 2) otherwise the language-capable user with the most recent prior login
    best, best_ts = None, -1
    for u in (x for x in users if lang in x["langs"]):
        prior = [t for t in logins.get(u["id"], []) if t <= at_ts + 1]
        if prior and max(prior) > best_ts:
            best, best_ts = u["username"], max(prior)
    return best or LANG_DEFAULT.get(lang, "someone")


# ---------------------------------------------------------------- DB snapshot
def snapshot(con):
    """Per-session aggregates used for event detection."""
    sess = {}
    for r in con.execute(
        "SELECT id, trip_id, folder_name, is_zh, status, submitted_by, approved_by, updated_at "
        "FROM sessions"):
        sess[r["id"]] = dict(r)
    # edit activity + progress per session
    for r in con.execute(
        "SELECT session_id, MIN(updated_at) first_ts, MAX(updated_at) last_ts, "
        "COUNT(*) touched, SUM(CASE WHEN flag='done' THEN 1 ELSE 0 END) done "
        "FROM field_edits GROUP BY session_id"):
        if r["session_id"] in sess:
            sess[r["session_id"]].update(
                first_ts=r["first_ts"], last_ts=r["last_ts"],
                touched=r["touched"], done=r["done"] or 0)
    # completed_trips (marker completions, incl. 'manual')
    completed = {}
    for r in con.execute("SELECT trip_id, completed_by, completed_at, method, session_id FROM completed_trips"):
        completed[r["trip_id"]] = dict(r)
    return sess, completed


# ---------------------------------------------------------------- state
def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return None


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def fresh_state():
    return {"baseline_at": None, "sessions": {}, "pending": [], "email_log": []}


# ---------------------------------------------------------------- detection
def detect(state, sess, completed, users, logins, now, live_tokens=None):
    """Mutate state.sessions and append events to state['pending']. Returns new events list."""
    new_events = []
    st = state["sessions"]
    for sid, s in sess.items():
        lang = session_language(s)
        touched = s.get("touched", 0)
        last_ts = s.get("last_ts")
        prev = st.get(sid, {"started": False, "finished": False, "break_last_ts": None,
                             "last_ts": None})

        # ---- START / RESUME ----
        if touched and last_ts is not None:
            resumed = (prev.get("last_ts") is not None
                       and last_ts > prev["last_ts"]
                       and prev.get("break_reported"))
            if not prev["started"] or resumed:
                # Attribute at the RECENT activity time, not first_ts — a session seeded
                # weeks ago by admin then picked up by ted must read "ted", not "admin"
                # (the 2026-07-07 mis-attribution).
                who = attribute(lang, last_ts, users, logins, live_tokens)
                verb = "resumed" if resumed else "started"
                new_events.append({"ts": last_ts if resumed else (s.get("first_ts") or last_ts),
                                   "kind": "start", "user": who, "trip": s["trip_id"],
                                   "lang": lang, "verb": verb})
                prev["started"] = True
                prev["break_reported"] = False
            prev["last_ts"] = last_ts

        # ---- FINISH (exact attribution) ----
        finished_now = None
        if s["status"] in ("submitted", "approved"):
            finished_now = ("approved" if s["status"] == "approved" else "submitted",
                            s["approved_by"] if s["status"] == "approved" else s["submitted_by"])
        comp = completed.get(s["trip_id"])
        if comp and comp.get("session_id") == sid and s["status"] not in ("submitted", "approved"):
            finished_now = (f"completed ({comp.get('method')})", comp.get("completed_by"))
        if finished_now and not prev["finished"]:
            state_verb, who = finished_now
            done = s.get("done", 0); touched_n = s.get("touched", 0)
            new_events.append({"ts": s.get("last_ts") or now, "kind": "finish",
                               "user": who or attribute(lang, now, users, logins, live_tokens),
                               "trip": s["trip_id"], "lang": lang, "verb": state_verb,
                               "done": done, "touched": touched_n})
            prev["finished"] = True

        # ---- BREAK 90m+ ----
        if (prev["started"] and not prev["finished"] and last_ts is not None
                and (now - last_ts) >= BREAK_SECONDS and not prev.get("break_reported")):
            who = attribute(lang, last_ts, users, logins, live_tokens)
            new_events.append({"ts": last_ts, "kind": "break", "user": who,
                               "trip": s["trip_id"], "lang": lang,
                               "done": s.get("done", 0), "touched": s.get("touched", 0),
                               "idle_min": int((now - last_ts) / 60)})
            prev["break_reported"] = True

        st[sid] = prev

    state["pending"].extend(new_events)
    return new_events


# ---------------------------------------------------------------- email
def fmt_time(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%H:%M")


def compose(pending):
    """Return (subject, text_body, html_body) for the buffered events."""
    evs = sorted(pending, key=lambda e: e["ts"])
    lines = []
    for e in evs:
        t = fmt_time(e["ts"])
        u = e["user"]
        if e["kind"] == "login":
            lines.append(f"{t} — {u} logged in")
        elif e["kind"] == "auto_review":
            if e.get("status") == "error":
                lines.append(f"{t} — auto-review of {e['trip']} FAILED — manual review needed")
            else:
                verdict = (f"{e.get('ok',0)} ok, {e.get('warn',0)} warning, "
                           f"{e.get('flag',0)} needs human")
                lines.append(f"{t} — auto-review: {e['trip']} — {verdict}")
        elif e["kind"] == "start":
            lines.append(f"{t} — {u} {e.get('verb','started')} {e['trip']} ({e['lang']})")
        elif e["kind"] == "finish":
            prog = f" — {e.get('done',0)}/{e.get('touched',0)} done" if e.get("touched") else ""
            lines.append(f"{t} — {u} {e['verb']} {e['trip']}{prog}")
        elif e["kind"] == "break":
            prog = f", {e.get('done',0)}/{e.get('touched',0)} done" if e.get("touched") else ""
            lines.append(f"{t} — {u} on a break (idle {e.get('idle_min','90')}m+); "
                         f"last on {e['trip']}{prog}")
    n = len(evs)
    subject = ("review-app: " + lines[0].split('— ',1)[1]) if n == 1 else f"review-app activity — {n} updates"
    text = "\n".join(f"- {l}" for l in lines)
    html = "<ul>" + "".join(f"<li>{l}</li>" for l in lines) + "</ul>"
    return subject, text, html


# ---------------------------------------------------------------- MailWizz transactional send
def send_via_mailwizz(cfg, subject, text_body, html_body):
    """POST a single transactional email to a MailWizz v2 instance.

    v2 auth = the single `X-Api-Key` header (no HMAC). The create-transactional-email
    endpoint takes form-data with fields nested under email[...]; body + plain_text are
    base64-encoded; send_at is required (UTC 'Y-m-d H:i:s').

    Config keys (scripts/notifier_config.json):
      base_url   e.g. https://mail.example.com/api/index.php   (the MailWizz API root)
      X-Api-Key  the v2 API key
      from_email, from_name, to_email, to_name
    """
    endpoint = cfg["base_url"].rstrip("/") + "/transactional-emails"
    fields = {
        "to_name": cfg.get("to_name", "Dave"),
        "to_email": cfg["to_email"],
        "from_name": cfg.get("from_name", "Review App"),
        "from_email": cfg["from_email"],
        "subject": subject,
        "body": base64.b64encode(html_body.encode()).decode(),
        "plain_text": base64.b64encode(text_body.encode()).decode(),
        "send_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }
    data = urllib.parse.urlencode({f"email[{k}]": v for k, v in fields.items()}).encode()
    headers = {
        "X-Api-Key": cfg["X-Api-Key"],
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")[:500]
    except urllib.error.HTTPError as e:  # surface MailWizz's JSON error body
        return e.code, e.read().decode("utf-8", "replace")[:500]


# ---------------------------------------------------------------- rate limit + main
def can_send(state, now, force):
    log = [t for t in state["email_log"] if now - t < 24 * 3600]
    state["email_log"] = log
    today = dt.date.fromtimestamp(now)
    sent_today = sum(1 for t in log if dt.date.fromtimestamp(t) == today)
    if sent_today >= MAX_PER_DAY:
        return False, f"daily cap reached ({sent_today}/{MAX_PER_DAY})"
    # login / start / finish must land immediately — only pure-break batches wait.
    if any(e.get("kind") in IMMEDIATE_KINDS for e in state["pending"]):
        force = True
    if not force and log and (now - max(log)) < MAX_PER_HOUR_SECONDS:
        wait = int((MAX_PER_HOUR_SECONDS - (now - max(log))) / 60)
        return False, f"1/hour gate: next send in ~{wait} min"
    return True, f"ok ({sent_today}/{MAX_PER_DAY} today)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the email, send nothing")
    ap.add_argument("--force", action="store_true", help="ignore the 1/hour gate")
    ap.add_argument("--reset-baseline", action="store_true", help="re-seed baseline, emit nothing")
    ap.add_argument("--test", action="store_true", help="send a one-off test email via MailWizz and exit")
    args = ap.parse_args()

    if args.test:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        status, resp = send_via_mailwizz(
            cfg, "review-app: notifier test",
            "This is a test of the review-app activity notifier. If you got this, MailWizz delivery works.",
            "<p>This is a test of the review-app activity notifier.<br>"
            "If you got this, MailWizz delivery works.</p>")
        print(f"[test] HTTP {status}\n{resp}")
        return

    now = time.time()
    con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    users = load_users(con)
    logins = logins_by_user(con)
    sess, completed = snapshot(con)

    state = load_state()
    if state is None or args.reset_baseline:
        # Silent baseline: mark every existing session as already started/finished so we
        # never blast historical activity. Only NEW work after now generates events.
        state = fresh_state()
        state["baseline_at"] = now
        state["login_watermark"] = now   # never blast historical logins
        for sid, s in sess.items():
            last_ts = s.get("last_ts")
            state["sessions"][sid] = {
                "started": bool(s.get("touched")),
                "finished": s["status"] in ("submitted", "approved") or s["trip_id"] in completed,
                "last_ts": last_ts,
                "break_reported": (last_ts is not None and (now - last_ts) >= BREAK_SECONDS),
            }
        save_state(state)
        print(f"[notifier] baseline seeded at {dt.datetime.fromtimestamp(now):%Y-%m-%d %H:%M} "
              f"({len(sess)} sessions); no email.")
        return

    new_events = detect_logins(state, users, logins, now)
    new_events += detect_auto_reviews(state, con, now)
    live_tokens = live_tokens_by_user(con, now)
    new_events += detect(state, sess, completed, users, logins, now, live_tokens)
    for e in new_events:
        print(f"[event] {e['kind']:6} {fmt_time(e['ts'])} {e['user']} {e.get('trip','')}")

    if not state["pending"]:
        print("[notifier] no pending events; nothing to send.")
        save_state(state)
        return

    subject, text, html = compose(state["pending"])
    if args.dry_run:
        print("\n--- DRY RUN: email that WOULD be sent ---")
        print("To:", "dave@dynamiclanguages.org")
        print("Subject:", subject)
        print(text)
        print("--- end ---")
        save_state(state)  # persist detection so dry-run doesn't re-report same events
        return

    ok, why = can_send(state, now, args.force)
    if not ok:
        print(f"[notifier] holding {len(state['pending'])} event(s): {why}")
        save_state(state)
        return

    if not CONFIG_PATH.exists():
        print(f"[notifier] {CONFIG_PATH.name} missing — cannot send. "
              f"{len(state['pending'])} event(s) held.")
        save_state(state)
        return
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    try:
        status, resp = send_via_mailwizz(cfg, subject, text, html)
        print(f"[notifier] sent ({status}); {len(state['pending'])} event(s). resp={resp!r}")
        state["email_log"].append(now)
        state["pending"] = []
    except Exception as e:  # noqa: BLE001 — never crash the scheduled task
        print(f"[notifier] send FAILED, events held: {e}")
    save_state(state)


if __name__ == "__main__":
    main()
