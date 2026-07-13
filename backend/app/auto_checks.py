"""
Deterministic auto-review — Gate 1 of the auto-review pipeline
(docs/auto-review-proposal.md). PURE checks over a session's field_edits rows; no
writes, no network. Called from ``sessions.validate`` so results ride the existing
hard/soft validation surface (submit 409-blocks on hard, FE already renders both).

Issue shape matches validate(): {scene_index, field_path, issue, severity} with
severity 'block' (hard) | 'warn' | 'note' (soft).

Origin: 2026-07-08 — four trips were submitted with only the Hans script edited;
Hant/zhuyin/en went stale and one approved trip shipped that way (repaired by hand).
These checks make that class of mistake impossible to submit.

hsk_lib (jieba/pypinyin) is imported LAZILY like sessions._zh_regen_pinyin; if the
environment lacks it we degrade to a single 'warn' instead of blocking reviews.
"""
from __future__ import annotations

import json
import re
import unicodedata

from . import config, zh_level

# Paired punctuation that must balance inside one field's text.
_PAIRS = [("（", "）"), ("(", ")"), ("「", "」"), ("『", "』"), ("《", "》"),
          ("[", "]"), ("“", "”")]

_ZH_SCRIPTS = ("Hans", "Hant", "zhuyin")   # the target-language scripts of the 4-box block


def _hsk():
    """Lazy hsk_lib import (its dir isn't on the default sys.path). None if missing."""
    try:
        import sys as _sys
        hsk_dir = str(config.SCRIPTS_ROOT / "Research and Writing" / "HSK Mandarin" / "stages")
        if hsk_dir not in _sys.path:
            _sys.path.insert(0, hsk_dir)
        import hsk_lib
        return hsk_lib
    except Exception:  # noqa: BLE001
        return None


def _strip_punct(text: str) -> str:
    """Drop punctuation/whitespace so script-correspondence compares WORDS only —
    a ，/。 difference between Hant and Hans is a style nit (warn), not a meaning
    mismatch (block)."""
    return "".join(c for c in text or ""
                   if unicodedata.category(c)[0] not in ("P", "Z", "C"))


def _srow_get(row, key):
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _fmt_field(f) -> str:
    fp = f["field_path"]
    return f"questionOption[{f['option_index']}]" if fp == "questionOption" else fp


def _format_issues(fp: str, si, text: str) -> list[dict]:
    out = []
    for a, b in _PAIRS:
        if text.count(a) != text.count(b):
            out.append({"scene_index": si, "field_path": fp,
                        "issue": f"unbalanced {a}…{b} in the edited text",
                        "severity": "warn"})
    if "  " in text.replace("\n", " "):
        out.append({"scene_index": si, "field_path": fp,
                    "issue": "double space in the edited text", "severity": "warn"})
    if text != text.strip():
        out.append({"scene_index": si, "field_path": fp,
                    "issue": "leading/trailing whitespace in the edited text",
                    "severity": "warn"})
    return out


def _zh_field_issues(f, loc: dict, hsk) -> list[dict]:
    """Checks for ONE changed _ZH localization field. `hsk` may be None (degraded)."""
    cur, orig = loc.get("cur") or {}, loc.get("orig") or {}
    si, fp = f["scene_index"], _fmt_field(f)
    hans, hant = cur.get("Hans") or "", cur.get("Hant") or ""
    zhuyin, en = cur.get("zhuyin") or "", cur.get("en") or ""
    issues: list[dict] = []

    present = [s for s in _ZH_SCRIPTS if cur.get(s) is not None]
    changed = [s for s in present if (cur.get(s) or "") != (orig.get(s) or "")]
    unchanged = [s for s in present if s not in changed]

    # -- partial sibling edit: the #1 field-consistency failure (34/34 on 2026-07-08) --
    if changed and unchanged:
        issues.append({"scene_index": si, "field_path": fp,
                       "issue": f"{'+'.join(changed)} changed but {', '.join(unchanged)} "
                                "unchanged — update the siblings (or confirm no change was "
                                "needed, e.g. punctuation-only)",
                       "severity": "warn"})

    # -- English sibling reminder (meaning drift is Gate 2's judgment; this is the nudge) --
    if changed and cur.get("en") is not None and (cur.get("en") or "") == (orig.get("en") or ""):
        issues.append({"scene_index": si, "field_path": fp,
                       "issue": "target text changed but the English translation wasn't — "
                                "check it still matches",
                       "severity": "note"})
    if changed and (orig.get("en") or "") and not en:
        issues.append({"scene_index": si, "field_path": fp,
                       "issue": "English translation was emptied", "severity": "block"})

    if hsk is None:
        return issues   # mechanical script checks need hsk_lib

    # -- script purity: traditional characters inside the Simplified box --
    try:
        simp_of_hans = hsk.to_simplified(hans)
    except Exception:  # noqa: BLE001
        simp_of_hans = hans
    if simp_of_hans != hans:
        bad = sorted({a for a, b in zip(hans, simp_of_hans) if a != b})
        issues.append({"scene_index": si, "field_path": fp,
                       "issue": f"traditional character(s) {'/'.join(bad)} inside the "
                                "Simplified text — use the simplified forms "
                                f"({'/'.join(hsk.to_simplified(c) for c in bad)})",
                       "severity": "block"})

    # -- Hant must be the traditional form of Hans (compare via to_simplified: stable).
    #    Punctuation-only differences are a WARN, not a block (dave, 2026-07-08). --
    if hant:
        try:
            simp_of_hant = hsk.to_simplified(hant)
        except Exception:  # noqa: BLE001
            simp_of_hant = None
        if simp_of_hant is not None and simp_of_hant != simp_of_hans:
            if _strip_punct(simp_of_hant) == _strip_punct(simp_of_hans):
                issues.append({"scene_index": si, "field_path": fp,
                               "issue": "Traditional and Simplified differ only in "
                                        "punctuation — align them when convenient",
                               "severity": "warn"})
            else:
                issues.append({"scene_index": si, "field_path": fp,
                               "issue": "Traditional text doesn't correspond to the "
                                        "Simplified text — they must say the same thing",
                               "severity": "block"})

    # -- zhuyin must align syllable-by-syllable with the (simplified) spoken text --
    if cur.get("zhuyin") is not None and fp != "tripgroup_description":
        if hans and not zhuyin:
            issues.append({"scene_index": si, "field_path": fp,
                           "issue": "zhuyin is empty", "severity": "block"})
        elif hans:
            try:
                _, warns = hsk.zhuyin_to_pinyin(zhuyin, simp_of_hans or hans)
            except Exception as e:  # noqa: BLE001
                warns = [f"aligner raised {e}"]
            if warns:
                issues.append({"scene_index": si, "field_path": fp,
                               "issue": "zhuyin doesn't align with the Simplified text "
                                        f"({warns[0]}) — update the zhuyin to match",
                               "severity": "block"})

    # -- format checks on the changed scripts --
    for s in changed:
        issues += _format_issues(fp, si, cur.get(s) or "")
    return issues


