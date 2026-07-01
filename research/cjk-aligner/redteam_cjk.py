"""Red-team the CJK cut planner (backend/app/cjk_splice.plan_cuts) — NO ElevenLabs, fast.
Every adversarial edit must either splice SAFELY (both cuts in real silence, confident
non-digit clause enders) or BAIL to whole-regen (None).

Run from the repo (needs the backend deps + the aligner venv at research/cjk-aligner/venv):
    cd backend
    PYTHONIOENCODING=utf-8 <py3.12> ../research/cjk-aligner/redteam_cjk.py
"""
import sys
sys.path.insert(0, r"D:\Projects\WebApp\review-app\backend")
from app import cjk_splice, cjk_align  # noqa: E402

ZH_BASE = r"D:\Projects\WebApp\review-app\backend\work\sess_5bc56203b40a\v2\1.mp3"  # pristine
JP_BASE = r"D:\Projects\WebApp\review-app\backend\work\sess_928f51fd60fd\working\1.mp3"
ZH = "这是高雄的莲池潭。它是一个很大的湖，水很干净。夏天的时候，湖里开很多花，非常漂亮。"
JP = ("ここはとうきょうすかいつりーで、にっぽんでいちばんたかいびるです。せかいでも3ばんめにたかくて、"
      "たかさは634めーとるもあります。この634というすうじは、むかしのこのまちのなまえ「むさし」とよむこともできます。")

# (name, base, old, new, lang, expect in {"splice","bail","either"})
CASES = [
    ("ZH single clause 干净→清澈",         ZH_BASE, ZH, ZH.replace("干净", "清澈"), "zh", "splice"),
    ("ZH multi-clause 干净→清澈 + 漂亮→美丽", ZH_BASE, ZH, ZH.replace("干净", "清澈").replace("漂亮", "美丽"), "zh", "splice"),
    ("ZH deletion-only (drop clause 3)",   ZH_BASE, ZH, ZH.replace("夏天的时候，湖里开很多花，", ""), "zh", "either"),
    ("ZH near-total rewrite (你好)",        ZH_BASE, ZH, "你好，欢迎来到高雄。", "zh", "bail"),
    ("ZH stale: mismatched base audio",    JP_BASE, ZH, ZH.replace("干净", "清澈"), "zh", "bail"),
    ("JP clause2 digit-free たかい→おおきい",  JP_BASE, JP, JP.replace("いちばんたかいびる", "いちばんおおきいびる"), "jp", "splice"),
    ("JP number-bearing clause4 めーとる→キロ", JP_BASE, JP, JP.replace("634めーとる", "634キロ"), "jp", "either"),
    ("JP first clause とうきょう→おおさか",    JP_BASE, JP, JP.replace("とうきょうすかいつりー", "おおさかすかいつりー"), "jp", "either"),
]

fails = 0
for name, base, old, new, lang, expect in CASES:
    c = cjk_splice.plan_cuts(base, old, new, lang)
    got = "splice" if c else "bail"
    ok = expect == "either" or got == expect
    line = f"[{'OK ' if ok else 'XXX'}] {name}: {got}"
    if c:
        d = c["detail"]
        sil = (d["left_pause"] or d["cutL_energy"] < d["sil_thr"]) and (d["right_pause"] or d["cutR_energy"] < d["sil_thr"])
        floor = 0.90 if lang == "zh" else 0.50
        conf = (d["left_pause"] or d["anchorL_conf"] >= floor) and (d["right_pause"] or d["anchorR_conf"] >= floor)
        line += (f"  tL={c['tL']:.2f} tR={c['tR']:.2f} conf={d['anchorL_conf']}/{d['anchorR_conf']}"
                 f" sil={sil} conf_ok={conf}  {d['revoiced_old']!r}->{d['revoiced_new']!r}")
        if not (sil and conf):
            line += "  <<< SAFETY VIOLATION"
            fails += 1
    if not ok:
        fails += 1
    print(line)
cjk_align.shutdown()
print(f"\n{'ALL OK' if fails == 0 else str(fails) + ' FAILURE(S)'}")
sys.exit(1 if fails else 0)
