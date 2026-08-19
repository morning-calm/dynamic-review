# 2026-08-18 — review-app

## ~10:00 — Italian reviewer outage: investigation + tunnel-down alerting

**Goals:** diagnose the Italian reviewer's "bad gateway this morning, then everything
froze" email; add alerting so tunnel outages reach dave before a reviewer does.

**What I did / found:**
- **Diagnosis (laptop logs):** the app itself was healthy throughout (uvicorn up since
  08-17 10:36, no OOM, disk 22%, load ~0.1). The laptop's **Wi-Fi/internet dropped
  repeatedly**: cloudflared lost all 4 tunnel connections in bursts Aug 17 20:37–21:36,
  Aug 18 01:21, 06:02, and 09:26–09:28 UK (NetworkManager shows the Wi-Fi association
  itself dropping at 09:26/09:27 — supplicant disconnect + DHCP renegotiation on
  `wlp3s0b1` → TP-Link_0FCC). Tunnel down ⇒ Cloudflare edge serves **502 Bad gateway**.
  The reviewer (session `sess_ebf738e2399c`) was actively working at 09:17 and the
  09:26 drop killed an in-flight `/audio/...` request = her "freeze". Zero Wi-Fi
  disconnects Aug 11–17, signal 100% ⇒ something changed 08-17 evening; most likely the
  **router/ISP** (several bursts lost internet without the association dropping).
  Recommended: Ethernet cable into the laptop (`enp2s0` currently cable-less) +
  power-cycle the TP-Link router. Not yet done — needs hands on the router.
- **Tunnel-down alerting (SHIPPED):** `scripts/tunnel_watch.py` + `run_tunnel_watch.cmd`
  + Task Scheduler task **`ReviewAppTunnelWatch`** on the **workstation** (every 5 min —
  deliberately NOT the laptop: tunnel-down usually = laptop internet down, which would
  also kill the alert email). Probes public `GET /api/health`; DOWN alert after 2
  consecutive failures, 6 h re-alert, recovery email with duration; MailWizz
  transactional (same path/config as the activity notifier). Doc: `docs/tunnel-watch.md`.
  Also added state files to `.gitignore`.

**Verified:** live probe UP via the scheduled task (logged in `backend/tunnel_watch.log`);
probe classification tested against a 401 path and a DNS-dead host; one real test email
sent and accepted by MailWizz (201). The full DOWN→recovery email cycle will only prove
itself on the next real outage.

**Open / low-urgency TODOs:**
- Physical fixes at the laptop site: Ethernet + router power-cycle (dave, in person).
- Set up Cloudflare's native "Tunnel status change" notification in the dashboard —
  covers the gap when this workstation is asleep.
- Tell the Italian reviewer: not her fault; outages were 1–10 min each, "wait a few
  minutes and retry" works.
- New files uncommitted — commit when reviewed.

**Next steps:** watch for the alert emails behaving on the next outage; consider
Ethernet as the permanent fix.
