"""Red-team the CJK SELECTION tools (backend/app/cjk_splice.plan_span_cuts /
plan_cjk_span / char_times + the remove-pause math) — NO ElevenLabs, fast.

Every highlighted span must either splice SAFELY (both cuts in real silence, confident
non-digit clause enders — the same bar as redteam_cjk.py) or BAIL; the pure text→text and
DSP helpers are asserted exactly. plan_cjk_span's EL call is stubbed so the phrase/meta
construction (incl. alt-text substitution) is tested end-to-end without credits.

Run from the repo (needs the backend deps + the aligner venv at research/cjk-aligner/venv):
    cd backend
    PYTHONIOENCODING=utf-8 <py3.12> ../research/cjk-aligner/redteam_cjk_selection.py
"""
import sys

import numpy as np

sys.path.insert(0, r"D:\Projects\WebApp\review-app\backend")
from app import audio_core, audio_io, cjk_align, cjk_splice, sessions  # noqa: E402

ZH_BASE = r"D:\Projects\WebApp\review-app\backend\work\sess_5bc56203b40a\v2\1.mp3"  # pristine
JP_BASE = r"D:\Projects\WebApp\review-app\backend\work\sess_928f51fd60fd\working\1.mp3"
ZH = "这是高雄的莲池潭。它是一个很大的湖，水很干净。夏天的时候，湖里开很多花，非常漂亮。"
JP = ("ここはとうきょうすかいつりーで、にっぽんでいちばんたかいびるです。せかいでも3ばんめにたかくて、"
      "たかさは634めーとるもあります。この634というすうじは、むかしのこのまちのなまえ「むさし」とよむこともできます。")

fails = 0


def check(name, ok, info=""):
    global fails
    if not ok:
        fails += 1
    print(f"[{'OK ' if ok else 'XXX'}] {name}{('  ' + info) if info else ''}")


def safe(detail, lang) -> bool:
    """The redteam_cjk safety bar: interior cuts in real silence + anchor conf floors."""
    floor = 0.90 if lang == "zh" else 0.50
    sil = ((detail["left_pause"] or detail["cutL_energy"] < detail["sil_thr"])
           and (detail["right_pause"] or detail["cutR_energy"] < detail["sil_thr"]))
    conf = ((detail["left_pause"] or detail["anchorL_conf"] >= floor)
            and (detail["right_pause"] or detail["anchorR_conf"] >= floor))
    return sil and conf


# ---------------------------------------------------------------------------- #
# A. map_new_span_to_old — pure text→text (no audio)
# ---------------------------------------------------------------------------- #
m = cjk_splice.map_new_span_to_old
check("map identity", m("ABCDE", "ABCDE", 1, 3) == (1, 3))
check("map replace", m("ABCDE", "ABXDE", 2, 3) == (2, 3))
check("map insert → zero-width old point", m("ABDE", "ABCDE", 2, 3) == (2, 2))
check("map bridges a deletion", m("ABCDE", "ABDE", 1, 3) == (1, 4))
check("map boundary caret", m("ABDE", "ABCDE", 2, 2)[0] == m("ABDE", "ABCDE", 2, 2)[1])

# ---------------------------------------------------------------------------- #
# B. plan_span_cuts — highlight geometry on the real takes
#    (name, base, old, new, span in NEW, lang, expect splice|bail|either, [asserts])
# ---------------------------------------------------------------------------- #
i_gj = ZH.index("干净")
i_gx = ZH.index("高雄")
i_pl = ZH.index("漂亮")
i_hu = ZH.index("湖，")
i_hua = ZH.index("花")
ZH_EDIT = ZH.replace("干净", "清澈")
ZH_INS = ZH.replace("水很干净。", "水很干净。湖水很蓝。")
i_tak = JP.index("たかいびる")
i_634 = JP.index("634")

