"""4K static-360 panoramas — the display copy of an ``isStaticImage`` scene.

A PIC scene ships as a 7680×7680 equirectangular JPEG (~15 MB). That is the master the
VR app consumes; it is a terrible thing to put in an ``<img>`` on a review card, and
until now the review app served exactly that — one 15 MB image per static scene, over
the tunnel, on a phone.

Dave keeps a 4K mono re-encode of every one of them (4096×2048, ~1 MB) at
``STATIC_4K_ROOT``, laid out ``<Country>/<Region>/<leaf>/<sceneIndex>-4k.jpg``. This
module maps a trip + scene index onto that tree; sessions serves the result in place of
the master and mirrors it to R2 (the live Ubuntu host has none of these trees locally).

⚠ The leaf folder names do NOT reliably encode the trip id. Some are trip ids
(``AviemoreInverness_A12_EN``), some are bare locations (``Bude``), and — the trap —
some locations have BOTH a ``Bude`` and a ``Bude_Beg`` folder whose scene indices
DISAGREE (7,8,9,10 vs 6,7,8,9). Serving scene 9's panorama from the wrong one would
show the reviewer a different place entirely and they would have no way to know.

So a folder is only accepted when EVERY index it holds is a real static scene of this
trip (``folder ⊆ want``). Name resolution proposes; the index set disposes:

  * ``Bude_A12_EN`` wants {7,8,9,10}. ``Bude`` holds exactly {7,8,9,10} → accepted.
    ``Bude_Beg`` holds {6,7,8,9}; 6 is not a static scene here, so the folder is
    numbering something else → rejected outright, even for the 7/8/9 it shares.
  * ``Tokyo_03`` wants {2..8} but only {2,3,4,6,7} were ever re-encoded. That is a
    clean subset — every image maps to a real static scene — so it is accepted and
    scenes 5 and 8 individually fall back to their master.

Read the test as: a folder that numbers scenes differently from this trip will hold at
least one index that isn't a static scene here, and that is what condemns it. A trip
that matches nothing just serves the 7680 master — big, but never the wrong place.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from . import config

_LOCK = threading.RLock()
_INDEX: dict[str, dict[int, Path]] | None = None   # normkey(leaf) -> {scene index: path}

_FILE_RE = re.compile(r"^(\d+)-4k\.jpg$", re.IGNORECASE)
# The filename the review app asks for; also what the R2 mirror is keyed by.
NAME_RE = re.compile(r"^(\d+)-4k\.jpg$", re.IGNORECASE)

_NORM_RE = re.compile(r"[\s_\-]+")
# Level/language tokens that sit between a location and the trip id's tail, stripped to
# recover the bare location a leaf folder may be named for (Bude_A12_EN -> Bude).
_LEVEL_RE = re.compile(r"_(?:A12|B1|B2|Beg|N[45]|HSK\d+)(?=_|$)", re.IGNORECASE)
_LANG_RE = re.compile(r"_(?:EN|JP|ZH|ES|FR|DE|IT)$", re.IGNORECASE)


def name_for(index: int) -> str:
    return f"{index}-4k.jpg"


def _norm(s: str) -> str:
    return _NORM_RE.sub("", s or "").lower()


def _build() -> dict[str, dict[int, Path]]:
    idx: dict[str, dict[int, Path]] = {}
    root = config.STATIC_4K_ROOT
    if not root or not root.is_dir():
        print(f"[static360] no 4K tree at {root} — static-360 scenes keep the full-size "
              "master (or the R2 copy)")
        return idx
    for p in root.rglob("*.jpg"):
        m = _FILE_RE.match(p.name)
        if not m:
            continue
        idx.setdefault(_norm(p.parent.name), {})[int(m.group(1))] = p
    print(f"[static360] indexed {sum(len(v) for v in idx.values())} 4K panoramas "
          f"across {len(idx)} folders")
    return idx


def _index() -> dict[str, dict[int, Path]]:
    global _INDEX
    if _INDEX is None:
        with _LOCK:
            if _INDEX is None:
                _INDEX = _build()
    return _INDEX


def candidate_names(trip_id: str, base_ids: list[str]) -> list[str]:
    """Folder names to try, most specific first: the trip id, then the display-image
    base ids the app already reduces to (``sessions._image_base_ids``), then the bare
    location (level + language tokens stripped)."""
    names = [trip_id, *base_ids]
    stem = _LANG_RE.sub("", trip_id or "")
    prev = None
    while stem != prev:                     # e.g. Tokyo_03_Beg_N4 -> Tokyo_03
        prev, stem = stem, _LEVEL_RE.sub("", stem)
    stem = _LANG_RE.sub("", stem)
    if stem:
        names.append(stem)
    seen, out = set(), []
    for n in names:
        k = _norm(n)
        if k and k not in seen:
            seen.add(k)
            out.append(n)
    return out


def resolve(trip_id: str, base_ids: list[str], want_indices: set[int],
            index: int) -> Path | None:
    """The 4K panorama for scene ``index``, or None.

    ``want_indices`` is every isStaticImage index of THIS trip. A candidate folder is
    only trusted when every index it carries is one of them — see the module docstring:
    two folders for the same location can number their scenes differently, and a
    partial match would serve a confidently-wrong image. A folder that is merely
    INCOMPLETE (not every scene was re-encoded) is fine: it still passes, and the
    scenes it lacks fall back to their master individually."""
    if index not in want_indices:
        return None
    idx = _index()
    for name in candidate_names(trip_id, base_ids):
        folder = idx.get(_norm(name))
        if folder and set(folder.keys()).issubset(want_indices):
            hit = folder.get(index)
            if hit is not None:
                return hit
    return None
