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


def v3_voice_settings(vs: dict) -> dict:
    """A v2 voice's settings made acceptable to eleven_v3. v3 ignores `speed` and
    rejects `style`; `stability` must be exactly 0.0 / 0.5 / 1.0 (the EN/EU voices
    carry 0.75, which v3 4xxes). Applied at the two API call sites whenever the
    request is v3, so a per-clip or session-wide model override can send any
    registry voice to v3 without a bad-settings rejection."""
    out = {k: v for k, v in vs.items() if k not in ("speed", "style")}
    st = out.get("stability")
    if st is not None and st not in (0.0, 0.5, 1.0):
        out["stability"] = 0.5
    return out


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
    import mandarin_number_clean as _zh_clean     # zh: the pipeline's own DeepSeek harness
    from gemini_number_clean_prompts import build_prompt as _build_prompt
    _CLEANER_ERROR: str | None = None
except Exception as _e:  # noqa: BLE001
    _shared = _ko_clean = _zh_clean = _build_prompt = None  # type: ignore[assignment]
    _CLEANER_ERROR = f"{type(_e).__name__}: {_e}"

#: Accepted-clean bar for the languages where Scripts HAS a numeral inventory registered
#: (`tts_number_clean.similarity_basis`), i.e. where a legitimate expansion is stripped from
#: both sides and so scores ~1.0. Languages without one are judged by `_prose_survival`
#: instead; see the guard block below for why the word ratio it would otherwise degrade to
#: cannot be used.
#: ⚠ AS OF 2026-08-09 EVERY PRODUCT LANGUAGE HAS ONE — en/fr/de/it/es/zh/jp/ko — so against
#: an up-to-date Scripts checkout `_prose_survival` no longer runs at all. It STAYS: it is
#: what a lagging laptop checkout falls back to (see `_scripts_inventory_basis`), and it is
#: the only arm that needs no vocabulary.
#: ⚠ NONE OF THIS WAS COSMETIC ON THE SCRIPTS SIDE. Without an inventory the guard rejected
#: CORRECT expansions three times and the ORIGINAL text — raw digits — went to ElevenLabs
#: while the log said OK. Measured there: ES 67 of 506 clips (13%), then ZH 265 clips over
#: 48 trips at **100%** (a space-free script splits into ONE token, so every change scores
#: exactly 0.00) and KO 141 over 22 (49%); EN/FR/DE were re-measured stratified by number
#: density and their dense band — every quiz question and answer option — ran 30%/48%/24%.
#: Nothing here needed changing for any of it, because the arm is chosen by feature-detecting
#: the basis — but the cache key did (see CLEANER_VERSION).
NUMBER_CLEAN_THRESHOLD = 0.8
NUMBER_CLEAN_MAX_RETRIES = 3

#: Bumped whenever the cleaner's OUTPUT for identical input could change — backend swap,
#: prompt-surface change, language dispatch. `sessions._cleaned_orig` mixes this into its
#: cache key so a session seeded under an older cleaner RE-CLEANS instead of diffing the
#: reviewer's new text against a stale (here: English-numbered) baseline.
#: ⚠ v5 changes output for real, in four ways beyond the new inventories: Korean years and
#: Latin acronyms are now pre-expanded on the generic `build_prompt` path (they were on the
#: `korean_number_clean.clean_field` path only); `clean_similarity` now pre-expands the
#: ORIGINAL before comparing, so the model is not charged for work done deterministically
#: ahead of it; `korean_number_clean._YEAR_RE` was FIXED — it carried a literal backspace
#: byte where `\b` was meant, so `sino_year` had never fired on any path; and THE PROMPT
#: SURFACE ITSELF CHANGED — EN gained an ORDINAL rule (it had none, and ordinals were 37 of
#: the 66 clips still voicing digits after the guard was fixed: `the 2nd Earl`, `the 33rd
#: Regiment`, `on the 10th of June`, `the 15th century`), ZH gained decade / Japanese-era /
#: route-number rules, KO gained short-decimal examples, and all three gained an explicit
#: completeness instruction. Measured on the 66: 61 now convert fully, 0 guard rejections.
CLEANER_VERSION = "5-all-languages-numeral-basis"

