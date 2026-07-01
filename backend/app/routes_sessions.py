"""Session + field REST endpoints (everything under /api except health/trips list).

Route handlers are sync ``def`` so FastAPI runs them in its worker threadpool — the
blocking work (requests to ElevenLabs/Gemini, ffmpeg, Whisper) never blocks the event
loop, and Whisper is additionally serialised behind WHISPER_LOCK inside sessions.py."""

from __future__ import annotations

from fastapi import APIRouter, UploadFile, File, Form, Depends
from fastapi.concurrency import run_in_threadpool

from . import auth, sessions
from .auth import scope_sid, scope_sid_editable
from .models import (CreateSession, TextUpdate, SourceUpdate, Regenerate, Fallback,
                     PlayedRanges, FlagSet, CommentSet, NarrationSet,
                     ClipCreate, ClipRegen, ClipComment, TrimNoise, TrimCandidate,
                     InsertSilence, RemoveSilence, RequestChanges, CompleteTrip,
                     LocalizationUpdate, VersionSet)

router = APIRouter(prefix="/api")

# Scoping dependencies (see auth.py):
#   scope_sid           — resolve {sid}->trip language, 403 on mismatch (read/workflow)
#   scope_sid_editable  — the above PLUS lock-on-submit (editing routes only)
_SCOPE = [Depends(scope_sid)]
_EDIT = [Depends(scope_sid_editable)]


@router.get("/trips")
def get_trips(user=Depends(auth.require_user)):
    # Language-filtered server-side (admins see all) inside the list_trips wrapper.
    return sessions.list_trips(user)


@router.get("/voices")
def get_voices(user=Depends(auth.require_user)):
    # Registry only (no trip content) — open to any authenticated user.
    return sessions.get_voices()


@router.post("/sessions")
def post_session(body: CreateSession, user=Depends(auth.require_user)):
    # [P0-1] language gate is enforced at the TOP of create_or_resume (keyed on trip_id).
    return sessions.create_or_resume(body.trip_id, user)


@router.get("/sessions/{sid}", dependencies=_SCOPE)
def get_session(sid: str):
    return sessions.get_session(sid)


@router.post("/sessions/{sid}/narration", dependencies=_EDIT)
def post_narration(sid: str, body: NarrationSet):
    return sessions.set_narration(sid, body.voice, body.speed, body.model,
                                  body.clear_speed, body.clear_model,
                                  body.reset_regenerated)


@router.put("/sessions/{sid}/fields/{fid}", dependencies=_EDIT)
def put_field(sid: str, fid: int, body: TextUpdate):
    return sessions.update_text(sid, fid, body.current_text)


@router.put("/sessions/{sid}/fields/{fid}/source", dependencies=_EDIT)
def put_source(sid: str, fid: int, body: SourceUpdate):
    return sessions.update_source(sid, fid, body.text)


# --- Mandarin (_ZH) 4-script editing + A/B version pick ---
@router.put("/sessions/{sid}/fields/{fid}/localization", dependencies=_EDIT)
def put_localization(sid: str, fid: int, body: LocalizationUpdate):
    return sessions.update_localization(sid, fid, body.script, body.text)


@router.post("/sessions/{sid}/version", dependencies=_EDIT)
def post_version(sid: str, body: VersionSet):
    return sessions.set_version(sid, body.version)


@router.post("/sessions/{sid}/fields/{fid}/regenerate", dependencies=_EDIT)
def post_regenerate(sid: str, fid: int, body: Regenerate):
    rng = body.range.model_dump() if body.range else None
    return sessions.regenerate(sid, fid, body.mode, rng, body.alt_text)


@router.post("/sessions/{sid}/fields/{fid}/combine", dependencies=_EDIT)
def post_combine(sid: str, fid: int):
    return sessions.combine(sid, fid)


@router.post("/sessions/{sid}/fields/{fid}/trim-candidate", dependencies=_EDIT)
async def post_trim_candidate(sid: str, fid: int, body: TrimCandidate):
    # ffmpeg decode/encode is blocking — keep it off the event loop.
    return await run_in_threadpool(sessions.trim_candidate, sid, fid, body.delta_ms)


@router.post("/sessions/{sid}/fields/{fid}/trim", dependencies=_EDIT)
async def post_trim(sid: str, fid: int, body: TrimNoise):
    # Whisper alignment + ffmpeg are blocking — keep them off the event loop.
    return await run_in_threadpool(sessions.trim_noise, sid, fid, body.start, body.end)


@router.post("/sessions/{sid}/fields/{fid}/trim-silence", dependencies=_EDIT)
async def post_trim_silence(sid: str, fid: int):
    # ffmpeg decode/encode is blocking — keep it off the event loop.
    return await run_in_threadpool(sessions.trim_silence, sid, fid)


@router.post("/sessions/{sid}/fields/{fid}/insert-silence", dependencies=_EDIT)
async def post_insert_silence(sid: str, fid: int, body: InsertSilence):
    # Whisper alignment + ffmpeg are blocking — keep them off the event loop.
    return await run_in_threadpool(sessions.insert_silence, sid, fid, body.pos, body.seconds)


@router.post("/sessions/{sid}/fields/{fid}/remove-silence", dependencies=_EDIT)
async def post_remove_silence(sid: str, fid: int, body: RemoveSilence):
    # Whisper/aligner + ffmpeg are blocking — keep them off the event loop.
    return await run_in_threadpool(sessions.remove_silence, sid, fid, body.pos, body.seconds)


