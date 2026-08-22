"""Perf-cache layer for the Publisher (2026-08-22): the Releases-board per-rung
probes are SQLite-materialised (release_probe_cache — the trip_list_cache pattern:
serve stale, sync-fetch only never-seen rungs, background refresh, explicit
invalidation), the R2 bus snapshots + job listing are short-TTL cached with
serve-stale-on-failure, and release batches resolve/socially-probe correctly."""

import json
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db as appdb            # noqa: E402
from app import final_checks           # noqa: E402
from app import release_batches        # noqa: E402
from app import review_bus             # noqa: E402
from app import tripdesc               # noqa: E402

ADMIN = SimpleNamespace(username="dave", is_admin=True)


@pytest.fixture(autouse=True)
def mem_db(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(appdb.SCHEMA)
    monkeypatch.setattr(appdb, "_CONN", conn)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def fresh_caches():
    final_checks._probe_mem.clear()
    final_checks._probe_loaded = False
    review_bus._snap_cache.clear()
    review_bus._jobs_cache.update({"at": 0.0, "jobs": None})
    yield
    final_checks._probe_mem.clear()
    final_checks._probe_loaded = False
    review_bus._snap_cache.clear()
    review_bus._jobs_cache.update({"at": 0.0, "jobs": None})


# --------------------------------------------------------------------------- #
# release_probe_cache
# --------------------------------------------------------------------------- #
def test_doc_probe_fetches_once_then_serves_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(final_checks, "_probe_fetch", lambda tid: (
        calls.append(tid) or {"has_keyword": True, "static_scenes": 2,
                              "keyword_keys": {0: "castle"}}))
    p1 = final_checks._doc_probe("Foo_A12_EN")
    p2 = final_checks._doc_probe("Foo_A12_EN")
    assert calls == ["Foo_A12_EN"]           # one network fetch
    assert p1["has_keyword"] and p2["static_scenes"] == 2
    row = appdb.query_one("SELECT * FROM release_probe_cache WHERE trip_id=?",
                          ("Foo_A12_EN",))
    assert row and row["has_keyword"] == 1   # materialised


def test_probe_survives_process_restart_via_table(monkeypatch):
    final_checks._store_probe("Foo_A12_EN", {"has_keyword": False,
                                             "static_scenes": 3,
                                             "keyword_keys": {1: "loch"}})
    # simulate a restart: memory gone, table intact
    final_checks._probe_mem.clear()
    final_checks._probe_loaded = False
    monkeypatch.setattr(final_checks, "_probe_fetch",
                        lambda tid: pytest.fail("must serve from the table"))
    p = final_checks._doc_probe("Foo_A12_EN")
    assert p["static_scenes"] == 3
    assert p["keyword_keys"] == {1: "loch"}  # int keys round-trip the JSON column


def test_prewarm_sync_fetches_missing_and_backgrounds_stale(monkeypatch):
    fetched, refreshed = [], []
    monkeypatch.setattr(final_checks, "_probe_fetch", lambda tid: (
        fetched.append(tid) or {"has_keyword": False, "static_scenes": 0,
                                "keyword_keys": {}}))
    monkeypatch.setattr(final_checks, "_refresh_probes_async",
                        lambda tids: refreshed.extend(tids))
    final_checks._store_probe("Old_A12_EN", {"has_keyword": False,
                                             "static_scenes": 0, "keyword_keys": {}})
    appdb.execute("UPDATE release_probe_cache SET fetched_at=? WHERE trip_id=?",
                  (time.time() - final_checks._PROBE_TTL_S - 1, "Old_A12_EN"))
    final_checks._probe_mem.clear()
    final_checks._probe_loaded = False        # reload from the table (restart-like)
    final_checks._prewarm_probes({"New_A12_EN", "Old_A12_EN"})
    assert fetched == ["New_A12_EN"]         # missing: sync, in-request
    assert refreshed == ["Old_A12_EN"]       # stale: served, refreshed off-request


def test_invalidate_probes_forces_refetch(monkeypatch):
    n = [0]

    def fetch(tid):
        n[0] += 1
        return {"has_keyword": False, "static_scenes": n[0], "keyword_keys": {}}
    monkeypatch.setattr(final_checks, "_probe_fetch", fetch)
    assert final_checks._doc_probe("Foo_A12_EN")["static_scenes"] == 1
    final_checks.invalidate_probes(["Foo_A12_EN"])
    assert final_checks._doc_probe("Foo_A12_EN")["static_scenes"] == 2
    final_checks.invalidate_probes(None)
    assert not appdb.query("SELECT * FROM release_probe_cache")


def test_probe_fetch_failure_degrades_uncached(monkeypatch):
    def boom(tid):
        raise RuntimeError("firestore down")
    monkeypatch.setattr(final_checks, "_probe_fetch", boom)
    p = final_checks._doc_probe("Foo_A12_EN")
    assert p == {"has_keyword": False, "static_scenes": 0, "keyword_keys": {}}
    assert "Foo_A12_EN" not in final_checks._probe_mem   # retried next time


# --------------------------------------------------------------------------- #
# review_bus snapshot + jobs caches
# --------------------------------------------------------------------------- #
class _FakeS3:
    def __init__(self, payloads):
        self.payloads, self.gets = payloads, 0

    def get_object(self, Bucket, Key):
        self.gets += 1
        body = json.dumps(self.payloads[Key]).encode("utf-8")
        return {"Body": SimpleNamespace(read=lambda b=body: b)}


def test_finalised_snapshot_ttl_and_invalidate(monkeypatch):
    s3 = _FakeS3({review_bus.FINALISED_KEY: {
        "trips": [{"trip_id": "Foo_A12_EN", "finalised_at": "2026-08-16T00:00:00Z"}]}})
    monkeypatch.setattr(review_bus, "_r2", lambda: s3)
    a = review_bus.get_finalised_snapshot()
    b = review_bus.get_finalised_snapshot()
    assert "Foo_A12_EN" in a and a == b
    assert s3.gets == 1                        # second call served from the TTL cache
    review_bus.invalidate_snapshots()
    review_bus.get_finalised_snapshot()
    assert s3.gets == 2                        # invalidation forces the re-fetch


def test_snapshot_serves_stale_on_failure(monkeypatch):
    s3 = _FakeS3({review_bus.STATIC4K_KEY: {"Foo_A12_EN": {"built_at": 1}}})
    monkeypatch.setattr(review_bus, "_r2", lambda: s3)
    assert "Foo_A12_EN" in review_bus.get_static4k_snapshot()
    review_bus.invalidate_snapshots()

    def broken():
        raise RuntimeError("r2 down")
    monkeypatch.setattr(review_bus, "_r2", broken)
    # the last good snapshot survives an outage (previously: everything blanked)
    assert "Foo_A12_EN" in review_bus.get_static4k_snapshot()


def test_list_jobs_default_shape_cached_and_filtered(monkeypatch):
    jobs = [{"id": "j2", "trip_id": "Foo", "requested_at": 2},
            {"id": "j1", "trip_id": "Bar", "requested_at": 1}]
    calls = []
    monkeypatch.setattr(review_bus, "_list_jobs_uncached",
                        lambda tid, limit, cap: calls.append(1) or list(jobs))
    assert len(review_bus.list_jobs()) == 2
    assert [j["id"] for j in review_bus.list_jobs(trip_id="Foo")] == ["j2"]
    assert len(calls) == 1                     # both served by one uncached walk
    review_bus.invalidate_jobs_cache()
    review_bus.list_jobs()
    assert len(calls) == 2


# --------------------------------------------------------------------------- #
# release batches
# --------------------------------------------------------------------------- #
@pytest.fixture
def batch_indexes(monkeypatch):
    docs = {"Foo": {"trips": ["Foo_A12_EN", "Foo_B1_EN"]},
            "Bar": {"trips": ["Bar_A12_EN"]}}
    monkeypatch.setattr(tripdesc, "_tripgroup_index",
                        lambda force=False: ({}, docs))
    monkeypatch.setattr(final_checks, "_loc_full_index",
                        lambda: {"Foo": [("LOC1", "Edinburgh", "Scotland")],
                                 "Bar": [("LOC1", "Edinburgh", "Scotland")]})


def test_batch_save_resolve_roundtrip(batch_indexes):
    b = release_batches.save_batch(
        "Scotland Borders", [{"kind": "group", "id": "Foo"},
                             {"kind": "trip", "id": "Solo_A12_EN"}], ADMIN)
    assert b["resolved"]["trip_ids"] == ["Foo_A12_EN", "Foo_B1_EN", "Solo_A12_EN"]
    # a location member pulls in every group on that TripLocation (doc id or name)
    b2 = release_batches.save_batch(
        "Edinburgh wave", [{"kind": "location", "id": "Edinburgh"}], ADMIN)
    assert set(b2["resolved"]["group_ids"]) == {"Foo", "Bar"}
    # same-name save updates in place, no duplicate
    release_batches.save_batch("Scotland Borders",
                               [{"kind": "group", "id": "Bar"}], ADMIN)
    names = [x["name"] for x in release_batches.list_batches()["batches"]]
    assert names.count("Scotland Borders") == 1


def test_batch_bad_member_rejected(batch_indexes):
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        release_batches.save_batch("X", [{"kind": "family", "id": "Foo"}], ADMIN)


def test_social_state_matches_loosely(tmp_path, monkeypatch, batch_indexes):
    d = tmp_path / "Social Posts"
    d.mkdir()
    (d / "LochNess_GreatGlen_meta.txt").write_text("x", encoding="utf-8")
    (d / "LochNess_GreatGlen_linkedin.txt").write_text("x", encoding="utf-8")
    news = tmp_path / "news.json"
    news.write_text(json.dumps([{"title": "Loch Ness & Great Glen"}]),
                    encoding="utf-8")
    monkeypatch.setattr(release_batches, "SOCIAL_DIR", d)
    monkeypatch.setattr(release_batches, "NEWS_JSON", news)
    s = release_batches.social_state("Loch Ness - Great Glen")
    assert s["state"] == "ready" and s["news"] is True
    assert release_batches.social_state("Mandarinx3")["state"] == "missing"
    monkeypatch.setattr(release_batches, "SOCIAL_DIR", tmp_path / "nope")
    assert release_batches.social_state("Anything")["state"] == "unknown"


def test_recently_published_window(batch_indexes):
    now = time.time()
    appdb.execute("INSERT INTO published_trips(trip_id,published_at,published_by,"
                  "source) VALUES(?,?,?,?)", ("New_A12_EN", now - 86400, "dave",
                                              "publisher"))
    appdb.execute("INSERT INTO published_trips(trip_id,published_at,published_by,"
                  "source) VALUES(?,?,?,?)", ("Ancient_EN", now - 400 * 86400, "",
                                              "trello_backfill"))
    out = release_batches.recently_published(12)
    assert [t["trip_id"] for t in out["trips"]] == ["New_A12_EN"]
