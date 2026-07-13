"""
Admin CLI for review-app users (no in-app admin page in phase 1).

Run from the backend/ directory with the same interpreter as the server:

    py -3.12 manage.py list-users
    py -3.12 manage.py add-user --username toshifumi --role reviewer --languages Japanese
    py -3.12 manage.py add-user --username dave --role admin
    py -3.12 manage.py reset-password --username ted
    py -3.12 manage.py set-languages --username ted --languages Mandarin,Japanese
    py -3.12 manage.py set-role --username dave --role admin
    py -3.12 manage.py deactivate --username olduser
    py -3.12 manage.py seed [--admin-password s3cret]   # toshifumi(JP), ted(ZH), admin

Passwords: pass --password to set one explicitly, otherwise a strong one is generated
and PRINTED once (it is never recoverable afterwards — only the PBKDF2 hash is stored).

Importing app.config intentionally puts D:\\Dynamic Languages\\Scripts on sys.path and
loads its .env (same bootstrap the server uses); that is expected.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time

from app import config  # noqa: F401  (sys.path + .env bootstrap) — keep first
from app import auth, db

VALID_LANGUAGES = ("English", "Japanese", "Mandarin")
VALID_ROLES = ("admin", "reviewer")
_LANG_CANON = {l.lower(): l for l in VALID_LANGUAGES}


def _parse_languages(raw: str | None) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    for part in raw.replace(";", ",").split(","):
        p = part.strip()
        if not p:
            continue
        canon = _LANG_CANON.get(p.lower())
        if not canon:
            sys.exit(f"error: unknown language '{p}' (valid: {', '.join(VALID_LANGUAGES)})")
        if canon not in out:
            out.append(canon)
    return out


def _gen_password() -> str:
    return secrets.token_urlsafe(12)


def _get_user(username: str):
    return db.query_one("SELECT * FROM users WHERE username=?", (username,))


def _require_user(username: str):
    row = _get_user(username)
    if not row:
        sys.exit(f"error: no such user '{username}'")
    return row


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_add_user(args) -> None:
    if args.role not in VALID_ROLES:
        sys.exit(f"error: role must be one of {VALID_ROLES}")
    if _get_user(args.username):
        sys.exit(f"error: user '{args.username}' already exists "
                 "(use reset-password / set-role / set-languages)")
    langs = _parse_languages(args.languages)
    if args.role == "admin" and not langs:
        langs = list(VALID_LANGUAGES)   # cosmetic; admins bypass language scoping
    password = args.password or _gen_password()
    db.execute(
        "INSERT INTO users(username,password_hash,role,languages_json,active,created_at) "
        "VALUES(?,?,?,?,1,?)",
        (args.username, auth.hash_password(password), args.role,
         json.dumps(langs), time.time()))
    print(f"created {args.role} '{args.username}' languages={langs or 'ALL (admin)'}")
    if not args.password:
        print(f"  password: {password}")


def cmd_reset_password(args) -> None:
    _require_user(args.username)
    password = args.password or _gen_password()
    db.execute("UPDATE users SET password_hash=? WHERE username=?",
               (auth.hash_password(password), args.username))
    # Revoke every active token for this user so the old password can't linger.
    row = _get_user(args.username)
    db.execute("DELETE FROM auth_sessions WHERE user_id=?", (row["id"],))
    print(f"password reset for '{args.username}' (existing sessions revoked)")
    if not args.password:
        print(f"  password: {password}")


def cmd_set_languages(args) -> None:
    _require_user(args.username)
    langs = _parse_languages(args.languages)
    db.execute("UPDATE users SET languages_json=? WHERE username=?",
               (json.dumps(langs), args.username))
    print(f"'{args.username}' languages set to {langs}")


def cmd_set_email(args) -> None:
    """Set (or clear, with --email '') a user's email. Used by the activity notifier to
    tell a reviewer their Gate-2 findings are waiting; no email = in-app badge only."""
    _require_user(args.username)
    email = (args.email or "").strip() or None
    if email and ("@" not in email or " " in email):
        sys.exit(f"error: '{email}' doesn't look like an email address")
    db.execute("UPDATE users SET email=? WHERE username=?", (email, args.username))
    print(f"'{args.username}' email {'set to ' + email if email else 'cleared'}")


def cmd_set_role(args) -> None:
    _require_user(args.username)
    if args.role not in VALID_ROLES:
        sys.exit(f"error: role must be one of {VALID_ROLES}")
    db.execute("UPDATE users SET role=? WHERE username=?", (args.role, args.username))
    print(f"'{args.username}' role set to {args.role}")


def cmd_deactivate(args) -> None:
    row = _require_user(args.username)
    db.execute("UPDATE users SET active=0 WHERE username=?", (args.username,))
    db.execute("DELETE FROM auth_sessions WHERE user_id=?", (row["id"],))
    print(f"'{args.username}' deactivated (sessions revoked)")


def cmd_list_users(_args) -> None:
    rows = db.query("SELECT username,role,languages_json,active,email,created_at FROM users "
                    "ORDER BY username")
    if not rows:
        print("(no users — run: py -3.12 manage.py seed)")
        return
    print(f"{'username':<20} {'role':<9} {'active':<7} {'email':<28} languages")
    for r in rows:
        try:
            langs = ", ".join(json.loads(r["languages_json"] or "[]"))
        except Exception:
            langs = r["languages_json"]
        print(f"{r['username']:<20} {r['role']:<9} "
              f"{'yes' if r['active'] else 'NO':<7} {(r['email'] or '—'):<28} {langs}")


def _seed_one(username: str, role: str, languages: list[str]) -> None:
    if _get_user(username):
        print(f"  {username}: exists — skipped")
        return
    password = _gen_password()
    db.execute(
        "INSERT INTO users(username,password_hash,role,languages_json,active,created_at) "
        "VALUES(?,?,?,?,1,?)",
        (username, auth.hash_password(password), role, json.dumps(languages), time.time()))
    print(f"  {username}: created ({role}, {languages or 'ALL'})  password: {password}")


def cmd_seed(args) -> None:
    print("seeding users:")
    _seed_one("toshifumi", "reviewer", ["Japanese"])
    _seed_one("ted", "reviewer", ["Mandarin"])
    # admin — password from --admin-password, else generated + printed.
    if _get_user("admin"):
        print("  admin: exists — skipped")
    else:
        password = args.admin_password or _gen_password()
        db.execute(
            "INSERT INTO users(username,password_hash,role,languages_json,active,created_at)"
            " VALUES(?,?,?,?,1,?)",
            ("admin", auth.hash_password(password), "admin",
             json.dumps(list(VALID_LANGUAGES)), time.time()))
        print(f"  admin: created (admin, ALL)  password: {password}")


def main() -> None:
    p = argparse.ArgumentParser(description="review-app user management")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add-user")
    a.add_argument("--username", required=True)
    a.add_argument("--role", default="reviewer", choices=VALID_ROLES)
    a.add_argument("--languages", default="", help="comma-separated, e.g. Japanese,Mandarin")
    a.add_argument("--password", default="")
    a.set_defaults(func=cmd_add_user)

    a = sub.add_parser("reset-password")
    a.add_argument("--username", required=True)
    a.add_argument("--password", default="")
    a.set_defaults(func=cmd_reset_password)

    a = sub.add_parser("set-languages")
    a.add_argument("--username", required=True)
    a.add_argument("--languages", required=True)
    a.set_defaults(func=cmd_set_languages)

    a = sub.add_parser("set-role")
    a.add_argument("--username", required=True)
    a.add_argument("--role", required=True, choices=VALID_ROLES)
    a.set_defaults(func=cmd_set_role)

    a = sub.add_parser("deactivate")
    a.add_argument("--username", required=True)
    a.set_defaults(func=cmd_deactivate)

    a = sub.add_parser("set-email")
    a.add_argument("--username", required=True)
    a.add_argument("--email", required=True, help="'' to clear")
    a.set_defaults(func=cmd_set_email)

    a = sub.add_parser("list-users")
    a.set_defaults(func=cmd_list_users)

    a = sub.add_parser("seed")
    a.add_argument("--admin-password", default="")
    a.set_defaults(func=cmd_seed)

    args = p.parse_args()
    db.init()
    args.func(args)


if __name__ == "__main__":
    main()
