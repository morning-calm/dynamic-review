# Phase-1 deploy — Cloudflare Tunnel (single-origin)

Expose the review app to off-machine reviewers over HTTPS, with the backend **and** the
built frontend served **single-origin** from this machine. $0 (uses the
`dynamiclanguages.org` Cloudflare zone). This machine must stay on with **both** the
backend and the tunnel running.

## One-time setup
1. **Install cloudflared** (Windows):
   ```
   winget install --id Cloudflare.cloudflared
   ```
   (or download `cloudflared.exe` from Cloudflare and put it on PATH)

2. **Authenticate** to the Cloudflare account that holds `dynamiclanguages.org`:
   ```
   cloudflared tunnel login
   ```
   A browser opens — authorize and pick the `dynamiclanguages.org` zone. Saves a cert to
   `%USERPROFILE%\.cloudflared\cert.pem`.

3. **Create the named tunnel:**
   ```
   cloudflared tunnel create review-app
   ```
   Prints a tunnel **UUID** and saves creds to `%USERPROFILE%\.cloudflared\<UUID>.json`.

4. **Route DNS** to the tunnel (your chosen subdomain):
   ```
   cloudflared tunnel route dns review-app review.dynamiclanguages.org
   ```

5. Copy `cloudflared-config.example.yml` → `%USERPROFILE%\.cloudflared\config.yml` and
   fill in your `<UUID>` and hostname.

## Run (each session)
1. Build the frontend (only when it changed):
   ```
   cd frontend && npm run build
   ```
2. Start the backend single-origin + HTTPS-cookie mode (keep this window open):
   ```
   deploy\run-backend-tunnel.ps1
   ```
   (sets `REVIEW_APP_SERVE_FRONTEND=1` + `REVIEW_APP_COOKIE_SECURE=1`, binds 127.0.0.1:8000)
3. Start the tunnel (separate window, keep open):
   ```
   cloudflared tunnel run review-app
   ```
4. Reviewers open **https://review.dynamiclanguages.org** → the login page.

## Notes
- The backend serves the whole app single-origin, so the tunnel only points at
  `http://localhost:8000` — no CORS, no separate frontend host.
- **`REVIEW_APP_COOKIE_SECURE=1` is required over HTTPS** (the media `<audio>`/`<img>`
  cookie needs the `Secure` flag). Leave it OFF for plain-HTTP localhost testing.
- Provision reviewer accounts with `backend/manage.py`; rotate any dev passwords first.
- **Auth is the entire perimeter** (app login). There is no other gate on an open tunnel —
  keep passwords strong. `/regenerate` burns ElevenLabs credits and `approve` writes
  staging, so a leaked login is real exposure.
- To run the tunnel as an always-on Windows service instead of a foreground window:
  `cloudflared service install` (after config.yml is in place).
