import json
from pathlib import Path

SPIKE = Path(__file__).resolve().parent
m = json.loads((SPIKE / "jp_manifest.json").read_text(encoding="utf-8"))[0]
kana = m["text"].split("\n")[-1].strip()   # the spoken (phonetic) line
out = [{"clip": m["clip"], "trip_id": m["trip_id"], "scene_index": m["scene_index"], "text": kana}]
(SPIKE / "jp_kana_manifest.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print("kana-only chars:", len(kana))
