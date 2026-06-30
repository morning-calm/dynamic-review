"""Session + field REST endpoints (everything under /api except health/trips list).

Route handlers are sync ``def`` so FastAPI runs them in its worker threadpool — the
blocking work (requests to ElevenLabs/Gemini, ffmpeg, Whisper) never blocks the event
loop, and Whisper is additionally serialised behind WHISPER_LOCK inside sessions.py."""

from __future__ import annotations

from fastapi import APIRouter, UploadFile, File
from fastapi.concurrency import run_in_threadpool

from . import sessions
from .models import (CreateSession, TextUpdate, SourceUpdate, Regenerate, Fallback,
                     PlayedRanges, FlagSet, CommentSet, NarrationSet,
                     ClipCreate, ClipRegen, TrimNoise)

router = APIRouter(prefix="/api")


@router.get("/trips")
def get_trips():
    return sessions.list_trips()


@router.get("/voices")
def get_voices():
    return sessions.get_voices()


@router.post("/sessions")
def post_session(body: CreateSession):
    return sessions.create_or_resume(body.trip_id)


@router.get("/sessions/{sid}")
def get_session(sid: str):
    return sessions.get_session(sid)


@router.post("/sessions/{sid}/narration")
def post_narration(sid: str, body: NarrationSet):
    return sessions.set_narration(sid, body.voice, body.speed, body.model,
                                  body.clear_speed, body.clear_model,
                                  body.reset_regenerated)


@router.put("/sessions/{sid}/fields/{fid}")
def put_field(sid: str, fid: int, body: TextUpdate):
    return sessions.update_text(sid, fid, body.current_text)


@router.put("/sessions/{sid}/fields/{fid}/source")
def put_source(sid: str, fid: int, body: SourceUpdate):
    return sessions.update_source(sid, fid, body.text)


@router.post("/sessions/{sid}/fields/{fid}/regenerate")
def post_regenerate(sid: str, fid: int, body: Regenerate):
    rng = body.range.model_dump() if body.range else None
    return sessions.regenerate(sid, fid, body.mode, rng, body.alt_text)


@router.post("/sessions/{sid}/fields/{fid}/combine")
def post_combine(sid: str, fid: int):
    return sessions.combine(sid, fid)


@router.post("/sessions/{sid}/fields/{fid}/trim")
async def post_trim(sid: str, fid: int, body: TrimNoise):
    # Whisper alignment + ffmpeg are blocking — keep them off the event loop.
    return await run_in_threadpool(sessions.trim_noise, sid, fid, body.start, body.end)


@router.post("/sessions/{sid}/fields/{fid}/fallback")
def post_fallback(sid: str, fid: int, body: Fallback):
    return sessions.fallback(sid, fid, body.extent, body.text, body.description)


@router.post("/sessions/{sid}/fields/{fid}/import-mp3")
async def post_import(sid: str, fid: int, file: UploadFile = File(...)):
    data = await file.read()
    # S7: re-encode (ffmpeg) is blocking — keep it off the event loop.
    return await run_in_threadpool(sessions.import_mp3, sid, fid, data)


# --- Manual-edit clip workspace ---
@router.post("/sessions/{sid}/fields/{fid}/clips")
def post_clip(sid: str, fid: int, body: ClipCreate):
    return sessions.create_clip(sid, fid, body.text)


@router.post("/sessions/{sid}/fields/{fid}/clips/upload")
async def post_clip_upload(sid: str, fid: int, file: UploadFile = File(...)):
    data = await file.read()
    return await run_in_threadpool(sessions.import_clip, sid, fid, data)


@router.post("/sessions/{sid}/fields/{fid}/clips/{cid}/regenerate")
def post_clip_regen(sid: str, fid: int, cid: int, body: ClipRegen):
    return sessions.regenerate_clip(sid, fid, cid, body.text)


@router.post("/sessions/{sid}/fields/{fid}/clips/{cid}/use")
def post_clip_use(sid: str, fid: int, cid: int):
    return sessions.use_clip_as_working(sid, fid, cid)


@router.delete("/sessions/{sid}/fields/{fid}/clips/{cid}")
def delete_clip(sid: str, fid: int, cid: int):
    return sessions.delete_clip(sid, fid, cid)


@router.post("/sessions/{sid}/fields/{fid}/played")
def post_played(sid: str, fid: int, body: PlayedRanges):
    return sessions.played(sid, fid, body.ranges, body.track)


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
