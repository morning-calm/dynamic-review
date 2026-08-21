"""TripGroup-description review endpoints (docs/tripgroup-description-review-proposal.md).

Auth: the admin list lazily seeds new items from the manifest; a reviewer's list shows
only their pending_tl items (language ACL). Per-item scoping lives in tripdesc.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from . import auth, tripdesc

router = APIRouter(prefix="/api/tripdesc")


class TripDescSave(BaseModel):
    en_text: str | None = None
    categories: list[str] | None = None
    tl_text: str | None = None


@router.get("/count")
def tripdesc_count(request: Request):
    return tripdesc.counts_for(auth.require_user(request))


@router.get("")
def list_tripdesc(request: Request):
    user = auth.require_user(request)
    if user.is_admin:
        # Lazy seed: every family in the current manifest gets a row. Cheap when
        # nothing is new (one SELECT per known TripGroup). No historical backfill.
        tripdesc.seed_from_manifest()
    return tripdesc.list_items(user)


@router.get("/categories")
def tripdesc_categories(request: Request, tg_id: str = ""):
    """Categories in use across staging TripGroups (admin). With `tg_id`, scoped
    to that group's country — Spanish categories never show on a Japanese trip."""
    auth.require_admin(request)
    return tripdesc.used_categories(tg_id or None)


@router.get("/{tg_id}/category-check")
def tripdesc_category_check(tg_id: str, category: str, request: Request):
    """Is `category` new to the vocabulary, and which sibling TripGroups in the
    same country/playlist look like they should carry it too? Read-only."""
    auth.require_admin(request)
    return tripdesc.category_check(tg_id, category)


@router.get("/{tg_id}")
def get_tripdesc(tg_id: str, request: Request):
    return tripdesc.get_item(tg_id, auth.require_user(request))


@router.put("/{tg_id}")
def save_tripdesc(tg_id: str, body: TripDescSave, request: Request):
    return tripdesc.save(tg_id, auth.require_user(request), en_text=body.en_text,
                         categories=body.categories, tl_text=body.tl_text)


@router.post("/{tg_id}/approve-en")
def approve_en(tg_id: str, request: Request):
    return tripdesc.approve_en(tg_id, auth.require_admin(request))


@router.post("/{tg_id}/approve-tl")
def approve_tl(tg_id: str, request: Request):
    return tripdesc.approve_tl(tg_id, auth.require_user(request))


@router.post("/{tg_id}/retry-translate")
def retry_translate(tg_id: str, request: Request):
    return tripdesc.retry_translate(tg_id, auth.require_admin(request))


@router.post("/{tg_id}/reopen")
def reopen(tg_id: str, request: Request):
    return tripdesc.reopen(tg_id, auth.require_admin(request))
