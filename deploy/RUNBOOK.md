# review-app — Runbook: start the full stack from scratch

How to bring up **backend + frontend + Cloudflare tunnel** so reviewers can reach
**https://review.dynamiclanguages.org**. Assumes the one-time setup below is already done
on this machine (it is). For a brand-new machine, do the one-time setup first
(`deploy/README-tunnel.md`).

Repo root: `D:\Projects\WebApp\review-app`

---

## TL;DR — start it (public/tunnel mode)

Open **two separate** PowerShell windows. In each, paste **one whole line** and press Enter.
Each window then shows logs and **no prompt returns — that's correct, leave it open.**

**Window 1 — backend** (one line; works from any folder):
```powershell
& "D:\Projects\WebApp\review-app\deploy\run-backend-tunnel.ps1"
```
Wait for `Application startup complete.` — then leave this window running.

**Window 2 — tunnel** (a *new* window; one line):
```powershell
cloudflared tunnel run review-app
```
Wait for four `Registered tunnel connection` lines — then leave this window running.

Then open **https://review.dynamiclanguages.org** → the login page. Done.

> ⚠️ **Paste each command on its own line.** Don't put `cd ...` and the script on the same
> line — PowerShell treats the second as an argument and errors. The one-liners above use a
> full path so you don't need `cd` at all.
>
> Only rebuild the frontend if you changed FE code — as two separate lines:
> `cd D:\Projects\WebApp\review-app\frontend` ⏎ then `npm run build` ⏎

---

## The two run modes

| Mode | What runs | Reachable at | Use when |
|---|---|---|---|
| **Public (tunnel)** | backend serves the **built** frontend single-origin, + `cloudflared` | https://review.dynamiclanguages.org | reviewers work off-machine |
| **Local dev** | backend (`:8000`) + **Vite** dev server (`:5173`) | http://localhost:5173 | you're developing on this machine |

You do **not** run Vite in tunnel mode — the backend serves the built `frontend/dist`.

---

## One-time setup (already done here — for reference / a new machine)

- Python 3.12 (`& "C:\Users\david\AppData\Local\Programs\Python\Python312\python.exe"`) with backend deps installed; Node + `npm install` in `frontend/`.
- `cloudflared` installed (`winget install --id Cloudflare.cloudflared`).
- `cloudflared tunnel login` (browser → picked the `dynamiclanguages.org` zone).
- `cloudflared tunnel create review-app`  → tunnel id `b8ed17ee-1b4b-4a86-a78c-ab1a03812b81`.
- `cloudflared tunnel route dns review-app review.dynamiclanguages.org`.
- Config at `%USERPROFILE%\.cloudflared\config.yml` (points the hostname at `http://localhost:8000`).
- Users seeded (`admin`, `toshifumi`, `ted`) via `backend/manage.py`.

---

## Start — public (tunnel) mode, step by step

1. **(Optional) rebuild the frontend** — only if you changed frontend code:
   ```powershell
   cd D:\Projects\WebApp\review-app\frontend
   npm run build
   ```

2. **Backend** — Window 1 (paste this one line; keep the window open):
   ```powershell
   & "D:\Projects\WebApp\review-app\deploy\run-backend-tunnel.ps1"
   ```
   The script sets `REVIEW_APP_SERVE_FRONTEND=1` + `REVIEW_APP_COOKIE_SECURE=1` and starts
   uvicorn on `127.0.0.1:8000`. Wait for **`Application startup complete.`** The window will
   sit there showing logs with no prompt — **that means it's working.** Don't close it.

   *If PowerShell blocks the script* ("running scripts is disabled"), run it inline instead:
   ```powershell
   $env:REVIEW_APP_SERVE_FRONTEND="1"; $env:REVIEW_APP_COOKIE_SECURE="1"
   cd D:\Projects\WebApp\review-app\backend
   & "C:\Users\david\AppData\Local\Programs\Python\Python312\python.exe" -m uvicorn --app-dir . app.main:app --host 127.0.0.1 --port 8000
   ```

3. **Tunnel** — Window 2 (keep open):
   ```powershell
   cloudflared tunnel run review-app
   ```
   Wait for four **`Registered tunnel connection`** lines. *If `cloudflared` isn't found*,
   use the full path: `& "C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel run review-app`.

4. **Verify:** open https://review.dynamiclanguages.org (login page), or:
   ```powershell
   curl.exe https://review.dynamiclanguages.org/api/health   # -> {"ok":true}
   ```

---

## Start — local dev mode (no tunnel)

```powershell
# Window 1 — backend (NO secure cookie in dev; plain http)
cd D:\Projects\WebApp\review-app\backend
& "C:\Users\david\AppData\Local\Programs\Python\Python312\python.exe" -m uvicorn --app-dir . app.main:app --host 127.0.0.1 --port 8000

# Window 2 — frontend (Vite, hot reload)
cd D:\Projects\WebApp\review-app\frontend
npm run dev
```
Open **http://localhost:5173**. (Do **not** set `REVIEW_APP_COOKIE_SECURE` in dev — over
plain http a Secure cookie won't be sent and media/login break.)

---

## Stop everything

In each window press **Ctrl+C**. To force-stop by port/name from any PowerShell:
```powershell
# backend (8000) and Vite (5173)
foreach ($p in 8000,5173) {
  Get-NetTCPConnection -LocalPort $p -State Listen -EA SilentlyContinue |
    Select-Object -Expand OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force }
}
# tunnel
Stop-Process -Name cloudflared -Force -EA SilentlyContinue
```

---

## Users / credentials

Log in with the seeded accounts. Manage them from `backend/`:
```powershell
& "C:\Users\david\AppData\Local\Programs\Python\Python312\python.exe" manage.py list-users
& "C:\Users\david\AppData\Local\Programs\Python\Python312\python.exe" manage.py reset-password --username toshifumi          # prints a new password
& "C:\Users\david\AppData\Local\Programs\Python\Python312\python.exe" manage.py add-user --username newadmin --role admin
& "C:\Users\david\AppData\Local\Programs\Python\Python312\python.exe" manage.py add-user --username someone --role reviewer --languages Japanese
```

---

## Troubleshooting

- **"Select an app to open 'py'" dialog pops up** — this machine's `py` launcher is broken
  (a stub at `C:\WINDOWS\py`). Close the dialog. The run script calls Python by full path, so
  just re-run the Window 1 one-liner. If you type Python yourself, use the full path
  `C:\Users\david\AppData\Local\Programs\Python\Python312\python.exe`.
- **`[Errno 10048]` / port 8000 already in use** — an old backend is still running; stop it
  (see *Stop everything*) and retry.
- **`cloudflared` : command not found** — open a fresh terminal (PATH updates after install),
  or use the full path `C:\Program Files (x86)\cloudflared\cloudflared.exe`.
- **Login page loads but calls fail / 502** — the backend isn't up on `:8000`; start Window 1
  first, confirm `curl.exe http://127.0.0.1:8000/api/health` returns `{"ok":true}`.
- **Public site shows a Cloudflare error (1033)** — the tunnel (Window 2) isn't running, or the
  backend it points to is down. Start the backend, then the tunnel.
- **Login works locally but not over the tunnel** — make sure the backend was started with
  `REVIEW_APP_COOKIE_SECURE=1` (the tunnel script does this). Conversely, that var must be
  **off** for local http dev.
- **`ted` sees no trips** — expected: there are no Mandarin (`_ZH`) trips in the manifest yet.
