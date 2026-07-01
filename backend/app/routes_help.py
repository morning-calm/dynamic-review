"""In-app help — serves ``docs/user-guides/`` to signed-in users (the top-bar ? button).

  GET /help/quick         the one-page visual quick reference (HTML, served as-is)
  GET /help/guide         the signed-in user's written guide, English (markdown → HTML)
  GET /help/guide-native  the 中文/日本語 variant for reviewers (falls back to English)

The guide is picked by role/language: admin → admin guide; reviewer with Japanese →
Toshifumi's; reviewer with Mandarin → Ted's. These are GETs opened in a NEW TAB, so they
authenticate via the httpOnly ``review_session`` cookie (like /audio); in the single-origin
deploy ``main.py`` lists ``/help/`` among the protected prefixes so the guides are never
public. Markdown renders per request — editing ``docs/user-guides/*.md`` is live, no rebuild.
"""

from __future__ import annotations

import re
from pathlib import Path

import markdown as _md
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, HTMLResponse, Response

from . import auth

router = APIRouter(prefix="/help")

_GUIDES = Path(__file__).resolve().parent.parent.parent / "docs" / "user-guides"

# Light, print-friendly reading page; CJK-aware font stack (the guides are en/zh/ja).
_CSS = """
  body { margin:0; background:#f8fafc; color:#1f2937; line-height:1.6;
         font-family: system-ui, "Segoe UI", Roboto, "Noto Sans CJK SC",
                      "Noto Sans CJK JP", "Hiragino Sans", "Microsoft YaHei", sans-serif; }
  main { max-width: 760px; margin: 0 auto; padding: 32px 20px 80px; }
  h1 { font-size: 26px; line-height:1.25; border-bottom: 2px solid #e5e7eb;
       padding-bottom: 10px; }
  h2 { font-size: 19px; margin-top: 2em; }
  a { color: #2563eb; }
  code { background:#eef2f7; padding: 1px 5px; border-radius: 4px; font-size: .92em; }
  li { margin: .25em 0; }
  strong { color: #111827; }
  em { color: #374151; }
  table { border-collapse: collapse; }
  th, td { border: 1px solid #d1d5db; padding: 5px 10px; text-align: left; }
  blockquote { border-left: 3px solid #d1d5db; margin: 1em 0; padding: .2em 1em;
               color:#4b5563; }
"""


def _guide_files(user) -> tuple[str, str | None]:
    """(english_md, native_md|None) for this user's role/languages."""
    langs = set(user.languages or [])
    if user.role != "admin":
        if "Japanese" in langs:
            return "toshifumi-japanese-reviewer.en.md", "toshifumi-japanese-reviewer.ja.md"
        if "Mandarin" in langs:
            return "ted-mandarin-reviewer.en.md", "ted-mandarin-reviewer.zh.md"
    return "admin-guide.en.md", None


def _render_md(name: str) -> Response:
    f = _GUIDES / name
    if not f.is_file():
        return Response(status_code=404)
    text = f.read_text(encoding="utf-8")
    m = re.search(r"^#\s+(.+)$", text, re.M)
    title = m.group(1).strip() if m else "Review App — Guide"
    body = _md.markdown(text, extensions=["tables", "sane_lists"])
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{title}</title><style>{_CSS}</style></head>"
        f"<body><main>{body}</main></body></html>")


@router.get("/quick")
def quick(user=Depends(auth.require_user)):
    f = _GUIDES / "quick-reference.html"
    if not f.is_file():
        return Response(status_code=404)
    return FileResponse(str(f), media_type="text/html")


@router.get("/guide")
def guide(user=Depends(auth.require_user)):
    en, _native = _guide_files(user)
    return _render_md(en)


@router.get("/guide-native")
def guide_native(user=Depends(auth.require_user)):
    en, native = _guide_files(user)
    return _render_md(native or en)