CASES = [
    ("ZH hl 干净 (old==new)",      ZH_BASE, ZH, ZH, (i_gj, i_gj + 2), "zh", "splice",
     {"revoiced": "水很干净", "hl_txt": "干净"}),
    ("ZH hl spans 2 clauses 湖→花", ZH_BASE, ZH, ZH, (i_hu, i_hua + 1), "zh", "either", {}),
    ("ZH hl first clause 高雄",     ZH_BASE, ZH, ZH, (i_gx, i_gx + 2), "zh", "splice",
     {"tL0": True}),
    ("ZH hl last clause 漂亮",      ZH_BASE, ZH, ZH, (i_pl, i_pl + 2), "zh", "splice", {}),
    ("ZH hl whole text",           ZH_BASE, ZH, ZH, (0, len(ZH)), "zh", "bail", {}),
    ("ZH hl w/ pending edit 清澈",  ZH_BASE, ZH, ZH_EDIT,
     (ZH_EDIT.index("清澈"), ZH_EDIT.index("清澈") + 2), "zh", "splice",
     {"revoiced": "水很清澈", "hl_txt": "清澈"}),
    ("ZH hl inside an insertion",   ZH_BASE, ZH, ZH_INS,
     (ZH_INS.index("湖水很蓝"), ZH_INS.index("湖水很蓝") + 4), "zh", "either", {}),
    ("ZH stale: mismatched audio",  JP_BASE, ZH, ZH, (i_gj, i_gj + 2), "zh", "bail", {}),
    ("JP hl たかい (old==new)",     JP_BASE, JP, JP, (i_tak, i_tak + 3), "jp", "splice",
     {"hl_txt": "たかい"}),
    ("JP hl digit-adjacent 634",    JP_BASE, JP, JP, (i_634, i_634 + 3), "jp", "either", {}),
]

for name, base, old, new, span, lang, expect, extra in CASES:
    c = cjk_splice.plan_span_cuts(base, old, new, lang, span)
    got = "splice" if c else "bail"
    ok = expect == "either" or got == expect
    info = got
    if c:
        d = c["detail"]
        info += (f" tL={c['tL']:.2f} tR={c['tR']:.2f} conf={d['anchorL_conf']}/"
                 f"{d['anchorR_conf']} {d['revoiced_old']!r}->{c['revoiced_new']!r}")
        if not safe(d, lang):
            ok = False
            info += "  <<< SAFETY VIOLATION"
        if extra.get("revoiced") and c["revoiced_new"] != extra["revoiced"]:
            ok = False
            info += f"  <<< revoiced != {extra['revoiced']!r}"
        if extra.get("hl_txt"):
            hs, he = c["hl_rel"]
            if c["revoiced_new"][hs:he] != extra["hl_txt"]:
                ok = False
                info += f"  <<< hl_rel slice {c['revoiced_new'][hs:he]!r} != {extra['hl_txt']!r}"
        if extra.get("tL0") and c["tL"] != 0.0:
            ok = False
            info += "  <<< expected clip-start cut"
    check(name, ok, info)

# ---------------------------------------------------------------------------- #
# C. plan_cjk_span meta/phrase construction — EL stubbed (no credits, no network)
# ---------------------------------------------------------------------------- #
_real_gen = audio_core.generate_with_timestamps
_calls = []


def _stub_gen(text, vid, vs, prev=None, nxt=None, model_id=None):
    _calls.append({"text": text, "prev": prev, "nxt": nxt, "model": model_id})
    return b"MP3", [{"word": text, "start": 0.0, "end": 1.0}]


audio_core.generate_with_timestamps = _stub_gen
try:
    p = cjk_splice.plan_cjk_span(ZH_BASE, ZH, ZH, (i_gj, i_gj + 2), None,
                                 "v", {}, "eleven_multilingual_v2", "zh")
    check("span plan (highlight): phrase = clause",
          p is not None and p.meta["phrase"] == "水很干净"
          and p.meta["span_only"] and p.meta["cjk"] and p.meta["cjk_lang"] == "zh",
          p.meta["phrase"] if p else "bail")
    check("span plan: v2 keeps EL context", _calls[-1]["prev"] is not None)
    p = cjk_splice.plan_cjk_span(ZH_BASE, ZH, ZH, (i_gj, i_gj + 2), "清彻",
                                 "v", {}, "eleven_multilingual_v2", "zh")
    check("span plan (alt): substituted inside the clause",
          p is not None and p.meta["phrase"] == "水很清彻",
          p.meta["phrase"] if p else "bail")
    p = cjk_splice.plan_cjk_span(JP_BASE, JP, JP, (i_tak, i_tak + 3), "たっかい",
                                 "v", {}, "eleven_v3", "jp")
    check("span plan (JP alt, v3): no EL context",
          p is not None and "たっかい" in p.meta["phrase"] and _calls[-1]["prev"] is None,
          p.meta["phrase"] if p else "bail")
