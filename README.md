# review-app

Internal tool for reviewing & correcting staged VR-trip content (text + audio) in
one GUI, replacing the manual GDoc-→-audio-editor relay. **Stage 1:** admins use it
themselves on English `_EN` trips. Everything works in **MP3**; the existing
**Stage 9** finalise converts to ogg, regenerates subtitles/timings, and uploads to
S3. See `API_CONTRACT.md`, `REQUIREMENTS.md`, and the design plan at
`C:\Users\david\.claude\plans\i-need-some-software-generic-gizmo.md`.

```
review-app/
  backend/    FastAPI (Python 3.12) — reuses D:\Dynamic Languages\Scripts modules
  frontend/   React 19 + Vite + Tailwind (mirrors ../library-app)
  docs/       supplementary notes
```

## Run (dev)
```bash
# backend  (uses global py -3.12, which already has faster-whisper + firebase-admin)
cd backend && py -3.12 -m pip install -r requirements.txt
py -3.12 -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# frontend
cd frontend && npm install && npm run dev    # http://127.0.0.1:5173
```

Backend reads `D:\Dynamic Languages\Scripts\.env` for `ELEVENLABS_API_KEY` /
`GEMINI_API_KEY` and uses `firebase_staging_key.json` there. Set `REVIEW_APP_TOKEN`
(dev default `dev-token`).

> Build status, what's verified vs needs real-data/GPU testing, and the
> requirement-by-requirement map live in `REQUIREMENTS.md`.
