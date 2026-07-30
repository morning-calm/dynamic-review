"""Conservative canonicalisation for reviewer-authored Zhuyin.

The staging writeback aligner expects one space-separated Bopomofo syllable per
hanzi and Taiwan's leading neutral-tone dot (``˙ㄉㄜ``). Reviewers naturally type
joined syllables and IME output commonly puts the dot after the syllable. Normalize
that mechanical presentation at the localization autosave boundary, while leaving
anything ambiguous or invalid untouched for Gate 1 to report.

The accepted syllables come from the same ``hsk_lib`` map used by
``zhuyin_to_pinyin``. This module degrades to a no-op when that optional dependency
is unavailable; approval validation remains the fail-safe.
"""

from __future__ import annotations

from functools import lru_cache
import json
import re

from . import config

_BOPO_RUN = re.compile(r"[ㄅ-ㄩˉˊˇˋ˙]+")
_BOPO_OR_TONE = "ㄅ-ㄩˉˊˇˋ˙"
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


@lru_cache(maxsize=1)
def _accepted_spellings() -> tuple[tuple[str, str], ...]:
    """Return ``(typed, canonical)`` spellings, longest typed form first."""
    try:
        map_path = (
            config.SCRIPTS_ROOT
            / "Research and Writing"
            / "HSK Mandarin"
            / "reference"
            / "zhuyin_pinyin_map.json"
        )
        canonical = tuple(json.loads(map_path.read_text(encoding="utf-8")).keys())
    except Exception:  # noqa: BLE001 -- optional dependency; Gate 1 remains the guard
        try:
            import sys

            hsk_dir = str(
                config.SCRIPTS_ROOT / "Research and Writing" / "HSK Mandarin" / "stages"
            )
            if hsk_dir not in sys.path:
                sys.path.insert(0, hsk_dir)
            import hsk_lib

            canonical = tuple(hsk_lib._zhuyin_pinyin_map().keys())
        except Exception:  # noqa: BLE001 -- no map: normalize nothing
            return ()

    aliases: dict[str, str] = {}
    for syllable in canonical:
        if not syllable or not _BOPO_RUN.fullmatch(syllable):
            continue
        aliases[syllable] = syllable
        if syllable.startswith("˙") and len(syllable) > 1:
            aliases[syllable[1:] + "˙"] = syllable
    return tuple(sorted(aliases.items(), key=lambda pair: (-len(pair[0]), pair[0])))


def _parse_options(
    run: str,
    spellings: tuple[tuple[str, str], ...],
    max_syllables: int,
) -> tuple[tuple[str, ...], ...]:
    """All parses of one run, capped at two alternatives per syllable count."""
    if not run or not spellings:
        return ()

    by_first: dict[str, list[tuple[str, str]]] = {}
    for typed, canonical in spellings:
        by_first.setdefault(typed[0], []).append((typed, canonical))

    @lru_cache(maxsize=None)
    def parse_from(pos: int) -> tuple[tuple[str, ...], ...]:
        if pos == len(run):
            return ((),)
        # Keep at most two distinct parses for each length. Two is enough to prove
        # ambiguity, while preventing a malformed run from exploding combinatorially.
        by_count: dict[int, set[tuple[str, ...]]] = {}
        for typed, canonical in by_first.get(run[pos], ()):
            if not run.startswith(typed, pos):
                continue
            for tail in parse_from(pos + len(typed)):
                solution = (canonical,) + tail
                count = len(solution)
                if count > max_syllables:
                    continue
                bucket = by_count.setdefault(count, set())
                if len(bucket) < 2:
                    bucket.add(solution)
        return tuple(solution for bucket in by_count.values() for solution in bucket)

    return parse_from(0)


def _unique_field_parse(
    runs: tuple[str, ...],
    spellings: tuple[tuple[str, str], ...],
    expected_syllables: int,
) -> tuple[tuple[str, ...], ...] | None:
    """Return the unique run-by-run parse with exactly ``expected_syllables``."""
    states: dict[int, set[tuple[tuple[str, ...], ...]]] = {0: {()}}
    for run in runs:
        options = _parse_options(run, spellings, expected_syllables)
        if not options:
            return None
        next_states: dict[int, set[tuple[tuple[str, ...], ...]]] = {}
        for used, prefixes in states.items():
            for option in options:
                total = used + len(option)
                if total > expected_syllables:
                    continue
                bucket = next_states.setdefault(total, set())
                for prefix in prefixes:
                    if len(bucket) < 2:
                        bucket.add(prefix + (option,))
        states = next_states
        if not states:
            return None
    matches = states.get(expected_syllables, set())
    return next(iter(matches)) if len(matches) == 1 else None


def normalize_zhuyin(text: str, hans: str = "") -> str:
    """Canonicalize unambiguous syllable spacing and neutral-tone dots.

    Invalid or ambiguous joined runs are preserved so the deterministic approval
    check exposes them instead of autosave guessing. Existing horizontal whitespace
    between Bopomofo syllables is collapsed to one space; newlines and other text are
    otherwise preserved.
    """
    value = text or ""
    spellings = _accepted_spellings()
    if not spellings:
        return value

    matches = tuple(_BOPO_RUN.finditer(value))
    expected = len(_HAN.findall(hans or ""))
    if not matches or not expected:
        return value
    try:
        parsed = _unique_field_parse(
            tuple(match.group(0) for match in matches),
            spellings,
            expected,
        )
    except RecursionError:
        # A pasted pathological run can exceed Python's recursion limit before the
        # ambiguity cap helps. Preserve it for Gate 1 instead of failing autosave.
        return value
    if parsed is None:
        return value

    pieces: list[str] = []
    cursor = 0
    for match, syllables in zip(matches, parsed):
        pieces.append(value[cursor : match.start()])
        pieces.append(" ".join(syllables))
        cursor = match.end()
    pieces.append(value[cursor:])
    normalized = "".join(pieces)
    return re.sub(
        rf"(?<=[{_BOPO_OR_TONE}])[ \t]+(?=[{_BOPO_OR_TONE}])",
        " ",
        normalized,
    )
