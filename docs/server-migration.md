# Always-on review-app server (laptop) + workstation pipeline

Goal: run the review app **always-on** on a dedicated laptop, while **this workstation**
keeps running the `dynamic-content` pipeline (Trello export, CEFR audio, stage 5c, stage 9).
The two machines stay loosely coupled through **GitHub** (code + trip manifest) and
**Cloudflare R2** (audio, review.db, handshake). Switching which machine hosts the app is a
deliberate, one-at-a-time "baton" pass — never both at once (review.db is the only copy of
review state and **diverges** if two hosts run).

## Roles

| | Workstation (this machine) | Laptop (always-on server) |
|---|---|---|
| CPU/GPU | fast + NVIDIA GPU | i5-3230M, 8 GB, **no GPU** (CPU whisper/torch — slow but works) |
| Runs | `dynamic-content` pipeline; audio generation; stage 9 finalise | `dynamic-review` app (uvicorn + cloudflared tunnel); backups; notifier |
| Owns | source audio masters + image trees on `D:\`; secrets | **canonical `review.db`**; the live tunnel |
| App | failover host only (not normally running the app) | primary host |

## Data buses (who produces → channel → who consumes)

| Data | Producer | Channel | Consumer |
|---|---|---|---|
| Code (both repos) | — | **GitHub** | both (`git pull`) |
| `trips_to_review.json` manifest | workstation `Trello/export_review_trips.py` | **GitHub** (this repo) | laptop app |
| Audio masters (seed) | workstation pipeline | **Phase 1: file sync** · **Phase 2: R2 `review-audio/<cid>/`** | laptop app (`resolve_audio_dir`) |
| Corrected `<i>.mp3` + `_fallback` + `versions` | laptop app (combine/approve) | **R2 `review-audio/`** (already mirrored) | workstation stage 9 |
| `completed_trips.json` (Stage-9 handshake) | laptop app | **R2 `_handshake/`** (add) or same-repo file | workstation stage 9 |
| `review.db` | laptop (canonical) | **R2 `_db-backups/`** (backup/restore) | failover host |
| Thumbnails | workstation `thumbs.py` upload | **R2 `dynamic-languages-thumbs`** | laptop app serves from `thumbs.dynamiclanguages.org` |
| Overlays / 360 stills | workstation image trees | **Phase 1 sync (optional)** | laptop app (display-only; 404s degrade gracefully) |
| Staging Trip/TripGroup text | laptop app (submit) | **Firebase** | pipeline / prod |

Key wins already in place: **thumbnails already serve from R2** (laptop needs no local thumb
trees), and **approve already mirrors corrected masters to R2** (server→workstation audio
handoff exists). The one gap for full decoupling is **seed-time** master fetch → Phase 2.

## What the laptop needs locally

**Hard (app won't run without these):**
- Python **3.12** (match minor), `ffmpeg` on PATH, `git`, `cloudflared`.
- `dynamic-review` repo → `D:\Projects\WebApp\review-app`.
- `dynamic-content` (Scripts) repo → `D:\Dynamic Languages\Scripts` (so `config.SCRIPTS_ROOT`
  resolves; otherwise edit `SCRIPTS_ROOT`). The backend **imports** modules from it.
- Scripts `.env` + `firebase_staging_key.json` (secrets — copy, never commit).
- `scripts/notifier_config.json` (MailWizz key — copy).
- `review.db` restored from R2 (see baton, below).
- **CJK aligner venv** `research/cjk-aligner/venv` (torch/torchaudio/uroman) — required for
  **Ted's Mandarin** (and Japanese) editing. Heaviest CPU/RAM item on this box.
- Audio masters — **Phase 1: synced from the workstation** · **Phase 2: fetched from R2**.

**Not needed / degraded:** local thumbnail source trees (thumbs come from R2); overlay/still
trees (display-only — optional sync); a GPU (CPU fallback, just slow).

---

## Phase 1 — stand up the laptop (Windows clone; masters via sync)

Gets you always-on with the least code change (all absolute-path assumptions preserved).

1. **OS + always-on:** clean install Win 10/11. Power plan → never sleep, never turn off
   disk. Set the `david` account to **auto-login** (the scheduled tasks use Interactive
   logon) — or edit each task to "Run whether user is logged on or not."
2. **Base tools:** install Python 3.12, Git, ffmpeg (on PATH), cloudflared, Node LTS.
3. **Repos (match paths):**
   ```powershell
   git clone <dynamic-review>  "D:\Projects\WebApp\review-app"
   git clone <dynamic-content> "D:\Dynamic Languages\Scripts"
   ```
4. **Secrets:** copy from the workstation → same relative paths on the laptop:
   `…\Scripts\.env`, `…\Scripts\firebase_staging_key.json`,
   `…\review-app\scripts\notifier_config.json`.
5. **Python deps:** `cd backend && py -3.12 -m pip install -r requirements.txt`.
   Then build the aligner venv per `research/cjk-aligner/` (torch/torchaudio/uroman).
6. **Frontend build:** `cd frontend && npm install && npm run build` → `frontend/dist`.
7. **review.db:** on the workstation `py -3.12 scripts/backup_review_db.py backup`; on the
   laptop `py -3.12 scripts/backup_review_db.py restore` (writes `backend/review.db`).
8. **Audio masters (sync):** mirror the workstation's master dirs to the **same paths** on the
   laptop — the dirs `resolve_audio_dir` searches: the Quicktrips `paths_for` mp3_dir,
   `Audio Generation\<trip>\`, and `Audio Generation\Sent to KP\MP3\<trip>\`. Use **Syncthing**
   (one-way, workstation→laptop) or a scheduled `robocopy /MIR`. Optionally also sync the
   overlay/still trees. *(Or skip sync and go straight to Phase 2.)*
9. **Tunnel:** copy the `review-app` tunnel credentials (`%USERPROFILE%\.cloudflared\*.json`
   + cert) to the laptop. **Run the tunnel on only one machine at a time.**
10. **Launch + persist:**
    ```powershell
    # backend (tunnel mode)
    $env:REVIEW_APP_SERVE_FRONTEND=1; $env:REVIEW_APP_COOKIE_SECURE=1
    py -3.12 -m uvicorn --app-dir backend app.main:app --host 127.0.0.1 --port 8000
    # tunnel
    cloudflared tunnel run review-app
    ```
    Wrap both as **Task Scheduler tasks at logon** (or NSSM services) so always-on survives
    reboots. Re-register `ReviewAppDbBackup` + `ReviewAppActivityNotify`
    (`docs/backup-and-restore.md`, `docs/activity-notifier.md`).
11. **Verify:** open the tunnel URL; seed a trip (audio resolves); regenerate a scene (whisper
    runs — slow OK); a Mandarin edit (aligner venv); approve → confirm the R2 mirror +
    `completed_trips.json` update.

---

## Phase 2 — decouple (recommended follow-on; removes the sync)

Implements the `CLAUDE.md` "Path B" deploy change so the workstation can be **off** without
breaking the app:

1. Backend: when `resolve_audio_dir`/seed can't find masters locally, **fetch from R2
   `review-audio/<cid>/`** into `work/` (cache). (New code — a small, contained change.)
2. Workstation: guarantee every reviewable trip's masters are on R2. Stage 5c already uploads
   CEFR English; add a bulk `upload_review_audio_r2.py` pass to `export_review_trips.py` for
   the non-5c trips (Japan `_EN`, HSK3 `_ZH`).
3. Server: on approve/complete, also push `completed_trips.json` to **R2 `_handshake/`**;
   workstation stage 9 pulls it before finalising.

After Phase 2 the laptop needs almost no big local trees — the two machines are coupled only
by GitHub + R2.

---

## Switching hosts — the baton (one host at a time)

**Golden rule:** exactly one machine runs uvicorn+tunnel **and** owns `review.db`. Never both
(review.db diverges). This is enforced by **`scripts/host_baton.py`** (wrappers
`host_release.cmd` / `host_acquire.cmd`) + an R2 marker
`s3://review-audio/_db-backups/ACTIVE_HOST.json` (`{host, state, at}`).

**To hand off** (only while the app is idle):

On the **current** host — release:
```
scripts\host_release.cmd
```
Stops uvicorn + cloudflared, backs up `review.db` to R2 (the baton), marks `released`. It
will **not** mark released if the backup fails.

On the **new** host — acquire:
```
scripts\host_acquire.cmd            REM add --force only if the other host died un-released
```
Refuses if the marker says another host is still `active` (or if something already listens on
the port); else pulls the canonical `review.db` from R2, marks `active`, starts uvicorn
(`SERVE_FRONTEND=1 COOKIE_SECURE=1`) + `cloudflared tunnel run review-app`, and waits for the
app to answer. Check who currently holds it any time with `py -3.12 scripts/host_baton.py status`.

Normal state = **laptop is host**; take the baton to the workstation only for occasional
GPU-heavy work, then hand it straight back. Config via env if paths differ:
`REVIEW_APP_CLOUDFLARED`, `REVIEW_APP_TUNNEL`, `REVIEW_APP_PORT`.

## Rollback / safety
- `review.db` backups in R2 are timestamped — restore any point.
- Masters also live on R2 + the workstation — a bad sync is recoverable.
- The workstation stays app-capable as the failover host.
