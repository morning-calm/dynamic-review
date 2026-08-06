"""
Audio core — the TTS half is PORTED from RegenerateSceneAudio-EditMe.py (that file has a
hyphen in its name and module-level side effects: Firebase init, argparse). The NUMBER
CLEANING is the opposite — imported from the Scripts repo, never copied; see the block
comment above `clean_text` for the French bug that copying caused.

Provides:
  VOICES                       voice_id + voice_settings for isla / harry / andrea
  strip_url_lines              drop bare URL lines from SceneDesc
  clean_text                   number/date/regnal/unit speller — the SHARED per-language
                               prompts from the Scripts repo, run on DeepSeek
  validate_and_clean           clean in the TRIP'S language, with a similarity guard;
                               reports fallback (→ edit_required)
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
import unicodedata

import requests

from . import config  # noqa: F401  (ensures SCRIPTS_ROOT + 'Audio Generation' on
                      # sys.path, and loads the Scripts .env) — keep first
from pronunciation_overrides import load_overrides, apply_overrides, prompt_rule

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
EL_MODEL = "eleven_multilingual_v2"   # K4: speed honoured only on v2

# --------------------------------------------------------------------------- #
# Approved-voice registry — the single source of truth for who narrates a trip.
# Mirrors the per-voice ElevenLabs scripts in `Audio Generation/` (voice_id +
# voice_settings + model). `gender`/`language`/`country` drive resolve_voice's
# (language, country, gender) lookup — the reviewer picks male/female at drafting,
# stored in the trip's staging_choices.json (see sessions.resolve_voice).
#
# NB the English `voice_settings` here are the review-app's existing values (kept
# verbatim — do not retune blind: the splice/seam DSP was calibrated against them).
# JP + ZH voices use eleven_v3 (speed NOT honoured by the API); EN uses v2.
VOICE_REGISTRY = {
    # name: {voice_id, settings, model, gender, language, country}
    "isla":    {"voice_id": "h8eW5xfRUGVJrZhAFxqK",
                "settings": {"speed": 1.0, "stability": 0.5, "similarity_boost": 0.75},
                "model": "eleven_multilingual_v2",
                "gender": "female", "language": "English", "country": "Scotland"},
    "harry":   {"voice_id": "HLXBCncM2sIxwTmiIZg8",
                "settings": {"stability": 0.75, "similarity_boost": 0.75, "style": 0, "speed": 1},
                "model": "eleven_multilingual_v2",
                "gender": "male", "language": "English", "country": "England"},
    "andrea":  {"voice_id": "bhVHbttQpONfnKTIWK9J",
                "settings": {"stability": 0.75, "similarity_boost": 0.75, "style": 0, "speed": 1},
                "model": "eleven_multilingual_v2",
                "gender": "female", "language": "English", "country": "England"},
    # Mandarin — eleven_v3 (dave/Ted 2026-07-02: Mandarin is V3-only; speed ignored, kept 1.0).
    "yu":      {"voice_id": "fQj4gJSexpu8RDE2Ii5m",
                "settings": {"speed": 1.0, "stability": 0.5, "similarity_boost": 0.75},
                "model": "eleven_v3",
                "gender": "male", "language": "Mandarin", "country": "Taiwan"},
    "annasu":  {"voice_id": "9lHjugDhwqoxA5MhX0az",
                "settings": {"speed": 1.0, "stability": 0.5, "similarity_boost": 0.75},
                "model": "eleven_v3",
                "gender": "female", "language": "Mandarin", "country": "China"},
    "jason":   {"voice_id": "DowyQ68vDpgFYdWVGjc3",
                "settings": {"speed": 1.0, "stability": 0.5, "similarity_boost": 0.75},
                "model": "eleven_v3",
                "gender": "male", "language": "Mandarin", "country": "China"},
    # European languages (added 2026-07-06 for the EU 5c review-app migration) —
    # eleven_multilingual_v2 (honours speed; _Beg_ trips are 0.7x at generation).
    # voice_ids from the Scripts CLAUDE.md voice table; settings mirror the EN v2
    # entries the splice DSP is calibrated against.
    "yvonne":    {"voice_id": "vTGV06pygfwa2WhLDZFp",
                  "settings": {"stability": 0.75, "similarity_boost": 0.75, "style": 0, "speed": 1},
                  "model": "eleven_multilingual_v2",
                  "gender": "female", "language": "French", "country": "France"},
    "sebastien": {"voice_id": "BUJMBsQ3Oq4cEeWSb48y",
                  "settings": {"stability": 0.75, "similarity_boost": 0.75, "style": 0, "speed": 1},
                  "model": "eleven_multilingual_v2",
                  "gender": "male", "language": "French", "country": "France"},
    "dana":      {"voice_id": "nF7t9cuYo0u3kuVI9q4B",
                  "settings": {"stability": 0.75, "similarity_boost": 0.75, "style": 0, "speed": 1},
                  "model": "eleven_multilingual_v2",
                  "gender": "female", "language": "German", "country": "Germany"},
    "noah":      {"voice_id": "LBdEwXpO9YwPdF4PqCd9",
                  "settings": {"stability": 0.75, "similarity_boost": 0.75, "style": 0, "speed": 1},
                  "model": "eleven_multilingual_v2",
                  "gender": "male", "language": "German", "country": "Germany"},
    "sara":      {"voice_id": "Ir1QNHvhaJXbAGhT50w3",
                  "settings": {"stability": 0.75, "similarity_boost": 0.75, "style": 0, "speed": 1},
                  "model": "eleven_multilingual_v2",
                  "gender": "female", "language": "Spanish", "country": "Spain"},
    "martin":    {"voice_id": "6xftrpatV0jGmFHxDjUv",
                  "settings": {"stability": 0.75, "similarity_boost": 0.75, "style": 0, "speed": 1},
                  "model": "eleven_multilingual_v2",
                  "gender": "male", "language": "Spanish", "country": "Spain"},
    "linda":     {"voice_id": "3DPhHWXDY263XJ1d2EPN",
                  "settings": {"stability": 0.75, "similarity_boost": 0.75, "style": 0, "speed": 1},
                  "model": "eleven_multilingual_v2",
                  "gender": "female", "language": "Italian", "country": "Italy"},
    "marco":     {"voice_id": "W71zT1VwIFFx3mMGH2uZ",
                  "settings": {"stability": 0.75, "similarity_boost": 0.75, "style": 0, "speed": 1},
                  "model": "eleven_multilingual_v2",
                  "gender": "male", "language": "Italian", "country": "Italy"},
    # Japanese — eleven_v3 (speed ignored by the API; kept at 1.0).
    "daisuke": {"voice_id": "ss9cJxDAEMXP4wfQ3GPr",
                "settings": {"speed": 1.0, "stability": 0.5, "similarity_boost": 0.75},
                "model": "eleven_v3",
                "gender": "male", "language": "Japanese", "country": "Japan"},
    "fumi":    {"voice_id": "PmgfHCGeS5b7sH90BOOJ",
                "settings": {"speed": 1.0, "stability": 0.5, "similarity_boost": 0.75},
                "model": "eleven_v3",
                "gender": "female", "language": "Japanese", "country": "Japan"},
    # Korean — eleven_v3 (speed ignored by the API; kept at 1.0). Chosen by the native
    # reviewer 2026-08-04 from a six-clip audition; both ids confirmed against the
    # ElevenLabs API. A family's three TOPIK rungs share ONE narrator (a learner
    # climbing TPK1 → TPK2 → TPK3/4 must not change guide), so the voice is chosen per
    # FAMILY, not per rung — Scripts/Research and Writing/Korean TOPIK/ko_families.py
    # is where that 4/4 split lives, and it reaches here via the manifest `voice` field.
    "annakim": {"voice_id": "uyVNoMrnUku1dZyVEXwD",
                "settings": {"speed": 1.0, "stability": 0.5, "similarity_boost": 0.75},
                "model": "eleven_v3",
                "gender": "female", "language": "Korean", "country": "Korea"},
    "hyuk":    {"voice_id": "ZJCNdZEjYwkOElxugmW2",
                "settings": {"speed": 1.0, "stability": 0.5, "similarity_boost": 0.75},
                "model": "eleven_v3",
                "gender": "male", "language": "Korean", "country": "Korea"},
}

# Back-compat view used by the splice/session code: {name: (voice_id, voice_settings)}.
VOICES = {n: (v["voice_id"], v["settings"]) for n, v in VOICE_REGISTRY.items()}


EL_MODELS = ["eleven_multilingual_v2", "eleven_v3"]  # v2 honours speed; v3 ignores it


def model_for_voice(name: str) -> str:
    """ElevenLabs model_id for a registry voice (falls back to the v2 default)."""
    return (VOICE_REGISTRY.get(name) or {}).get("model", EL_MODEL)


def display_name(name: str) -> str:
    n = (name or "").strip().lower()
    return {"annasu": "Anna-Su", "annakim": "Anna Kim"}.get(n, n.capitalize())


def registry_list() -> list[dict]:
    """The approved voices for the UI picker (name + display + metadata)."""
    return [
        {"name": n, "display": display_name(n), "gender": v["gender"],
         "language": v["language"], "country": v["country"], "model": v["model"]}
        for n, v in VOICE_REGISTRY.items()
    ]


_EU_LANG_SUFFIXES = {"_ES": "Spanish", "_FR": "French", "_DE": "German", "_IT": "Italian"}


def language_of(trip_id: str) -> str:
    """Narration language inferred from the trip-id suffix (`_JP`/`_ZH`/`_KO`/`_ES`/
    `_FR`/`_DE`/`_IT`; anything else — including `_EN` — is English).

    ⚠ This is the single point that decides FOUR things, so an unmapped suffix fails
    quietly in four places at once: the reviewer language ACL (`auth.language_allowed`
    — an unmapped language reads "English", so the target-language reviewer is 403'd
    and only an admin can open the trip), the Whisper transcription language
    (`sessions._whisper_lang` — an unmapped trip is transcribed as English, which
    silently wrecks the splice anchors), the fallback narrator, and the trip list's
    language column. `_KO` was unmapped until 2026-08-05, when the first 24 Korean
    rungs reached the queue."""
    t = (trip_id or "").upper()
    if t.endswith("_JP"):
        return "Japanese"
    if t.endswith("_ZH"):
        return "Mandarin"
    if t.endswith("_KO"):
        return "Korean"
    for suf, lang in _EU_LANG_SUFFIXES.items():
        if t.endswith(suf):
            return lang
    return "English"


def voice_for_gender(language: str, country: str, gender: str) -> str | None:
    """Map the drafting choice (male/female) + trip language/country to an approved
    voice name. Returns None if gender is missing/unknown so the caller can fall
    back to the legacy country guess."""
    g = (gender or "").strip().lower()
    if g not in ("male", "female"):
        return None
    c = (country or "").strip()
    if language == "Japanese":
        return "daisuke" if g == "male" else "fumi"
    if language == "Mandarin":
        if g == "female":
            return "annasu"
        return "yu" if c == "Taiwan" else "jason"
    if language == "Korean":
        return "hyuk" if g == "male" else "annakim"
    if language == "Spanish":
        return "martin" if g == "male" else "sara"
    if language == "French":
        return "sebastien" if g == "male" else "yvonne"
    if language == "German":
        return "noah" if g == "male" else "dana"
    if language == "Italian":
        return "marco" if g == "male" else "linda"
    # English (default): Scotland has its own female voice; UK male is Harry.
    if g == "female":
        return "isla" if c == "Scotland" else "andrea"
    return "harry"


def speed_for_trip(trip_id: str) -> float:
    """Narration speed by level — eleven_multilingual_v2 honours `speed`, so a
    regenerated clip must match the original take's level:
        A1-A2  (`_A12_EN`, `_A12_{ES,FR,DE,IT}`)  -> 0.7
        Beg EU (`_Beg_{ES,FR,DE,IT}`)             -> 0.7
        B1     (`_B1_EN`, `_B1_{ES,FR,DE,IT}`)    -> 0.85
        B2+ and native                            -> 1.0
    Mirrors run_eu.py's `is_low_level()` on the pipeline side (EU low-level trips are
    generated at 0.7x). NB Japanese AND Mandarin use the v3 API where speed is always
    1.0 (Mandarin went V3-only 2026-07-02); only v2 languages branch on level here."""
    t = (trip_id or "").upper()
    if t.endswith("_A12_EN"):
        return 0.7
    if t.endswith("_B1_EN"):
        return 0.85
    for lang in ("ES", "FR", "DE", "IT"):
        if t.endswith(f"_A12_{lang}") or t.endswith(f"_BEG_{lang}"):
            return 0.7
        if t.endswith(f"_B1_{lang}"):
            return 0.85
    return 1.0

# --------------------------------------------------------------------------- #
# Number / date / unit cleaning — the SHARED pipeline prompt surface
# --------------------------------------------------------------------------- #
# ⚠ There is exactly ONE set of number-clean prompts and it lives in the Scripts repo
# (`Audio Generation/`). This module used to carry its own PORTED copy — English-only,
# on gemini-2.5-flash — applied to every language without dispatch. That is how a French
# trip came to be voiced "Louis the Fourteenth ... eighteen sixty eight" while the
# pipeline's own master said "Louis quatorze ... mille huit cent soixante-huit"
# (reported by the French reviewer 2026-08-06; 167 of 357 queued trips were exposed —
# ES/FR/KO/DE/IT). Do NOT re-port these prompts back in here: a second copy cannot be
# kept in step, and the failure it produces is silent and language-shaped.
#
# The prompts also moved Gemini → DeepSeek on the Scripts side; importing rather than
# copying is what makes that a non-event for this app.
try:  # noqa: SIM105
    import tts_number_clean as _shared            # transport + guard helpers
    import korean_number_clean as _ko_clean       # ko: sino-year + acronym pre-expansion
    from gemini_number_clean_prompts import build_prompt as _build_prompt
    _CLEANER_ERROR: str | None = None
except Exception as _e:  # noqa: BLE001
    _shared = _ko_clean = _build_prompt = None    # type: ignore[assignment]
    _CLEANER_ERROR = f"{type(_e).__name__}: {_e}"

#: Accepted-clean bar for the languages where Scripts HAS a numeral inventory registered
#: (`tts_number_clean.similarity_basis` — today it/jp), i.e. where a legitimate expansion
#: is stripped from both sides and so scores ~1.0. Languages without one are judged by
#: `_prose_survival` instead; see the guard block below for why the word ratio it would
#: otherwise degrade to cannot be used. Registering fr/de/es in `_STRIPPERS` on the
#: Scripts side would let this bar cover them too and retire our arm.
NUMBER_CLEAN_THRESHOLD = 0.8
NUMBER_CLEAN_MAX_RETRIES = 3

#: Bumped whenever the cleaner's OUTPUT for identical input could change — backend swap,
#: prompt-surface change, language dispatch. `sessions._cleaned_orig` mixes this into its
#: cache key so a session seeded under an older cleaner RE-CLEANS instead of diffing the
#: reviewer's new text against a stale (here: English-numbered) baseline.
CLEANER_VERSION = "2-shared-deepseek"

#: `language_of()` output → the `gemini_number_clean_prompts` language key.
#: ⚠ ENUMERATED SET: every value `language_of` can return needs an entry here. An absent
#: one does NOT fall back to English (that is the bug this whole change exists to kill) —
#: it disables cleaning for that language, loudly. `test_number_clean_language.py` pins
#: the two sets equal.
_LANG_CODES = {
    "English": "en", "French": "fr", "German": "de", "Italian": "it",
    "Spanish": "es", "Mandarin": "zh", "Japanese": "jp", "Korean": "ko",
}

#: Korean cleans through its OWN Scripts harness rather than the generic build_prompt
#: path: it carries deterministic pre-expansion (`sino_year` — the model dropped the 百
#: from 1963; `expand_latin_acronyms` — it read KBS as BTS and the guard passed it) plus a
#: Hangul-numeral-aware similarity check. It applies its own pronunciation overrides, so
#: it gets the RAW text, not `pre`. Verified live 2026-08-06: 「1963년…21명」 →
#: 「천구백육십삼 년…스물한 명」.
def _own_harness(lang: str):
    return {"ko": _ko_clean}.get(lang)


#: ⚠ zh/jp are NOT number-cleaned here, deliberately.
#: 1. CONSISTENCY. `sessions.regenerate` routes them through `_cjk_spoken`, which voices
#:    the Hans/kana line RAW and never calls this function. `fallback()` is the only path
#:    that reaches us, so cleaning here would make the reference clip say something the
#:    working take does not — and it is the working take the splice engine diffs against.
#: 2. The zh harness is currently emitting the year TWICE. Measured 2026-08-06, three
#:    identical runs: 「1999年」 → 「一九九九年（一九九九年）」. Its own guard cannot see it
#:    (both sides strip to nothing once numerals are removed — the blind spot its own
#:    `sino_year` docstring describes). Voicing that would read the date twice.
#: What they must NOT do is what they did until today: fall through to the ENGLISH prompt.
#: Revisit when the zh duplication is fixed on the Scripts side and CJK cleaning is wanted
#: on both paths at once, not just this one.
_NO_CLEAN_LANGS = {"zh", "jp"}


#: Post-condition on a self-guarding harness's output: a digit or currency/percent symbol
#: still present means it gave up and handed back the original (both harnesses fall back
#: silently), which is exactly the `used_fallback` signal the splice engine wants.
_LEFTOVER_NUMERIC_RE = re.compile(r"[0-9０-９°%£¥€$₩]")


# --------------------------------------------------------------------------- #
# The accept/reject guard
# --------------------------------------------------------------------------- #
# ⚠ A PLAIN WORD RATIO REJECTS CORRECT WORK, and it does so hardest on exactly the
# sentences the cleaner exists for. Measured 2026-08-06 against the bar of 0.80, on
# cleans that are all four perfect:
#     en  "King Charles I fled in 1642 … 5 km"      -> 0.62
#     fr  "sous Louis XIV en 1668 … XIXe siecle"    -> 0.71
#     es  "Carlos V llego en 1526 … 12 km"          -> 0.40
#     de  "Ludwig XIV kam 1868 mit 5 km"            -> 0.47
# The denser the numbers, the more certain the rejection. This is not new and is not
# caused by the DeepSeek move — the old English-only Gemini path used the same measure,
# so number-dense ENGLISH scenes have been silently falling back to raw digits all along.
#
# Scripts solves this per language by stripping the numeral vocabulary from both sides
# (`tts_number_clean.similarity_basis`), but only it/jp are registered, and building a
# numeral inventory for every language is a real piece of work. So where an inventory
# EXISTS we defer to it, and where it does not we measure the thing the guard is actually
# for, in a way that needs no vocabulary at all:
#
#   recall  — how much of the input's NON-CONVERTIBLE prose survived, in order. Number
#             words the model legitimately added cannot lower this, because they are not
#             in the skeleton being looked for. Rewritten or dropped prose does.
#   growth  — how much longer the output got, budgeted against the number of convertible
#             tokens. Recall alone cannot see an INSERTION (added sentences score 1.0),
#             and "You MUST NOT add new sentences" is half of what we are enforcing.
#
# Both must pass. Together they accept every clean above and still reject a hallucination.
_CONVERTIBLE_TOKEN_RE = re.compile(r"[0-9０-９£¥€$₩%°]")

#: Words a single convertible token may legitimately become. Generous on purpose: German
#: compounds a year into one word, French spends five on it ("mille huit cent soixante-huit"),
#: and "2°C" becomes "two degrees celsius". Its job is to catch a model that started
#: writing paragraphs, not to price expansions accurately.
_WORDS_PER_CONVERTIBLE = 8

PROSE_RECALL_THRESHOLD = 0.9


#: Unit abbreviations an expansion turns into words. An explicit set, NOT "any short
#: lowercase token" — that reading swallows `le`, `est`, `et`, `de`, `the`, `and`, which
#: would both hollow out the skeleton and inflate the growth budget by 8 words apiece.
_UNIT_TOKENS = {"km", "cm", "mm", "m", "kg", "g", "l", "ml", "ha", "ft", "mi",
                "h", "min", "sec", "s", "°c", "°f"}


def _is_convertible(token: str) -> bool:
    """True if the cleaner is licensed to rewrite this INPUT token — a digit, a currency
    or percent or degree symbol, a Roman numeral, or a unit abbreviation.

    ⚠ Roman numerals are matched only when UPPERCASE. Case-folding first looks harmless
    and is not: `de` → `D`, `Le` → `L`, `me` → `M` once a French/Spanish/Italian ordinal
    suffix is stripped, so the three commonest words in the corpus would read as regnal
    numerals. Distinct from `is_numberish`, which judges anchor eligibility on the OUTPUT
    side and so also matches spelled-out ordinals."""
    t = token.strip(".,;:!?\"'()«»").strip()
    if not t:
        return False
    if _CONVERTIBLE_TOKEN_RE.search(t):
        return True
    core = t.rstrip("eèᵉ.")
    if len(core) >= 2 and core.isupper() and _ROMAN_RE.match(core):
        return True                                      # XIXe siècle, XVIII., XIV
    if len(t) <= 6 and t.isupper() and _ROMAN_RE.match(t):
        return True                                      # V, XIV
    return t.lower() in _UNIT_TOKENS


def _fold(word: str) -> str:
    """Casing, accents and edge punctuation out — the comparison should measure whether
    the PROSE survived, not its orthography. A model that tidies an accent while spelling
    a number ("recorrio" → "recorrió") is doing its job, and must not cost us the clean:
    that single mismatch dropped a correct Spanish expansion to 0.667 in testing."""
    w = unicodedata.normalize("NFKD", word.lower())
    return "".join(c for c in w if not unicodedata.combining(c)).strip(".,;:!?\"'()«»—–-")


def _prose_survival(pre: str, cleaned: str) -> tuple[float, bool]:
    """``(recall, growth_ok)`` — see the block comment above."""
    pre_words = (pre or "").split()
    n_convertible = sum(1 for w in pre_words if _is_convertible(w))
    # Folding can empty a token entirely (a lone dash, stray quotes). Drop those from both
    # sides — an empty string matching an empty string is not evidence of anything.
    skeleton = [f for f in (_fold(w) for w in pre_words if not _is_convertible(w)) if f]
    out_words = [f for f in (_fold(w) for w in (cleaned or "").split()) if f]
    growth_ok = len(out_words) <= len(skeleton) + _WORDS_PER_CONVERTIBLE * n_convertible
    if not skeleton:
        return 1.0, growth_ok
    matched = sum(b.size for b in
                  difflib.SequenceMatcher(None, skeleton, out_words).get_matching_blocks())
    return matched / len(skeleton), growth_ok


def clean_accepted(lang: str, pre: str, cleaned: str) -> bool:
    """Is ``cleaned`` a trustworthy rendering of ``pre``? Uses the Scripts per-language
    numeral-stripped comparison where that language has an inventory registered, and the
    vocabulary-free recall+growth pair where it does not."""
    if _shared is not None and _shared.similarity_basis(lang, pre) is not None:
        return _shared.clean_similarity(lang, pre, cleaned) >= NUMBER_CLEAN_THRESHOLD
    recall, growth_ok = _prose_survival(pre, cleaned)
    return growth_ok and recall >= PROSE_RECALL_THRESHOLD


_warned: set[str] = set()


def _warn_once(key: str, msg: str) -> None:
    if key not in _warned:
        _warned.add(key)
        print(f"[audio_core] {msg}", flush=True)


def clean_lang_code(trip_id: str) -> str | None:
    """Trip id → number-clean language key, or None if the language has no prompt."""
    return _LANG_CODES.get(language_of(trip_id))


def cleaner_status() -> dict:
    """Startup/health probe — see `main._startup`. `ok=False` means every clean will be
    skipped and flagged `edit_required`, which is visible but degraded; the fix is a
    Scripts checkout at REVIEW_APP_SCRIPTS_ROOT with `Audio Generation/` present."""
    return {
        "ok": _shared is not None,
        "error": _CLEANER_ERROR,
        "model": getattr(_shared, "DEEPSEEK_MODEL", None),
        "api_key_set": bool(getattr(_shared, "_API_KEY", "")),
        "version": CLEANER_VERSION,
        "languages": sorted(set(_LANG_CODES.values())),
    }


_URL_LINE_RE = re.compile(r"^\s*https?://\S+\s*$", re.IGNORECASE)


def strip_url_lines(text: str) -> str:
    if not text:
        return text
    return "\n".join(
        ln for ln in text.split("\n") if not _URL_LINE_RE.match(ln)
    ).strip()


def clean_text(text: str, strict: bool = False, overrides=None, *,
               lang: str = "en") -> str:
    """Spell numbers/dates/regnal numerals/units for TTS in ``lang``, via the shared
    Scripts prompt for that language.

    ⚠ RAISES on transport failure — it does NOT hand back the input. The old Gemini port
    returned ``text`` on any API error, which then scored a perfect 1.0 against itself and
    was reported to the caller as a SUCCESSFUL clean: the ``used_fallback`` → ``edit_required``
    routing the docstring promised could only ever fire on a similarity miss, never on an
    outage. ``validate_and_clean`` is the only caller and turns the raise into that flag.

    ``overrides`` only reaches the model for English. Every non-English prompt is
    target-language-only by design on the Scripts side (no English instruction leakage),
    and `prompt_rule` is English prose; the load-bearing half — substituting the agreed
    spoken forms INTO the text — has already happened in `apply_overrides`. fr/de/it/es
    additionally have no `{extra}` slot in their templates at all.
    """
    if _shared is None or _build_prompt is None:
        raise RuntimeError(f"shared number-cleaner unavailable ({_CLEANER_ERROR})")
    extra = prompt_rule(overrides) if lang == "en" else ""
    return _shared.complete_prompt(
        _build_prompt(lang, text, strict=strict, extra=extra), lang=lang)


def validate_and_clean(text: str, doc_id: str, scene_index) -> tuple[str, bool]:
    """Clean with a similarity guard. Returns ``(cleaned, used_fallback)``.

    ``used_fallback=True`` means "this text was NOT cleaned" — the splice engine routes
    that to ``edit_required`` rather than aligning on drifted tokens (plan S2 / C1), so
    every failure mode below is visible to the reviewer instead of silently voiced.

    The language comes from ``doc_id`` (the trip id) via `clean_lang_code`; there is no
    default. An unmapped language disables cleaning rather than falling back to English —
    English-by-default on a French trip is the exact defect this function was rewritten
    to remove."""
    lang = clean_lang_code(doc_id)
    overrides = load_overrides(doc_id)
    pre = apply_overrides(text, overrides)
    if not (pre or "").strip():
        return pre, False

    if _shared is None:
        _warn_once("unavailable",
                   f"WARN shared number-cleaner unavailable ({_CLEANER_ERROR}) — text is "
                   f"voiced as written and flagged edit_required. Check that "
                   f"'Audio Generation/' exists under REVIEW_APP_SCRIPTS_ROOT.")
        return pre, True
    if lang is None:
        _warn_once(f"lang:{language_of(doc_id)}",
                   f"WARN no number-clean prompt for language {language_of(doc_id)!r} "
                   f"({doc_id}) — add it to audio_core._LANG_CODES. Text voiced as written.")
        return pre, True
    if lang in _NO_CLEAN_LANGS:
        return pre, False       # by design — see _NO_CLEAN_LANGS

    # Korean runs its own harness, which applies its own overrides → RAW text, not `pre`.
    harness = _own_harness(lang)
    if harness is not None:
        try:
            cleaned = harness.clean_field(text, doc_id)
        except Exception as e:  # noqa: BLE001
            _warn_once(f"harness:{lang}", f"WARN {lang} number-clean failed: {e}")
            return pre, True
        # Both harnesses fall back to their input silently; leftover digits say they did.
        return cleaned, bool(_LEFTOVER_NUMERIC_RE.search(cleaned or ""))

    # Nothing convertible → return unchanged WITHOUT an API call. Not just a saving: a
    # cleaner handed prose with no numbers in it can only do harm, and was measured doing
    # exactly that (it deleted a particle from a Japanese kana line, 2026-08-04).
    #
    # ⚠ NOT `_shared.needs_number_clean` here. That gate counts "a Latin token of 2+
    # letters" as convertible — right for the CJK/Korean text it was written for, where
    # stray Latin IS the work, but on a Latin-script language EVERY word matches and the
    # gate never fires. `_is_convertible` is the Latin-script reading of the same
    # question, and is the predicate the skeleton is already built from.
    if not any(_is_convertible(w) for w in pre.split()):
        return pre, False

    for attempt in range(NUMBER_CLEAN_MAX_RETRIES):
        try:
            cleaned = clean_text(pre, strict=attempt > 0, overrides=overrides, lang=lang)
        except Exception as e:  # noqa: BLE001
            _warn_once(f"api:{lang}", f"WARN number-clean API failure ({lang}): {e}")
            continue
        if clean_accepted(lang, pre, cleaned):
            return cleaned, False
    return pre, True


# --------------------------------------------------------------------------- #
# ElevenLabs
# --------------------------------------------------------------------------- #
def _headers() -> dict:
    return {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}


def generate_audio(text: str, voice_id: str, voice_settings: dict,
                   model_id: str = EL_MODEL) -> bytes:
    """Plain TTS → mp3 bytes (whole-block + fallback clips). Raises on non-200."""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    body = {"text": text, "model_id": model_id, "voice_settings": voice_settings}
    r = requests.post(url, json=body, headers=_headers(), timeout=180)
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:300]}")
    return r.content


# A faithful alignment that places the FIRST word this deep into the clip means the
# model voiced something before the requested text — the eleven_multilingual_v2
# previous_text leak (docs/splice-end-cutoff-analysis.md; heard in the field as the
# prior sentence's tail playing before the candidate). Normal TTS lead silence is
# well under this.
_LEAK_RETRY_LEAD_S = 0.4


def generate_with_timestamps(text: str, voice_id: str, voice_settings: dict,
                             previous_text: str | None = None,
                             next_text: str | None = None,
                             model_id: str = EL_MODEL) -> tuple[bytes, list[dict]]:
    """TTS with character alignment → (mp3 bytes, word list) — see
    ``_generate_with_timestamps``. When ``previous_text`` was sent and the first
    aligned word starts suspiciously late (the v2 context-leak: the model voiced a
    tail of the context before the phrase), re-request ONCE without ``previous_text``
    — a clause-length phrase has acceptable standalone prosody (the JP/v3 path omits
    context entirely), and clean audio beats a leaky lead."""
    mp3, words = _generate_with_timestamps(text, voice_id, voice_settings,
                                           previous_text, next_text, model_id)
    if previous_text and words and float(words[0]["start"] or 0.0) > _LEAK_RETRY_LEAD_S:
        mp3, words = _generate_with_timestamps(text, voice_id, voice_settings,
                                               None, next_text, model_id)
    return mp3, words


def _generate_with_timestamps(text: str, voice_id: str, voice_settings: dict,
                              previous_text: str | None = None,
                              next_text: str | None = None,
                              model_id: str = EL_MODEL) -> tuple[bytes, list[dict]]:
    """TTS with character alignment → (mp3 bytes, word list).

    Words are aggregated from the per-character alignment by splitting on spaces;
    word.start = first char start, word.end = last char end."""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
    body = {"text": text, "model_id": model_id, "voice_settings": voice_settings}
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
    """Per-char alignment → word list. Each word also carries ``letter_end`` — the end
    of its last ALPHANUMERIC char (kana/hanzi count). The trailing punctuation char
    absorbs the clip's trailing silence (a final '.' routinely "ends" at the very end
    of the clip), so ``end`` is useless as a word boundary there; ``letter_end`` lands
    within ~50 ms of the audible word end and is the floor the candidate trailing trim
    must never cut before."""
    words: list[dict] = []
    cur, cur_start, cur_end, cur_letter_end = "", None, None, None

    def _flush():
        nonlocal cur, cur_start, cur_end, cur_letter_end
        if cur:
            words.append({"word": cur, "start": cur_start, "end": cur_end,
                          "letter_end": cur_letter_end})
        cur, cur_start, cur_end, cur_letter_end = "", None, None, None

    for ch, st, en in zip(chars, starts, ends):
        if ch.isspace():
            _flush()
            continue
        if not cur:
            cur_start = st
        cur += ch
        cur_end = en
        if ch.isalnum():
            cur_letter_end = en
    _flush()
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
    because the number speller may re-render it differently than Whisper heard it."""
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
