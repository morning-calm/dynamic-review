@echo off
REM Daily review.db -> R2 hot-backup, run by Windows Task Scheduler (task "ReviewAppDbBackup").
REM Portable: cd's to the repo root relative to this file, so it works from any checkout /
REM machine. Appends stdout+stderr to backend\backup.log (gitignored via *.log).
REM Re-register on a new machine: see docs/backup-and-restore.md.
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
echo [%date% %time%] starting daily backup >> "%~dp0..\backend\backup.log"
py -3.12 scripts\backup_review_db.py >> "%~dp0..\backend\backup.log" 2>&1
echo [%date% %time%] exit code %errorlevel% >> "%~dp0..\backend\backup.log"
