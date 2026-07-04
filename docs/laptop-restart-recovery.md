# If this laptop restarts or loses power — what to do

**Short version: nothing.** This laptop is set up to bring the review app back by itself
after any reboot or power loss. This page is for confirming that happened, and what to do
in the rare case it didn't.

## Step 1 — Wait

Plug the laptop in / turn it on. Wait about **2 minutes** for Ubuntu to boot and the app to
start. You don't need to log into the desktop — the app runs as a background system
service and starts before any login screen appears.

## Step 2 — Check it worked

Open **https://review.dynamiclanguages.org/** in a browser (on any device, not just this
laptop). If the page loads and you can log in, **you're done — stop here.**

## Step 3 — If it did NOT load after 2 minutes

Sit at the laptop itself, log in (see "Login" below if you don't know the password), open a
**Terminal** (Ctrl+Alt+T), and run this one line:

```bash
systemctl is-active review-app review-tunnel
```

You should see two lines that both say `active`. Then re-check the site
(https://review.dynamiclanguages.org/) — it can take another 15-30 seconds for the
Cloudflare tunnel to finish reconnecting even after the services say `active`.

### If either one says something other than `active` (e.g. `failed` or `inactive`)

Run this to restart both:

```bash
sudo systemctl restart review-app review-tunnel
```

It will ask for the laptop's password (typed characters won't show on screen — that's
normal, just type and press Enter). Wait 30 seconds, then re-check the site.

### If that still doesn't fix it

Run this and read the last ~30 lines it prints — it usually says in plain English what's
wrong (e.g. "no such file", "connection refused", "port already in use"):

```bash
journalctl -u review-app -u review-tunnel --no-pager -n 60
```

Paste that output into a Claude Code session in this folder (`~/Desktop/Server`) and ask it
to diagnose — it has full context on how this box is set up.

## Login

- **Laptop user account:** `dynamic-languages` — ask Dave for the password if you don't
  have it.
- Desktop auto-login is **not** currently enabled on this box (a deliberate choice — anyone
  with physical access to the laptop would otherwise reach the desktop with no password).
  This does **not** stop the app itself — see Step 1. It only matters if you need to sit at
  the laptop and open a terminal.

## Things that are ALREADY automatic — you should never need to do these by hand

- **The app itself** (`review-app` + `review-tunnel` systemd services) — starts on boot,
  restarts itself if it crashes.
- **Nightly `review.db` backup to Cloudflare R2** — 3am daily, via cron.
- **Activity email digest** — every 15 minutes, via cron.

## Things that are NOT automatic — only matters if this laptop is dead/replaced

If this laptop is destroyed, stolen, or needs replacing, the reviewer/admin data itself is
safe (nightly R2 backups of `review.db`; audio masters live on R2 + the workstation). But a
**new** machine needs manual setup from scratch — follow `docs/ubuntu-server-setup.md` and
`docs/server-migration.md` in this repo. Do not run the workstation's `host_acquire.cmd` to
take over hosting while THIS laptop is still trying to reboot/reconnect — check
`py scripts/host_baton.py status` first, so both machines never run the app at once (see
`server-migration.md`, "Switching hosts — the baton").

## If you just need someone to know the laptop rebooted

Nothing to send — the activity notifier and backup jobs run regardless, and there's no
separate "the laptop rebooted" alert. If you want to confirm the app is genuinely healthy
after a restart, Step 2 above (opening the site) is the real test.
