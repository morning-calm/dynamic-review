# Ubuntu server setup — repurposing the old laptop for the review app

Turn the old Windows 7 laptop (i5-3230M, 8 GB, integrated Intel graphics) into the always-on
review-app host. **Part A** gets Ubuntu running (beginner-friendly — the part you asked
about; drivers are a non-issue on this 2013 Intel hardware). **Part B** installs the base
server tooling. **Part C** runs the app — which on Linux requires the path-porting from
`server-migration.md` (Phase 2) first.

## What you'll need
- The laptop + charger.
- A USB stick **≥ 8 GB** (it gets erased).
- An **Ethernet cable** (simplest networking; Wi-Fi usually works too).
- ~1 hour.
- *(Recommended)* a cheap **SSD** if it still has a spinning hard drive — the single biggest
  speed win for an always-on box on this CPU. Swap it in before installing.

---

## Part A — install Ubuntu (the OS)

### A1. Make the install USB (on your Windows machine)
- Download **Ubuntu Desktop 24.04 LTS** (`.iso`) from ubuntu.com. Desktop (not Server) is
  friendlier for a first-timer and still runs fine as the always-on host; 24.04 ships
  **Python 3.12**, which the app needs.
- Flash it to the USB with **Rufus** (rufus.ie) or **balenaEtcher**: select the ISO, select
  the USB, write. ~5 min.

### A2. Boot the laptop from the USB
- Insert the USB, power on, and tap the **boot-menu key** repeatedly (often **F12**; Dell F12,
  HP F9/Esc, Lenovo F12). Choose the USB drive.
- If it won't boot the USB, enter **BIOS/UEFI setup** (F2 or Del at power-on) and: disable
  **Secure Boot** if present, and make sure USB boot is enabled. A Win7-era machine is often
  plain Legacy BIOS — the simplest case.

### A3. Try it BEFORE installing (the de-risk step)
- At the Ubuntu menu pick **"Try Ubuntu"** — a full live session running off the USB, nothing
  written to disk yet.
- Check: the screen looks right, **Wi-Fi sees networks** (or just plug in Ethernet), the
  trackpad moves, sound plays. If these work in the live session they'll work once installed —
  this is where you'd catch the rare Wi-Fi-chip quirk before committing anything.

### A4. Install
- Double-click **"Install Ubuntu"** on the live desktop.
- Language/keyboard → connect to network → tick **"Install third-party software / drivers"**
  (pulls any proprietary Wi-Fi/firmware automatically).
- Install type: **"Erase disk and install Ubuntu"** — you're wiping Windows 7 entirely; this
  box is now a server.
- Set a computer name (e.g. `review-server`), a **username + strong password** (used for login
  and `sudo`). Enable **"Log in automatically"** so an always-on box comes back cleanly after a
  power blip.
- Let it install (~15–20 min), remove the USB when prompted, reboot.

You're now on the Ubuntu desktop. The part you were nervous about is done.

---

## Part B — base server tooling

Open **Terminal** (Ctrl+Alt+T) and run these (paste one block at a time; it'll ask for your
password):

```bash
# 1. system up to date
sudo apt update && sudo apt upgrade -y

# 2. core tooling: Python 3.12 (default on 24.04) + venv/pip, ffmpeg, git, build tools
sudo apt install -y python3 python3-venv python3-pip python3-dev ffmpeg git build-essential

# 3. SSH so you can manage it headless from the workstation
sudo apt install -y openssh-server
sudo systemctl enable --now ssh
hostname -I            # note the laptop's IP (e.g. 192.168.x.x) → from the workstation: ssh <user>@<ip>

# 4. cloudflared (the tunnel) — install the official .deb (version-independent, simplest)
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o /tmp/cloudflared.deb
sudo dpkg -i /tmp/cloudflared.deb

# 5. Node LTS (to build the frontend) — via NodeSource
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt install -y nodejs
```

**Keep it always-on** (a laptop must not sleep, especially with the lid shut):

```bash
# never suspend/hibernate
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target

# don't sleep when the lid closes
sudo sed -i 's/^#\?HandleLidSwitch=.*/HandleLidSwitch=ignore/' /etc/systemd/logind.conf
sudo systemctl restart systemd-logind
```
Also in **Settings → Power**, set "Screen Blank" to Never and disable Automatic Suspend.

---

## Part C — run the review app on Linux

