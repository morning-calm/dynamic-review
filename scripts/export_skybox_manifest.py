"""Export the shipped-skybox manifest for the Final-check TripLocation editor
(docs/post-approval-admin-spec.md §2.4).

`TripLocations.skyboxTextureId` resolves in the headset to
``s3://vr-languages-public-content/360_Skyboxes/<id>.jpg``
(DownloadManager.GetSkyboxUrl; an Addressables build asset under
``Assets/_Textures/Skyboxes`` is the offline fallback for some locations). So the
authoritative inventory is the S3 listing, merged with the Unity checkout's tree;
the UI warns (never blocks) on an id outside this manifest.

Run on the WORKSTATION (S3 creds come from the Scripts repo's .env, same as
publish_audio_gate), commit the JSON like the VideoIds snapshot:

    py -3.12 scripts/export_skybox_manifest.py
    # -> skybox_manifest.json at the repo root; commit + push, pull on the laptop

Env overrides: REVIEW_APP_UNITY_ROOT (default D:\\Projects\\dynamic-languages),
REVIEW_APP_SCRIPTS_ROOT (default D:\\Dynamic Languages\\Scripts).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "skybox_manifest.json"
UNITY_ROOT = Path(os.environ.get("REVIEW_APP_UNITY_ROOT",
                                 r"D:\Projects\dynamic-languages"))
SCRIPTS_ROOT = Path(os.environ.get("REVIEW_APP_SCRIPTS_ROOT",
                                   r"D:\Dynamic Languages\Scripts"))
SKYBOX_DIR = UNITY_ROOT / "Assets" / "_Textures" / "Skyboxes"
S3_PREFIX = "360_Skyboxes/"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".exr", ".hdr"}


def s3_ids() -> list[str] | None:
    """Skybox ids on S3 (the runtime source of truth), or None on any failure."""
    try:
        sys.path.insert(0, str(SCRIPTS_ROOT))
        import publish_audio_gate as gate
        s3 = gate.make_s3_client()
        if s3 is None:
            return None
        ids: list[str] = []
        for page in s3.get_paginator("list_objects_v2").paginate(
                Bucket=gate.BUCKET, Prefix=S3_PREFIX):
            for obj in page.get("Contents", []):
                name = obj["Key"][len(S3_PREFIX):]
                stem, dot, ext = name.rpartition(".")
                if dot and ("." + ext).lower() in IMAGE_EXTS and stem:
                    ids.append(stem)
        return ids
    except Exception as e:  # noqa: BLE001
        print(f"!! S3 skybox listing failed: {e}")
        return None


def unity_ids() -> dict[str, str]:
    """{stem: country folder} from the Unity checkout's build-asset tree."""
    out: dict[str, str] = {}
    if not SKYBOX_DIR.is_dir():
        print(f"   (no Unity tree at {SKYBOX_DIR} — S3 only)")
        return out
    for p in sorted(SKYBOX_DIR.rglob("*")):
        if p.suffix.lower() in IMAGE_EXTS:
            rel = p.relative_to(SKYBOX_DIR).parts
            out.setdefault(p.stem, rel[0] if len(rel) > 1 else "")
    return out


def main() -> int:
    from_s3 = s3_ids()
    from_unity = unity_ids()
    if from_s3 is None and not from_unity:
        print("!! neither S3 nor the Unity tree yielded anything — manifest NOT "
              "written (previous kept)")
        return 1
    merged: dict[str, dict] = {}
    for sid in from_s3 or []:
        merged[sid] = {"id": sid, "on_s3": True,
                       "folder": from_unity.get(sid, "")}
    for sid, folder in from_unity.items():
        merged.setdefault(sid, {"id": sid, "on_s3": False, "folder": folder})
    payload = {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": f"s3:{S3_PREFIX} ({'ok' if from_s3 is not None else 'FAILED'}) "
                  f"+ {SKYBOX_DIR}",
        "count": len(merged),
        "skyboxes": sorted(merged.values(), key=lambda s: s["id"].lower()),
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"wrote {len(merged)} skybox ids ({len(from_s3 or [])} on S3) -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