#: `language_of()` output → the `gemini_number_clean_prompts` language key.
#: ⚠ ENUMERATED SET: every value `language_of` can return needs an entry here. An absent
#: one does NOT fall back to English (that is the bug this whole change exists to kill) —
#: it disables cleaning for that language, loudly. `test_number_clean_language.py` pins
#: the two sets equal.
_LANG_CODES = {
    "English": "en", "French": "fr", "German": "de", "Italian": "it",
    "Spanish": "es", "Mandarin": "zh", "Japanese": "jp", "Korean": "ko",
}

#: Korean and Mandarin clean through their OWN Scripts harnesses rather than the generic
#: build_prompt path:
#:   ko — deterministic pre-expansion (`sino_year` — the model dropped the 百 from 1963;
#:        `expand_latin_acronyms` — it read KBS as BTS and the guard passed it) plus a
#:        Hangul-numeral-aware similarity check. Verified live 2026-08-06:
#:        「1963년…21명」 → 「천구백육십삼 년…스물한 명」.
#:   zh — `mandarin_number_clean.clean_field`, EXACTLY what the three `_ZH` voice
#:        templates call, so an app regenerate voices the same string a master was voiced
#:        from. Carries its own hanzi-numeral similarity guard and the deterministic
#:        year-gloss dedup (the 「一九九九年（一九九九年）」 fix, 2026-08-07).
#: Both apply their own pronunciation overrides, so they get the RAW text, not `pre`.
#: jp stays on the generic path: the six JP templates run `build_prompt("jp", …)` through
#: their own validate_and_clean — the generic path here IS that, and `tts_number_clean`
#: registers a jp numeral inventory so `clean_accepted` defers to `clean_similarity`.
def _own_harness(lang: str):
    return {"ko": _ko_clean, "zh": _zh_clean}.get(lang)


#: zh/jp ARE number-cleaned here since 2026-08-07 (the CJK voicing-parity fix — Scripts
#: `docs/plans/2026-08-07-review-app-cjk-voicing-parity.md`). The pipeline always cleaned
#: all three CJK languages before TTS (zh `mandarin_number_clean`, jp `build_prompt("jp",…)`
#: on the kana line, ko `korean_number_clean`), so until this an app regenerate voiced RAW
#: text — 「634めーとる」 read as digits — while the master said the kana expansion, and a
#: `_ZH` regenerate also dropped the trip's pronunciation overrides (`台北101` un-pinned),
#: because `apply_overrides` lives only inside `validate_and_clean`.
#:
#: The constraint that shaped the fix: cleaning cannot move upstream of selection/diff/
#: alignment — `cjk_splice` char-diffs OLD→NEW in RAW space and reads cut times from the
#: forced aligner against audio that SAYS the cleaned string. So the surgical path is
#: DISABLED for any field whose OLD or NEW spoken line has convertible content
#: (`cjk_convertible` below; sessions.regenerate bails those to whole-regen, which cleans).
#: For such fields the aligner was already scoring raw 「634」 against kana audio — bailing
#: is an honesty improvement, not a capability loss.
#: What none of this may EVER do is what it did until 2026-08-06: fall through to the
#: ENGLISH prompt.
_NO_CLEAN_LANGS: set[str] = set()


#: Deterministic "could the cleaner change this CJK spoken line?" gate — digits (incl.
#: fullwidth), currency/percent/degree symbols, or ANY Latin (Roman numerals, unit
#: abbreviations, acronyms the harnesses expand). Mirrors the pipeline gates
#: (`mandarin_number_clean.DIGIT_RE`, `tts_number_clean.needs_number_clean`) but is local
#: and dependency-free: it decides whether the SURGICAL path is safe, so it must give the
#: same answer whether or not the Scripts checkout is present or current.
_CJK_CONVERTIBLE_RE = re.compile(r"[0-9０-９A-Za-z$£¥€₩%°]")


