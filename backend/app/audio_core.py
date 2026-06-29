"""
Audio core — PORTED (not imported) from RegenerateSceneAudio-EditMe.py because that
file has a hyphen in its name and module-level side effects (Firebase init, argparse).

Provides:
  VOICES                       voice_id + voice_settings for isla / harry / andrea
  strip_url_lines              drop bare URL lines from SceneDesc
  clean_text                   Gemini number/regnal/unit speller (gemini-2.5-flash)
  validate_and_clean           clean with a similarity guard; reports fallback
  generate_audio               ElevenLabs TTS  -> mp3 bytes
  generate_with_timestamps     ElevenLabs TTS  -> mp3 bytes + per-WORD alignment
  is_numberish / has_non_latin anchor-eligibility helpers for the splice engine

Keeps using the shared pronunciation_overrides module so per-trip spoken forms
(e.g. "Taipei one oh one") survive cleaning.
"""

from __future__ import annotations

import base64
import difflib
import os
import re
import time
import unicodedata

import requests
from google import genai

from . import config  # noqa: F401  (ensures SCRIPTS_ROOT on sys.path) — keep first
from pronunciation_overrides import load_overrides, apply_overrides, prompt_rule

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
EL_MODEL = "eleven_multilingual_v2"   # K4: speed honoured only on v2

VOICES = {
    # name: (voice_id, voice_settings)
    "isla":   ("h8eW5xfRUGVJrZhAFxqK",
               {"speed": 1.0, "stability": 0.5, "similarity_boost": 0.75}),
    "harry":  ("HLXBCncM2sIxwTmiIZg8",
               {"stability": 0.75, "similarity_boost": 0.75, "style": 0, "speed": 1}),
    "andrea": ("bhVHbttQpONfnKTIWK9J",
               {"stability": 0.75, "similarity_boost": 0.75, "style": 0, "speed": 1}),
}


def speed_for_trip(trip_id: str) -> float:
    """CEFR English narration speed by level — eleven_multilingual_v2 honours `speed`,
    so a regenerated clip must match the original take's level:
        A1-A2  (`_A12_EN`) -> 0.7
        B1     (`_B1_EN`)  -> 0.85
        B2+ and native `_EN` -> 1.0
    NB Japanese uses the v3 API where speed is always 1.0; HSK Mandarin is undefined
    (translator deciding v2-vs-v3 + speed). When those languages get backend support,
    branch on language/model here (and likely drop v2 for JP)."""
    t = (trip_id or "").upper()
    if t.endswith("_A12_EN"):
        return 0.7
    if t.endswith("_B1_EN"):
        return 0.85
    return 1.0

GEMINI_DELAY = 0.1
GEMINI_MAX_RETRIES = 3
GEMINI_RETRY_DELAY = 5
GEMINI_SIMILARITY_THRESHOLD = 0.8
GEMINI_MAX_CLEAN_RETRIES = 3

_client = None


def _gemini():
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    return _client


_URL_LINE_RE = re.compile(r"^\s*https?://\S+\s*$", re.IGNORECASE)


def strip_url_lines(text: str) -> str:
    if not text:
        return text
    return "\n".join(
        ln for ln in text.split("\n") if not _URL_LINE_RE.match(ln)
    ).strip()


def clean_text(text: str, strict: bool = False, overrides=None) -> str:
    """Gemini speller. Returns rewritten text, or the input on any API failure."""
    pron_block = prompt_rule(overrides)
    extra_instruction = ""
    if strict:
        extra_instruction = """
CRITICAL: You MUST NOT alter the meaning, word order, or structure of the text.
You MUST NOT add new sentences, facts, or paragraphs.
You MUST NOT expand or elaborate on the content in any way.
You MUST NOT add information not present in the original text.
Only apply the specific number/symbol conversions listed below. Nothing else.
"""

    prompt = f"""Rewrite the following text by applying ONLY these exact changes. Do not add, remove, or modify any content beyond these rules. Preserve the original wording, punctuation, and structure except where specified:
{extra_instruction}{pron_block}1. ROMAN NUMERALS AFTER A NAME OR TITLE (kings, queens, popes, emperors, dukes, tsars etc.) are regnal numbers and MUST be converted to "the" + ordinal word, keeping any possessive 's:
   - King Charles I → King Charles the First
   - King Charles I's attempts → King Charles the First's attempts
   - Elizabeth II → Elizabeth the Second
   - Henry VIII → Henry the Eighth
   - Pope Pius XII → Pope Pius the Twelfth
   NEVER read a regnal numeral as a plain number ("King Charles one" is WRONG).
   Roman numerals NOT after a person's name become the spoken form people actually use (usually a plain number): World War II → World War Two, Part III → Part Three, Act IV → Act Four.

2. YEARS (HIGHEST PRIORITY): Any 4-digit number from 1000-2099 must be treated as a YEAR and spoken naturally as two pairs of digits. Never treat years as ordinary numbers. Examples:
   - 1868 → eighteen sixty eight (NOT one thousand eight hundred and sixty eight)
   - 1172 → eleven seventy two
   - 1603 → sixteen oh three
   - 1512 → fifteen twelve
   - 2026 → twenty twenty six
   - 1900 → nineteen hundred
   - 2000 → two thousand
   - 2005 → two thousand and five
   Only treat a 4-digit number as a regular number if the context makes it clearly a quantity (e.g., "1,500 soldiers", "cost 2000 yen").

3. REGULAR NUMBERS: For all other numbers (not years), convert to full words. For numbers with 3 or more digits, include "and" before the final part (e.g., 150 becomes one hundred and fifty, 1507 becomes one thousand five hundred and seven). Always treat thousands and hundreds separately (do not use forms like fifteen hundred and seven).

4. Convert all currency symbols, such as £5 or $10 to full words and place the unit after the number (e.g. five pounds or ten dollars).

5. Convert temperatures to full words (e.g., 2°C becomes two degrees celsius, -5°F becomes minus five degrees fahrenheit).

6. Expand any abbreviated units to their full forms (e.g., km becomes kilometres, cm becomes centimetres, kg becomes kilograms).

Output only the rewritten text, nothing else.

Text: {text}"""

    time.sleep(GEMINI_DELAY)
    for attempt in range(GEMINI_MAX_RETRIES + 1):
        try:
            response = _gemini().models.generate_content(
                model="gemini-2.5-flash", contents=prompt)
            return response.text.strip()
        except Exception as e:  # noqa: BLE001
            error_str = str(e)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                if attempt < GEMINI_MAX_RETRIES:
                    time.sleep(GEMINI_RETRY_DELAY * (2 ** attempt))
                    continue
            return text
    return text


