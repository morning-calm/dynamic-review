"""Bug-report REST endpoints. A reviewer/admin files a report on a field; the admin (and I,
when investigating) reply; the reviewer can reply back — all in-app, any language.

Auth: creating/viewing/replying requires an authenticated user and is scoped in
``bug_reports`` (admin sees all; a reviewer sees only their own). Status changes are admin-only.
The audio-snapshot GET authenticates via the httpOnly cookie so a browser <audio> can play it.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import auth, bug_reports

router = APIRouter(prefix="/api")


class BugCreate(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class BugReply(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class BugStatus(BaseModel):
    status: str


@router.post("/sessions/{sid}/fields/{fid}/bug-report", dependencies=[Depends(auth.scope_sid)])
def create_bug(sid: str, fid: int, body: BugCreate, request: Request):
    return bug_reports.create(sid, fid, body.body, auth.require_user(request))


# NOTE: /bug-reports/count is declared BEFORE /bug-reports/{rid} so it isn't shadowed.
@router.get("/bug-reports/count")
def bug_count(request: Request):
    return bug_reports.counts(auth.require_user(request))


@router.get("/bug-reports")
def list_bugs(request: Request):
    return bug_reports.list_reports(auth.require_user(request))


@router.get("/bug-reports/{rid}")
def get_bug(rid: int, request: Request):
    return bug_reports.get(rid, auth.require_user(request))


@router.post("/bug-reports/{rid}/messages")
def reply_bug(rid: int, body: BugReply, request: Request):
    return bug_reports.add_message(rid, body.body, auth.require_user(request))


@router.post("/bug-reports/{rid}/status")
def status_bug(rid: int, body: BugStatus, request: Request):
    return bug_reports.set_status(rid, body.status, auth.require_admin(request))


@router.get("/bug-reports/{rid}/audio/{which}")
def bug_audio(rid: int, which: str, request: Request):
    user = auth.require_user(request)
    path = bug_reports.audio_path(rid, which, user)
    return FileResponse(str(path), media_type="audio/mpeg",
                        headers={"Accept-Ranges": "bytes", "Cache-Control": "no-store"})