def cjk_convertible(text: str) -> bool:
    """True when a zh/jp spoken line contains content voicing-time cleaning would rewrite
    — i.e. raw text and cleaned text differ, so raw-space char-diff/alignment against the
    (cleaned-voiced) audio cannot be trusted and whole-regen is the only honest path."""
    return bool(_CJK_CONVERTIBLE_RE.search(text or ""))


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
# (`tts_number_clean.similarity_basis`). As of 2026-08-09 every product language is
# registered, so all four examples above are now handled THERE and this arm no longer runs
# against an up-to-date checkout — but the registration is a fact about the OTHER repo, and
# the live laptop is a separate checkout that has sat 30 commits behind. So where an
# inventory exists we defer to it, and where it does not — a lagging checkout, or a
# language added to `_LANG_CODES` before Scripts has a stripper for it — we measure the
# thing the guard is actually for, in a way that needs no vocabulary at all:
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

#: The French FIRST-ordinal regnal forms, matched as WHOLE TOKENS.
#: `Albert Ier` → `Albert premier` costs a skeleton word otherwise, because `Ier` survives
#: neither the Roman-numeral test (not all-uppercase) nor the ordinal-suffix strip (`r` is
#: not in it). Measured on the live corpus: four occurrences, all on the Monaco trips, all
#: still passing but at recall 0.944–0.987 — a shorter sentence carrying `Ier` would drop
#: under the 0.9 bar and lose a correct clean.
#: ⚠ Whole-token equality, NOT a suffix rule. Extending the strip set to `r` would make
#: `LIVRE`→`LIV` convertible, and any "roman core + lowercase suffix" rule reads `Le`→`L`
#: and `de`→`D` — the Romance-article trap this function already guards against. `1er`/`1re`
#: need no entry: they carry a digit and are caught above.
_FRENCH_FIRST_ORDINALS = {"Ier", "Iers", "Ière", "Ières", "Iere"}


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
    if t in _FRENCH_FIRST_ORDINALS:
        return True                                      # François Ier, Albert Ier
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
    # ⚠ `autojunk=False`, for the reason `cjk_splice` already gives: difflib discards any
    # element occurring in more than 1% of a 200+ element sequence, and in a long scene the
    # commonest WORDS ("the", "de", "der") clear that easily — so recall is depressed on
    # exactly the longest clips. Measured on the Scripts side of the same defect (character
    # level, `Baden-Baden_EN` s10): a fully correct clean scored 0.703 with the default and
    # 0.985 without it. It can only ever depress a score, so removing it cannot admit a
    # hallucination.
    matched = sum(b.size for b in
                  difflib.SequenceMatcher(None, skeleton, out_words,
                                          autojunk=False).get_matching_blocks())
    return matched / len(skeleton), growth_ok


def _scripts_inventory_basis(lang: str, text: str):
    """`tts_number_clean.similarity_basis(lang, text)`, or None if this checkout predates
    it.

    ⚠ Feature-DETECTED, never assumed. The live laptop is a separate checkout of the
    Scripts repo that can sit behind this one — on the day this shipped it was 30 commits
    back, with `build_prompt` and all nine language prompts present but `similarity_basis`
    and `clean_similarity` not yet written. A bare attribute access here would have raised
    AttributeError inside every regenerate on the live host. Same failure class as the
    jieba (07-08) and opencc (07-29) outages: an optional Scripts capability treated as
    guaranteed. `_prose_survival` needs no vocabulary and covers every language, so the
    inventory is an upgrade, not a dependency."""
    fn = getattr(_shared, "similarity_basis", None)
    return fn(lang, text) if fn else None


