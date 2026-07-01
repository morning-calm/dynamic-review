"""End-to-end SELECTION-tool splice validation (real ElevenLabs + audio_splice.do_splice)
+ align-verify. Writes orig/spliced mp3 pairs to research/cjk-aligner/out/selection/ for
the human seam listen. Mirrors validate_cjk.py but drives cjk_splice.plan_cjk_span (the
highlight / alt-text path) instead of the diff path.

Uses EL credits (one clause TTS per case). Run from the repo:
    cd backend
    PYTHONIOENCODING=utf-8 <py3.12> ../research/cjk-aligner/validate_cjk_selection.py
"""
import sys
from pathlib import Path
sys.path.insert(0, r"D:\Projects\WebApp\review-app\backend")
from app import cjk_splice, audio_splice, audio_io, cjk_align  # noqa: E402

OUT = Path(__file__).resolve().parent / "out" / "selection"
OUT.mkdir(parents=True, exist_ok=True)
ZH_BASE = r"D:\Projects\WebApp\review-app\backend\work\sess_5bc56203b40a\v2\1.mp3"
JP_BASE = r"D:\Projects\WebApp\review-app\backend\work\sess_928f51fd60fd\working\1.mp3"
ZH = "这是高雄的莲池潭。它是一个很大的湖，水很干净。夏天的时候，湖里开很多花，非常漂亮。"
JP = ("ここはとうきょうすかいつりーで、にっぽんでいちばんたかいびるです。せかいでも3ばんめにたかくて、"
      "たかさは634めーとるもあります。この634というすうじは、むかしのこのまちのなまえ「むさし」とよむこともできます。")
# annasu = the FEMALE Mandarin voice — MUST match the sess_5bc56203b40a masters (female).
# (yu/jason are male; splicing them into the female original gives a voice mismatch.)
ANNASU = ("9lHjugDhwqoxA5MhX0az", {"speed": 0.85, "stability": 0.5, "similarity_boost": 0.75}, "eleven_multilingual_v2")
FUMI = ("PmgfHCGeS5b7sH90BOOJ", {"speed": 1.0, "stability": 0.5, "similarity_boost": 0.75}, "eleven_v3")

i_gj = ZH.index("干净")
i_tak = JP.index("たかいびる")

# (name, base, old(=what the audio says), new(=displayed), span in NEW, alt|None, voice, lang,
#  say = the text the SPLICED audio should now say, for align-verify)
CASES = [
    # Regenerate highlighted, text unchanged: fresh take of 水很干净 spliced in place.
    ("zh_hl",  ZH_BASE, ZH, ZH, (i_gj, i_gj + 2), None,  ANNASU, "zh", ZH),
    # …with alt text: audio says 清澈 where the text still shows 干净.
    ("zh_alt", ZH_BASE, ZH, ZH, (i_gj, i_gj + 2), "清澈", ANNASU, "zh", ZH.replace("干净", "清澈")),
    # JP highlight, text unchanged: fresh take of clause 2 (kana line).
    ("jp_hl",  JP_BASE, JP, JP, (i_tak, i_tak + 3), None, FUMI, "jp", JP),
    # JP alt: audio says おおきい where the kana still shows たかい.
    ("jp_alt", JP_BASE, JP, JP, (i_tak, i_tak + 3), "おおきい", FUMI, "jp",
     JP.replace("たかいびる", "おおきいびる")),
]


def mean(path, text):
    ch = cjk_align.align(str(path), text)
    ok = [c for c in ch if c["start"] is not None]
    return sum(c["score"] for c in ok) / max(1, len(ok))


readme = ["Selection-tool splice validation — orig vs spliced pairs for the seam listen.",
          "Voices: ZH=annasu (female, matches masters), JP=fumi. Same assembly as production", ""]
for name, base, old, new, span, alt, (vid, vs, model), lang, say in CASES:
    print(f"\n### {name} ({lang}, {'alt=' + alt if alt else 'highlight'})")
    plan = cjk_splice.plan_cjk_span(base, old, new, span, alt, vid, vs, model, lang)
    if plan is None:
        print("  plan_cjk_span -> None (would fall back)")
        readme.append(f"{name}: PLAN BAILED (no files)")
        continue
    d = plan.meta["cjk_detail"]
    print(f"  revoice {d['revoiced_old']!r} -> {plan.meta['phrase']!r}  "
          f"tL={plan.meta['tL']:.3f} tR={plan.meta['tR']:.3f}")
    cand = OUT / f"{name}_cand.mp3"; cand.write_bytes(plan.candidate_mp3)
    base_s = audio_io.mp3_to_samples(base); cand_s = audio_io.mp3_to_samples(cand)
    res = audio_splice.do_splice(base_s, cand_s, plan.meta)
    audio_io.mp3_to_mp3_copy(base, OUT / f"{name}_orig.mp3")
    audio_io.samples_to_mp3(res.samples, OUT / f"{name}_spliced.mp3")
    v_new = mean(OUT / f"{name}_spliced.mp3", say)
    v_old = mean(OUT / f"{name}_spliced.mp3", old) if say != old else v_new
    print(f"  spliced {len(res.samples)/audio_io.SR:.2f}s (orig {len(base_s)/audio_io.SR:.2f}s) "
          f"conf={res.confidence} gain={res.detail.get('gain_db'):.2f}dB")
    print(f"  align-verify: says-expected={v_new:.3f}" +
          (f"  says-old={v_old:.3f}" if say != old else "  (text unchanged)"))
    readme.append(f"{name}: {d['revoiced_old']!r} -> {plan.meta['phrase']!r} "
                  f"@ {plan.meta['tL']:.2f}-{plan.meta['tR']:.2f}s; listen for the seams there.")
(OUT / "README.txt").write_text("\n".join(readme), encoding="utf-8")
cjk_align.shutdown()
print(f"\norig/spliced mp3 pairs in {OUT}")
