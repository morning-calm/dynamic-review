"""CJK voicing parity — a regenerate voices what the pipeline voiced (2026-08-07).

The pipeline cleans all three CJK languages before TTS (zh `mandarin_number_clean`,
jp `build_prompt("jp", …)` on the kana line, ko `korean_number_clean`), but until this
fix `sessions.regenerate` routed zh/jp through `_cjk_spoken` → `plan_whole(raw)` and
never called `validate_and_clean` — so 「634めーとる」 was re-voiced with raw digits while
the master said the kana expansion, and a `_ZH` regenerate dropped the trip's pinned
pronunciation overrides (`台北101` → `台北一〇一` lives in `apply_overrides`, which had
exactly one call site: `validate_and_clean`). Measured exposure: 51 fields / 6 trips.

The constraint that shaped the fix (plan §4): cleaning cannot move upstream of
selection/diff/alignment — `cjk_splice` char-diffs OLD→NEW in RAW space and reads cut
times from the forced aligner against audio that SAYS the cleaned string. So whole-regen
cleans, and the surgical path is DISABLED for any field whose OLD or NEW spoken line has
convertible content (`audio_core.cjk_convertible`).

No network anywhere: harnesses/transport are monkeypatched; `plan_whole` is captured.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import audio_core, audio_splice, sessions  # noqa: E402

pytestmark = pytest.mark.skipif(
    audio_core._shared is None,
    reason=f"shared Scripts cleaner unavailable: {audio_core._CLEANER_ERROR}")


# --------------------------------------------------------------------------- #
# The surgical-path gate
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", [
    "台北101大楼有508米高。",       # digits
    "せかいでも3ばんめにたかい",     # digit inside kana
    "たかさは６３４めーとる",        # fullwidth digits
    "JRでいきます",                  # Latin (acronyms the harnesses expand)
    "気温は2°Cです",                 # degree symbol
    "50%くらい",                     # percent
])
def test_convertible_lines_detected(text):
    assert audio_core.cjk_convertible(text)


@pytest.mark.parametrize("text", [
    "台北一〇一大楼有五百零八米高。",   # already-cleaned hanzi numerals
    "せかいでもさんばんめにたかい",     # pure kana
    "これは大きな建物です。",           # pure kanji/kana, no numbers
    "", None,
])
def test_non_convertible_lines_pass(text):
    assert not audio_core.cjk_convertible(text)


def test_cleaned_output_is_not_convertible():
    """The gate must not re-trip on the CLEANED form — otherwise a field whose baseline
    ever moved to cleaned space would lose surgical forever for no reason."""
    assert audio_core.cjk_convertible("台北101")
    assert not audio_core.cjk_convertible("台北一〇一")


# --------------------------------------------------------------------------- #
# regenerate wiring — what reaches plan_whole
# --------------------------------------------------------------------------- #
ZH_RAW = "台北101大楼有508米高，1999年开始建造。"
ZH_CLEAN = "台北一〇一大楼有五百零八米高，一九九九年开始建造。"
JP_RAW = "せかいでも3ばんめにたかくて、たかさは634めーとるもあります"
JP_CLEAN = "せかいでもさんばんめにたかくて、たかさはろっぴゃくさんじゅうよんめーとるもあります"


def _zh_frow(hans: str, working_hans: str | None = None, **over):
    import json
    loc = {"orig": {"Hans": hans}, "cur": {"Hans": hans}}
    if working_hans is not None:
        loc["working_hans"] = working_hans
    base = {"id": 7, "has_audio": 1, "mp3_name": "3.mp3", "field_path": "SceneDesc",
            "scene_index": 3, "original_text": hans, "working_text": None,
            "current_text": hans, "comment": "", "flag": None,
            "candidate_mp3_path": None, "splice_meta_json": None,
            "localization_json": json.dumps(loc, ensure_ascii=False)}
    base.update(over)
    return base


def _jp_frow(text: str, **over):
    base = {"id": 7, "has_audio": 1, "mp3_name": "3.mp3", "field_path": "SceneDesc",
            "scene_index": 3, "original_text": text, "working_text": None,
            "current_text": text, "comment": "", "flag": None,
            "candidate_mp3_path": None, "splice_meta_json": None,
            "localization_json": None}
    base.update(over)
    return base


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """Run `sessions.regenerate` far enough to see the text handed to `plan_whole`,
    with no DB, no network and no audio files. plan_whole returns a hard-reject plan
    (edit_required, no candidate) so regenerate exits before the DSP block; the captured
    text is the assertion surface."""
    cap = {}

    def fake_plan_whole(text, fb, *a, **k):
        cap["text"], cap["fb"] = text, fb
        return audio_splice.RegenPlan(edit_required=True, reason="captured")

    def fake_plan_cjk_span(*a, **k):
        cap["surgical"] = True
        return audio_splice.RegenPlan(edit_required=True, reason="captured")

    monkeypatch.setattr(audio_splice, "plan_whole", fake_plan_whole)
    monkeypatch.setattr(sessions.cjk_splice, "plan_cjk_span", fake_plan_cjk_span)
    monkeypatch.setattr(sessions, "_session_row", lambda sid: cap["srow"])
    monkeypatch.setattr(sessions, "_field_row", lambda sid, fid: cap["frow"])
    monkeypatch.setattr(sessions, "work_dirs", lambda sid: {
        k: tmp_path / k for k in ("working", "candidate", "orig", "versions")})
    monkeypatch.setattr(sessions.db, "update_fields", lambda fid, **kw: cap.setdefault("patch", kw))
    monkeypatch.setattr(sessions.db, "touch_session", lambda sid: None)
    monkeypatch.setattr(sessions, "serialize_field", lambda sid, frow: {"ok": True})
    monkeypatch.setattr(audio_core, "VOICES",
                        {"testvoice": ("vid", {"stability": 0.5})})

    def run(frow, trip_id, mode="whole", rng=None, alt_text=None):
        cap.clear()
        cap["srow"] = {"id": "sess_x", "trip_id": trip_id, "voice": "testvoice",
                       "speed_override": None, "model_override": None}
        cap["frow"] = frow
        sessions.regenerate("sess_x", 7, mode, rng, alt_text)
        return cap

    return run


def test_zh_regenerate_sends_cleaned_hanzi(harness, monkeypatch):
    """Acceptance §9.1: a `_ZH` regenerate of Taipei101 sends 台北一〇一 (overrides) and
    一九九九年 (numbers) to ElevenLabs, not 台北101 / 1999年."""
    monkeypatch.setattr(audio_core._zh_clean, "clean_field",
                        lambda t, d=None, **k: ZH_CLEAN)
    cap = harness(_zh_frow(ZH_RAW), "Taipei101_HSK3_ZH")
    assert cap["text"] == ZH_CLEAN
    assert cap["fb"] is False


def test_jp_regenerate_sends_kana_expansion(harness, monkeypatch):
    """Acceptance §9.2: a `_JP` regenerate voices the kana expansion of 634, matching
    the master."""
    monkeypatch.setattr(audio_core, "validate_and_clean",
                        lambda t, d, s: (JP_CLEAN, False) if t == JP_RAW
                        else pytest.fail(f"unexpected clean input: {t!r}"))
    cap = harness(_jp_frow("六三四\n" + JP_RAW), "Tokyo_03_Beg_N4_JP")
    assert cap["text"] == JP_CLEAN


def test_non_convertible_field_makes_no_cleaner_call(harness, monkeypatch):
    """Acceptance §9.3: nothing convertible → no model call, voiced as written (the
    Tokyo_08 regression shape — the approved CJK reference trip is pure kana)."""
    monkeypatch.setattr(audio_core._shared, "complete_prompt",
                        lambda *a, **k: pytest.fail("cleaner called with nothing to convert"))
    text = "これはとてもたかいたわーです"
    cap = harness(_jp_frow("高い塔\n" + text), "Tokyo_08_Beg_N5_JP")
    assert cap["text"] == text
    assert cap["fb"] is False


def test_convertible_field_never_attempts_surgical(harness, monkeypatch, tmp_path):
    """Acceptance §9.5: a convertible field must not cut at stale times — the audio says
    the CLEANED string, the aligner would score RAW text against it. Highlight on such a
    field bails to (cleaned) whole-regen instead of plan_cjk_span."""
    monkeypatch.setattr(audio_core._zh_clean, "clean_field",
                        lambda t, d=None, **k: ZH_CLEAN)
    (tmp_path / "working").mkdir(parents=True, exist_ok=True)
    (tmp_path / "working" / "3.mp3").write_bytes(b"x")   # working take exists
    frow = _zh_frow(ZH_RAW, working_hans=ZH_RAW)
    cap = harness(frow, "Taipei101_HSK3_ZH", mode="highlight",
                  rng={"start": 0, "end": 5})
    assert "surgical" not in cap, "surgical splice attempted on a convertible field"
    assert cap["text"] == ZH_CLEAN


def test_old_convertible_also_disables_surgical(harness, monkeypatch, tmp_path):
    """The edit that DELETES the number: NEW is clean but the take's audio still says the
    cleaned form of OLD — raw-space alignment is just as untrustworthy."""
    monkeypatch.setattr(audio_core._zh_clean, "clean_field", lambda t, d=None, **k: t)
    (tmp_path / "working").mkdir(parents=True, exist_ok=True)
    (tmp_path / "working" / "3.mp3").write_bytes(b"x")
    import json
    frow = _zh_frow("这座大楼很高。", working_hans="台北101大楼很高。")
    frow["localization_json"] = json.dumps(
        {"orig": {"Hans": "台北101大楼很高。"}, "cur": {"Hans": "这座大楼很高。"},
         "working_hans": "台北101大楼很高。"}, ensure_ascii=False)
    cap = harness(frow, "Taipei101_HSK3_ZH", mode="highlight",
                  rng={"start": 0, "end": 3})
    assert "surgical" not in cap


def test_non_convertible_field_still_gets_surgical(harness, monkeypatch, tmp_path):
    """Acceptance §9.4: the selection tools' surgical path is untouched where nothing is
    convertible — the CJK splice arc must not regress."""
    (tmp_path / "working").mkdir(parents=True, exist_ok=True)
    (tmp_path / "working" / "3.mp3").write_bytes(b"x")
    frow = _zh_frow("这座大楼很高。", working_hans="这座大楼很高。")
    cap = harness(frow, "Taipei101_HSK3_ZH", mode="highlight",
                  rng={"start": 0, "end": 3})
    assert cap.get("surgical") is True


def test_alt_on_convertible_field_refuses_with_number_reason(harness, monkeypatch, tmp_path):
    """Alt must NEVER be voiced as the whole field; on a convertible field the refusal
    must say why (numbers), not claim there is no working audio."""
    monkeypatch.setattr(audio_core._zh_clean, "clean_field",
                        lambda t, d=None, **k: pytest.fail("alt refuse must not clean"))
    (tmp_path / "working").mkdir(parents=True, exist_ok=True)
    (tmp_path / "working" / "3.mp3").write_bytes(b"x")
    cap = harness(_zh_frow(ZH_RAW, working_hans=ZH_RAW), "Taipei101_HSK3_ZH",
                  mode="alt", rng={"start": 2, "end": 5}, alt_text="一零一")
    assert "text" not in cap, "alt was whole-voiced"
    assert "numbers/symbols" in cap["patch"]["comment"]
    assert cap["patch"]["flag"] == "edit_required"


def test_whole_alt_text_is_cleaned(harness, monkeypatch):
    """Regenerate-with-alt-text on a whole field (no selection) voices the ALT — it must
    be cleaned like any other spoken text."""
    monkeypatch.setattr(audio_core._zh_clean, "clean_field",
                        lambda t, d=None, **k: "五百零八米")
    cap = harness(_zh_frow(ZH_RAW), "Taipei101_HSK3_ZH", alt_text="508米")
    assert cap["text"] == "五百零八米"


def test_cleaner_fallback_flags_edit_required(harness, monkeypatch):
    """A failed clean is voiced without number expansion but MUST reach a human
    (plan_whole's S2 flag) — and the deterministic override substitution still applies:
    `apply_overrides` needs no model, so 台北一〇一 survives a DeepSeek outage."""
    def die(*a, **k):
        raise SystemExit("Missing DeepSeek_API_KEY in .env")

    monkeypatch.setattr(audio_core._zh_clean, "clean_field", die)
    cap = harness(_zh_frow(ZH_RAW), "Taipei101_HSK3_ZH")
    assert cap["text"] == ZH_RAW.replace("台北101", "台北一〇一")   # overrides kept…
    assert cap["fb"] is True          # …numbers raw → flagged for a mandatory listen


# --------------------------------------------------------------------------- #
# The seam itself (option D): no voicing path may skip the cleaner
# --------------------------------------------------------------------------- #
def test_no_clean_langs_is_empty():
    """zh/jp were the enumerated exceptions; the set must stay empty now. A language that
    genuinely cannot be cleaned belongs in the set WITH a comment, not out of _LANG_CODES
    (that path warns loudly instead of silently passing through)."""
    assert audio_core._NO_CLEAN_LANGS == set()


def test_fallback_narration_cleans_zh(monkeypatch):
    """`fallback()` shares validate_and_clean — with the working take now voiced from
    cleaned text, the reference clip agrees with it again."""
    monkeypatch.setattr(audio_core._zh_clean, "clean_field",
                        lambda t, d=None, **k: ZH_CLEAN)
    out, fb = audio_core.validate_and_clean(ZH_RAW, "Taipei101_HSK3_ZH", 3)
    assert (out, fb) == (ZH_CLEAN, False)
