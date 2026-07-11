"""Audio streaming (HTTP Range), archived versions, overlay images, and the admin
download-all zip. Audio paths are resolved server-side from the DB — the frontend
only ever uses the URLs handed back in Field.audio / Field.versions."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse

from . import db, sessions
from .auth import require_admin, scope_sid

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


@router.get("/api/sessions/{sid}/scenes/{index}/download",
            dependencies=[Depends(require_admin)] + _SCOPE)
def download_scene(sid: str, index: int):
    """ADMIN ONLY: one scene's audio, for fixing a reviewer's `edit_required` flag in a
    desktop editor. Each take is named for the FIELD it belongs to (see
    `sessions.field_download_name`) so the admin can't lose track of which file goes back
    into which slot — SceneDesc vs questionKey vs option 2. Pristine v0s ride along under
    `orig/` for reference."""
    srow = sessions._session_row(sid)
    rows = db.query(
        "SELECT * FROM field_edits WHERE session_id=? AND scene_index=? AND has_audio=1 "
        "ORDER BY id", (sid, index))
    dirs = sessions.work_dirs(sid)
    buf = io.BytesIO()
    wrote = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for frow in rows:
            if not frow["mp3_name"]:
                continue
            name = sessions.field_download_name(srow["trip_id"], frow)
            working = dirs["working"] / frow["mp3_name"]
            if working.exists():
                zf.write(working, arcname=name)
                wrote += 1
            orig = dirs["orig"] / frow["mp3_name"]
            if orig.exists():
                zf.write(orig, arcname=f"orig/{name}")
    if not wrote:
        raise HTTPException(404, detail={
            "error": "no_audio", "detail": f"scene {index} has no audio takes"})
    buf.seek(0)
    trip = sessions._UNSAFE_FN.sub("_", srow["trip_id"])
    fname = f"{trip}_scene{index}_audio.zip"
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/api/sessions/{sid}/download",
            dependencies=[Depends(require_admin)] + _SCOPE)
def download_all(sid: str):
    """ADMIN ONLY (403 for reviewers): the mp3 bundle exists so an admin can take the
    takes off to a desktop audio editor and re-import them. Reviewers do their listening
    in-app, where the coverage/Done gate can see it."""
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
