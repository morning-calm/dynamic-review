@echo off
REM Release the review-app baton on THIS host: stop uvicorn+cloudflared, back up review.db
REM to R2, mark 'released' so another host may acquire. See docs/server-migration.md.
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
py -3.12 scripts\host_baton.py release
