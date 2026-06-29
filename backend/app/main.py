"""
FastAPI app for the review-app backend.

IMPORTANT: ``app.config`` is imported first so SCRIPTS_ROOT lands on sys.path and the
Scripts .env is loaded before anything touches the reused modules.

Security (stage-1): uvicorn binds to 127.0.0.1 only; every /api request (except the
health probe) must carry ``X-Review-Token``. Media GETs (/audio, /overlays) are token
-exempt because browser <audio>/<img> elements cannot attach custom headers — the
localhost-only bind is the control there.
"""

from __future__ import annotations

from . import config  # noqa: F401  (side effects: sys.path + .env) — keep first
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db, routes_audio, routes_sessions
from .config import CORS_ORIGINS, HOST, PORT, REVIEW_TOKEN

app = FastAPI(title="review-app backend", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_TOKEN_EXEMPT_PREFIXES = ("/audio/", "/overlays/")
_TOKEN_EXEMPT_PATHS = {"/api/health", "/docs", "/openapi.json", "/redoc"}


@app.middleware("http")
async def require_token(request: Request, call_next):
    path = request.url.path
    if (request.method == "OPTIONS"
            or path in _TOKEN_EXEMPT_PATHS
            or any(path.startswith(p) for p in _TOKEN_EXEMPT_PREFIXES)):
        return await call_next(request)
    if request.headers.get("X-Review-Token") != REVIEW_TOKEN:
        return JSONResponse(status_code=401,
                            content={"error": "unauthorized",
                                     "detail": "missing or invalid X-Review-Token"})
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


app.include_router(routes_sessions.router)
app.include_router(routes_audio.router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)
