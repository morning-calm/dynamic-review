"""Session + field REST endpoints (everything under /api except health/trips list).

Route handlers are sync ``def`` so FastAPI runs them in its worker threadpool — the
blocking work (requests to ElevenLabs/Gemini, ffmpeg, Whisper) never blocks the event
loop, and Whisper is additionally serialised behind WHISPER_LOCK inside sessions.py."""

from __future__ import annotations

from fastapi import APIRouter, UploadFile, File
from fastapi.concurrency import run_in_threadpool

from . import sessions
from .models import (CreateSession, TextUpdate, Regenerate, Fallback,
                     PlayedRanges, FlagSet, CommentSet)

router = APIRouter(prefix="/api")


@router.get("/trips")
def get_trips():
    return sessions.list_trips()


@router.post("/sessions")
def post_session(body: CreateSession):
    return sessions.create_or_resume(body.trip_id)


@router.get("/sessions/{sid}")
def get_session(sid: str):
    return sessions.get_session(sid)


@router.put("/sessions/{sid}/fields/{fid}")
def put_field(sid: str, fid: int, body: TextUpdate):
    return sessions.update_text(sid, fid, body.current_text)


@router.post("/sessions/{sid}/fields/{fid}/regenerate")
def post_regenerate(sid: str, fid: int, body: Regenerate):
    rng = body.range.model_dump() if body.range else None
    return sessions.regenerate(sid, fid, body.mode, rng)


@router.post("/sessions/{sid}/fields/{fid}/combine")
def post_combine(sid: str, fid: int):
    return sessions.combine(sid, fid)


@router.post("/sessions/{sid}/fields/{fid}/fallback")
def post_fallback(sid: str, fid: int, body: Fallback):
    return sessions.fallback(sid, fid, body.extent, body.text, body.description)


@router.post("/sessions/{sid}/fields/{fid}/import-mp3")
async def post_import(sid: str, fid: int, file: UploadFile = File(...)):
    data = await file.read()
    # S7: re-encode (ffmpeg) is blocking — keep it off the event loop.
    return await run_in_threadpool(sessions.import_mp3, sid, fid, data)


@router.post("/sessions/{sid}/fields/{fid}/played")
def post_played(sid: str, fid: int, body: PlayedRanges):
    return sessions.played(sid, fid, body.ranges)


@router.post("/sessions/{sid}/fields/{fid}/flag")
def post_flag(sid: str, fid: int, body: FlagSet):
    return sessions.set_flag(sid, fid, body.flag)


@router.post("/sessions/{sid}/fields/{fid}/comment")
def post_comment(sid: str, fid: int, body: CommentSet):
    return sessions.set_comment(sid, fid, body.text)


@router.post("/sessions/{sid}/fields/{fid}/revert")
def post_revert(sid: str, fid: int):
    return sessions.revert(sid, fid)


@router.post("/sessions/{sid}/submit")
def post_submit(sid: str):
    return sessions.submit(sid)
