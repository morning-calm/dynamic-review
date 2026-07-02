"""Host baton — hand the review app (and its canonical review.db) between machines,
one host at a time. See docs/server-migration.md ("Switching hosts").

review.db is the ONLY copy of review state and DIVERGES if two hosts run the app at once.
This enforces a single active host via an R2 marker + the existing R2 db-backup as the baton:

    release  (on the CURRENT host)  stop uvicorn+tunnel -> backup review.db to R2 -> mark released
    acquire  (on the NEW host)      refuse if another host is active -> restore review.db from R2
                                     -> mark active -> start uvicorn+tunnel
    status                          print the R2 marker (who holds the baton)

Marker: s3://<review-audio>/_db-backups/ACTIVE_HOST.json  {host, state, at}.
Safe by design: acquire refuses when the marker says another host is 'active' (use --force to
override, e.g. the other machine died without releasing). Always released cleanly = no surprises.

Cross-platform: the DB/marker/backup layer is pure Python (SQLite + boto3) and identical on
Windows and Linux, so a host can be either OS. Only the process-control differs and is branched:
  * Windows  — taskkill + a detached launch of uvicorn/cloudflared.
  * Linux    — the review-app / review-tunnel systemd units (see docs/ubuntu-server-setup.md).
"""
from __future__ import annotations
import argparse, json, os, socket, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app import config, review_audio  # noqa: E402
import backup_review_db as bkp        # noqa: E402  (reuse backup() + R2 constants)

IS_WIN = os.name == "nt"
REPO = Path(__file__).resolve().parent.parent
MARKER_KEY = f"{bkp.DB_PREFIX}/ACTIVE_HOST.json"
HOST = socket.gethostname()
PORT = int(os.environ.get("REVIEW_APP_PORT", "8000"))
TUNNEL = os.environ.get("REVIEW_APP_TUNNEL", "review-app")
# Windows: launch cloudflared directly from this path (detached).
CLOUDFLARED = os.environ.get(
    "REVIEW_APP_CLOUDFLARED",
    r"C:\Program Files (x86)\cloudflared\cloudflared.exe" if IS_WIN else "cloudflared")
DETACH = ((getattr(subprocess, "DETACHED_PROCESS", 0)
           | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) if IS_WIN else 0)
# Linux: manage uvicorn + tunnel via these systemd units (docs/ubuntu-server-setup.md).
SVC = os.environ.get("REVIEW_APP_SERVICE", "review-app")
TUNNEL_SVC = os.environ.get("REVIEW_APP_TUNNEL_SERVICE", "review-tunnel")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _r2():
    s3 = review_audio._r2()
    if s3 is None:
        sys.exit("[baton] R2 creds missing (Cloudfare_* in the Scripts .env) — aborting.")
    return s3


def read_marker() -> dict | None:
    s3 = _r2()
    try:
        obj = s3.get_object(Bucket=bkp.BACKUP_BUCKET, Key=MARKER_KEY)
        return json.loads(obj["Body"].read())
    except Exception:  # noqa: BLE001 — NoSuchKey / first run
        return None


def write_marker(state: str) -> None:
    s3 = _r2()
    body = json.dumps({"host": HOST, "state": state, "at": _now()}, indent=2).encode()
    s3.put_object(Bucket=bkp.BACKUP_BUCKET, Key=MARKER_KEY, Body=body,
                  ContentType="application/json")
    print(f"[baton] marker -> state={state} host={HOST} at={_now()}")


