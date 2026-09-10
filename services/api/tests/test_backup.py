import json
import stat
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.auth import AuthService
from app.backup import create_backup, restore_backup
from app.captures import CaptureService
from app.config import Settings
from app.db import Database
from app.domain import Group, Task, digest, json_hash
from app.errors import AppError
from app.locking import workspace_lock
from app.schemas import CaptureInput
from app.storage import ContentStore


def backup_path(settings):
    return settings.data_root.parent / (settings.data_root.name + "-backup.zip")


def test_offline_backup_restores_data_but_revokes_old_sessions(system):
    settings, db, store, _, profile, changes = system
    ws = profile["workspace"]["id"]
    captures = CaptureService(db, store)
    capture = captures.save(ws, CaptureInput(client_capture_id="backup-capture-key", raw_text="原文永远保留"))
    group = Group(title="恢复验证", tasks=[Task(title="检查恢复结果")])
    operation = changes.operation(ws, None, group, path="恢复验证.md", folder_id=None)
    changes.execute(ws, "backup-group-key", json_hash({"group": group.id}), "create", "备份验证", [operation])
    output = backup_path(settings)
    result = create_backup(settings, output)
    assert output.exists() and result["encrypted"] is False
    with zipfile.ZipFile(output) as archive:
        assert not any(name.endswith(".env") or "/exports/" in name or name.endswith(".lock") for name in archive.namelist())
    target = settings.data_root.parent / (settings.data_root.name + "-restored")
    restored = restore_backup(output, target)
    assert restored["files"] == result["files"]
    restored_settings = Settings(_env_file=None, data_root=target)
    restored_db = Database(restored_settings)
    restored_store = ContentStore(target)
    try:
        auth = AuthService(restored_db, restored_store, restored_settings)
        with pytest.raises(AppError):
            auth.identity(profile["token"])
        assert auth.login("researcher", "correct-horse-2026")["workspace"]["id"] == ws
        assert CaptureService(restored_db, restored_store).get(ws, capture["id"])["raw_text"] == capture["raw_text"]
        assert restored_store.read(ws, "files", "恢复验证.md") == store.read(ws, "files", "恢复验证.md")
    finally:
        restored_db.dispose()


def test_backup_refuses_running_server_and_existing_output(system):
    settings = system[0]
    output = backup_path(settings)
    with workspace_lock(settings.data_root / "server.lock", timeout=1):
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(create_backup, settings, output)
            with pytest.raises(AppError) as running:
                result.result(timeout=5)
    assert running.value.code == "WORKSPACE_BUSY" and not output.exists()
    create_backup(settings, output)
    original = output.read_bytes()
    with pytest.raises(AppError):
        create_backup(settings, output)
    assert output.read_bytes() == original


def test_restore_never_overwrites_existing_directory(system):
    settings = system[0]
    output = backup_path(settings)
    create_backup(settings, output)
    with pytest.raises(AppError):
        restore_backup(output, settings.data_root)
    assert (settings.data_root / "gkd.sqlite3").exists()


@pytest.mark.parametrize("damage", ["traversal", "drive", "symlink", "checksum", "unlisted", "duplicate"])
def test_restore_rejects_unsafe_or_damaged_archives_without_partial_destination(system, damage):
    settings = system[0]
    original = backup_path(settings)
    create_backup(settings, original)
    with zipfile.ZipFile(original) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(members["manifest.json"])
    tampered = original.with_name(original.stem + "-tampered.zip")
    with zipfile.ZipFile(tampered, "w", zipfile.ZIP_DEFLATED) as archive:
        if damage == "checksum":
            manifest["entries"][0]["sha256"] = digest("not the database")
            members["manifest.json"] = json.dumps(manifest).encode()
        for name, content in members.items():
            archive.writestr(name, content)
        if damage in {"traversal", "drive", "unlisted"}:
            name = {"traversal": "../escape.txt", "drive": "C:/escape.txt", "unlisted": "extra.txt"}[damage]
            archive.writestr(name, b"must not be extracted")
        elif damage == "symlink":
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "../outside")
        elif damage == "duplicate":
            archive.writestr("GKD.SQLITE3", members["gkd.sqlite3"])
    target = settings.data_root.parent / (settings.data_root.name + "-rejected")
    with pytest.raises(AppError):
        restore_backup(tampered, target)
    assert not target.exists()
    assert not (settings.data_root.parent / "escape.txt").exists()
    assert list(target.parent.glob(".gkd-restore-*")) == []
