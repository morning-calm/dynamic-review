"""Pydantic request bodies. Responses are built as plain dicts that match
API_CONTRACT.md exactly (see app/sessions.py serialisers)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class CreateSession(BaseModel):
    trip_id: str


class TextUpdate(BaseModel):
    current_text: str


class RegenRange(BaseModel):
    start: int
    end: int


class Regenerate(BaseModel):
    mode: Literal["segment", "whole", "highlight"]
    range: Optional[RegenRange] = None


class Fallback(BaseModel):
    extent: Literal["sentence", "scene", "custom"]
    text: Optional[str] = None
    description: str = ""


class PlayedRanges(BaseModel):
    ranges: list[list[float]]


class FlagSet(BaseModel):
    flag: Literal["none", "done", "edit_required"]


class CommentSet(BaseModel):
    text: str
