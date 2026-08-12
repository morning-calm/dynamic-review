"""Regression: a delta manifest re-issued while its card sat open (CastellodiBrolio_A12_IT,
2026-08-12).

A 3-scene delta card was open (zero reviewer work) when Scripts re-uploaded the manifest
with a 4th scene. The open session's frozen `delta_json` kept serving [2, 8, 9]; approving
it would have deleted the 4-scene manifest and silently dropped scene 1 forever. Invariant
pinned here: a card can never approve away clips it never showed — approve refuses on a
diverged manifest (sessions.approve) and the consume step is compare-and-delete
(deltas.delete_object(expect_doc=...)).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import deltas, review_audio  # noqa: E402

MANIFEST_3 = {"contentId": "CastellodiBrolio_A12_IT", "created": "2026-08-11",
              "scenes": [{"index": i, "clips": [str(i)]} for i in (2, 8, 9)]}
MANIFEST_4 = {"contentId": "CastellodiBrolio_A12_IT", "created": "2026-08-12",
              "scenes": [{"index": i, "clips": [str(i)]} for i in (1, 2, 8, 9)]}


class _FakeS3:
    def __init__(self):
        self.deleted = []

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)


def _wire(monkeypatch, live_doc):
    s3 = _FakeS3()
    monkeypatch.setattr(review_audio, "_r2", lambda: s3)
    monkeypatch.setattr(deltas, "fetch", lambda tid: live_doc)
    return s3


def test_field_sets_of_the_two_castello_manifests_differ():
    assert deltas.field_keys(MANIFEST_3) != deltas.field_keys(MANIFEST_4)
    # ...and the divergence is exactly the never-shown scene 1
    assert deltas.field_keys(MANIFEST_4) - deltas.field_keys(MANIFEST_3) == {
        (1, "SceneDesc", None)}


def test_delete_refused_when_live_manifest_was_reissued(monkeypatch):
    s3 = _wire(monkeypatch, MANIFEST_4)
    assert deltas.delete_object("CastellodiBrolio_A12_IT",
                                expect_doc=MANIFEST_3) is False
    assert s3.deleted == []


def test_delete_proceeds_when_live_manifest_matches_seed(monkeypatch):
    s3 = _wire(monkeypatch, MANIFEST_3)
    assert deltas.delete_object("CastellodiBrolio_A12_IT",
                                expect_doc=MANIFEST_3) is True
    assert s3.deleted == ["_delta/CastellodiBrolio_A12_IT.json"]


def test_delete_proceeds_when_manifest_already_gone(monkeypatch):
    # Object-gone mid-approve: nothing to protect, the delete is idempotent.
    s3 = _wire(monkeypatch, None)
    assert deltas.delete_object("CastellodiBrolio_A12_IT",
                                expect_doc=MANIFEST_3) is True


def test_legacy_call_without_expect_doc_still_deletes(monkeypatch):
    s3 = _wire(monkeypatch, MANIFEST_4)
    assert deltas.delete_object("CastellodiBrolio_A12_IT") is True
    assert s3.deleted == ["_delta/CastellodiBrolio_A12_IT.json"]
