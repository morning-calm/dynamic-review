"""TripGroup description review (tripdesc.py): tooltip derivation, stage transitions,
role scoping, and the EN-target (Scotland/UK) shortcut. Staging writes + the claude
translation are mocked — this exercises the workflow machinery only."""

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db as appdb            # noqa: E402
from app import tripdesc               # noqa: E402
from app.auth import User              # noqa: E402


ADMIN = User(id=1, username="dave", role="admin")
FR = User(id=2, username="claire", role="reviewer", languages=["French"])
JP = User(id=3, username="toshi", role="reviewer", languages=["Japanese"])


@pytest.fixture(autouse=True)
def mem_db(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(appdb.SCHEMA)
    monkeypatch.setattr(appdb, "_CONN", conn)
    yield conn
    conn.close()


@pytest.fixture
def staged(monkeypatch):
    """Capture TripGroup writes; suppress the background translation thread."""
    writes: list[tuple[str, dict]] = []
    monkeypatch.setattr(tripdesc, "_write_tripgroup",
                        lambda tg_id, fields: writes.append((tg_id, fields)))
    spawned: list[str] = []
    monkeypatch.setattr(tripdesc, "_spawn_translation", spawned.append)
    return writes, spawned


def _seed_row(tg_id="Monaco2_Beg_FR", language="French", en="Prose one. More.\n\nTrip Type: History",
              tl="Prose un. Encore.\n\nType de voyage : Histoire"):
    now = time.time()
    appdb.execute(
        "INSERT INTO tripgroup_reviews(tg_id, language, family, rep_trip_id, status, "
        "en_text, en_original, tl_text, tl_original, categories_json, created_at, "
        "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (tg_id, language, "Monaco2", "Monaco2_A12_FR", "pending_en",
         en, en, tl, tl, json.dumps(["History"]), now, now))
    return tg_id


# ---------------------------------------------------------------- tooltip rule
def test_first_sentence_takes_up_to_first_period():
    assert tripdesc.first_sentence("A trip. Second sentence.") == "A trip."
    assert tripdesc.first_sentence("No period here") == "No period here"
    assert tripdesc.first_sentence("") == ""


def test_tooltip_joins_target_then_home_with_blank_line():
    assert tripdesc.build_tooltip("Un voyage. Suite.", "A trip. More.") == \
        "Un voyage.\n\nA trip."
    assert tripdesc.build_tooltip("", "A trip. More.") == "A trip."
    assert tripdesc.build_tooltip("Un voyage.", "") == "Un voyage."


# ------------------------------------------------------------- stage A: EN check
def test_admin_saves_en_text_and_categories(staged):
    tg = _seed_row()
    out = tripdesc.save(tg, ADMIN, en_text="New prose. Rest.", categories=["History", "Food"])
    assert out["en_text"] == "New prose. Rest."
    assert out["categories"] == ["History", "Food"]


def test_reviewer_cannot_edit_en(staged):
    tg = _seed_row()
    with pytest.raises(HTTPException) as e:
        tripdesc.save(tg, FR, en_text="nope")
    assert e.value.status_code == 403


def test_approve_en_writes_home_and_categories_then_translates(staged):
    writes, spawned = staged
    tg = _seed_row()
    out = tripdesc.approve_en(tg, ADMIN)
    assert out["status"] == "translating"
    assert spawned == [tg]
    (wtg, fields), = writes
    assert wtg == tg
    assert fields["descriptionHome"].startswith("Prose one.")
    assert fields["tripCategories"] == ["History"]
    # tooltip: live TL first sentence + new EN first sentence
    assert fields["tooltip"] == "Prose un.\n\nProse one."


def test_approve_en_english_target_completes_in_one_step(staged):
    writes, spawned = staged
    tg = _seed_row(tg_id="Edinburgh1", language="English", tl="")
    out = tripdesc.approve_en(tg, ADMIN)
    assert out["status"] == "done"
    assert spawned == []
    (_, fields), = writes
    assert fields["descriptionTarget"] == fields["descriptionHome"]
    assert fields["tooltip"] == "Prose one.\n\nProse one."


def test_approve_en_refuses_empty_description(staged):
    tg = _seed_row(en="")
    with pytest.raises(HTTPException) as e:
        tripdesc.approve_en(tg, ADMIN)
    assert e.value.status_code == 422


# ------------------------------------------------------------ stage C: TL review
def _to_pending_tl(tg):
    appdb.execute("UPDATE tripgroup_reviews SET status='pending_tl' WHERE tg_id=?", (tg,))


def test_reviewer_sees_only_pending_tl_in_their_language(staged):
    tg = _seed_row()
    assert tripdesc.list_items(FR)["items"] == []          # pending_en: admin's stage
    _to_pending_tl(tg)
    assert [i["tg_id"] for i in tripdesc.list_items(FR)["items"]] == [tg]
    assert tripdesc.list_items(JP)["items"] == []          # wrong language
    with pytest.raises(HTTPException):
        tripdesc.get_item(tg, JP)


