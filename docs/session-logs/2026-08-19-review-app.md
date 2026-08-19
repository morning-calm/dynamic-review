# review-app — 2026-08-19

## 10:40 — Tunnel watch: killed the flashing cmd window
- **Goal:** stop ReviewAppTunnelWatch's 5-minute console flash on the workstation (dave was ready to delete the task over it).
- **What I did:** kept the alerting; re-registered the task to launch through a new windowless wrapper `scripts/run_tunnel_watch_hidden.vbs` (wscript runs the .cmd with window style 0). Updated `docs/tunnel-watch.md` (how-it-runs + re-register recipe).
- **Verified:** test-run of the re-registered task ran silently and appended a `probe: UP — 200 ok` line to `backend/tunnel_watch.log`.
- **Open:** Cloudflare dashboard "Tunnel status change" notification still unset — remains the zero-gap complement (workstation-sleep coverage gap stands).

## 10:55 — Tunnel watch retired; Cloudflare notification takes over
- dave enabled Cloudflare's "Tunnel status change" notification in the dashboard (edge-side, no local machine needed) — the right fit since this workstation is often off.
- Deleted the `ReviewAppTunnelWatch` scheduled task (confirmed gone). Scripts + recipe kept in repo; `docs/tunnel-watch.md` now carries a RETIRED banner noting the one uncovered case (uvicorn wedged while the tunnel is up).
