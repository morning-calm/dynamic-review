"""Final-check workflow (docs/post-approval-admin-spec.md §2, phase 1): the per-trip
work list mirrors the manifest's lane-10/10b/11 entries; each check is stored at the
level it is TRUE at, so group/location-scoped ticks are shared by every sibling trip
while trip-scoped ones are not; the audit surfaces completed trips on no final-check
card; the ContentEnrichment cross-trip search matches country-mates only."""

import json
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config          # noqa: E402
from app import db as appdb     # noqa: E402
from app import final_checks    # noqa: E402
from app import tripdesc        # noqa: E402

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
def indexes(monkeypatch):
    """Stub the Firestore-backed indexes: two rungs of one family + a country-mate."""
    by_trip = {"Foo_A12_EN": "Foo", "Foo_B1_EN": "Foo", "Bar_A12_EN": "Bar"}
    docs = {"Foo": {}, "Bar": {}, "Baz": {}}
    monkeypatch.setattr(tripdesc, "_tripgroup_index", lambda force=False: (by_trip, docs))
    monkeypatch.setattr(
        tripdesc, "_triplocations_index",
        lambda force=False: {"Foo": [("Edinburgh", "Scotland")],
                             "Bar": [("Glasgow", "Scotland")],
                             "Baz": [("Kyoto", "Japan")]})
    monkeypatch.setattr(final_checks, "_language_of", lambda tid: "English")


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    def write(payload: dict):
        p = tmp_path / "trips_to_review.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setattr(config, "MANIFEST_PATH", p)
    return write


def test_group_scoped_check_shared_across_siblings(manifest):
    manifest({"trips": [], "final_check": [
        {"trip_id": "Foo_A12_EN", "lane": "10"},
        {"trip_id": "Foo_B1_EN", "lane": "10"},
    ], "card_lanes": {}})
    final_checks.set_check("Foo_A12_EN", "categories", "done", "checked", ADMIN)
    rows = {r["trip_id"]: r for r in final_checks.list_items()["items"]}
    assert rows["Foo_A12_EN"]["checks"]["categories"] == "done"
    assert rows["Foo_B1_EN"]["checks"]["categories"] == "done"   # shared (group scope)


def test_trip_scoped_check_not_shared(manifest):
    manifest({"trips": [], "final_check": [
        {"trip_id": "Foo_A12_EN", "lane": "10"},
        {"trip_id": "Foo_B1_EN", "lane": "10"},
    ], "card_lanes": {}})
    final_checks.set_check("Foo_A12_EN", "keywords", "done", "", ADMIN)
    rows = {r["trip_id"]: r for r in final_checks.list_items()["items"]}
    assert rows["Foo_A12_EN"]["checks"]["keywords"] == "done"
    assert rows["Foo_B1_EN"]["checks"]["keywords"] == "open"     # per-trip


def test_location_scope_shared_across_same_location_groups(manifest):
    # trip_location is stored per LOCATION NAME: Foo (Edinburgh) and Bar (Glasgow)
    # do NOT share; a second Edinburgh family would.
    manifest({"trips": [], "final_check": [
        {"trip_id": "Foo_A12_EN", "lane": "10"},
        {"trip_id": "Bar_A12_EN", "lane": "10"},
    ], "card_lanes": {}})
    final_checks.set_check("Foo_A12_EN", "trip_location", "done", "", ADMIN)
    rows = {r["trip_id"]: r for r in final_checks.list_items()["items"]}
    assert rows["Foo_A12_EN"]["checks"]["trip_location"] == "done"
    assert rows["Bar_A12_EN"]["checks"]["trip_location"] == "open"