def clean_accepted(lang: str, pre: str, cleaned: str) -> bool:
    """Is ``cleaned`` a trustworthy rendering of ``pre``? Uses the Scripts per-language
    numeral-stripped comparison where that language has an inventory registered AND this
    checkout provides it, and the vocabulary-free recall+growth pair otherwise."""
    if not (cleaned or "").strip():
        # An empty render is never a trustworthy clean — and neither comparison arm can
        # say so on its own when ``pre`` is ALL-convertible (a year-only quiz option like
        # "1868."): the prose skeleton is empty so recall is vacuously 1.0 with an empty
        # output inside budget, and the Scripts strippers reduce both sides to "" (score
        # 1.0 — the numerals-strip blind spot the zh harness's own docs describe).
        # DeepSeek HAS returned empty content in production (whole token budget spent on
        # reasoning), so this input is real, not hypothetical.
        return False
    # Detect the attribute we CALL, not just its sibling: `similarity_basis` and
    # `clean_similarity` shipped together, but a lagging Scripts checkout owes us neither
    # (see _scripts_inventory_basis for the outage this guard exists to prevent).
    cmp_fn = getattr(_shared, "clean_similarity", None) if _shared is not None else None
    if cmp_fn is not None and _scripts_inventory_basis(lang, pre) is not None:
        return cmp_fn(lang, pre, cleaned) >= NUMBER_CLEAN_THRESHOLD
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
    codes = set(_LANG_CODES.values())
    return {
        "ok": _shared is not None,
        "error": _CLEANER_ERROR,
        "model": getattr(_shared, "DEEPSEEK_MODEL", None),
        "api_key_set": bool(getattr(_shared, "_API_KEY", "")),
        "version": CLEANER_VERSION,
        # Split, so the startup line cannot tell an operator that zh/jp are being cleaned
        # when they are deliberately passed through (see _NO_CLEAN_LANGS).
        "languages": sorted(codes - _NO_CLEAN_LANGS),
        "not_cleaned": sorted(codes & _NO_CLEAN_LANGS),
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
        return pre, False       # empty today — kept as the documented off-switch shape

    # ko/zh run their own harnesses, which apply their own overrides → RAW text, not `pre`.
    harness = _own_harness(lang)
    if harness is not None:
        try:
            cleaned = harness.clean_field(text, doc_id)
        except (Exception, SystemExit) as e:  # noqa: BLE001
            # SystemExit too: the harness is a CLI-shaped Scripts module whose missing-
            # API-key path is `raise SystemExit` — a BaseException that `except Exception`
            # lets straight through. Exactly the degraded state _startup warns about must
            # fall back like every other language, not crash the request.
            _warn_once(f"harness:{lang}", f"WARN {lang} number-clean failed: {e}")
            return pre, True
        # An empty answer is a FAILED clean, not a clean. The harness's own guard scores
        # "" a perfect match when the input strips to pure numerals (a year-only quiz
        # option), so it can hand one back as accepted — never voice it.
        if not (cleaned or "").strip():
            return pre, True
        # Both harnesses fall back to their input silently; leftover digits say they did.
        return cleaned, bool(_LEFTOVER_NUMERIC_RE.search(cleaned))

    # Nothing convertible → return unchanged WITHOUT an API call. Not just a saving: a
    # cleaner handed prose with no numbers in it can only do harm, and was measured doing
    # exactly that (it deleted a particle from a Japanese kana line, 2026-08-04).
    #
    # ⚠ `_shared.needs_number_clean` ONLY for jp. That gate counts "a Latin token of 2+
    # letters" as convertible — right for the CJK/Korean text it was written for, where
    # stray Latin IS the work, but on a Latin-script language EVERY word matches and the
    # gate never fires. `_is_convertible` is the Latin-script reading of the same
    # question, and is the predicate the skeleton is already built from. (Feature-detected
    # like `similarity_basis`: a lagging Scripts checkout owes us neither.)
    nnc = getattr(_shared, "needs_number_clean", None)
    if lang == "jp" and nnc is not None:
        if not nnc(pre):
            return pre, False
    elif not any(_is_convertible(w) for w in pre.split()):
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
    if model_id == "eleven_v3":
        voice_settings = v3_voice_settings(voice_settings)
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
    if model_id == "eleven_v3":
        # v3 hard-rejects previous_text/next_text on /with-timestamps
        # ("unsupported_model", live 400 on the first spliced V3 fix-pronunciation,
        # 2026-08-10). Drop them HERE, at the choke point, so no caller has to know:
        # cjk_splice guards its own two call sites, but the English engine
        # (plan_segment) was v2-only until the per-clip V3 override and predictably
        # didn't. Nulling before the first call also keeps the leak-retry below inert.
        previous_text = next_text = None
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
    if model_id == "eleven_v3":
        voice_settings = v3_voice_settings(voice_settings)
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
