# Start the review-app backend in single-origin + HTTPS-cookie mode for the Cloudflare
# tunnel. Run in a terminal and keep the window open. (Dev/local uses the Vite server on
# :5173 instead and does NOT set these vars.)
$env:REVIEW_APP_SERVE_FRONTEND = "1"   # serve the built frontend/dist from FastAPI
$env:REVIEW_APP_COOKIE_SECURE  = "1"   # required over HTTPS (Secure cookie for media GETs)
Set-Location "$PSScriptRoot\..\backend"

# Call Python 3.12 by full path — the bare `py` launcher can pop a Windows
# "Select an app to open 'py'" dialog in some shells on this machine.
$py = "C:\Users\david\AppData\Local\Programs\Python\Python312\python.exe"
if (-not (Test-Path $py)) { $py = "python" }   # fallback if Python moves
& $py -m uvicorn --app-dir . app.main:app --host 127.0.0.1 --port 8000
