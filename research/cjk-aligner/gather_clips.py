import json
import os
import shutil
from pathlib import Path
import requests

BASE = "http://127.0.0.1:8000"
SID = "sess_5bc56203b40a"
SPIKE = Path(__file__).resolve().parent
V2 = Path(r"D:\Projects\WebApp\review-app\backend\work") / SID / "v2"

_user = os.environ.get("REVIEW_USER", "admin")
_pw = os.environ.get("REVIEW_PASS")
if not _pw:
    raise SystemExit("Set REVIEW_USER / REVIEW_PASS (the admin login) to run this.")
tok = requests.post(f"{BASE}/api/login", json={"username": _user, "password": _pw}).json()["token"]
H = {"Authorization": f"Bearer {tok}"}
j = requests.get(f"{BASE}/api/sessions/{SID}", headers=H).json()

cand = []
for sc in j.get("scenes", []):
    for f in sc.get("fields", []):
        if f.get("field_path") == "SceneDesc" and f.get("has_audio"):
            hans = ((f.get("localization") or {}).get("cur") or {}).get("Hans") or ""
            si = f.get("scene_index")
            src = V2 / f"{si}.mp3"
            if hans.strip() and src.exists():
                cand.append({"clip": f"{si}.mp3", "scene_index": si, "hans": hans, "chars": len(hans)})

cand.sort(key=lambda m: m["chars"], reverse=True)
sel = cand[:3]
(SPIKE / "clips").mkdir(exist_ok=True)
for m in sel:
    shutil.copyfile(V2 / m["clip"], SPIKE / "clips" / m["clip"])
(SPIKE / "manifest.json").write_text(json.dumps(sel, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"{len(cand)} SceneDesc clips available; selected {len(sel)} longest:")
for m in sel:
    print(f"  {m['clip']} scene={m['scene_index']} chars={m['chars']}: {m['hans'][:36]}...")