> ⚠️ **Prerequisite — do this first.** The backend currently hardcodes Windows `D:\` paths
> (`config.SCRIPTS_ROOT`, the thumbnail/ogg trees, the audio masters). On Linux those must be
> made configurable/relative and masters fetched from R2 — the **Phase 2** work in
> `server-migration.md`. Until that's done the app won't start on Linux. The steps below assume
> it's been ported.

1. **Clone both repos** (pick a Linux home, e.g. `~/review`):
   ```bash
   mkdir -p ~/review && cd ~/review
   git clone <dynamic-review>  review-app
   git clone <dynamic-content> Scripts        # SCRIPTS_ROOT points here after the port
   ```
2. **Copy the secrets** from the workstation (into the Scripts repo): `.env`,
   `firebase_staging_key.json`, and `review-app/scripts/notifier_config.json`. e.g.:
   ```bash
   scp <user>@<workstation-ip>:"/d/Dynamic Languages/Scripts/.env"  ~/review/Scripts/
   ```
3. **Python deps in a venv** (Ubuntu 24.04 blocks system-wide pip — venv is required):
   ```bash
   cd ~/review/review-app/backend
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```
   Then build the **CJK aligner** venv per `research/cjk-aligner/` (torch/torchaudio/uroman,
   CPU build — needed for Ted's Mandarin; slow on this CPU but works).
4. **review.db** — restore the canonical copy from R2 (do this on handoff, not while the
   workstation is serving — see the baton in `server-migration.md`):
   `python scripts/backup_review_db.py restore`, then move it into `backend/review.db`.
5. **Build the frontend:** `cd ../frontend && npm install && npm run build` → `dist/`.
6. **cloudflared tunnel creds** — copy the tunnel's credentials from the workstation so
   `cloudflared tunnel run review-app` works here:
   ```bash
   mkdir -p ~/.cloudflared
   scp <user>@<workstation-ip>:"C:/Users/david/.cloudflared/*"  ~/.cloudflared/
   ```
7. **Run it** (note: `python` from the venv, not the Windows `py -3.12`):
   ```bash
   cd ~/review/review-app/backend
   REVIEW_APP_SERVE_FRONTEND=1 REVIEW_APP_COOKIE_SECURE=1 \
     .venv/bin/python -m uvicorn --app-dir . app.main:app --host 127.0.0.1 --port 8000
   # in another shell:
   cloudflared tunnel run review-app
   ```

### Make it start at boot with systemd (replaces Windows Task Scheduler)
Two services so uvicorn + the tunnel start on boot and restart on crash. Create
`/etc/systemd/system/review-app.service`:
```ini
[Unit]
Description=review-app backend
After=network-online.target
[Service]
User=<you>
WorkingDirectory=/home/<you>/review/review-app/backend
Environment=REVIEW_APP_SERVE_FRONTEND=1 REVIEW_APP_COOKIE_SECURE=1
ExecStart=/home/<you>/review/review-app/backend/.venv/bin/python -m uvicorn --app-dir . app.main:app --host 127.0.0.1 --port 8000
Restart=always
[Install]
WantedBy=multi-user.target
```
and `review-tunnel.service` (`ExecStart=/usr/bin/cloudflared tunnel run review-app`,
`Restart=always`, same `User`). Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now review-app review-tunnel
sudo systemctl status review-app         # verify it's up
```

Move the **backup + notifier** off Task Scheduler onto **cron**:
```bash
crontab -e
# daily review.db backup at 03:00
0 3 * * *   cd ~/review/review-app && backend/.venv/bin/python scripts/backup_review_db.py >> backend/backup.log 2>&1
# activity notifier every 15 min
*/15 * * * * cd ~/review/review-app && backend/.venv/bin/python scripts/activity_notifier.py >> backend/notifier.log 2>&1
```

### The host baton on Linux
`host_baton.py` is cross-platform — on Linux it drives the **systemd units** above (via
`host_release.sh` / `host_acquire.sh`), while the DB/marker/backup layer is identical to
Windows. Run the wrappers with **sudo** if the units are system-wide (they are, as installed
above). Override unit names with `REVIEW_APP_SERVICE` / `REVIEW_APP_TUNNEL_SERVICE` if you
named them differently.

### Still to port before relying on Linux
- The `D:\` path assumptions (the Part C prerequisite above) — the real blocker.
- Whisper + the CJK aligner run **CPU-only** here (no GPU) — functional, just slow.

Pairs with **`server-migration.md`** (two-machine topology, data buses, the host baton).