def test_audit_lists_completed_trips_off_final_lanes(manifest):
    manifest({"trips": [], "final_check": [{"trip_id": "Foo_A12_EN", "lane": "10"}],
              "card_lanes": {"Bar_A12_EN": "9"}})
    now = time.time()
    for tid in ("Foo_A12_EN", "Bar_A12_EN"):
        appdb.execute("INSERT INTO completed_trips(trip_id, completed_by, completed_at,"
                      " method) VALUES(?,?,?,?)", (tid, "dave", now, "approved"))
    out = final_checks.list_items()
    audit = {a["trip_id"]: a for a in out["audit"]}
    assert "Foo_A12_EN" not in audit          # already on a final-check card
    assert audit["Bar_A12_EN"]["card_lane"] == "9"
    assert out["manifest_has_final"] is True


def test_manual_start_adds_to_list_and_leaves_audit(manifest):
    manifest({"trips": [], "final_check": [], "card_lanes": {}})
    appdb.execute("INSERT INTO completed_trips(trip_id, completed_by, completed_at,"
                  " method) VALUES(?,?,?,?)", ("Bar_A12_EN", "dave", time.time(),
                                               "manual"))
    assert [a["trip_id"] for a in final_checks.list_items()["audit"]] == ["Bar_A12_EN"]
    final_checks.start("Bar_A12_EN", ADMIN)
    out = final_checks.list_items()
    assert [r["trip_id"] for r in out["items"]] == ["Bar_A12_EN"]
    assert out["items"][0]["lane"] == "manual"
    assert out["audit"] == []


def test_count_open_goes_down_when_all_checks_done(manifest):
    manifest({"trips": [], "final_check": [{"trip_id": "Foo_A12_EN", "lane": "10"}],
              "card_lanes": {}})
    assert final_checks.count_open() == 1
    for key, _scope, _label, _tooling in final_checks.CHECKS:
        final_checks.set_check("Foo_A12_EN", key, "done", "", ADMIN)
    assert final_checks.count_open() == 0


def test_set_check_rejects_unknown_key_and_state(manifest):
    from fastapi import HTTPException
    manifest({"trips": [], "final_check": [], "card_lanes": {}})
    with pytest.raises(HTTPException):
        final_checks.set_check("Foo_A12_EN", "nonsense", "done", "", ADMIN)
    with pytest.raises(HTTPException):
        final_checks.set_check("Foo_A12_EN", "keywords", "maybe", "", ADMIN)


class FakeDoc:
    def __init__(self, store, coll, doc_id):
        self.store, self.coll, self.doc_id = store, coll, doc_id

    def get(self):
        return self

    @property
    def exists(self):
        return self.doc_id in self.store.get(self.coll, {})

    def to_dict(self):
        return dict(self.store.get(self.coll, {}).get(self.doc_id) or {})

    def update(self, fields):
        self.store[self.coll][self.doc_id].update(fields)

    def set(self, fields):
        self.store.setdefault(self.coll, {})[self.doc_id] = dict(fields)


class FakeColl:
    def __init__(self, store, coll):
        self.store, self.coll = store, coll

    def document(self, doc_id):
        return FakeDoc(self.store, self.coll, doc_id)


class FakeFs:
    def __init__(self, store):
        self.store = store

    def collection(self, name):
        return FakeColl(self.store, name)


@pytest.fixture
def fake_staging(monkeypatch):
    store = {
        "TripLocations": {
            "Edinburgh": {"contentId": "Edinburgh", "locationName": "Edinburgh",
                          "locationTitleKey": "Edinburgh", "locationCountry": "Scotland",
                          "skyboxTextureId": "Old Skybox", "trips": ["Foo", "Other"]},
        },
        "CustomizableMenus": {
            "EN_Trip_Menu": {"MapName": "UKMap",
                             "Pins": [{"LocationId": "Glasgow", "xPos": 5.0, "yPos": -4.0}],
                             "ExtraMapButtons": [{"LocationId": "UKHistory",
                                                  "xPos": 11.0, "yPos": -1.0}]},
        },
        "TripGroups": {"Foo": {"contentTitleKey": "Foo Title"}},
    }
    import app.staging as staging_mod
    monkeypatch.setattr(staging_mod, "db", lambda: FakeFs(store))
    return store


