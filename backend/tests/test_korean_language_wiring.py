"""Korean (`_TPK*_KO`) language wiring — pins the four things `language_of` decides.

The Korean rungs reached the review queue on 2026-08-05 with `_KO` unmapped, so every
one of these read "English": the reviewer ACL (target-language reviewer 403'd, admin
only), the Whisper language (Korean audio transcribed as English → wrecked splice
anchors), the fallback narrator, and the trip list's language column. One unmapped
suffix, four silent failures — hence a test per consequence rather than one on the map.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import audio_core, config  # noqa: E402

KO_RUNGS = [
    "Gyeongju1_Bulguksa_TPK1_KO",
    "Gyeongju1_Bulguksa_TPK2_KO",
    "Gyeongju1_Bulguksa_TPK3_KO",
    "Busan_UNMemorial_TPK4_KO",
]


def test_ko_suffix_is_korean():
    for cid in KO_RUNGS:
        assert audio_core.language_of(cid) == "Korean", cid


def test_korean_family_english_rung_stays_english():
    # The family's `_EN` rung is the English source, not a Korean rung.
    assert audio_core.language_of("Gyeongju1_Bulguksa_EN") == "English"


def test_whisper_language_is_ko():
    from app import sessions

    for cid in KO_RUNGS:
        assert sessions._whisper_lang(cid) == "ko", cid


def test_both_korean_voices_registered_on_v3():
    for name, gender in (("annakim", "female"), ("hyuk", "male")):
        v = audio_core.VOICE_REGISTRY[name]
        assert v["language"] == "Korean"
        assert v["gender"] == gender
        # ElevenLabs v3 ignores voice_settings.speed; the TPK1 dwell time comes from the
        # 3 s narration pad baked in at generation, never from a slower read.
        assert v["model"] == "eleven_v3"
        assert v["settings"]["speed"] == 1.0
    assert audio_core.display_name("annakim") == "Anna Kim"


def test_voice_for_gender_korean():
    assert audio_core.voice_for_gender("Korean", "Korea", "male") == "hyuk"
    assert audio_core.voice_for_gender("Korean", "Korea", "female") == "annakim"


def test_fallback_voice_stays_in_language():
    # No COUNTRY_VOICE_GUESS entry for Korea, so the language fallback must catch it —
    # an English narrator on a Korean trip is always wrong.
    assert "Korea" not in config.COUNTRY_VOICE_GUESS
    assert config.LANGUAGE_FALLBACK_VOICE["Korean"] in audio_core.VOICE_REGISTRY
    assert audio_core.VOICE_REGISTRY[config.LANGUAGE_FALLBACK_VOICE["Korean"]]["language"] == "Korean"


def test_every_language_language_of_can_return_is_grantable():
    # manage.py's allow-list must stay in step with language_of: a language the app can
    # infer but the CLI cannot grant is admin-only forever. Hit the EU four in July 2026;
    # Korean would have been the second time.
    import manage

    representative = {
        "Trip_EN": "English",
        "Trip_Beg_JP": "Japanese",
        "Trip_HSK3_ZH": "Mandarin",
        "Trip_TPK1_KO": "Korean",
        "Trip_A12_ES": "Spanish",
        "Trip_A12_FR": "French",
        "Trip_A12_DE": "German",
        "Trip_A12_IT": "Italian",
    }
    for cid, lang in representative.items():
        assert audio_core.language_of(cid) == lang, cid
        assert lang in manage.VALID_LANGUAGES, f"{lang} is inferable but not grantable"


def test_korean_rungs_share_the_english_parents_images():
    # A rung matching no _IMAGE_BASE_RES rule searches only its own id — a folder that
    # does not exist — so every scene image in the review card comes back null. That is
    # what happened to `_N3_JP`; the same shape would have hit all 24 Korean rungs.
    from app import sessions

    for cid in KO_RUNGS:
        base = sessions._image_base_ids(cid)[-1]
        assert base.endswith("_EN"), cid
        assert base != cid, f"{cid} reduces to itself — no image rule matched"
    # One R2 copy under the parent serves all four rungs of a family.
    assert sessions._overlay_base("Gyeongju1_Bulguksa_TPK3_KO") == "Gyeongju1_Bulguksa_EN"
    assert sessions._overlay_base("Busan_UNMemorial_TPK4_KO") == "Busan_UNMemorial_EN"
    assert sessions._overlay_base("Gyeongju1_Bulguksa_EN") == "Gyeongju1_Bulguksa_EN"


def test_speed_is_1x_on_every_rung():
    # Korean generates on v3 at 1.0x for every rung (topik_config.AUDIO_SPEED_BY_LEVEL);
    # a regenerated clip must match the take it splices into.
    for cid in KO_RUNGS:
        assert audio_core.speed_for_trip(cid) == 1.0, cid
