"""Undo/redo must move the TEXT baseline with the audio.

`working_text` is what the splice engine believes the working take says. Before
2026-08-02, `_restore_audio_version` stepped the audio back but left it pointing at the
newest take's text, so after undoing a combine "Generate from edit" saw no pending change
and the reviewer could not re-voice the edit at all — the same class of wedge as the
waveform deadlock in test_text_ahead_of_audio.py.

These cover the snapshot decisions directly; the round trip through real audio is
exercised by the integration smoke.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import sessions  # noqa: E402

SEED = "The bridge has stone statues."
EDIT = "The bridge has two stone lions."


def _frow(**over):
    base = {"id": 1, "has_audio": 1, "mp3_name": "4.mp3", "original_text": SEED,
            "working_text": SEED, "current_text": SEED, "localization_json": None}
    base.update(over)
    return base


def _zh(cur_hans, working_hans=None, orig_hans="原文"):
    loc = {"cur": {"Hans": cur_hans}, "orig": {"Hans": orig_hans}}
    if working_hans is not None:
        loc["working_hans"] = working_hans
    return _frow(localization_json=json.dumps(loc, ensure_ascii=False))


# --- what a new version records ------------------------------------------------------
def test_revoicing_ops_record_the_text_they_voiced():
    """combine / import / promote pass the text they just voiced."""
    assert sessions._version_text_snapshot(_frow(current_text=EDIT), EDIT, None)[0] == EDIT


def test_reshaping_ops_inherit_the_current_baseline():
    """A waveform cut or a pause tweak changes the audio but not what it SAYS — the
    version must inherit, not silently claim the reviewer's un-voiced text edit."""
    frow = _frow(working_text=SEED, current_text=EDIT)   # pending, un-voiced edit
    assert sessions._version_text_snapshot(frow, None, None)[0] == SEED


def test_snapshot_inherits_the_zh_hans_baseline():
    frow = _zh(cur_hans="新的", working_hans="旧的")
    assert sessions._version_text_snapshot(frow, None, None)[1] == "旧的"


def test_snapshot_takes_a_new_hans_when_given_one():
    frow = _zh(cur_hans="新的", working_hans="旧的")
    assert sessions._version_text_snapshot(frow, EDIT, "新的")[1] == "新的"


def test_snapshot_hans_is_empty_before_any_combine():
    """No working_hans yet → '' (= unset), which resolves to orig.Hans."""
    assert sessions._version_text_snapshot(_zh(cur_hans="新的"), None, None)[1] == ""


def test_snapshot_survives_a_corrupt_localization_blob():
    frow = _frow(localization_json="{not json")
    assert sessions._version_text_snapshot(frow, None, None) == (SEED, "")


# --- applying a snapshot back (the undo/redo step) ------------------------------------
def test_working_hans_patch_sets_and_clears():
    frow = _zh(cur_hans="新的", working_hans="旧的")
    set_to = json.loads(sessions._working_hans_patch(frow, "别的")["localization_json"])
    assert set_to["working_hans"] == "别的"
    # '' = the take predates any combine → clear it so _cjk_spoken falls back to orig.Hans
    cleared = json.loads(sessions._working_hans_patch(frow, "")["localization_json"])
    assert "working_hans" not in cleared
    assert cleared["cur"]["Hans"] == "新的" and cleared["orig"]["Hans"] == "原文"


def test_working_hans_patch_is_a_noop_for_non_zh():
    assert sessions._working_hans_patch(_frow(), "anything") == {}


def test_zh_working_hans_patch_still_rebaselines_to_cur_hans():
    """The combine-time helper is unchanged in behaviour after being refactored onto the
    general setter — including its no-op when there is no hanzi to record."""
    patched = sessions._zh_working_hans_patch(_zh(cur_hans="新的", working_hans="旧的"))
    assert json.loads(patched["localization_json"])["working_hans"] == "新的"
    assert sessions._zh_working_hans_patch(_zh(cur_hans="")) == {}
    assert sessions._zh_working_hans_patch(_frow()) == {}


def test_zh_current_hans_reads_cur_not_working():
    assert sessions._zh_current_hans(_zh(cur_hans="新的", working_hans="旧的")) == "新的"
    assert sessions._zh_current_hans(_frow()) == ""
    assert sessions._zh_current_hans(_frow(localization_json="{not json")) == ""


# --- the seed-time TripLocalizations helpers must stay reachable ----------------------
# `_zh_localization` (field's localization_json) and `_loc_block` (a TripLocalizations
# NODE) are different things with confusingly similar jobs. A red-team pass briefly named
# the first `_loc_block`, which shadowed the second at module scope and broke seeding for
# every Mandarin trip — silently, because nothing here exercised it and an import check
# can't see a redefinition.
def test_loc_block_flattens_a_tripLocalizations_node():
    node = {"target": {"Hans": "简", "Hant": "繁", "zhuyin": "ㄅ"}, "home": {"en": "x"}}
    assert sessions._loc_block(node) == {"Hans": "简", "Hant": "繁", "zhuyin": "ㄅ", "en": "x"}
    assert sessions._loc_block(None) is None


def test_index_localization_indexes_scenes_and_description():
    doc = {
        "scenes": [{"index": 1,
                    "SceneDesc": {"target": {"Hans": "简", "Hant": "繁", "zhuyin": "ㄅ"},
                                  "home": {"en": "x"}},
                    "questionOptionKeys": [{"target": {"Hans": "选"}, "home": {"en": "opt"}}]}],
        "description": {"target": {"Hans": "描述"}, "home": {"en": "desc"}},
    }
    out = sessions._index_localization(doc)
    assert out[(1, "SceneDesc", None)]["Hans"] == "简"
    assert out[(1, "questionOption", 0)]["Hans"] == "选"
    assert out[(None, "tripgroup_description", None)]["en"] == "desc"


def test_zh_localization_is_a_distinct_helper_from_loc_block():
    """Same shape of job, different input — keep them apart by name AND signature."""
    assert sessions._zh_localization is not sessions._loc_block
    assert sessions._zh_localization(_zh(cur_hans="新的"))["cur"]["Hans"] == "新的"
    assert sessions._zh_localization(_frow()) == {}
    assert sessions._zh_localization(_frow(localization_json="{not json")) == {}