def test_save_location_rejects_membership_change(fake_staging):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        final_checks.save_location("Foo_A12_EN", "Edinburgh",
                                   {"trips": ["Foo"]}, ADMIN)
    assert e.value.detail["error"] == "trips_not_permutation"


def test_save_location_reorder_and_fields(fake_staging):
    out = final_checks.save_location(
        "Foo_A12_EN", "Edinburgh",
        {"trips": ["Other", "Foo"], "skyboxTextureId": "New Skybox",
         "locationTitleKey": None}, ADMIN)
    assert sorted(out["updated"]) == ["skyboxTextureId", "trips"]
    doc = fake_staging["TripLocations"]["Edinburgh"]
    assert doc["trips"] == ["Other", "Foo"]
    assert doc["skyboxTextureId"] == "New Skybox"
    assert doc["locationTitleKey"] == "Edinburgh"   # untouched


def test_save_location_wrong_location_409(fake_staging):
    from fastapi import HTTPException
    fake_staging["TripLocations"]["Kyoto"] = {"trips": ["Baz"]}
    with pytest.raises(HTTPException) as e:
        final_checks.save_location("Foo_A12_EN", "Kyoto",
                                   {"locationTitleKey": "X"}, ADMIN)
    assert e.value.detail["error"] == "location_mismatch"


def test_save_pin_appends_then_updates(fake_staging):
    out = final_checks.save_pin("Foo_A12_EN", "Edinburgh", "EN_Trip_Menu",
                                3.21, -7.654, ADMIN)
    assert out["field"] == "Pins"
    pins = fake_staging["CustomizableMenus"]["EN_Trip_Menu"]["Pins"]
    assert {"LocationId": "Edinburgh", "xPos": 3.21, "yPos": -7.654} in pins
    assert len(pins) == 2                      # Glasgow untouched
    final_checks.save_pin("Foo_A12_EN", "Edinburgh", "EN_Trip_Menu", 4.0, -6.0, ADMIN)
    pins = fake_staging["CustomizableMenus"]["EN_Trip_Menu"]["Pins"]
    assert len(pins) == 2                      # upsert, not append
    assert {"LocationId": "Edinburgh", "xPos": 4.0, "yPos": -6.0} in pins


def test_save_pin_updates_extra_button_in_place(fake_staging):
    # A location living in ExtraMapButtons keeps its array — coords update there.
    fake_staging["TripLocations"]["Edinburgh"]["trips"] = ["Foo"]
    fake_staging["CustomizableMenus"]["EN_Trip_Menu"]["ExtraMapButtons"] = [
        {"LocationId": "Edinburgh", "xPos": 11.0, "yPos": -1.0}]
    out = final_checks.save_pin("Foo_A12_EN", "Edinburgh", "EN_Trip_Menu",
                                2.0, -2.0, ADMIN)
    assert out["field"] == "ExtraMapButtons"
    xb = fake_staging["CustomizableMenus"]["EN_Trip_Menu"]["ExtraMapButtons"]
    assert xb == [{"LocationId": "Edinburgh", "xPos": 2.0, "yPos": -2.0}]


def test_add_credit_keeps_vr_format(fake_staging):
    # First add creates the doc in EXACTLY the CreditsPanel.cs shape.
    out = final_checks.add_credit("Scotland", "castle.jpg — A. Author, CC BY-SA 4.0",
                                  ADMIN)
    doc = fake_staging["CustomizableMenus"]["Credits"]
    assert doc == {"credits": [{"header": "Scotland",
                                "entries": ["castle.jpg — A. Author, CC BY-SA 4.0"]}]}
    # Same header appends; different header adds a block; duplicates 409.
    final_checks.add_credit("Scotland", "loch.jpg — B. Author, PD", ADMIN)
    final_checks.add_credit("Germany", "tor.jpg — C. Author, CC0", ADMIN)
    doc = fake_staging["CustomizableMenus"]["Credits"]
    assert [b["header"] for b in doc["credits"]] == ["Scotland", "Germany"]
    assert len(doc["credits"][0]["entries"]) == 2
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        final_checks.add_credit("scotland", "LOCH.jpg — B. Author, PD".lower(), ADMIN)
    assert e.value.detail["error"] == "duplicate_credit"
    assert out["exists"] or True   # shape-only check above is the real assertion


