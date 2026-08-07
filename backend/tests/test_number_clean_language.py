"""The number cleaner speaks the TRIP'S language — and the guard keeps correct work.

Two defects are pinned here, both reported/measured 2026-08-06:

  1. `validate_and_clean` applied an English-only prompt to every language, so a French
     trip was voiced "Louis the Fourteenth ... eighteen sixty eight" while the pipeline's
     own master said "Louis quatorze ... mille huit cent soixante-huit". 167 of the 357
     queued trips were exposed (ES/FR/KO/DE/IT).
  2. The accept/reject guard was a plain word ratio, which scores a PERFECT expansion at
     0.40–0.71 against a bar of 0.80 — so even English number-dense scenes silently fell
     back to raw digits. The denser the numbers, the more certain the rejection.

No network: the transport is monkeypatched everywhere. What is being tested is the
dispatch and the guard, not DeepSeek.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import audio_core  # noqa: E402

pytestmark = pytest.mark.skipif(
    audio_core._shared is None,
    reason=f"shared Scripts cleaner unavailable: {audio_core._CLEANER_ERROR}")


# --------------------------------------------------------------------------- #
# Language dispatch
# --------------------------------------------------------------------------- #
def test_every_language_has_a_prompt():
    """⚠ ENUMERATED SET (the recurring bug class in this app): adding a language to
    `language_of` without adding it here is what produced the French bug in the first
    place. Both directions are checked — a code that `build_prompt` would reject is just
    as broken as a missing one."""
    from gemini_number_clean_prompts import build_prompt

    languages = {audio_core.language_of(t) for t in
                 ("X_EN", "X_FR", "X_DE", "X_IT", "X_ES", "X_ZH", "X_JP", "X_KO", "X")}
    assert languages == set(audio_core._LANG_CODES), (
        "language_of() and _LANG_CODES have drifted apart")
    for code in set(audio_core._LANG_CODES.values()):
        build_prompt(code, "1868", strict=False)     # raises on an unsupported key


@pytest.mark.parametrize("trip_id,code", [
    ("Monaco1_FR", "fr"), ("Monaco1_Beg_FR", "fr"), ("Toledo_A12_ES", "es"),
    ("Radda_SanGusme_A12_IT", "it"), ("Berlin1_B1_DE", "de"),
    ("York_I_B2_EN", "en"), ("Busan1_UNMemorial_TPK2_KO", "ko"),
    ("Taipei101_HSK3_ZH", "zh"), ("Tokyo_03_Beg_N4_JP", "jp"),
])
def test_clean_lang_code(trip_id, code):
    assert audio_core.clean_lang_code(trip_id) == code


def test_french_trip_gets_the_french_prompt(monkeypatch):
    """The regression itself: a `_FR` trip must not be handed English rules."""
    seen = {}

    def fake(prompt, *, lang=None):
        seen["prompt"], seen["lang"] = prompt, lang
        return "Louis quatorze en mille six cent soixante-huit"

    monkeypatch.setattr(audio_core._shared, "complete_prompt", fake)
    audio_core.validate_and_clean("Louis XIV en 1668", "Monaco1_FR", 0)

    assert seen["lang"] == "fr"
    assert "français" in seen["prompt"], "not the French prompt"
    # The exact English rules that were reaching French trips.
    assert "King Charles the First" not in seen["prompt"]
    assert "eighteen sixty eight" not in seen["prompt"]


def test_english_trip_still_gets_english(monkeypatch):
    seen = {}
    monkeypatch.setattr(audio_core._shared, "complete_prompt",
                        lambda p, *, lang=None: seen.update(p=p, lang=lang) or "x")
    audio_core.validate_and_clean("Charles I in 1642", "York_B1_EN", 0)
    assert seen["lang"] == "en"
    assert "King Charles the First" in seen["p"]


def test_zh_uses_the_mandarin_harness(monkeypatch):
    """zh must reach `mandarin_number_clean.clean_field` — EXACTLY what the pipeline's
    `_ZH` voice templates call, so a regenerate voices the same string the master was
    voiced from (CJK voicing-parity fix, 2026-08-07). The harness applies its own
    overrides, so it gets the RAW text and the doc_id."""
    calls = {}
    monkeypatch.setattr(audio_core._zh_clean, "clean_field",
                        lambda t, d=None, **k: calls.update(text=t, doc=d)
                        or "台北一〇一大楼一九九九年开始建造。")
    out, fb = audio_core.validate_and_clean("台北101大楼1999年开始建造。", "Taipei101_HSK3_ZH", 0)
    assert calls == {"text": "台北101大楼1999年开始建造。", "doc": "Taipei101_HSK3_ZH"}
    assert (out, fb) == ("台北一〇一大楼一九九九年开始建造。", False)


def test_jp_gets_the_japanese_prompt(monkeypatch):
    """jp goes through the generic path — the same `build_prompt("jp", …)` the six JP
    templates run — never the English prompt, and never raw passthrough."""
    seen = {}

    def fake(prompt, *, lang=None):
        seen["prompt"], seen["lang"] = prompt, lang
        return "たかさはろっぴゃくさんじゅうよんめーとるもあります"

    monkeypatch.setattr(audio_core._shared, "complete_prompt", fake)
    out, fb = audio_core.validate_and_clean(
        "たかさは634めーとるもあります", "Tokyo_03_Beg_N4_JP", 0)
    assert seen["lang"] == "jp"
    assert "テキスト" in seen["prompt"], "not the Japanese prompt"
    assert "King Charles the First" not in seen["prompt"]
    assert (out, fb) == ("たかさはろっぴゃくさんじゅうよんめーとるもあります", False)


def test_zh_harness_failure_reports_fallback(monkeypatch):
    """Same degraded shape as Korean: a SystemExit (missing key) must fall back with
    the flag, not crash the request or claim a clean."""
    def die(*a, **k):
        raise SystemExit("Missing DeepSeek_API_KEY in .env")

    monkeypatch.setattr(audio_core._zh_clean, "clean_field", die)
    out, fb = audio_core.validate_and_clean("1999年开始", "Taipei101_HSK3_ZH", 0)
    assert (out, fb) == ("1999年开始", True)


def test_korean_uses_its_own_harness(monkeypatch):
    """Korean must reach `korean_number_clean`, which pre-expands years and acronyms
    deterministically — the model dropped a 百 from 1963 and read KBS as BTS."""
    calls = {}
    monkeypatch.setattr(audio_core._ko_clean, "clean_field",
                        lambda t, d=None, **k: calls.update(text=t, doc=d) or "천구백육십삼 년")
    out, fb = audio_core.validate_and_clean("1963년에", "Busan1_TPK2_KO", 0)
    assert calls["doc"] == "Busan1_TPK2_KO"
    assert (out, fb) == ("천구백육십삼 년", False)


def test_korean_leftover_digits_report_fallback(monkeypatch):
    """`clean_field` gives up by returning its input. Digits still in the output are how
    we tell — the flag is what routes the clip to `edit_required`."""
    monkeypatch.setattr(audio_core._ko_clean, "clean_field", lambda t, d=None, **k: "1963년에")
    _, fb = audio_core.validate_and_clean("1963년에", "Busan1_TPK2_KO", 0)
    assert fb is True


def test_korean_missing_key_degrades_instead_of_crashing(monkeypatch):
    """`korean_number_clean` is a CLI-shaped Scripts module: its missing-DeepSeek-key path
    is `raise SystemExit`, a BaseException that `except Exception` lets straight through.
    On a host in the key-missing DEGRADED state (the one _startup warns about) every
    Korean clean would have crashed the request instead of falling back."""
    def die(*a, **k):
        raise SystemExit("Missing DeepSeek_API_KEY / DEEPSEEK_API_KEY in .env")

    monkeypatch.setattr(audio_core._ko_clean, "clean_field", die)
    out, fb = audio_core.validate_and_clean("1963년", "Busan1_TPK2_KO", 0)
    assert (out, fb) == ("1963년", True)


def test_korean_empty_clean_reports_fallback(monkeypatch):
    """The harness's own guard scores "" a perfect match when the input strips to pure
    numerals (a year-only quiz option), so it CAN hand back empty as accepted — and the
    leftover-digit probe sees nothing numeric in "". Must fall back, never voice it."""
    monkeypatch.setattr(audio_core._ko_clean, "clean_field", lambda t, d=None, **k: "")
    out, fb = audio_core.validate_and_clean("1963년", "Busan1_TPK2_KO", 0)
    assert (out, fb) == ("1963년", True)


# --------------------------------------------------------------------------- #
# The guard
# --------------------------------------------------------------------------- #
#: Real DeepSeek output for real prompts, captured 2026-08-06. Every one is correct and
#: every one was REJECTED by the old word ratio (its score is in the comment).
CORRECT_CLEANS = [
    ("en", "King Charles I fled in 1642, and the walls run for 5 km.",
           "King Charles the First fled in sixteen forty two, and the walls run for "
           "five kilometres."),                                              # was 0.62
    ("fr", "Le château fut reconstruit sous Louis XIV en 1668, puis agrandi au XIXe siècle.",
           "Le château fut reconstruit sous Louis quatorze en mille six cent soixante-huit, "
           "puis agrandi au dix-neuvième siècle."),                          # was 0.71
    ("es", "Carlos V llegó en 1526 y recorrió 12 km.",
           "Carlos quinto llegó en mil quinientos veintiséis y recorrió doce kilómetros."),
                                                                             # was 0.40
    ("de", "Ludwig XIV kam 1868 mit 5 km Straße.",
           "Ludwig der Vierzehnte kam achtzehnhundertachtundsechzig mit fünf Kilometer "
           "Straße."),                                                       # was 0.47
]


@pytest.mark.parametrize("lang,pre,cleaned", CORRECT_CLEANS)
def test_correct_expansions_are_accepted(lang, pre, cleaned):
    assert audio_core.clean_accepted(lang, pre, cleaned), (
        f"correct {lang} clean rejected — the guard is throwing away real work")


def test_accent_repair_does_not_cost_a_clean():
    """Measured: one "recorrio"→"recorrió" mismatch dropped a correct Spanish clean to
    0.667. Orthography is not what the guard is for."""
    assert audio_core.clean_accepted(
        "es", "Carlos V llego en 1526 y recorrio 12 km.",
        "Carlos quinto llegó en mil quinientos veintiséis y recorrió doce kilómetros.")


@pytest.mark.parametrize("name,pre,cleaned", [
    ("dropped clause", "King Charles I fled in 1642, and the walls run for 5 km.",
                       "King Charles the First fled in sixteen forty two."),
    ("rewrote prose",  "King Charles I fled in 1642, and the walls run for 5 km.",
                       "The monarch departed in sixteen forty two and the fortifications "
                       "extend five kilometres."),
    ("answered in the wrong language",
                       "Le château fut reconstruit sous Louis XIV en 1668.",
                       "The castle was rebuilt under Louis the Fourteenth in sixteen "
                       "sixty eight."),
    ("added a paragraph", "It was built in 1642.",
                       "It was built in sixteen forty two. The castle is a magnificent "
                       "example of medieval military architecture, and visitors today can "
                       "walk the full circuit of its walls, admiring the views across the "
                       "valley and the rooftops of the old town beyond."),
    ("empty", "It was built in 1642.", ""),
    # ⚠ All-convertible input is the empty guard's ONLY real job: with no prose skeleton,
    # recall is vacuously 1.0 and an empty output is inside the growth budget — and the
    # Scripts strippers reduce both sides to "" (score 1.0) the same way. A year-only quiz
    # option + DeepSeek returning empty content (seen in production) would otherwise be
    # accepted and voiced as silence.
    ("empty on an all-numeric field", "1868.", ""),
])
def test_hallucinations_are_rejected(name, pre, cleaned):
    assert not audio_core.clean_accepted("en", pre, cleaned), f"guard let through: {name}"


@pytest.mark.parametrize("token", [
    # ⚠ Case-folding Roman numerals swallows the commonest words in the corpus: strip a
    # French/Spanish/Italian ordinal suffix from `de`/`Le`/`me` and you are left with
    # D/L/M. That would hollow out the skeleton AND hand the growth budget 8 free words
    # per article — i.e. quietly disable both arms of the guard on Romance languages.
    "de", "Le", "le", "me", "est", "et", "the", "and", "la", "di", "der", "el",
])
def test_common_words_are_not_read_as_numerals(token):
    assert not audio_core._is_convertible(token)


@pytest.mark.parametrize("token", ["1868", "XIV", "V", "XIXe", "XVIII.", "£5", "50%", "2°C",
                                   "km", "kg", "Ier", "Ière", "1er"])
def test_convertible_tokens(token):
    assert audio_core._is_convertible(token)


def test_french_first_ordinal_does_not_cost_a_clean():
    """`Albert Ier` → `Albert premier`. Four real occurrences on the live Monaco trips; they
    passed at recall 0.944–0.987, i.e. one skeleton word down, so a SHORTER sentence carrying
    `Ier` would have dropped under the bar. Whole-token match — `LIVRE` and `Le` must not
    become convertible on the way (the Romance-article trap)."""
    assert audio_core._is_convertible("Ier")
    for safe in ("LIVRE", "Le", "de", "Iberia", "hier", "premier", "ier"):
        assert not audio_core._is_convertible(safe), safe
    pre = "Le prince Albert Ier a construit le musée en 1910."
    cleaned = "Le prince Albert premier a construit le musée en mille neuf cent dix."
    recall, growth_ok = audio_core._prose_survival(pre, cleaned)
    assert recall == 1.0 and growth_ok
    assert audio_core.clean_accepted("fr", pre, cleaned)


def test_cleaner_status_does_not_claim_zh_jp_are_cleaned():
    """The startup line is the operator's first diagnostic; listing a passthrough language
    among the cleaned ones sends them looking in the wrong place."""
    st = audio_core.cleaner_status()
    assert set(st["not_cleaned"]) == audio_core._NO_CLEAN_LANGS
    assert not (set(st["languages"]) & audio_core._NO_CLEAN_LANGS)
    assert set(st["languages"]) | set(st["not_cleaned"]) == set(audio_core._LANG_CODES.values())


def test_growth_is_what_catches_insertion():
    """Recall alone cannot see added text — every original word is still there. The two
    arms are not redundant; this pins which one does the work."""
    pre = "It was built in 1642."
    padded = "It was built in sixteen forty two. " + "and more prose " * 20
    recall, growth_ok = audio_core._prose_survival(pre, padded)
    assert recall == 1.0
    assert growth_ok is False


@pytest.mark.skipif(not hasattr(audio_core._shared, "similarity_basis"),
                    reason="Scripts checkout predates similarity_basis")
def test_registered_inventory_defers_to_scripts():
    """Where Scripts HAS a numeral inventory (it/jp) its comparison is authoritative —
    we only supply our own where it would otherwise degrade to the word ratio."""
    assert audio_core._scripts_inventory_basis("it", "nel 1348") is not None
    assert audio_core._scripts_inventory_basis("fr", "en 1668") is None


def test_survives_a_scripts_checkout_without_the_inventory(monkeypatch):
    """⚠ The live laptop is a SEPARATE Scripts checkout and was 30 commits behind on the
    day this shipped: `build_prompt` and all nine prompts present, `similarity_basis` and
    `clean_similarity` not yet written. Accessing them unguarded raised AttributeError
    inside every regenerate on the live host. An optional Scripts capability must never be
    a hard dependency — the same shape as the jieba and opencc outages."""
    monkeypatch.delattr(audio_core._shared, "similarity_basis", raising=False)
    monkeypatch.delattr(audio_core._shared, "clean_similarity", raising=False)
    for lang, pre, cleaned in CORRECT_CLEANS:
        assert audio_core.clean_accepted(lang, pre, cleaned)
    # Italian normally defers to Scripts; with the inventory gone it must still be judged.
    assert audio_core.clean_accepted(
        "it", "Fu costruito nel 1348 da Carlo IV.",
        "Fu costruito nel milletrecentoquarantotto da Carlo quarto.")
    assert not audio_core.clean_accepted(
        "it", "Fu costruito nel 1348 da Carlo IV.", "It was built in thirteen forty eight.")


def test_survives_similarity_basis_without_clean_similarity(monkeypatch):
    """The two inventory attrs shipped together, but nothing holds them together: the
    guard must detect the attribute it CALLS (`clean_similarity`), not just its sibling.
    Detecting only `similarity_basis` left an AttributeError live for any checkout that
    ever carries one without the other — the exact class dc31260 fixed."""
    monkeypatch.delattr(audio_core._shared, "clean_similarity", raising=False)
    for lang, pre, cleaned in CORRECT_CLEANS:
        assert audio_core.clean_accepted(lang, pre, cleaned)
    # Italian normally defers to Scripts; it must degrade to _prose_survival, not raise.
    assert audio_core.clean_accepted(
        "it", "Fu costruito nel 1348 da Carlo IV.",
        "Fu costruito nel milletrecentoquarantotto da Carlo quarto.")


def test_italian_accepted_by_either_route():
    assert audio_core.clean_accepted(
        "it", "Fu costruito nel 1348 da Carlo IV.",
        "Fu costruito nel milletrecentoquarantotto da Carlo quarto.")


# --------------------------------------------------------------------------- #
# Failure modes stay visible
# --------------------------------------------------------------------------- #
def test_nothing_convertible_skips_the_api(monkeypatch):
    """No numbers → no call. Not just a saving: a cleaner handed prose it cannot help
    with has been measured DELETING a particle (JP, 2026-08-04)."""
    monkeypatch.setattr(audio_core._shared, "complete_prompt",
                        lambda *a, **k: pytest.fail("called with nothing to convert"))
    text = "Le village est très calme et agréable."
    assert audio_core.validate_and_clean(text, "Monaco1_FR", 0) == (text, False)


def test_api_failure_reports_fallback(monkeypatch):
    """⚠ The old port returned the INPUT on an API error, which then scored 1.0 against
    itself and was reported as a successful clean — so the `edit_required` routing could
    only ever fire on a similarity miss, never on an outage."""
    def boom(*a, **k):
        raise RuntimeError("DeepSeek down")

    monkeypatch.setattr(audio_core._shared, "complete_prompt", boom)
    out, fb = audio_core.validate_and_clean("Louis XIV en 1668", "Monaco1_FR", 0)
    assert (out, fb) == ("Louis XIV en 1668", True)


def test_unmapped_language_disables_cleaning_rather_than_defaulting(monkeypatch):
    """An unknown language must NOT quietly become English — that is the whole bug."""
    monkeypatch.setattr(audio_core, "language_of", lambda t: "Klingon")
    monkeypatch.setattr(audio_core._shared, "complete_prompt",
                        lambda *a, **k: pytest.fail("cleaned in the wrong language"))
    out, fb = audio_core.validate_and_clean("Louis XIV en 1668", "X_TLH", 0)
    assert (out, fb) == ("Louis XIV en 1668", True)


def test_cleaner_version_is_in_the_cache_key(monkeypatch):
    """`sessions._cleaned_orig` caches "what the working audio says". When the cleaner
    changes, the same raw text yields different words and a stale entry becomes a lie the
    splice engine diffs against — sessions seeded before today hold English-numbered
    baselines for FR/ES/DE/IT/KO trips. Bumping CLEANER_VERSION must change the key.
    (Pinned on the extracted `_cleaned_cache_key`, not on source inspection — the old
    source-string assert passed as long as a COMMENT mentioned the constant.)"""
    from app import sessions

    text = "Louis XIV en 1668"
    before = sessions._cleaned_cache_key(text)
    assert sessions._cleaned_cache_key("other text") != before
    monkeypatch.setattr(audio_core, "CLEANER_VERSION",
                        audio_core.CLEANER_VERSION + "-bumped")
    assert sessions._cleaned_cache_key(text) != before
