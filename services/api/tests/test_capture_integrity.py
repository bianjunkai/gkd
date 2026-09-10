import errno
import json

import pytest
from sqlalchemy import delete, select

from app.captures import CaptureService
from app.db import CaptureRecord
from app.domain import new_id
from app.errors import AppError
from app.schemas import CaptureInput


def save(service, ws, client_id):
    return service.save(ws, CaptureInput(client_capture_id=client_id, raw_text="  保留原文\n"))


@pytest.mark.parametrize("damage", ["identity", "tenant", "timezone", "timestamp", "epoch", "shape"])
def test_corrupt_capture_does_not_block_other_orphan_recovery(system, damage):
    _, db, store, _, profile, _ = system
    ws = profile["workspace"]["id"]
    service = CaptureService(db, store)
    broken = save(service, ws, "orphan-broken")
    valid = save(service, ws, "orphan-valid")
    name = service.file_name(broken["client_capture_id"])
    raw = json.loads(store.read(ws, "captures", name))
    if damage == "identity":
        raw["id"] = "capture-not-a-uuid"
    elif damage == "tenant":
        raw["workspace_id"] = new_id("workspace")
    elif damage == "timezone":
        raw["reference_timezone"] = "Invalid/Nowhere"
    elif damage == "timestamp":
        raw["created_at"] = "2026-09-08T12:00:00"
    elif damage == "epoch":
        raw["created_epoch"] = float("nan")
    else:
        raw = []
    damaged = json.dumps(raw, ensure_ascii=False)
    store.write(ws, "captures", name, damaged)
    with db.session() as session:
        session.execute(delete(CaptureRecord))
    assert service.recover() == [{"workspace_id": ws, "path": name}]
    with db.session() as session:
        assert list(session.scalars(select(CaptureRecord.id))) == [valid["id"]]
    assert service.get(ws, valid["id"])["raw_text"] == valid["raw_text"]
    assert store.read(ws, "captures", name) == damaged
    with pytest.raises(AppError) as failure:
        save(service, ws, "orphan-broken")
    assert failure.value.code == "STORAGE_CORRUPTED"


def test_capture_recovery_isolates_database_constraint_failure(system):
    _, db, store, _, profile, _ = system
    ws = profile["workspace"]["id"]
    service = CaptureService(db, store)
    existing = save(service, ws, "same-client-key")
    orphan = save(service, ws, "another-valid-key")
    name = service.file_name(existing["client_capture_id"])
    raw = json.loads(store.read(ws, "captures", name))
    raw["id"] = new_id("capture")  # Valid UUID, but duplicates the DB client key.
    store.write(ws, "captures", name, json.dumps(raw))
    with db.session() as session:
        session.execute(delete(CaptureRecord).where(CaptureRecord.id == orphan["id"]))
    assert service.recover() == [{"workspace_id": ws, "path": name}]
    assert service.get(ws, orphan["id"])["raw_text"] == orphan["raw_text"]
    with db.session() as session:
        assert session.get(CaptureRecord, existing["id"]) is not None


def test_capture_disk_full_is_not_reported_as_saved(system, monkeypatch):
    _, db, store, _, profile, _ = system
    ws = profile["workspace"]["id"]
    service = CaptureService(db, store)

    def full(*args):
        raise OSError(errno.ENOSPC, "Simulated full disk")

    with monkeypatch.context() as patch:
        patch.setattr(store, "write", full)
        with pytest.raises(OSError):
            save(service, ws, "disk-full-retry")
    with db.session() as session:
        assert list(session.scalars(select(CaptureRecord))) == []
    first = save(service, ws, "disk-full-retry")
    assert save(service, ws, "disk-full-retry")["id"] == first["id"]
