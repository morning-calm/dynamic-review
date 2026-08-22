"""Final-check endpoints (docs/post-approval-admin-spec.md §2) — ALL admin-only.

The work list mirrors Trello lanes 10/10b/11 via the manifest's `final_check`
entries; per-check state + the staging writes live in final_checks.py. The audio /
overlay media routes are admin-only too but reached by browser <audio>/<img>
elements, which authenticate via the httpOnly review_session cookie.
"""
from __future__ import annotations

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from . import auth, final_checks, release_batches
from .routes_audio import _serve_range

router = APIRouter(prefix="/api/final")


class SetCheck(BaseModel):
    state: str                      # open | done
    note: str = ""


class SaveTitleKey(BaseModel):
    value: str


class SaveCategories(BaseModel):
    categories: list[str]


class StartFinal(BaseModel):
    trip_id: str


class SaveLocation(BaseModel):
    loc_id: str
    locationTitleKey: str | None = None
    skyboxTextureId: str | None = None
    trips: list[str] | None = None      # reorder only (permutation-checked)


class SavePin(BaseModel):
    loc_id: str
    menu_id: str
    x: float
    y: float


class SetTiming(BaseModel):
    scene_index: int
    filename: str
    appear: float          # 0.1s granularity — the headset field is a Unity float
    disappear: float


class AddCredit(BaseModel):
    header: str
    entry: str


class AddAnswerKey(BaseModel):
    scene_index: int
    key: str


@router.get("/count")
def final_count(request: Request):
    user = auth.require_user(request)
    if not user.is_admin:
        return {"open": 0}
    return {"open": final_checks.count_open()}


@router.get("")
def list_final(request: Request):
    auth.require_admin(request)
    return final_checks.list_items()


@router.get("/releases")
def list_releases(request: Request):
    """Group-centric release board (Publisher page): per family, every staging
    rung's position in the pipeline + live prod membership."""
    auth.require_admin(request)
    return final_checks.releases()


@router.get("/releases/{tg_id}/diff")
def release_group_diff(tg_id: str, request: Request):
    """Staging→prod field diff for one group card (lazy — fetched on expand)."""
    auth.require_admin(request)
    return final_checks.group_diff(tg_id)


class SaveBatch(BaseModel):
    name: str
    members: list[dict]                 # [{"kind":"trip|group|location","id":…}]
    id: int | None = None
    status: str | None = None           # planned | published | archived


@router.get("/batches")
def get_batches(request: Request):
    """Release batches (the Publishing Queue's group-by-batch unit), each with its
    resolved membership + social-post readiness."""
    auth.require_admin(request)
    return release_batches.list_batches()


@router.post("/batches")
def post_batch(body: SaveBatch, request: Request):
    return release_batches.save_batch(body.name, body.members,
                                      auth.require_admin(request),
                                      batch_id=body.id, status=body.status)


@router.post("/batches/import-trello")
def post_batches_import(request: Request):
    """Seed/update batches from the Trello 'TG Release Schedule' lane's cards."""
    return release_batches.import_trello(auth.require_admin(request))


@router.delete("/batches/{batch_id}")
def delete_batch(batch_id: int, request: Request):
    auth.require_admin(request)
    return release_batches.delete_batch(batch_id)


@router.get("/published")
def get_published(request: Request, months: int = 12):
    """The 'Recently published' list, from the durable published_trips ledger."""
    auth.require_admin(request)
    return release_batches.recently_published(months)


@router.post("/start")
def start_final(body: StartFinal, request: Request):
    return final_checks.start(body.trip_id, auth.require_admin(request))


# Fixed paths BEFORE the /{trip_id} catch-all — FastAPI matches in registration
# order, so these would otherwise resolve as trip_id="credits"/"speech-token".
@router.get("/credits")
def get_credits(request: Request):
    auth.require_admin(request)
    return final_checks.get_credits()


@router.post("/credits")
def post_credit(body: AddCredit, request: Request):
    return final_checks.add_credit(body.header, body.entry,
                                   auth.require_admin(request))


@router.get("/speech-token")
def get_speech_token(request: Request):
    auth.require_admin(request)
    return final_checks.speech_token()


@router.get("/{trip_id}")
def get_final(trip_id: str, request: Request):
    auth.require_admin(request)
    return final_checks.get_item(trip_id)


@router.get("/{trip_id}/location")
def get_final_location(trip_id: str, request: Request):
    auth.require_admin(request)
    return final_checks.get_location(trip_id)


@router.put("/{trip_id}/location")
def put_final_location(trip_id: str, body: SaveLocation, request: Request):
    return final_checks.save_location(
        trip_id, body.loc_id,
        {"locationTitleKey": body.locationTitleKey,
         "skyboxTextureId": body.skyboxTextureId, "trips": body.trips},
        auth.require_admin(request))