finally:
    audio_core.generate_with_timestamps = _real_gen

# ---------------------------------------------------------------------------- #
# D. char_times + the pause-tool seed chain (no DB — the DSP the tools sit on)
# ---------------------------------------------------------------------------- #
ct = cjk_splice.char_times(ZH_BASE, ZH, "zh")
check("char_times: good ZH placed + gated", ct is not None and ct[1] >= 0.5,
      f"mean={ct[1]:.3f}" if ct else "None")
check("char_times: stale text → None", cjk_splice.char_times(JP_BASE, ZH, "zh") is None)

pos, _mean = ct
base_s = audio_io.mp3_to_samples(ZH_BASE)
sr = audio_io.SR
caret = ZH.index("。") + 1                      # right after the first full stop
seed = max((q for q in pos if q < caret), default=None)
seed_ok = seed is not None and not audio_core.is_numberish(ZH[seed])
run = audio_io.silence_run_nearest(base_s, sr, pos[seed][1], 0.4, 0.4) if seed_ok else None
check("caret after 。: seed = clause ender, real pause found",
      seed_ok and run is not None and abs(run[0] - pos[seed][1]) <= 0.45,
      f"seed={ZH[seed]!r}@{pos[seed][1]:.2f}s run={run}" if run else "no run")
mid_caret = ZH.index("很干净")                  # mid-clause (connected speech)
mseed = max((q for q in pos if q < mid_caret), default=None)
mrun = audio_io.silence_run_nearest(base_s, sr, pos[mseed][1], 0.4, 0.4)
print(f"      (info) mid-clause caret → run={mrun} (None = insert/remove would 409 there)")

# ---------------------------------------------------------------------------- #
# E. remove-pause arithmetic on a synthetic voiced–silence–voiced signal
# ---------------------------------------------------------------------------- #
rng = np.random.default_rng(0)
voice = (0.3 * rng.standard_normal(int(0.8 * sr))).astype(np.float32)
# NB long pauses are reported CLIPPED at silence_run_nearest's scan buffer (t_anchor +
# fwd + 0.7s) — a SUBSET of true silence, so cuts stay safe and one click may remove a
# bit under `seconds` on a pathological pause. Assert the invariants, not an exact amount.
for pause_s, removable in ((1.5, True), (0.28, False)):
    sig = np.concatenate([voice, np.zeros(int(pause_s * sr), np.float32), voice])
    run = audio_io.silence_run_nearest(sig, sr, 0.9, 0.4, 0.4)
    if run is None:
        check(f"remove-pause synth {pause_s}s: run found", False)
        continue
    run_len = run[1] - run[0]
    remove = min(1.0, run_len - sessions._REMOVE_PAUSE_KEEP)
    if not removable:
        check(f"remove-pause synth {pause_s}s: refuses (nothing to spare)", remove < 0.05,
              f"run={run_len:.2f}s removable={remove:.2f}s")
        continue
    c0 = run[0] + (run_len - remove) / 2.0
    a, b = int(round(c0 * sr)), int(round((c0 + remove) * sr))
    out = np.concatenate([sig[:a], sig[b:]])
    kept = run_len - remove
    edges_silent = sig[a] == 0.0 and sig[min(b, len(sig) - 1)] == 0.0
    voiced_kept = np.sum(np.abs(out) > 1e-6) >= 2 * len(voice) - int(0.01 * sr)
    check(f"remove-pause synth {pause_s}s: −{remove:.2f}s inside silence, voice intact, "
          f"keeps ≥{sessions._REMOVE_PAUSE_KEEP}s",
          0.5 <= remove <= 1.0 and edges_silent and voiced_kept
          and kept >= sessions._REMOVE_PAUSE_KEEP - 0.01,
          f"run={run_len:.2f}s kept={kept:.2f}s out={len(out)/sr:.2f}s")

cjk_align.shutdown()
print(f"\n{'ALL OK' if fails == 0 else str(fails) + ' FAILURE(S)'}")
sys.exit(1 if fails else 0)
