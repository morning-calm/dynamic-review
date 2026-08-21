"""Trip-list cache (2026-08-21): GET /api/trips serves from the trip_list_cache table
instead of doing a Firestore read + R2 audio probe per manifest entry per request
(that scaled ~N×latency/16 and hit ~90s once the JP batch pushed the manifest past
~600 trips). Never-seen entries fetch synchronously once; stale rows serve their
cached values and re-fetch in the background; reviewable rows go stale far slower
than unreviewable ones (config.TRIP_CACHE_TTL_*); invalidate_trip_cache forces an
immediate re-probe (refresh_trips.py `clear` does the same via SQL)."""

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config    # noqa: E402
from app import db as appdb  # noqa: E402
from app import sessions  # noqa: E402


@pytest.fixture(autouse=True)
def mem_db(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(appdb.SCHEMA)
    monkeypatch.setattr(appdb, "_CONN", conn)
    yield conn
    conn.close()


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    def write(trip_ids):
        p = tmp_path / "trips_to_review.json"
        p.write_text(json.dumps({"trips": [{"trip_id": t, "lane": "6"}
                                           for t in trip_ids]}), encoding="utf-8")
        monkeypatch.setattr(config, "MANIFEST_PATH", p)
    return write


@pytest.fixture
def fetches(monkeypatch):
    """Stub the per-trip network I/O, record which trips it runs for, and run the
    background refresher inline so assertions are deterministic."""
    calls: list[str] = []

    def fake(entry):
        calls.append(entry["trip_id"])
        return ({"contentTitleKey": f"Title {entry['trip_id']}",
                 "folderName": "Folder"}, True, 42.0)

    monkeypatch.setattr(sessions, "_fetch_trip_and_reviewable", fake)
    monkeypatch.setattr(sessions, "fb_db", lambda: None)
    monkeypatch.setattr(sessions, "_refresh_trip_cache_async",
                        sessions._refresh_entries)
    return calls


def _set_row(tid, *, reviewable, age_s):
    appdb.execute("UPDATE trip_list_cache SET reviewable=?, fetched_at=? "
                  "WHERE trip_id=?", (int(reviewable), time.time() - age_s, tid))


def test_first_listing_fetches_once_then_serves_from_cache(manifest, fetches):
    manifest(["A_EN", "B_EN"])
    out = sessions._list_trips_from_manifest()
    assert sorted(fetches) == ["A_EN", "B_EN"]
    assert out[0]["title"] == "Title A_EN"
    assert out[0]["folder_name"] == "Folder"
    assert out[0]["reviewable"] is True
    assert out[0]["duration_sec"] == 42.0

    fetches.clear()
    out2 = sessions._list_trips_from_manifest()
    assert fetches == []                      # warm listing: zero network work
    assert out2[0]["title"] == "Title A_EN"


def test_fresh_reviewable_row_is_not_refetched_but_stale_unreviewable_is(
        manifest, fetches):
    manifest(["A_EN", "B_EN"])
    sessions._list_trips_from_manifest()
    fetches.clear()
    # A: reviewable, 2h old — inside the 12h TTL. B: unreviewable, 2h old — well
    # past the 30min TTL (it is waiting for audio to land).
    _set_row("A_EN", reviewable=True, age_s=2 * 3600)
    _set_row("B_EN", reviewable=False, age_s=2 * 3600)
    out = sessions._list_trips_from_manifest()
    assert fetches == ["B_EN"]
    # the stale value was still SERVED this request (refresh is background)
    assert [t["reviewable"] for t in out] == [True, False]
    fetches.clear()
    out = sessions._list_trips_from_manifest()  # refreshed row now serves fresh
    assert fetches == []
    assert [t["reviewable"] for t in out] == [True, True]


def test_invalidate_forces_immediate_refetch(manifest, fetches):
    manifest(["A_EN", "B_EN"])
    sessions._list_trips_from_manifest()
    fetches.clear()
    assert sessions.invalidate_trip_cache(["B_EN"]) == 1
    sessions._list_trips_from_manifest()
    assert fetches == ["B_EN"]


def test_missing_staging_doc_lists_with_manifest_title_and_unreviewable(
        manifest, monkeypatch):
    manifest(["Ghost_JP"])
    monkeypatch.setattr(sessions, "fb_db", lambda: None)
    monkeypatch.setattr(sessions, "_fetch_trip_and_reviewable",
                        lambda e: (None, False, None))
    monkeypatch.setattr(sessions, "_refresh_trip_cache_async",
                        sessions._refresh_entries)
    out = sessions._list_trips_from_manifest()
    assert out[0]["title"] == "Ghost_JP"
    assert out[0]["reviewable"] is False
    assert out[0]["folder_name"] == ""