def _port_in_use(port: int) -> bool:
    """Cross-platform: is something already listening on 127.0.0.1:port?"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _pid_on_port(port: int) -> str | None:
    """Windows only — the PID listening on the port, for taskkill."""
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if f":{port} " in line and "LISTENING" in line:
            return line.split()[-1]
    return None


def _systemctl(action: str) -> bool:
    """Linux: start/stop the review-app + tunnel systemd units. Returns success."""
    units = [SVC, TUNNEL_SVC]
    r = subprocess.run(["systemctl", action, *units], capture_output=True, text=True)
    out = (r.stderr or r.stdout).strip()
    if r.returncode == 0:
        print(f"[baton] systemctl {action} {' '.join(units)}")
        return True
    print(f"[baton] systemctl {action}: {out or 'failed'}")
    if any(w in out.lower() for w in ("auth", "privilege", "permission", "access denied")):
        print("[baton] hint: run with sudo — system units need root (e.g. `sudo scripts/host_acquire.sh`).")
    return False


def _wait_for_up() -> None:
    import urllib.request
    for _ in range(30):
        time.sleep(1)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2) as r:
                if r.status == 200:
                    print("[baton] app is up."); return
        except Exception:  # noqa: BLE001
            pass
    tail = "server.log" if IS_WIN else f"journalctl -u {SVC}"
    print(f"[baton] WARNING: app did not answer within 30s — check {tail}.")


def _stop_processes() -> None:
    if not IS_WIN:
        _systemctl("stop")   # best-effort; a not-loaded unit is fine
        return
    pid = _pid_on_port(PORT)
    if pid:
        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
        print(f"[baton] stopped uvicorn (pid {pid})")
    else:
        print("[baton] no uvicorn on the port")
    r = subprocess.run(["taskkill", "/IM", "cloudflared.exe", "/F"], capture_output=True, text=True)
    print("[baton] stopped cloudflared" if r.returncode == 0 else "[baton] no cloudflared running")


def _start_processes() -> None:
    if not IS_WIN:
        if not _systemctl("start"):
            sys.exit(1)
        _wait_for_up()
        return
    logs = config.BACKEND_ROOT / "server.log"
    env = {**os.environ, "REVIEW_APP_SERVE_FRONTEND": "1", "REVIEW_APP_COOKIE_SECURE": "1"}
    with open(logs, "ab") as lf:
        subprocess.Popen(
            ["py", "-3.12", "-m", "uvicorn", "--app-dir", "backend",
             "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
            cwd=str(REPO), env=env, stdout=lf, stderr=subprocess.STDOUT, creationflags=DETACH)
    print(f"[baton] launched uvicorn (log -> {logs})")
    if Path(CLOUDFLARED).exists():
        subprocess.Popen([CLOUDFLARED, "tunnel", "run", TUNNEL],
                         cwd=str(REPO), creationflags=DETACH)
        print(f"[baton] launched cloudflared tunnel '{TUNNEL}'")
    else:
        print(f"[baton] cloudflared not found at {CLOUDFLARED} — start the tunnel manually.")
    _wait_for_up()


def cmd_status() -> int:
    m = read_marker()
    print(json.dumps(m, indent=2) if m else "[baton] no marker yet (no host has acquired).")
    return 0


def cmd_release() -> int:
    print(f"[baton] releasing on {HOST} …")
    _stop_processes()
    if bkp.backup(include_work=False) != 0:
        return 1  # do NOT mark released if the canonical backup failed
    write_marker("released")
    print("[baton] released. Another host may now acquire.")
    return 0


def cmd_acquire(force: bool) -> int:
    m = read_marker()
    if m and m.get("state") == "active" and m.get("host") != HOST and not force:
        sys.exit(f"[baton] REFUSED: {m['host']} holds the baton (active since {m.get('at')}). "
                 f"Release it there first, or re-run with --force if that host is down.")
    if _port_in_use(PORT):
        sys.exit(f"[baton] REFUSED: something is already listening on :{PORT} here — stop it first.")
    # pull the canonical DB from R2 (replace the local review.db; clear stale WAL/SHM)
    s3 = _r2()
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(config.DB_PATH) + suffix)
        if p.exists():
            p.unlink()
    s3.download_file(bkp.BACKUP_BUCKET, bkp.LATEST_DB_KEY, str(config.DB_PATH))
    print(f"[baton] restored review.db from R2 -> {config.DB_PATH}")
    write_marker("active")
    _start_processes()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Hand the review app between hosts (one at a time).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("release")
    acq = sub.add_parser("acquire")
    acq.add_argument("--force", action="store_true",
                     help="acquire even if the marker says another host is active (it's down)")
    args = ap.parse_args()
    if args.cmd == "status":
        return cmd_status()
    if args.cmd == "release":
        return cmd_release()
    return cmd_acquire(args.force)


if __name__ == "__main__":
    raise SystemExit(main())
