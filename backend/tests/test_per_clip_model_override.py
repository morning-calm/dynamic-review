"""Per-clip V3 override (Fix pronunciation / Create new, 2026-08-10).

A v2 session can voice ONE candidate/take with eleven_v3 when the v2 voice won't say
a word right however it's spelled. Pins the two pure pieces the feature stands on:

  1. `v3_voice_settings` — v3 rejects settings every EN/EU registry voice carries
     (`speed` unsupported, `style` unsupported, stability must be 0.0/0.5/1.0; andrea
     and the EU voices carry 0.75), so the API call sites must sanitize or the first
     cross-model request 4xxes. It must also not MUTATE the shared registry dict —
     `VOICES` hands out the live settings object, so an in-place strip would leave the
     session's own v2 regens without `speed` for the rest of the process's life.
  2. `_validated_model` — an unknown model id is a 422 at the boundary, same contract
     as set_narration; None falls through to the session's effective model.

Not covered here (they need a seeded session + the ElevenLabs API): that the override
is per-REQUEST only (nothing is written to the session row) and that `combine` notes a
candidate voiced under a model ≠ the session's. Those ride on the integration smoke.
"""

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import audio_core  # noqa: E402


def test_v3_settings_drop_speed_and_style():
    vs = {"speed": 0.7, "stability": 0.5, "similarity_boost": 0.75, "style": 0}
    out = audio_core.v3_voice_settings(vs)
    assert "speed" not in out and "style" not in out
    assert out["similarity_boost"] == 0.75


def test_v3_settings_snap_off_grid_stability():
    # andrea + every EU voice: stability 0.75 → must land on the v3 grid.
    out = audio_core.v3_voice_settings({"stability": 0.75, "similarity_boost": 0.75})
    assert out["stability"] == 0.5


def test_v3_settings_keep_on_grid_stability():
    for ok in (0.0, 0.5, 1.0):
        assert audio_core.v3_voice_settings({"stability": ok})["stability"] == ok


def test_v3_settings_do_not_mutate_input():
    vs = {"speed": 0.7, "stability": 0.75}
    audio_core.v3_voice_settings(vs)
    assert vs == {"speed": 0.7, "stability": 0.75}


def test_every_registry_voice_survives_v3_sanitizing():
    # The offer can send ANY voice to v3 — no registry settings may remain off-grid.
    for name, (_vid, vs) in audio_core.VOICES.items():
        out = audio_core.v3_voice_settings(vs)
        assert out.get("stability") in (None, 0.0, 0.5, 1.0), name
        assert "speed" not in out and "style" not in out, name


def test_validated_model_passes_known_and_none():
    from app import sessions

    assert sessions._validated_model(None) is None
    for m in audio_core.EL_MODELS:
        assert sessions._validated_model(m) == m


def test_validated_model_rejects_unknown():
    from app import sessions

    with pytest.raises(HTTPException) as e:
        sessions._validated_model("eleven_v99")
    assert e.value.status_code == 422
