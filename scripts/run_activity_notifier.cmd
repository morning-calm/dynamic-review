@echo off
REM review-app activity notifier, run every ~15 min by Windows Task Scheduler
REM (task "ReviewAppActivityNotify"). Read-only on review.db; safe while a review is live.
REM Portable: cd's to the repo root relative to this file. Appends to backend\notifier.log
REM (gitignored via *.log). PYTHONIOENCODING keeps the em-dash from mojibaking in the log.
REM Re-register on a new machine: see docs/activity-notifier.md.
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
echo [%date% %time%] run >> "%~dp0..\backend\notifier.log"
py -3.12 scripts\activity_notifier.py >> "%~dp0..\backend\notifier.log" 2>&1
echo [%date% %time%] exit %errorlevel% >> "%~dp0..\backend\notifier.log"
