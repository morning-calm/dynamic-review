"""Regression: the waveform-edit deadlock (Kaohsiung Lotus Pond EN, 2026-08-01).

A reviewer deleted the trailing sentences of a SceneDesc and removed the matching audio
by hand in the waveform editor. `working_text` still claimed the take said the deleted
words, so from then on the field was wedged:

  * highlight / "Fix pronunciation" → 409 `unvoiced_edits_outside_highlight`
    ("use Generate from edit first"), and
  * "Generate from edit" → "Edit removed text only — use whole-regenerate",

with no exit that keeps the hand audio work. The live log shows exactly that loop —
regenerate 409, 409, 409 — ending in a Revert. These tests pin both jaws of the trap and
the state `sessions.accept_text_as_voiced` moves to open it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import audio_splice, sessions  # noqa: E402

# The real scene-4 shapes, shortened. The reviewer's edit is a pure DELETION of the tail.
VOICED = ("At the entrance to the bridge are some stone statues. "
          "In the middle stands a mythical creature. "
          "The body of a turtle and the head of a dragon. "
          "The body of a snake and the head of a dog.")
EDITED = ("At the entrance to the bridge are some stone statues. "
          "In the middle stands a mythical creature.")


def _hl(text: str, phrase: str) -> tuple[int, int]:
    i = text.index(phrase)
    return i, i + len(phrase)


def _frow(**over):
    base = {"has_audio": 1, "mp3_name": "4.mp3", "original_text": VOICED,
            "working_text": None, "current_text": VOICED}
    base.update(over)
    return base


# --- jaw 1: every highlight/alt op is refused while the text is ahead of the take ------
def test_highlight_refused_while_text_ahead_of_take():
    start, end = _hl(EDITED, "stone statues")
    assert audio_splice.pending_edit_outside_highlight(VOICED, EDITED, start, end) is True


def test_highlight_allowed_once_the_take_is_re_baselined():
    """What accept_text_as_voiced achieves: working_text == current_text, so the pending
    deletion is gone and the highlight is planned normally again."""
    start, end = _hl(EDITED, "stone statues")
    assert audio_splice.pending_edit_outside_highlight(EDITED, EDITED, start, end) is False


# --- jaw 2: the advice it hands you ("Generate from edit") has nothing to voice --------
def test_generate_from_edit_on_a_delete_only_change_has_nothing_to_voice():
    plan = audio_splice.plan_segment(
        "KaohsiungLotusPond_EN", VOICED, EDITED, False,
        whisper_words=[], voice_id="v", voice_settings={},
        base_samples=None, sr=44100)
    assert plan.edit_required and plan.candidate_mp3 is None
    assert plan.reason == audio_splice._REMOVED_ONLY


def test_removed_only_advice_points_at_a_route_that_keeps_the_audio():
    """Whole-regenerate discards hand audio work; the message must offer the other route."""
    assert "waveform editor" in audio_splice._REMOVED_ONLY
    assert "Audio already matches" in audio_splice._REMOVED_ONLY


# --- the signal the FE gates the escape hatch on --------------------------------------
def test_text_ahead_of_audio_true_for_the_wedged_field():
    assert sessions._text_ahead_of_audio("KaohsiungLotusPond_EN",
                                         _frow(current_text=EDITED)) is True


def test_text_ahead_of_audio_false_once_re_baselined():
    frow = _frow(working_text=EDITED, current_text=EDITED)
    assert sessions._text_ahead_of_audio("KaohsiungLotusPond_EN", frow) is False


def test_text_ahead_of_audio_false_on_a_pristine_field():
    assert sessions._text_ahead_of_audio("KaohsiungLotusPond_EN", _frow()) is False


def test_text_ahead_of_audio_false_for_a_text_only_field():
    """No take to be ahead OF — the escape hatch must not appear on text-only fields."""
    frow = _frow(has_audio=0, mp3_name=None, current_text=EDITED)
    assert sessions._text_ahead_of_audio("KaohsiungLotusPond_EN", frow) is False


def test_text_ahead_of_audio_reads_the_kana_line_for_japanese():
    """_JP is voiced from the LAST line only: a kanji-line-only edit is not 'ahead', and a
    kana edit is — comparing current_text would get both wrong."""
    kanji_only = _frow(original_text="漢字\nかな", working_text="漢字\nかな",
                       current_text="漢字がちがう\nかな")
    kana_edit = _frow(original_text="漢字\nかな", working_text="漢字\nかな",
                      current_text="漢字\nかなちがう")
    assert sessions._text_ahead_of_audio("Tokyo_08_Beg_N5_JP", kanji_only) is False
    assert sessions._text_ahead_of_audio("Tokyo_08_Beg_N5_JP", kana_edit) is True


# --- when the escape hatch is OFFERED --------------------------------------------------
# It asserts something no machine can verify, so it must appear only where it is needed:
# after the reviewer shaped the audio BY HAND. During ordinary edit-then-regenerate the
# honest answer is "no, the audio does not say that yet".
def _offer(kind: str, current_text: str = EDITED) -> bool:
    return sessions._can_accept_text_as_voiced(
        "KaohsiungLotusPond_EN", _frow(current_text=current_text), kind)


def test_offered_after_a_hand_audio_edit():
    for kind in ("wave_delete", "wave_move", "wave_silence", "wave_insert_silence",
                 "wave_insert_clip", "noise_trim", "remove_silence", "manual_edit",
                 "import"):
        assert _offer(kind) is True, kind


def test_not_offered_during_ordinary_edit_then_regenerate():
    """The take is pristine or app-generated, so the text really isn't voiced yet —
    offering the claim here is how a reviewer ships text over audio that doesn't say it."""
    assert _offer("v0_original") is False
    assert _offer("splice") is False


def test_not_offered_when_there_is_nothing_to_re_baseline():
    assert _offer("wave_delete", current_text=VOICED) is False
