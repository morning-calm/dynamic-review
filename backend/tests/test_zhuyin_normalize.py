from __future__ import annotations

import unittest
from unittest.mock import patch

from app import zhuyin_normalize


SPELLINGS = (
    ("ㄑㄧㄡˊ", "ㄑㄧㄡˊ"),
    ("ㄓㄜˋ", "ㄓㄜˋ"),
    ("ㄎㄜ", "ㄎㄜ"),
    ("ㄧㄡˇ", "ㄧㄡˇ"),
    ("ㄧˊ", "ㄧˊ"),
    ("ㄍㄜˋ", "ㄍㄜˋ"),
    ("ㄖㄣˊ", "ㄖㄣˊ"),
    ("ㄗㄞˋ", "ㄗㄞˋ"),
    ("ㄉㄚˋ", "ㄉㄚˋ"),
    ("ㄌㄡˊ", "ㄌㄡˊ"),
    ("ㄕㄤˋ", "ㄕㄤˋ"),
    ("ㄊㄚ", "ㄊㄚ"),
    ("ㄓㄢˋ", "ㄓㄢˋ"),
    ("ㄉㄜ˙", "˙ㄉㄜ"),
    ("˙ㄉㄜ", "˙ㄉㄜ"),
    ("ㄉㄧˋ", "ㄉㄧˋ"),
    ("ㄈㄤ", "ㄈㄤ"),
    ("ㄏㄣˇ", "ㄏㄣˇ"),
    ("ㄍㄠ", "ㄍㄠ"),
)


class NormalizeZhuyinTests(unittest.TestCase):
    def normalize(self, text: str, hans: str) -> str:
        with patch.object(
            zhuyin_normalize, "_accepted_spellings", return_value=SPELLINGS
        ):
            return zhuyin_normalize.normalize_zhuyin(text, hans)

    def test_taipei_option_inserts_missing_space(self):
        self.assertEqual(
            self.normalize("ㄓㄜˋ ㄎㄜㄑㄧㄡˊ", "这颗球"),
            "ㄓㄜˋ ㄎㄜ ㄑㄧㄡˊ",
        )

    def test_taipei_scene_normalizes_joined_runs_and_neutral_dot(self):
        actual = self.normalize(
            "ㄧㄡˇㄧˊ ㄍㄜˋ ㄖㄣˊ ㄗㄞˋ ㄉㄚˋ ㄌㄡˊ ㄕㄤˋ。 "
            "ㄊㄚ ㄓㄢˋㄉㄜ˙ㄉㄧˋㄈㄤ ㄏㄣˇ ㄍㄠ。",
            "有一个人在大楼上。他站的地方很高。",
        )
        self.assertEqual(
            actual,
            "ㄧㄡˇ ㄧˊ ㄍㄜˋ ㄖㄣˊ ㄗㄞˋ ㄉㄚˋ ㄌㄡˊ ㄕㄤˋ。 "
            "ㄊㄚ ㄓㄢˋ ˙ㄉㄜ ㄉㄧˋ ㄈㄤ ㄏㄣˇ ㄍㄠ。",
        )

    def test_existing_canonical_text_is_unchanged(self):
        text = "ㄓㄜˋ ㄎㄜ ㄑㄧㄡˊ。 ˙ㄉㄜ"
        self.assertEqual(self.normalize(text, "这颗球的"), text)

    def test_horizontal_whitespace_between_syllables_is_collapsed(self):
        self.assertEqual(self.normalize("ㄓㄜˋ  \tㄎㄜ", "这颗"), "ㄓㄜˋ ㄎㄜ")

    def test_ambiguous_run_is_not_changed(self):
        ambiguous = (("ㄅㄚ", "ㄅㄚ"), ("ㄅㄚ", "ㄆㄚ"))
        with patch.object(
            zhuyin_normalize, "_accepted_spellings", return_value=ambiguous
        ):
            self.assertEqual(zhuyin_normalize.normalize_zhuyin("ㄅㄚ", "八"), "ㄅㄚ")

    def test_invalid_run_is_not_changed(self):
        self.assertEqual(self.normalize("ㄅㄅㄅ", "八"), "ㄅㄅㄅ")

    def test_missing_dependency_is_noop(self):
        with patch.object(zhuyin_normalize, "_accepted_spellings", return_value=()):
            text = "ㄧㄡˇㄧˊ ㄉㄜ˙"
            self.assertEqual(zhuyin_normalize.normalize_zhuyin(text, "有一的"), text)

    def test_overlong_run_is_noop_instead_of_recursion_error(self):
        text = "ㄧ" * 2000
        with patch.object(
            zhuyin_normalize,
            "_accepted_spellings",
            return_value=(("ㄧ", "ㄧ"),),
        ):
            self.assertEqual(
                zhuyin_normalize.normalize_zhuyin(text, "一" * 2000),
                text,
            )

    def test_real_hsk_map_normalizes_taipei_regressions(self):
        spellings = zhuyin_normalize._accepted_spellings()
        if not spellings:
            self.skipTest("hsk_lib pronunciation map unavailable")
        self.assertEqual(
            zhuyin_normalize.normalize_zhuyin(
                "ㄓㄜˋ ㄎㄜㄑㄧㄡˊ ㄕˋ ㄐㄧㄣ ㄙㄜˋ ˙ㄉㄜ。",
                "这颗球是金色的。",
            ),
            "ㄓㄜˋ ㄎㄜ ㄑㄧㄡˊ ㄕˋ ㄐㄧㄣ ㄙㄜˋ ˙ㄉㄜ。",
        )
        self.assertEqual(
            zhuyin_normalize.normalize_zhuyin(
                "ㄧㄡˇㄧˊ ㄍㄜˋ ㄖㄣˊ ㄗㄞˋ ㄉㄚˋ ㄌㄡˊ ㄕㄤˋ。 "
                "ㄊㄚ ㄓㄢˋㄉㄜ˙ㄉㄧˋㄈㄤ ㄏㄣˇ ㄍㄠ。",
                "有一个人在大楼上。他站的地方很高。",
            ),
            "ㄧㄡˇ ㄧˊ ㄍㄜˋ ㄖㄣˊ ㄗㄞˋ ㄉㄚˋ ㄌㄡˊ ㄕㄤˋ。 "
            "ㄊㄚ ㄓㄢˋ ˙ㄉㄜ ㄉㄧˋ ㄈㄤ ㄏㄣˇ ㄍㄠ。",
        )


if __name__ == "__main__":
    unittest.main()
