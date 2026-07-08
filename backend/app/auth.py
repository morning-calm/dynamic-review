"""
Authentication + authorization for the review app — stdlib crypto only (no new deps).

Design (see the auth-roles plan §4.2/§4.3):
  * Passwords: PBKDF2-HMAC-SHA256, per-user random salt, >=600k iterations, stored
    Django-style ``pbkdf2_sha256$<iters>$<salt_b64>$<hash_b64>``; verified with
    ``hmac.compare_digest``.
  * Session token = an OPAQUE handle (``secrets.token_urlsafe(32)``). We store only
    ``sha256(token)`` in ``auth_sessions`` with an ``expires_at``; the raw token is
    never persisted. Instant revocation = delete the row. No signing key to manage.
  * role / languages / active are resolved FRESH from the ``users`` row on every
    request, so deactivation + role/language changes take effect immediately and a
    stolen token is bounded to its TTL.
  * Fail closed: an empty ``users`` table (or an unknown/expired token) => 401 on every
    non-exempt request. There is no default-secret / implicit-admin path.

Transport rule enforced by the middleware in ``main.py`` (via ``extract_token``):
  * writes (POST/PUT/DELETE) MUST carry ``Authorization: Bearer <token>`` — a cookie is
    never accepted for a write (CSRF defence);
  * safe GET/HEAD (media, download) may additionally use the httpOnly ``review_session``
    cookie, so browser <audio>/<img> subresource requests authenticate without a header.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException, Request, Response

from . import audio_core, config, db
from .models import Login

# --------------------------------------------------------------------------- #
# Password hashing (PBKDF2-HMAC-SHA256, Django-style encoding)
# --------------------------------------------------------------------------- #
_ALGO = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 600_000   # OWASP 2023 floor for PBKDF2-HMAC-SHA256


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    """Return ``pbkdf2_sha256$<iters>$<salt_b64>$<hash_b64>`` for a plaintext password."""
    if not password:
        raise ValueError("empty password")
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_ALGO}${iterations}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time verify of a plaintext against a stored ``pbkdf2_sha256$…`` string."""
    try:
        algo, iters_s, salt_b64, hash_b64 = (encoded or "").split("$")
        if algo != _ALGO:
            return False
        iterations = int(iters_s)
        salt = _unb64(salt_b64)
        expected = _unb64(hash_b64)
    except Exception:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk, expected)


# --------------------------------------------------------------------------- #
# User model + resolution
# --------------------------------------------------------------------------- #
@dataclass
class User:
    id: int
    username: str
    role: str                       # "admin" | "reviewer"
    languages: list[str] = field(default_factory=list)
    active: bool = True

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _row_to_user(row) -> User:
    try:
        languages = json.loads(row["languages_json"] or "[]")
        if not isinstance(languages, list):
            languages = []
    except Exception:
        languages = []
    return User(id=row["id"], username=row["username"], role=row["role"],
                languages=[str(x) for x in languages], active=bool(row["active"]))


# --------------------------------------------------------------------------- #
# Opaque DB-backed tokens
# --------------------------------------------------------------------------- #
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(user_id: int) -> tuple[str, float]:
    """Mint an opaque token for a user; store only its sha256 + expiry. Returns
    ``(raw_token, expires_at)`` — the raw token is handed to the client and never
    stored."""
    token = secrets.token_urlsafe(32)
    now = time.time()
    expires_at = now + config.AUTH_TOKEN_TTL_SECONDS
    db.execute(
        "INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at) "
        "VALUES(?,?,?,?)",
        (_hash_token(token), user_id, now, expires_at))
    return token, expires_at


def revoke_token(token: str) -> None:
    db.execute("DELETE FROM auth_sessions WHERE token_hash=?", (_hash_token(token),))


def resolve_user(token: str | None) -> User | None:
    """Look up the live user for a presented token. Rejects unknown/expired tokens and
    deactivated users; role/languages/active come FRESH from the users row (never from
    the token). Returns None (=> 401) if anything is off — including an empty users
    table, which is the fail-closed default."""
    if not token:
        return None
    row = db.query_one(
        "SELECT s.expires_at AS expires_at, u.id AS id, u.username AS username, "
        "u.role AS role, u.languages_json AS languages_json, u.active AS active "
        "FROM auth_sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash=?",
        (_hash_token(token),))
    if not row:
        return None
    if float(row["expires_at"]) < time.time():
        try:
            db.execute("DELETE FROM auth_sessions WHERE token_hash=?",
                       (_hash_token(token),))
        except Exception:
            pass
        return None
    if not row["active"]:
        return None
    return _row_to_user(row)


