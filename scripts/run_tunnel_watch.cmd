@echo off
REM Tunnel-down alerting probe, run by Windows Task Scheduler (task "ReviewAppTunnelWatch",
REM every 5 min). MUST run on a machine other than the laptop — see scripts/tunnel_watch.py.
REM Appends stdout+stderr to backend\tunnel_watch.log (gitignored via *.log).
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
py -3.12 scripts\tunnel_watch.py >> "%~dp0..\backend\tunnel_watch.log" 2>&1
