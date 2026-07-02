@echo off
REM Acquire the review-app baton on THIS host: refuse if another host is active, else pull
REM the canonical review.db from R2, mark 'active', and start uvicorn+cloudflared.
REM Pass --force to take over from a host that died without releasing. docs/server-migration.md.
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
py -3.12 scripts\host_baton.py acquire %*