@router.post("/sessions/{sid}/fields/{fid}/fallback", dependencies=_EDIT)
def post_fallback(sid: str, fid: int, body: Fallback):
    return sessions.fallback(sid, fid, body.extent, body.text, body.description)


@router.post("/sessions/{sid}/fields/{fid}/import-mp3", dependencies=_EDIT)
async def post_import(sid: str, fid: int, file: UploadFile = File(...)):
    data = await file.read()
    # S7: re-encode (ffmpeg) is blocking — keep it off the event loop.
    return await run_in_threadpool(sessions.import_mp3, sid, fid, data)


# --- "Create new" attachment workspace (manual edit) ---
@router.post("/sessions/{sid}/fields/{fid}/clips", dependencies=_EDIT)
def post_clip(sid: str, fid: int, body: ClipCreate):
    return sessions.create_clip(sid, fid, body.text, body.comment)


@router.post("/sessions/{sid}/fields/{fid}/clips/upload", dependencies=_EDIT)
async def post_clip_upload(sid: str, fid: int, file: UploadFile = File(...),
                           comment: str = Form("")):
    data = await file.read()
    return await run_in_threadpool(sessions.import_clip, sid, fid, data, comment)


@router.post("/sessions/{sid}/fields/{fid}/clips/{cid}/regenerate", dependencies=_EDIT)
def post_clip_regen(sid: str, fid: int, cid: int, body: ClipRegen):
    return sessions.regenerate_clip(sid, fid, cid, body.text)


@router.post("/sessions/{sid}/fields/{fid}/clips/{cid}/comment", dependencies=_EDIT)
def post_clip_comment(sid: str, fid: int, cid: int, body: ClipComment):
    return sessions.set_clip_comment(sid, fid, cid, body.comment)


@router.delete("/sessions/{sid}/fields/{fid}/clips/{cid}", dependencies=_EDIT)
def delete_clip(sid: str, fid: int, cid: int):
    return sessions.delete_clip(sid, fid, cid)


@router.post("/sessions/{sid}/fields/{fid}/played", dependencies=_SCOPE)
def post_played(sid: str, fid: int, body: PlayedRanges):
    # Playback coverage — allowed in any state (an admin must be able to listen while
    # reviewing a submitted trip); it never changes text/audio/flags.
    return sessions.played(sid, fid, body.ranges, body.track)


@router.post("/sessions/{sid}/fields/{fid}/flag", dependencies=_EDIT)
def post_flag(sid: str, fid: int, body: FlagSet):
    return sessions.set_flag(sid, fid, body.flag)


@router.post("/sessions/{sid}/fields/{fid}/comment", dependencies=_EDIT)
def post_comment(sid: str, fid: int, body: CommentSet):
    return sessions.set_comment(sid, fid, body.text)


@router.post("/sessions/{sid}/fields/{fid}/revert", dependencies=_EDIT)
def post_revert(sid: str, fid: int):
    return sessions.revert(sid, fid)


@router.post("/sessions/{sid}/fields/{fid}/undo", dependencies=_EDIT)
async def post_undo(sid: str, fid: int):
    return await run_in_threadpool(sessions.undo_audio, sid, fid)


@router.post("/sessions/{sid}/fields/{fid}/redo", dependencies=_EDIT)
async def post_redo(sid: str, fid: int):
    return await run_in_threadpool(sessions.redo_audio, sid, fid)


# --- Submit -> approve workflow ---
@router.post("/sessions/{sid}/submit", dependencies=_SCOPE)
def post_submit(sid: str, user=Depends(auth.require_user)):
    # Reviewer/admin (own language, enforced by scope_sid): validate only, no writes.
    return sessions.submit(sid, user)


@router.post("/sessions/{sid}/approve", dependencies=_SCOPE)
def post_approve(sid: str, admin=Depends(auth.require_admin)):
    return sessions.approve(sid, admin)


@router.post("/sessions/{sid}/request-changes", dependencies=_SCOPE)
def post_request_changes(sid: str, body: RequestChanges,
                         admin=Depends(auth.require_admin)):
    return sessions.request_changes(sid, admin, body.note)


@router.get("/review-queue")
def get_review_queue(admin=Depends(auth.require_admin)):
    return sessions.review_queue()


# --- Completed queue ---
@router.get("/completed")
def get_completed(user=Depends(auth.require_user)):
    # Both roles; reviewers filtered to their languages (admins see all), newest first.
    return sessions.completed(user)


@router.post("/trips/{trip_id}/complete")
def post_complete(trip_id: str, admin=Depends(auth.require_admin),
                  body: CompleteTrip | None = None):
    # Admin manual (bypass) complete — writes NOTHING to staging/masters. trip_id may
    # contain spaces/periods; FastAPI URL-decodes the path param. Body is optional.
    return sessions.complete_trip(admin, trip_id, body.note if body else "")


@router.delete("/trips/{trip_id}/complete")
def delete_complete(trip_id: str, admin=Depends(auth.require_admin)):
    return sessions.uncomplete_trip(admin, trip_id)


# --- In-app prioritisation (pin to top) ---
@router.post("/trips/{trip_id}/pin")
def post_pin_trip(trip_id: str, admin=Depends(auth.require_admin)):
    return sessions.pin_trip(admin, trip_id)


@router.delete("/trips/{trip_id}/pin")
def delete_pin_trip(trip_id: str, admin=Depends(auth.require_admin)):
    return sessions.unpin_trip(admin, trip_id)
