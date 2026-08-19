# Tunnel-down alerting (ReviewAppTunnelWatch)

> **RETIRED 2026-08-19:** the workstation task is deleted — the workstation is often
> off, so it was never a reliable checker. Alerting is now Cloudflare's own
> **"Cloudflare Tunnel status change"** notification (dashboard → Notifications),
> enabled by dave 2026-08-19, which emails on tunnel down/recovery from Cloudflare's
> edge. Caveat it does NOT cover: uvicorn wedged while the tunnel is up. The scripts
> below stay in the repo — re-register per the recipe if HTTP-level probing is ever
> wanted again (on a machine that stays awake).

Emails dave when the live app stops answering — built 2026-08-18 after the Italian
reviewer hit "Bad gateway" + a mid-session freeze (the laptop's Wi-Fi/internet dropped
repeatedly 08-17 evening → 08-18 morning; see the 2026-08-18 session log).

## How it works

`scripts/tunnel_watch.py` probes the public, auth-exempt
`https://review.dynamiclanguages.org/api/health` and emails via the same MailWizz
transactional path as the activity notifier (`scripts/notifier_config.json`).

- **DOWN** = non-200 / bad body / timeout. Alert after **2 consecutive** failed probes
  (5 min apart — skips the short self-healing blips), re-alert every **6 h** while
  still down, one **recovery** email with the outage duration when it returns.
- State: `scripts/.tunnel_watch_state.json` (gitignored). Log:
  `backend/tunnel_watch.log`. The script always exits 0.

## Where it runs — and why it must NOT run on the laptop

Windows Task Scheduler task **`ReviewAppTunnelWatch`** on the **workstation**, every
5 min, via `scripts/run_tunnel_watch_hidden.vbs` (a wscript wrapper around
`scripts/run_tunnel_watch.cmd` — running the .cmd directly flashes a console window
in the interactive session every 5 min; wscript runs it windowless). Tunnel-down almost always means the
laptop's *internet* is down, so laptop-side detection could never send the alert.
Caveat: the workstation must be awake to probe — if it sleeps overnight, coverage
gaps follow. The zero-gap complement is Cloudflare's own **tunnel health
notification** (dashboard → Notifications → "Cloudflare Tunnel status change" for
tunnel `review-app`) which runs in Cloudflare's infra; set it up in the dashboard
when convenient — the two overlap harmlessly.

## Re-register on a new machine

```
schtasks /Create /TN ReviewAppTunnelWatch ^
  /TR "wscript.exe \"<repo>\scripts\run_tunnel_watch_hidden.vbs\"" /SC MINUTE /MO 5 /F
```

Needs `scripts/notifier_config.json` present (copy from the example + fill keys).
Test end-to-end: `py -3.12 scripts/tunnel_watch.py` (prints the probe result;
sends nothing while the app is up).
