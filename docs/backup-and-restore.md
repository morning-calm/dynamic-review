# review.db backup & restore

`backend/review.db` (SQLite/WAL) is the **only** copy of in-progress review state — text
edits, flags, comments, coverage, audio-version history, users (with password hashes), and
bug-report threads. Per-session audio lives beside it in `backend/work/{sid}/`. Both sit on
the server machine only; neither is in git, and R2's `review-audio/<contentID>/` mirrors
corrected audio *bytes* but not the review state. **Lose this disk and you lose reviewer
work** unless it's been backed up.

## Backup script

`scripts/backup_review_db.py` — a WAL-safe **hot** backup: it uses SQLite's online-backup
API, so it captures a consistent snapshot even while uvicorn holds the DB open. Safe to run
on a schedule against the live app.

```bash
py -3.12 scripts/backup_review_db.py            # snapshot review.db -> R2
py -3.12 scripts/backup_review_db.py --work     # also zip+upload backend/work/ (large)
py -3.12 scripts/backup_review_db.py restore    # pull latest DB back to review.restored.db
```

- **Destination:** Cloudflare R2 bucket `review-audio` (reuses the app's `Cloudfare_*` creds
  from the Scripts `.env`), under prefix `_db-backups/`. Each run writes a timestamped key
  **and** overwrites `_db-backups/review-latest.db`. Override the bucket with
  `REVIEW_BACKUP_BUCKET`. `--work` zips `backend/work/` to `_work-backups/work-<ts>.zip`.
- **R2 is a backup target, not a live store.** The app always reads a *local* `review.db`
  and serves audio from local `work/`. You cannot point the app at R2 — recovery means
  downloading a snapshot back to disk (see Restore).

## Scheduled daily backup (Windows Task Scheduler)

A task named **`ReviewAppDbBackup`** runs `scripts/run_daily_backup.cmd` daily at **03:00**,
in the logged-on user session (the machine already stays logged in to serve the app +
cloudflared tunnel). Missed runs catch up on next login (`-StartWhenAvailable`). The wrapper
`cd`s to the repo relative to its own path and appends stdout+stderr to
`backend/backup.log` (gitignored).

### Re-create it on another machine

The wrapper (`scripts/run_daily_backup.cmd`) is committed and path-independent, so on a new
checkout you only need to (a) have `py -3.12` + the `Cloudfare_*` creds available (same
prerequisites as running the backend) and (b) register the task. Run this once in PowerShell
(adjust the repo path if not `D:\Projects\WebApp\review-app`):

```powershell
$taskName = "ReviewAppDbBackup"
$wrapper  = "D:\Projects\WebApp\review-app\scripts\run_daily_backup.cmd"
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
$action    = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$wrapper`""
$trigger   = New-ScheduledTaskTrigger -Daily -At 3:00AM
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Daily WAL-safe hot-backup of review.db to Cloudflare R2."
```

Verify / operate:

```powershell
Start-ScheduledTask   -TaskName "ReviewAppDbBackup"   # run now
Get-ScheduledTaskInfo -TaskName "ReviewAppDbBackup"   # LastRunTime / LastTaskResult (0 = ok)
Unregister-ScheduledTask -TaskName "ReviewAppDbBackup" -Confirm:$false   # remove
```

`LastTaskResult` `0x0` = success; also check the tail of `backend/backup.log`.

> Note: the task runs only while the user is logged on (no stored password needed). That
> matches this app's model — the machine stays logged in to run uvicorn + cloudflared. If you
> ever run the server headless/as a service, re-register with `-LogonType S4U` (or a stored
> password) so backups fire without an interactive session.

## Restore

R2 backups are disaster recovery. To bring one back:

```bash
py -3.12 scripts/backup_review_db.py restore    # downloads latest -> backend/review.restored.db
```

Then, with **uvicorn stopped** (so the file isn't locked / WAL doesn't shadow it):

```powershell
# remove the stale WAL sidecars, then swap the restored file in
Remove-Item "D:\Projects\WebApp\review-app\backend\review.db-wal","D:\Projects\WebApp\review-app\backend\review.db-shm" -ErrorAction SilentlyContinue
Move-Item -Force "D:\Projects\WebApp\review-app\backend\review.restored.db" "D:\Projects\WebApp\review-app\backend\review.db"
```

Restart uvicorn. If you also need the in-flight per-session audio, restore a
`_work-backups/work-<ts>.zip` into `backend/work/` (the DB references `work/{sid}/` paths, so
DB and `work/` are a matched pair — restore both from **near the same timestamp**).

## Moving the server to a different machine

`review.db` has **no cross-machine sync**. Two servers pointing at the same trips create two
diverging databases (a fresh machine starts empty and re-seeds trips from the pristine
masters; `submit` is last-writer-wins to Firebase). To migrate cleanly:

1. Stop uvicorn on the old machine (quiesce writes).
2. Run a final `py -3.12 scripts/backup_review_db.py --work`, **or** copy `backend/review.db`
   **and** `backend/work/` together to the new machine (they're a matched pair).
3. On the new machine: same prerequisites as the backend (Scripts modules, ffmpeg, GPU,
   `Cloudfare_*` + Firebase/EL/Gemini secrets), place `review.db` + `work/`, then
   **re-register the scheduled task** using the block above.

Don't run two live servers against the same trips unless the backend is first moved to a
shared datastore (the "Path B — deploy" lift in `CLAUDE.md`).
