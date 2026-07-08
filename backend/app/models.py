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


class Heartbeat(BaseModel):
    """Presence ping: what the user is looking at right now (free text shown to admins,
    e.g. "Scene 4 · SceneDesc — editing"). Sent every ~30s while a session page is open."""
    context: str = ""


class Recall(BaseModel):
    """Reviewer asks for their submitted trip back. `reason` is required when the recall
    can't be auto-granted (admin mid-review / already approved) and becomes the pinned
    request's message to the admin."""
    reason: str = ""


class RecallResolve(BaseModel):
    """Admin resolves a pinned recall request: grant = send the trip back to the reviewer
    (changes_requested; un-completes first if it was approved), decline = keep it."""
    action: Literal["grant", "decline"]
    note: str = ""


class CreateSession(BaseModel):
    trip_id: str


class TextUpdate(BaseModel):
    current_text: str


class SourceUpdate(BaseModel):
    text: str   # the editable English translation (*En) of a non-EN field


class LocalizationUpdate(BaseModel):
    """Autosave one script of a Mandarin (_ZH) field's 4-script block. `script` is the
    variant being edited; `text` its new value. zhuyin is not accepted on the trip
    description (it carries no phonetics)."""
    script: Literal["Hans", "Hant", "zhuyin", "en"]
    text: str


class ApplySuggestedFix(BaseModel):
    """Apply the latest Gate-2 report's machine-verified suggested fix to one _ZH field,
    identified by its report location (scene/field/option — the same keys the report uses)."""
    scene: int
    field: str
    option: Optional[int] = None


class VersionSet(BaseModel):
    """Set the trip's preferred ElevenLabs A/B version (per-session). ``null`` clears
    the pick and returns the session to the V2/V3 side-by-side audition (audio edit
    state on the picked take is dropped; text/script edits are kept)."""
    version: Optional[Literal["v2", "v3"]] = None


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


class RemoveSilence(BaseModel):
    """Shorten the pause at the TEXT caret by up to ``seconds`` (the inverse of
    InsertSilence; a minimum natural pause is always kept). Same caret semantics:
    char offset into current_text (JP: must sit in the kana line) / the Hans field (ZH)."""
    pos: int                 # caret char offset (per-language reference text above)
    seconds: float = 1.0     # pause to remove (capped by what the pause can spare)


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
