@echo off
rem ---------------------------------------------------------------------------
rem Workstation Publish console (docs/post-approval-admin-spec.md §4).
rem Starts the review-app in PUBLISHER MODE on 127.0.0.1:8010 (loopback only, no
rem tunnel) serving the built SPA, and opens it in an Edge --app window.
rem
rem One-time setup on this workstation:
rem   1. cd frontend && npm run build        (the console serves frontend\dist)
rem   2. an admin login in the WORKSTATION backend\review.db:
rem        cd backend && py -3.12 manage.py add-user --username <name> --role admin
rem      (the workstation DB is separate from the laptop's — review state stays
rem       on the laptop; this instance only needs a login.)
rem Closing this window stops the console. Production writes still require the
rem per-job Apply confirmation AND each script's own --apply --i-am-sure gates.
rem ---------------------------------------------------------------------------
set REVIEW_APP_PUBLISHER=1
set REVIEW_APP_SERVE_FRONTEND=1
cd /d "%~dp0..\backend"
if not exist "..\frontend\dist\index.html" (
    echo !! frontend\dist is missing - run:  cd frontend ^&^& npm run build
    pause
    exit /b 1
)
start "" cmd /c "timeout /t 4 >nul & start msedge --app=http://127.0.0.1:8010"
py -3.12 -m uvicorn --app-dir . app.main:app --host 127.0.0.1 --port 8010