def authenticate(username: str, password: str) -> User | None:
    """Validate a username/password. Returns the live User or None (caller must emit a
    generic error — no user-enumeration)."""
    row = db.query_one("SELECT * FROM users WHERE username=?", (username or "",))
    if not row or not row["active"]:
        _dummy_verify()   # spend a hash even on miss to blunt the timing side-channel
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return _row_to_user(row)


_DUMMY_HASH = None


def _dummy_verify() -> None:
    """Constant-ish work on the username-miss path (timing-attack blunting)."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("dummy-password-placeholder")
    verify_password("dummy-password-placeholder", _DUMMY_HASH)


# --------------------------------------------------------------------------- #
# Token extraction (transport rule) — used by the middleware
# --------------------------------------------------------------------------- #
def extract_token(request: Request) -> str | None:
    """Header first (always); cookie only for GET/HEAD. A write with only a cookie
    yields None => the middleware 401s it (CSRF defence)."""
    header = request.headers.get("Authorization") or ""
    if header[:7].lower() == "bearer ":
        tok = header[7:].strip()
        return tok or None
    if request.method in ("GET", "HEAD"):
        return request.cookies.get(config.AUTH_COOKIE_NAME) or None
    return None


# --------------------------------------------------------------------------- #
# Authorization helpers + FastAPI dependencies
# --------------------------------------------------------------------------- #
def language_allowed(user: User, trip_id: str) -> bool:
    """Admins see every trip; reviewers only trips whose narration language is in their
    allow-list. ``language_of`` fail-safes unknown suffixes to English (=> admin-only)."""
    if user.role == "admin":
        return True
    return audio_core.language_of(trip_id) in (user.languages or [])


def require_user(request: Request) -> User:
    """Dependency: the authenticated user (set by the middleware). 401 if absent."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, detail={"error": "unauthorized",
                                         "detail": "authentication required"})
    return user


def require_admin(request: Request) -> User:
    """Dependency: an authenticated ADMIN. 403 for reviewers."""
    user = require_user(request)
    if not user.is_admin:
        raise HTTPException(403, detail={"error": "forbidden", "detail": "admin only"})
    return user


def scope_sid(sid: str, request: Request) -> str:
    """Dependency for every ``/api/sessions/{sid}/*`` + media/zip route: resolve
    ``{sid}`` -> ``trip_id`` -> language and 403 if the trip's language isn't allowed
    for the user. Returns the trip_id. 404 if the session is unknown."""
    from . import sessions   # lazy: sessions imports heavy modules; avoids any cycle
    user = require_user(request)
    trip_id = sessions.trip_id_for_session(sid)
    if not language_allowed(user, trip_id):
        raise HTTPException(403, detail={
            "error": "forbidden",
            "detail": "this trip's narration language is not assigned to you"})
    return trip_id


def scope_sid_editable(sid: str, request: Request) -> str:
    """Scope check PLUS the lock-on-submit gate: 403 while the session is not in an
    editable state (submitted/approving/approved are read-only). Applied to the editing
    routes only — read/workflow routes (get, played, submit, approve, …) use scope_sid.
    ADMINS are additionally allowed to edit a 'submitted' session (approve-page inline
    touch-ups) — see sessions.assert_editable."""
    from . import sessions
    trip_id = scope_sid(sid, request)
    sessions.assert_editable(sid, require_user(request))
    return trip_id


# --------------------------------------------------------------------------- #
# Login / logout / me
# --------------------------------------------------------------------------- #
router = APIRouter(prefix="/api")


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=config.AUTH_COOKIE_NAME,
        value=token,
        max_age=int(config.AUTH_TOKEN_TTL_SECONDS),
        httponly=True,
        samesite="lax",
        secure=config.AUTH_COOKIE_SECURE,
        path="/",
    )


@router.post("/login")
def login(body: Login, response: Response):
    """{username, password} -> {token, user:{username, role, languages}} + Set-Cookie.
    401 with a generic message on bad creds (no user-enumeration)."""
    user = authenticate(body.username, body.password)
    if user is None:
        raise HTTPException(401, detail={"error": "unauthorized",
                                         "detail": "invalid username or password"})
    token, _ = issue_token(user.id)
    _set_session_cookie(response, token)
    return {"token": token,
            "user": {"username": user.username, "role": user.role,
                     "languages": user.languages}}


@router.post("/logout")
def logout(request: Request):
    """Delete the presented token's auth_sessions row and clear the cookie -> 204."""
    token = extract_token(request)
    if token:
        try:
            revoke_token(token)
        except Exception:
            pass
    resp = Response(status_code=204)
    resp.delete_cookie(config.AUTH_COOKIE_NAME, path="/",
                       secure=config.AUTH_COOKIE_SECURE, httponly=True, samesite="lax")
    return resp


@router.get("/me")
def me(request: Request):
    user = require_user(request)
    return {"username": user.username, "role": user.role, "languages": user.languages}