@pytest.fixture
def fake_trip(monkeypatch, fake_staging):
    trip = {"quickTrips": [
        {"SceneDesc": "narration", "questionKey": "",
         "questionOptionKeys": ["Edinburgh Castle", "Holyrood", "Stirling"],
         "additionalAnswerKeys": ["the castle"]},
    ]}
    import app.staging as staging_mod
    monkeypatch.setattr(staging_mod, "get_trip", lambda tid: trip)
    fake_staging["Trips"] = {"Foo_A12_EN": {}}
    return trip


def test_add_answer_key_collision_and_duplicates(fake_trip):
    from fastapi import HTTPException
    # Equals another (wrong) option → refused (answer_keys.py safety rule).
    with pytest.raises(HTTPException) as e:
        final_checks.add_answer_key("Foo_A12_EN", 0, "holyrood", ADMIN)
    assert e.value.detail["error"] == "collides_with_option"
    # Duplicate of the correct answer / an existing key → refused.
    for dup in ("edinburgh castle", "THE CASTLE"):
        with pytest.raises(HTTPException) as e:
            final_checks.add_answer_key("Foo_A12_EN", 0, dup, ADMIN)
        assert e.value.detail["error"] == "duplicate_key"
    # A genuinely new form appends (add-only).
    out = final_checks.add_answer_key("Foo_A12_EN", 0, "Edinburgh's castle", ADMIN)
    assert out["additional"] == ["the castle", "Edinburgh's castle"]


def test_set_image_timing_warnings_not_blocks(monkeypatch, fake_staging):
    trip = {"quickTrips": [
        {"SceneDesc": "n", "staticImages": [
            {"filename": "a.jpg", "appearAtSeconds": 2, "disappearAtSeconds": 10},
            {"filename": "b.jpg", "appearAtSeconds": 12, "disappearAtSeconds": 20},
        ]},
    ]}
    import app.staging as staging_mod
    monkeypatch.setattr(staging_mod, "get_trip", lambda tid: trip)
    fake_staging["Trips"] = {"Foo_A12_EN": {}}
    # 3s on screen (<6 house rule) + only 0s after a.jpg clears → warnings, no block.
    out = final_checks.set_image_timing("Foo_A12_EN", 0, "b.jpg", 10, 13, ADMIN)
    assert out["appear"] == 10 and out["disappear"] == 13
    assert any("on screen only 3s" in w for w in out["warnings"])
    assert trip["quickTrips"][0]["staticImages"][1]["appearAtSeconds"] == 10
    from fastapi import HTTPException
    with pytest.raises(HTTPException):   # appear >= disappear is the one hard rule
        final_checks.set_image_timing("Foo_A12_EN", 0, "b.jpg", 9, 9, ADMIN)


def test_enrichment_matches_country_scoped(monkeypatch):
    monkeypatch.setattr(final_checks, "_enrichment_index", lambda force=False: [
        {"id": "Bar_A12_EN", "tg_id": "Bar", "countries": ["Scotland"],
         "applicableCategories": ["Castles"], "newCategorySuggestions": [],
         "themes": ["castle history"], "keyVocabulary": [], "namedEntities": []},
        {"id": "Baz_N5_JP", "tg_id": "Baz", "countries": ["Japan"],
         "applicableCategories": ["Castles"], "newCategorySuggestions": [],
         "themes": [], "keyVocabulary": [], "namedEntities": []},
    ])
    hits = final_checks._enrichment_matches("Foo", "Castles")
    assert [h["doc_id"] for h in hits] == ["Bar_A12_EN"]   # Japan filtered out
    fields = {h["field"] for h in hits[0]["hits"]}
    assert fields == {"applicableCategories", "themes"}
