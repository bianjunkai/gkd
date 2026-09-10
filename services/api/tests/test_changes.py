import pytest
from sqlalchemy import select

from app.db import ChangeSet, GroupRecord, TaskIndex
from app.domain import Group, Task, json_hash
from app.errors import AppError


def create_group(system):
    _, db, store, _, profile, engine = system
    ws = profile["workspace"]["id"]
    group = Group(title="合同跟进", tasks=[Task(title="发送合同")])
    op = engine.operation(ws, None, group, path="合同跟进.md", folder_id=None)
    result = engine.execute(ws, "create-one", json_hash({"title": group.title}), "create", "新建任务组", [op])
    return ws, group, result


def test_repeated_request_does_not_create_a_second_changeset(system):
    _, db, _, _, _, engine = system
    ws, group, result = create_group(system)
    duplicate = engine.execute(ws, "create-one", json_hash({"title": group.title}), "create", "重复提交", [])
    assert duplicate["id"] == result["id"]
    with db.session() as session:
        assert len(list(session.scalars(select(TaskIndex)))) == 1
        assert len(list(session.scalars(select(ChangeSet)))) == 1


def test_same_key_with_different_payload_is_rejected(system):
    _, _, _, _, _, engine = system
    ws, _, _ = create_group(system)
    with pytest.raises(AppError, match="不同内容"):
        engine.execute(ws, "create-one", json_hash({"different": True}), "create", "不同内容", [])


def test_external_edit_is_never_overwritten(system):
    _, db, store, _, _, engine = system
    ws, group, _ = create_group(system)
    with db.session() as session:
        record = session.get(GroupRecord, group.id)
    original = store.read(ws, "files", record.path)
    group.revision += 1
    group.tasks[0].title = "修改合同"
    original_usage = store.usage(ws)
    operation = engine.operation(ws, record, group, path=record.path, folder_id=None, previous=original)
    assert store.usage(ws) == original_usage, "Preparing a proposal must not publish a version blob"
    external = original + "\n用户在编辑器里新增的记录。\n"
    store.write(ws, "files", record.path, external)
    changed_usage = store.usage(ws)
    with pytest.raises(AppError, match="外部修改"):
        engine.execute(ws, "edit", json_hash({"edit": 1}), "edit", "修改任务", [operation])
    assert store.read(ws, "files", record.path) == external
    assert store.usage(ws) == changed_usage, "Rejected preflight must not leak snapshots or journals"


@pytest.mark.parametrize("phase", ["prepared", "file_0", "files_applied", "before_commit"])
def test_interrupted_multifile_write_is_recovered_as_one_batch(system, phase):
    _, db, store, _, profile, engine = system
    ws = profile["workspace"]["id"]
    groups = [Group(title="文件一", tasks=[Task(title="任务一")]), Group(title="文件二")]
    operations = [
        engine.operation(ws, None, group, path=group.title + ".md", folder_id=None)
        for group in groups
    ]

    def crash(here, _):
        if here == phase:
            raise SystemExit("Simulated process interruption")

    engine.fault_hook = crash
    with pytest.raises(SystemExit):
        engine.execute(ws, "interrupted", json_hash({"batch": 1}), "create", "两份文件", operations)
    engine.fault_hook = None
    assert engine.recover_all() == []
    for group in groups:
        assert not store.path(ws, "files", group.title + ".md").exists()
    with db.session() as session:
        assert list(session.scalars(select(GroupRecord))) == []
        assert list(session.scalars(select(TaskIndex))) == []
        assert session.scalar(select(ChangeSet)).status == "rolled_back"


def test_undo_creates_recoverable_trash_and_never_rewinds_revision(system):
    _, db, store, _, _, engine = system
    ws, group, created = create_group(system)
    result = engine.undo(ws, created["id"], "undo-one", json_hash({"undo": created["id"]}))
    assert result["status"] == "committed"
    with db.session() as session:
        record = session.get(GroupRecord, group.id)
        assert record.deleted_at is not None
        assert record.revision > group.revision
        assert list(session.scalars(select(TaskIndex))) == []
    assert not store.path(ws, "files", "合同跟进.md").exists()
    repeated = engine.undo(ws, created["id"], "undo-again", json_hash({"undo": created["id"]}))
    assert repeated["id"] == result["id"]


@pytest.mark.parametrize("phase", ["prepared", "file_0", "files_applied", "before_commit"])
def test_interrupted_case_only_rename_restores_content_and_disk_spelling(system, phase):
    _, db, store, _, profile, engine = system
    ws = profile["workspace"]["id"]
    group = Group(title="合同", tasks=[Task(title="保留原任务")])
    operation = engine.operation(ws, None, group, path="Contract.md", folder_id=None)
    engine.execute(ws, "case-create", json_hash({"create": True}), "create", "新建", [operation])
    with db.session() as session:
        record = session.get(GroupRecord, group.id)
    original = store.read(ws, "files", "Contract.md")
    group.revision += 1
    operation = engine.operation(ws, record, group, path="contract.md", folder_id=None, previous=original)

    def crash(here, _):
        if here == phase:
            raise SystemExit("Simulated case-only rename interruption")

    engine.fault_hook = crash
    with pytest.raises(SystemExit):
        engine.execute(ws, "case-rename", json_hash({"rename": True}), "rename", "仅修改大小写", [operation])
    engine.fault_hook = None
    assert engine.recover_all() == []
    assert store.read(ws, "files", "Contract.md") == original
    assert [p.name for p in (store.workspace_root(ws) / "files").glob("*.md")] == ["Contract.md"]
    with db.session() as session:
        current = session.get(GroupRecord, group.id)
        assert current.path == "Contract.md" and current.revision == 1
        assert len(list(session.scalars(select(TaskIndex)))) == 1
        changes = list(session.scalars(select(ChangeSet)))
        assert {change.status for change in changes} == {"committed", "rolled_back"}
        assert all("_content" not in op for change in changes for op in change.operations)