def test_translator_saves_and_approves_tl(staged):
    writes, _ = staged
    tg = _seed_row()
    _to_pending_tl(tg)
    tripdesc.save(tg, FR, tl_text="Corrigé. Suite.")
    out = tripdesc.approve_tl(tg, FR)
    assert out["status"] == "done"
    (wtg, fields), = writes
    assert fields["descriptionTarget"] == "Corrigé. Suite."
    assert fields["tooltip"].startswith("Corrigé.")


def test_approve_tl_wrong_status_409(staged):
    tg = _seed_row()
    with pytest.raises(HTTPException) as e:
        tripdesc.approve_tl(tg, FR)
    assert e.value.status_code == 409


# ------------------------------------------------------------------ badge counts
def test_counts_are_role_and_language_scoped(staged):
    a = _seed_row(tg_id="A_FR", language="French")
    _seed_row(tg_id="B_JP", language="Japanese")
    assert tripdesc.counts_for(ADMIN)["open"] == 2          # two English checks
    assert tripdesc.counts_for(FR)["open"] == 0
    _to_pending_tl(a)
    assert tripdesc.counts_for(ADMIN)["open"] == 1
    assert tripdesc.counts_for(FR)["open"] == 1
    assert tripdesc.counts_for(JP)["open"] == 0


def test_seed_trips_backfill_is_idempotent(staged, monkeypatch):
    # UK-style TripGroup: description lives in descriptionTarget only (target = EN);
    # an EN-target family must seed its English text from there.
    monkeypatch.setattr(tripdesc, "_tripgroup_index", lambda force=False: (
        {"Melrose_EN": "Melrose"},
        {"Melrose": {"descriptionHome": "", "descriptionTarget": "A town. More.",
                     "tripCategories": ["History"]}}))
    out = tripdesc.seed_trips(["Melrose_EN", "Unknown_EN"])
    assert out["seeded"] == ["Melrose"]
    assert out["skipped"] == ["Unknown_EN"]
    row = appdb.query_one("SELECT * FROM tripgroup_reviews WHERE tg_id='Melrose'")
    assert row["status"] == "pending_en" and row["en_text"] == "A town. More."
    assert row["language"] == "English"          # _EN suffix → EN-target family
    # Second run: nothing re-seeded, nothing clobbered.
    out2 = tripdesc.seed_trips(["Melrose_EN"])
    assert out2["seeded"] == [] and out2["skipped"] == ["Melrose_EN"]


# -------------------------------------------------- production-snapshot seed guard
def _manifest_env(tmp_path, monkeypatch, trips, prod_ids=None):
    from app import config
    man = tmp_path / "trips_to_review.json"
    man.write_text(json.dumps({"trips": trips}), encoding="utf-8")
    monkeypatch.setattr(config, "MANIFEST_PATH", man)
    prod = tmp_path / "prod_tripgroups.json"
    if prod_ids is not None:
        prod.write_text(json.dumps({"tripgroup_ids": prod_ids}), encoding="utf-8")
    monkeypatch.setattr(config, "PROD_TRIPGROUPS_PATH", prod)
    monkeypatch.setattr(tripdesc, "_tripgroup_index", lambda force=False: (
        {"Monaco2_A12_IT": "Monaco_IT", "Kyoto1_Beg_N5_JP": "Kyoto1_Beg_JP"},
        {"Monaco_IT": {"descriptionHome": "En.", "descriptionTarget": "It.",
                       "tripCategories": []},
         "Kyoto1_Beg_JP": {"descriptionHome": "En.", "descriptionTarget": "",
                           "tripCategories": []}}))


def test_seed_skips_tripgroups_already_on_production(tmp_path, monkeypatch, staged):
    _manifest_env(tmp_path, monkeypatch,
                  [{"trip_id": "Monaco2_A12_IT", "language": "Italian"},
                   {"trip_id": "Kyoto1_Beg_N5_JP", "language": "Japanese"}],
                  prod_ids=["Monaco_IT"])
    out = tripdesc.seed_from_manifest()
    assert out == {"seeded": 1, "skipped": 0, "skipped_prod": 1}
    assert appdb.query_one(
        "SELECT 1 FROM tripgroup_reviews WHERE tg_id='Monaco_IT'") is None
    assert appdb.query_one(
        "SELECT 1 FROM tripgroup_reviews WHERE tg_id='Kyoto1_Beg_JP'") is not None


def test_seed_missing_snapshot_degrades_to_seed_everything(tmp_path, monkeypatch, staged):
    _manifest_env(tmp_path, monkeypatch,
                  [{"trip_id": "Monaco2_A12_IT", "language": "Italian"}],
                  prod_ids=None)   # no snapshot file at all
    out = tripdesc.seed_from_manifest()
    assert out["seeded"] == 1 and out["skipped_prod"] == 0


def test_explicit_backfill_ignores_prod_snapshot(tmp_path, monkeypatch, staged):
    _manifest_env(tmp_path, monkeypatch, [], prod_ids=["Monaco_IT"])
    out = tripdesc.seed_trips(["Monaco2_A12_IT"])
    assert out["seeded"] == ["Monaco_IT"]   # the "review it anyway" escape hatch


def test_reopen_returns_to_pending_en(staged):
    tg = _seed_row()
    _to_pending_tl(tg)
    out = tripdesc.reopen(tg, ADMIN)
    assert out["status"] == "pending_en"
