import json
import os
import shutil
from pathlib import Path
import requests

BASE = "http://127.0.0.1:8000"
SPIKE = Path(__file__).resolve().parent
WORK = Path(r"D:\Projects\WebApp\review-app\backend\work")

_user = os.environ.get("REVIEW_USER", "admin")
_pw = os.environ.get("REVIEW_PASS")
if not _pw:
    raise SystemExit("Set REVIEW_USER / REVIEW_PASS (the admin login) to run this.")
tok = requests.post(f"{BASE}/api/login", json={"username": _user, "password": _pw}).json()["token"]
H = {"Authorization": f"Bearer {tok}"}

sids = [d for d in os.listdir(WORK) if (WORK / d / "orig").is_dir()]
found = None
for sid in sids:
    try:
        j = requests.get(f"{BASE}/api/sessions/{sid}", headers=H).json()
    except Exception:
        continue
    tid = (j.get("trip_id") or "")
    if not tid.upper().endswith("_JP"):
        continue
    for sc in j.get("scenes", []):
        for f in sc.get("fields", []):
            if f["field_path"] == "SceneDesc" and f.get("has_audio") and (f.get("current_text") or "").strip():
                si = f["scene_index"]
                mp3 = f"{si}.mp3"
                src = WORK / sid / "working" / mp3
                if not src.exists():
                    src = WORK / sid / "orig" / mp3
                if src.exists():
                    shutil.copyfile(src, SPIKE / "clips" / f"jp_{mp3}")
                    found = {"clip": f"jp_{mp3}", "trip_id": tid, "scene_index": si, "text": f["current_text"]}
                    break
        if found:
            break
    if found:
        break

if found:
    (SPIKE / "jp_manifest.json").write_text(json.dumps([found], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JP clip: {found['clip']} trip={found['trip_id']} scene={found['scene_index']} chars={len(found['text'])}")
else:
    print(f"no JP session with audio+text found among {len(sids)} sessions")
