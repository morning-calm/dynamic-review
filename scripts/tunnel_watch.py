"""Tunnel-down alerting for the live review app.

Probes the PUBLIC health endpoint (https://review.dynamiclanguages.org/api/health)
and emails dave when it stops answering, plus a recovery email when it comes back.

⚠ This must run OFF the laptop (the Windows workstation, task "ReviewAppTunnelWatch",
every 5 min): when the tunnel is down it is almost always because the laptop's internet
is down, so laptop-side detection could never send the alert. Wrapper:
scripts/run_tunnel_watch.cmd; register per docs/tunnel-watch.md.

Behaviour:
  - A probe is DOWN on any non-200 / bad body / timeout. 502/530 = tunnel or uvicorn
    down; a transport error is reported as such (it may also mean THIS machine is
    offline, in which case the email cannot send either — acceptable, nothing can).
  - Alert only after CONSECUTIVE_FAILS consecutive DOWN probes (skips sub-5-min blips,
    which the 2026-08-18 Wi-Fi flaps showed are common and self-heal).
  - One alert per outage, re-alerted every REALERT_HOURS while still down, and one
    recovery email (with outage duration) when it returns.
  - State in scripts/.tunnel_watch_state.json (gitignored). Exit code 0 always —
    Task Scheduler treats nonzero as task failure and this should never "fail".

Email = the same MailWizz transactional path as the activity notifier, config
scripts/notifier_config.json (gitignored; already present on this machine).
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
from activity_notifier import send_via_mailwizz  # noqa: E402

HEALTH_URL = "https://review.dynamiclanguages.org/api/health"
STATE_FILE = SCRIPTS_DIR / ".tunnel_watch_state.json"
CONFIG_FILE = SCRIPTS_DIR / "notifier_config.json"
PROBE_TIMEOUT_S = 20
CONSECUTIVE_FAILS = 2       # at 5-min cadence: alert after ~5-10 min of outage
REALERT_HOURS = 6


def probe() -> tuple[bool, str]:
    """Return (up, detail). Up = HTTP 200 with the {"ok": true} body."""
    req = urllib.request.Request(HEALTH_URL, headers={"User-Agent": "review-app-tunnel-watch"})
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_S) as resp:
            body = resp.read(200).decode("utf-8", "replace")
            if resp.status == 200 and '"ok"' in body:
                return True, "200 ok"
            return False, f"HTTP {resp.status} with unexpected body {body!r}"
    except urllib.error.HTTPError as e:
        # 502/530 = Cloudflare edge is up but the tunnel/origin is not — the
        # exact "Bad gateway" reviewers see.
        return False, f"HTTP {e.code} from the Cloudflare edge (tunnel or backend down)"
    except Exception as e:  # URLError, timeout, DNS — transport-level
        return False, f"no response ({type(e).__name__}: {e})"


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text("utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), "utf-8")
    tmp.replace(STATE_FILE)


def send(subject: str, lines: list[str]) -> None:
    cfg = json.loads(CONFIG_FILE.read_text("utf-8"))
    text = "\n".join(lines)
    html = "<p>" + "<br>".join(lines) + "</p>"
    status, body = send_via_mailwizz(cfg, subject, text, html)
    print(f"email: {status} {body[:200]}")


def fmt_ts(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def main() -> None:
    now = time.time()
    state = load_state()
    up, detail = probe()
    print(f"{fmt_ts(now)} probe: {'UP' if up else 'DOWN'} — {detail}")

    if up:
        if state.get("alerted"):
            since = state.get("down_since", now)
            mins = max(1, round((now - since) / 60))
            send(
                "review-app: back UP",
                [f"The review app is reachable again ({detail}).",
                 f"Outage started around {fmt_ts(since)} and lasted about {mins} min.",
                 f"Probed from this workstation: {HEALTH_URL}"],
            )
        state = {"fails": 0, "alerted": False}
    else:
        fails = state.get("fails", 0) + 1
        state["fails"] = fails
        state.setdefault("down_since", now)
        overdue = now - state.get("last_alert", 0) > REALERT_HOURS * 3600
        if fails >= CONSECUTIVE_FAILS and (not state.get("alerted") or overdue):
            still = " (still down)" if state.get("alerted") else ""
            send(
                f"review-app: tunnel/app DOWN{still}",
                [f"The review app is not answering: {detail}.",
                 f"Down since around {fmt_ts(state['down_since'])} "
                 f"({fails} consecutive failed probes, 5 min apart).",
                 "Reviewers will be seeing 'Bad gateway'.",
                 "Checks, in order: laptop internet/Wi-Fi (the usual cause — "
                 "ssh review-laptop), then 'systemctl status review-tunnel' "
                 "and the uvicorn process. Runbook: docs/server-migration.md.",
                 f"Probed from this workstation: {HEALTH_URL}"],
            )
            state["alerted"] = True
            state["last_alert"] = now

    save_state(state)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Never propagate — a crashing watcher must not look like a "task failure"
        # storm in Task Scheduler. It prints, and the next 5-min run retries.
        print(f"tunnel_watch error: {type(e).__name__}: {e}")
