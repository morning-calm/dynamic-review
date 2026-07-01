"""Pydantic request bodies. Responses are built as plain dicts that match
API_CONTRACT.md exactly (see app/sessions.py serialisers)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class Login(BaseModel):
    username: str
    password: str


class RequestChanges(BaseModel):
    note: str = ""


class CompleteTrip(BaseModel):
    note: str = ""   # optional admin note for a manual (old-system) completion


class CreateSession(BaseModel):
    trip_id: str


class TextUpdate(BaseModel):
    current_text: str


class SourceUpdate(BaseModel):
    text: str   # the editable English translation (*En) of a non-EN field


class RegenRange(BaseModel):
    start: int
    end: int


class Regenerate(BaseModel):
    mode: Literal["segment", "whole", "highlight", "alt"]
    range: Optional[RegenRange] = None
    alt_text: Optional[str] = None   # mode="alt": free/phonetic text to voice in the span


class TrimNoise(BaseModel):
    """Manual sliver backstop: char range (in current_text) of the space the reviewer
    highlighted as containing leftover/unwanted audio noise."""
    start: int
    end: int


class InsertSilence(BaseModel):
    """Insert a pause into the working take at the TEXT caret (char offset into
    current_text — normally just after a full stop). The caret is mapped to an audio
    time via the clip's word timing."""
    pos: int                 # caret char offset into current_text
    seconds: float = 1.0     # length of silence to insert


class Fallback(BaseModel):
    extent: Literal["sentence", "scene", "custom"]
    text: Optional[str] = None
    description: str = ""


class PlayedRanges(BaseModel):
    ranges: list[list[float]]
    track: Literal["working", "original"] = "working"


class FlagSet(BaseModel):
    flag: Literal["none", "done", "edit_required"]


class CommentSet(BaseModel):
    text: str


class ClipCreate(BaseModel):
    text: str
    comment: str = ""   # required instruction to the admin about this 'Create new' take


class ClipRegen(BaseModel):
    text: Optional[str] = None   # None → re-voice the clip's existing text


class ClipComment(BaseModel):
    comment: str = ""   # admin note; non-empty commits the draft + flags edit-required


class TrimCandidate(BaseModel):
    """Nudge the trailing trim on the current candidate before combining. >0 trims more
    off the end (drop a breath/bleed), <0 restores."""
    delta_ms: float


class NarrationSet(BaseModel):
    """Correct a session's narration settings. Omit a field to leave it unchanged;
    set clear_speed / clear_model true to drop an override back to the auto value.
    reset_regenerated: when true, takes regenerated under the OLD settings are discarded
    back to the master (text kept); when false (default) they are left as-is."""
    voice: Optional[str] = None
    speed: Optional[float] = None
    model: Optional[str] = None
    clear_speed: bool = False
    clear_model: bool = False
    reset_regenerated: bool = False
