"""
FastAPI app for the review-app backend.

IMPORTANT: ``app.config`` is imported first so SCRIPTS_ROOT lands on sys.path and the
Scripts .env is loaded before anything touches the reused modules.

Security: DB-backed users + opaque tokens (see app/auth.py). Every request except
``POST /api/login`` and ``GET /api/health`` must resolve to a live, active user:
  * writes (POST/PUT/DELETE) MUST carry ``Authorization: Bearer <token>`` (a cookie is
    never accepted for a write — CSRF defence);
  * safe GET/HEAD (media, download) may use the httpOnly ``review_session`` cookie so
    browser <audio>/<img> subresource requests authenticate without a header.
Fail-closed: an empty users table (or a missing/expired token) => 401. Interactive docs
(/docs, /redoc, /openapi.json) are DISABLED so an unauthenticated tunnel client can't
enumerate the API. uvicorn also binds 127.0.0.1 only.
"""

from __future__ import annotations

from . import config  # noqa: F401  (side effects: sys.path + .env) — keep first
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import auth, db, routes_audio, routes_sessions
from .config import CORS_ORIGINS, HOST, PORT

app = FastAPI(title="review-app backend", version="1.0",
              docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# The ONLY unauthenticated routes (method + path exact).
_AUTH_EXEMPT = {("POST", "/api/login"), ("GET", "/api/health")}


@app.middleware("http")
async def require_auth(request: Request, call_next):
    if request.method == "OPTIONS":               # CORS preflight
        return await call_next(request)
    if (request.method, request.url.path) in _AUTH_EXEMPT:
        return await call_next(request)
    # Header for writes; cookie also accepted for GET/HEAD (see auth.extract_token).
    user = auth.resolve_user(auth.extract_token(request))
    if user is None:
        return JSONResponse(status_code=401,
                            content={"error": "unauthorized",
                                     "detail": "authentication required"})
    request.state.user = user
    return await call_next(request)


@app.exception_handler(StarletteHTTPException)
async def http_exc(_: Request, exc: StarletteHTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        content = detail
    else:
        content = {"error": f"http_{exc.status_code}", "detail": detail}
    return JSONResponse(status_code=exc.status_code, content=content)


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    return JSONResponse(status_code=500,
                        content={"error": "internal", "detail": str(exc)})


@app.on_event("startup")
def _startup():
    db.init()


@app.get("/api/health")
def health():
    return {"ok": True}


app.include_router(auth.router)
app.include_router(routes_sessions.router)
app.include_router(routes_audio.router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)
