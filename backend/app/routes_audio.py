"""Audio streaming (HTTP Range), archived versions, overlay images, and the admin
download-all zip. Audio paths are resolved server-side from the DB — the frontend
only ever uses the URLs handed back in Field.audio / Field.versions."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse

from . import db, sessions
from .auth import scope_sid

router = APIRouter()

# Every {sid} media/zip route is language-scoped (403 on mismatch). These GETs
# authenticate via the httpOnly review_session cookie (browser <audio>/<img> can't
# send a header); the middleware accepts the cookie for GET/HEAD only.
_SCOPE = [Depends(scope_sid)]

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
_CHUNK = 256 * 1024


def _serve_range(path: Path, request: Request, media_type: str) -> Response:
    if not path.exists():
        return Response(status_code=404)
    size = path.stat().st_size
    rng = request.headers.get("range") or request.headers.get("Range")
    if not rng:
        return FileResponse(path, media_type=media_type,
                            headers={"Accept-Ranges": "bytes"})
    m = _RANGE_RE.search(rng)
    if not m:
        return FileResponse(path, media_type=media_type,
                            headers={"Accept-Ranges": "bytes"})
    start = int(m.group(1)) if m.group(1) else 0
    end = int(m.group(2)) if m.group(2) else size - 1
    start = max(0, start)
    end = min(end, size - 1)
    if start > end:
        return Response(status_code=416,
                        headers={"Content-Range": f"bytes */{size}"})
    length = end - start + 1

    def body():
        with open(path, "rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(_CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Content-Range": f"bytes {start}-{end}/{size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Cache-Control": "no-store",
    }
    return StreamingResponse(body(), status_code=206, media_type=media_type,
                             headers=headers)


@router.get("/audio/{sid}/{fid}/v/{n}", dependencies=_SCOPE)
def get_version(sid: str, fid: int, n: int, request: Request):
    return _serve_range(sessions.version_path(sid, fid, n), request, "audio/mpeg")


@router.get("/audio/{sid}/{fid}/clip/{cid}", dependencies=_SCOPE)
def get_clip(sid: str, fid: int, cid: int, request: Request):
    return _serve_range(sessions.clip_path(sid, fid, cid), request, "audio/mpeg")


@router.get("/audio/{sid}/{fid}/{which}", dependencies=_SCOPE)
def get_audio(sid: str, fid: int, which: str, request: Request):
    if which not in ("original", "working", "candidate", "fallback"):
        return Response(status_code=404)
    return _serve_range(sessions.audio_path(sid, fid, which), request, "audio/mpeg")


@router.get("/overlays/{sid}/{filename}", dependencies=_SCOPE)
def get_overlay(sid: str, filename: str, request: Request):
    path = sessions.overlay_path(sid, filename)
    if not path:
        # No local file (hosted/laptop has no source trees) — fall back to the R2 mirror.
        url = sessions.overlay_r2_url(sid, filename)
        if url:
            return RedirectResponse(url, status_code=302)
        return Response(status_code=404)
    ext = path.suffix.lower()
    media = "image/png" if ext == ".png" else "image/jpeg"
    return _serve_range(path, request, media)


@router.get("/api/sessions/{sid}/download", dependencies=_SCOPE)
def download_all(sid: str):
    sessions._session_row(sid)   # 404 if missing
    dirs = sessions.work_dirs(sid)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for sub in ("orig", "working", "versions"):
            d = dirs[sub]
            for f in sorted(Path(d).glob("*.mp3")):
                zf.write(f, arcname=f"{sub}/{f.name}")
    buf.seek(0)
    srow = db.query_one("SELECT trip_id FROM sessions WHERE id=?", (sid,))
    fname = f"{srow['trip_id'] if srow else sid}_audio.zip"
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})