@router.put("/{trip_id}/pin")
def put_final_pin(trip_id: str, body: SavePin, request: Request):
    return final_checks.save_pin(trip_id, body.loc_id, body.menu_id, body.x, body.y,
                                 auth.require_admin(request))


@router.get("/{trip_id}/credit-proposals")
def get_credit_proposals(trip_id: str, request: Request):
    """Auto-fill candidates for check 5's credits, from the drafting webfetch
    attribution sidecars. Read-only — adding stays a human click."""
    auth.require_admin(request)
    return final_checks.credit_proposals(trip_id)


@router.get("/{trip_id}/static-images")
def get_static_images(trip_id: str, request: Request):
    auth.require_admin(request)
    return final_checks.get_static_images(trip_id)


@router.put("/{trip_id}/static-images/timing")
def put_image_timing(trip_id: str, body: SetTiming, request: Request):
    return final_checks.set_image_timing(trip_id, body.scene_index, body.filename,
                                         body.appear, body.disappear,
                                         auth.require_admin(request))


@router.post("/{trip_id}/static-images/replace")
async def post_overlay_replace(trip_id: str, filename: str, request: Request,
                               file: UploadFile = File(...)):
    admin = auth.require_admin(request)
    data = await file.read()
    return final_checks.replace_overlay(trip_id, filename, data, admin)


@router.get("/{trip_id}/scenes/{scene_index}/audio")
def get_scene_audio(trip_id: str, scene_index: int, request: Request,
                    kind: str = "narration", opt: int = 0):
    auth.require_admin(request)
    p = final_checks.scene_audio_file(trip_id, scene_index, kind, opt)
    return _serve_range(p, request, "audio/mpeg")


@router.post("/{trip_id}/static-images/revert")
def post_overlay_revert(trip_id: str, filename: str, request: Request):
    # Sync on purpose: revert_overlay does several blocking R2 calls — as an
    # `async def` (no await) those would run ON the event loop and stall every
    # concurrent request; a plain def lands in the threadpool.
    """Undo the last Replace-image of one overlay (one-level; see revert_overlay)."""
    return final_checks.revert_overlay(trip_id, filename,
                                       auth.require_admin(request))


@router.get("/{trip_id}/overlays/{filename}")
def get_final_overlay(trip_id: str, filename: str, request: Request):
    auth.require_admin(request)
    local, redirect = final_checks.overlay_image(trip_id, filename)
    if redirect:
        return RedirectResponse(redirect)
    from fastapi.responses import FileResponse
    media = "image/png" if str(local).lower().endswith(".png") else "image/jpeg"
    return FileResponse(str(local), media_type=media)


@router.get("/{trip_id}/keywords")
def get_final_keywords(trip_id: str, request: Request):
    auth.require_admin(request)
    return final_checks.get_keywords(trip_id)


@router.post("/{trip_id}/answer-keys")
def post_answer_key(trip_id: str, body: AddAnswerKey, request: Request):
    return final_checks.add_answer_key(trip_id, body.scene_index, body.key,
                                       auth.require_admin(request))


@router.post("/{trip_id}/answer-keys/delete")
def post_answer_key_delete(trip_id: str, body: AddAnswerKey, request: Request):
    """Remove one additionalAnswerKeys entry (mis-added / test variant)."""
    return final_checks.delete_answer_key(trip_id, body.scene_index, body.key,
                                          auth.require_admin(request))


@router.get("/{trip_id}/thumbnail")
def get_final_thumbnail(trip_id: str, request: Request):
    auth.require_admin(request)
    return final_checks.get_thumbnail(trip_id)


@router.post("/{trip_id}/thumbnail")
async def post_final_thumbnail(trip_id: str, request: Request,
                               file: UploadFile = File(...)):
    admin = auth.require_admin(request)
    data = await file.read()
    return final_checks.upload_thumbnail(trip_id, data, admin)


@router.get("/{trip_id}/category-check")
def final_category_check(trip_id: str, category: str, request: Request):
    auth.require_admin(request)
    return final_checks.category_check(trip_id, category)


@router.post("/{trip_id}/check/{check_key}")
def set_final_check(trip_id: str, check_key: str, body: SetCheck, request: Request):
    return final_checks.set_check(trip_id, check_key, body.state, body.note,
                                  auth.require_admin(request))


@router.put("/{trip_id}/title-key")
def put_title_key(trip_id: str, body: SaveTitleKey, request: Request):
    return final_checks.save_title_key(trip_id, body.value,
                                       auth.require_admin(request))


@router.put("/{trip_id}/categories")
def put_categories(trip_id: str, body: SaveCategories, request: Request):
    return final_checks.save_categories(trip_id, body.categories,
                                        auth.require_admin(request))


@router.post("/{trip_id}/ready")
def post_ready(trip_id: str, request: Request):
    return final_checks.ready_to_publish(trip_id, auth.require_admin(request))


@router.post("/{trip_id}/reopen-description")
def reopen_description(trip_id: str, request: Request):
    return final_checks.reopen_description(trip_id, auth.require_admin(request))
