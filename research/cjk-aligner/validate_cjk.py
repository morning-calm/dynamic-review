"""End-to-end CJK splice validation (real ElevenLabs + audio_splice.do_splice) + align-verify.
Writes orig/spliced mp3 pairs to research/cjk-aligner/out/ for the human seam listen.

Uses EL credits (one clause TTS per case). Run from the repo:
    cd backend
    PYTHONIOENCODING=utf-8 <py3.12> ../research/cjk-aligner/validate_cjk.py
"""
import sys
from pathlib import Path
sys.path.insert(0, r"D:\Projects\WebApp\review-app\backend")
from app import cjk_splice, audio_splice, audio_io, cjk_align  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)
ZH = r"D:\Projects\WebApp\review-app\backend\work\sess_5bc56203b40a"
JP = r"D:\Projects\WebApp\review-app\backend\work\sess_928f51fd60fd"
ZH_OLD = "这是高雄的莲池潭。它是一个很大的湖，水很干净。夏天的时候，湖里开很多花，非常漂亮。"
JP_OLD = ("ここはとうきょうすかいつりーで、にっぽんでいちばんたかいびるです。せかいでも3ばんめにたかくて、"
          "たかさは634めーとるもあります。この634というすうじは、むかしのこのまちのなまえ「むさし」とよむこともできます。")
YU = ("fQj4gJSexpu8RDE2Ii5m", {"speed": 0.85, "stability": 0.5, "similarity_boost": 0.75}, "eleven_multilingual_v2")
FUMI = ("PmgfHCGeS5b7sH90BOOJ", {"speed": 1.0, "stability": 0.5, "similarity_boost": 0.75}, "eleven_v3")

CASES = [
    ("zh_mid",   rf"{ZH}\v2\1.mp3",      ZH_OLD, ZH_OLD.replace("干净", "清澈"),         YU,   "zh"),
    ("zh_first", rf"{ZH}\v2\1.mp3",      ZH_OLD, ZH_OLD.replace("高雄的莲池潭", "台北的莲池潭"), YU, "zh"),
    ("jp_mid",   rf"{JP}\working\1.mp3", JP_OLD, JP_OLD.replace("いちばんたかいびる", "いちばんおおきいびる"), FUMI, "jp"),
]

def mean(path, text):
    ch = cjk_align.align(str(path), text)
    ok = [c for c in ch if c["start"] is not None]
    return sum(c["score"] for c in ok) / max(1, len(ok))

for name, base, old, new, (vid, vs, model), lang in CASES:
    print(f"\n### {name} ({lang})")
    plan = cjk_splice.plan_cjk(base, old, new, vid, vs, model, lang)
    if plan is None:
        print("  plan_cjk -> None (would whole-regen)")
        continue
    d = plan.meta["cjk_detail"]
    print(f"  revoice {d['revoiced_old']!r} -> {d['revoiced_new']!r}  tL={plan.meta['tL']:.3f} tR={plan.meta['tR']:.3f}")
    cand = OUT / f"{name}_cand.mp3"; cand.write_bytes(plan.candidate_mp3)
    base_s = audio_io.mp3_to_samples(base); cand_s = audio_io.mp3_to_samples(cand)
    res = audio_splice.do_splice(base_s, cand_s, plan.meta)
    audio_io.mp3_to_mp3_copy(base, OUT / f"{name}_orig.mp3")
    audio_io.samples_to_mp3(res.samples, OUT / f"{name}_spliced.mp3")
    print(f"  spliced {len(res.samples)/audio_io.SR:.2f}s (orig {len(base_s)/audio_io.SR:.2f}s) "
          f"conf={res.confidence} gain={res.detail.get('gain_db'):.2f}dB")
    print(f"  align-verify: NEW={mean(OUT / f'{name}_spliced.mp3', new):.3f}  OLD={mean(OUT / f'{name}_spliced.mp3', old):.3f}")
cjk_align.shutdown()
print(f"\norig/spliced mp3 pairs in {OUT}")