def validate_and_clean(text: str, doc_id: str, scene_index) -> tuple[str, bool]:
    """Clean with a word-similarity guard. Returns (cleaned, used_fallback).

    PORT NOTE: the original silently returns the *uncleaned* text on failure. We
    surface that as ``used_fallback=True`` so the splice engine can route the edit to
    ``edit_required`` instead of aligning on drifted tokens (plan S2 / C1)."""
    overrides = load_overrides(doc_id)
    pre = apply_overrides(text, overrides)
    for attempt in range(GEMINI_MAX_CLEAN_RETRIES):
        cleaned = clean_text(pre, strict=attempt > 0, overrides=overrides)
        ratio = difflib.SequenceMatcher(
            None, pre.lower().split(), cleaned.lower().split()).ratio()
        if ratio >= GEMINI_SIMILARITY_THRESHOLD:
            return cleaned, False
    return pre, True


# --------------------------------------------------------------------------- #
# ElevenLabs
# --------------------------------------------------------------------------- #
def _headers() -> dict:
    return {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}


def generate_audio(text: str, voice_id: str, voice_settings: dict) -> bytes:
    """Plain TTS → mp3 bytes (whole-block + fallback clips). Raises on non-200."""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    body = {"text": text, "model_id": EL_MODEL, "voice_settings": voice_settings}
    r = requests.post(url, json=body, headers=_headers(), timeout=180)
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:300]}")
    return r.content


def generate_with_timestamps(text: str, voice_id: str, voice_settings: dict,
                             previous_text: str | None = None,
                             next_text: str | None = None) -> tuple[bytes, list[dict]]:
    """TTS with character alignment → (mp3 bytes, word list).

    Words are aggregated from the per-character alignment by splitting on spaces;
    word.start = first char start, word.end = last char end."""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
    body = {"text": text, "model_id": EL_MODEL, "voice_settings": voice_settings}
    if previous_text:
        body["previous_text"] = previous_text
    if next_text:
        body["next_text"] = next_text
    r = requests.post(url, json=body, headers=_headers(), timeout=180)
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs(ts) {r.status_code}: {r.text[:300]}")
    data = r.json()
    mp3 = base64.b64decode(data["audio_base64"])
    align = data.get("alignment") or {}
    words = _chars_to_words(
        align.get("characters") or [],
        align.get("character_start_times_seconds") or [],
        align.get("character_end_times_seconds") or [],
    )
    return mp3, words


def _chars_to_words(chars: list[str], starts: list[float], ends: list[float]) -> list[dict]:
    words: list[dict] = []
    cur, cur_start, cur_end = "", None, None
    for ch, st, en in zip(chars, starts, ends):
        if ch.isspace():
            if cur:
                words.append({"word": cur, "start": cur_start, "end": cur_end})
                cur, cur_start, cur_end = "", None, None
            continue
        if not cur:
            cur_start = st
        cur += ch
        cur_end = en
    if cur:
        words.append({"word": cur, "start": cur_start, "end": cur_end})
    return words


# --------------------------------------------------------------------------- #
# Anchor eligibility (splice engine helpers)
# --------------------------------------------------------------------------- #
_NUM_RE = re.compile(r"\d")
_ROMAN_RE = re.compile(r"^[IVXLCDM]+$")          # regnal / Roman numerals
_ORDINAL_WORDS = {
    "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth",
    "ninth", "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth",
}


def is_numberish(token: str) -> bool:
    """True if a token is a number / regnal numeral / ordinal — a BAD anchor
    because the Gemini speller may re-render it differently than Whisper heard it."""
    t = token.strip(".,;:!?\"'()").strip()
    if not t:
        return False
    if _NUM_RE.search(t):
        return True
    if _ROMAN_RE.match(t.upper()) and len(t) <= 4:
        return True
    if t.lower() in _ORDINAL_WORDS:
        return True
    return False


def has_non_latin(token: str) -> bool:
    """True if the token contains a non-Latin letter (hanzi, kana…). Such a token in
    the changed span or an anchor → whole-regen / edit_required (plan K3)."""
    for ch in token:
        if not ch.isalpha():
            continue
        name = unicodedata.name(ch, "")
        if not name.startswith("LATIN"):
            return True
    return False


def override_phrases(doc_id: str) -> list[str]:
    out: list[str] = []
    for o in load_overrides(doc_id):
        for key in ("phrase", "say"):
            v = (o.get(key) or "").strip()
            if v:
                out.extend(v.lower().split())
    return out