def _level_issue(f, loc: dict, lchk) -> dict | None:
    """WARN if this edit introduced vocabulary above the trip's HSK band (zh_level).

    Never blocks: an out-of-band word is a legitimate i+1 choice, it just has to be a
    conscious one. Diffed against the ORIGINAL text so the reviewer is only asked about
    words their own edit added, not ones the approved draft already carried.
    """
    cur, orig = loc.get("cur") or {}, loc.get("orig") or {}
    hans, orig_hans = cur.get("Hans") or "", orig.get("Hans") or ""
    if not hans or hans == orig_hans:
        return None
    try:
        new_out = zh_level.introduced_out_of_band(orig_hans, hans, lchk)
    except Exception:  # noqa: BLE001 — a level check must never break a submit
        return None
    if not new_out:
        return None
    words = "、".join(f"{w} ({lvl})" if lvl != "not-in-list" else w
                      for w, lvl in new_out.items())
    return {"scene_index": f["scene_index"], "field_path": _fmt_field(f),
            "issue": f"this edit introduces {words} — above {zh_level.band_label(lchk.band)} "
                     "for this trip. Keep it only if the word is worth teaching here; "
                     "otherwise use a simpler one",
            "severity": "warn"}


def run_checks(frows, is_zh: bool, trip_id: str = "") -> tuple[list[dict], list[dict]]:
    """Gate-1 deterministic checks over a session's field rows.
    Returns (hard, soft) in validate()'s issue shape."""
    hard: list[dict] = []
    soft: list[dict] = []
    hsk = _hsk() if is_zh else None
    hsk_warned = False
    # None when the trip has no HSK band, or jieba/the vocab snapshot is unavailable
    # (e.g. a host without the reference) — the level check then simply stays silent.
    lchk = zh_level.checker_for(trip_id) if is_zh else None

    for f in frows:
        loc_raw = _srow_get(f, "localization_json")
        if is_zh and loc_raw:
            loc = json.loads(loc_raw)
            if (loc.get("cur") or {}) == (loc.get("orig") or {}):
                continue
            if hsk is None and not hsk_warned:
                soft.append({"scene_index": None, "field_path": "*",
                             "issue": "hsk_lib unavailable — script-consistency checks "
                                      "degraded to reminders only",
                             "severity": "warn"})
                hsk_warned = True
            for issue in _zh_field_issues(f, loc, hsk):
                (hard if issue["severity"] == "block" else soft).append(issue)
            if lchk is not None:
                lvl_issue = _level_issue(f, loc, lchk)
                if lvl_issue:
                    soft.append(lvl_issue)
            continue

        # ---- non-ZH: target text vs its editable English sibling ----
        cur_t = f["current_text"] or ""
        if cur_t != (f["original_text"] or ""):
            src, orig_src = (_srow_get(f, "source_text") or ""), (_srow_get(f, "original_source") or "")
            if src and src == orig_src:
                soft.append({"scene_index": f["scene_index"], "field_path": _fmt_field(f),
                             "issue": "text changed but the English translation wasn't — "
                                      "check it still matches",
                             "severity": "note"})
            soft.extend(_format_issues(_fmt_field(f), f["scene_index"], cur_t))

    return hard, soft
