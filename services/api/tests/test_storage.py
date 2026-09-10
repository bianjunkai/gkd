import pytest

from app.domain import Group, Task, TaskStatus
from app.errors import AppError
from app.storage import ContentStore, checked_relative, parse_document, render_document


@pytest.mark.parametrize("schema_version", [1, 2])
def test_round_trip_preserves_unknown_fields_and_unmanaged_body(schema_version):
    group = Group(schema_version=schema_version, title="技术方案", tasks=[Task(title="补充售后章节")], customer_code="S2026")
    body = "# 手工标题\n\n原文中的 [参考](../资料.md) 与空行。\n\n## 工作记录\n\n已沟通。\n"
    raw = render_document(group, body=body)
    parsed, _, _ = parse_document(raw)
    parsed.tasks[0].status = TaskStatus.DONE
    parsed.tasks[0].completed_at = parsed.updated_at
    parsed.revision += 1
    updated = render_document(parsed, previous=raw)
    loaded, updated_body, _ = parse_document(updated)
    assert loaded.model_extra["customer_code"] == "S2026"
    assert body.strip() in updated_body
    assert loaded.tasks[0].status == TaskStatus.DONE
    assert ("- [x] 补充售后章节" if schema_version == 1 else "### 补充售后章节") in updated


@pytest.mark.parametrize("schema_version", [1, 2])
def test_duplicate_keys_and_aliases_are_rejected(schema_version):
    raw = render_document(Group(title="任务", schema_version=schema_version))
    for modified in [
        raw.replace(f"schema_version: {schema_version}", f"schema_version: {schema_version}\nschema_version: {schema_version}"),
        raw.replace("title: 任务", "title: &alias 任务\nother: *alias"),
        raw.replace("title: 任务", "title: !!python/object/apply:os.system ['echo unsafe']"),
    ]:
        with pytest.raises(AppError, match="YAML|格式"):
            parse_document(modified)


def test_missing_id_is_not_silently_generated():
    raw = render_document(Group(title="记录"))
    raw = "\n".join(line for line in raw.splitlines() if not line.startswith("id:"))
    with pytest.raises(AppError):
        parse_document(raw)


@pytest.mark.parametrize("value", ["../file.md", "C:/a.md", "/a.md", "a\\b.md", "CON.md", "a/../b.md"])
def test_unsafe_paths_are_rejected(value):
    with pytest.raises(AppError):
        checked_relative(value)


def test_reentrant_workspace_lock(system):
    _, _, store, _, profile, _ = system
    with store.lock(profile["workspace"]["id"], timeout=0.1):
        with store.lock(profile["workspace"]["id"], timeout=0.1):
            assert True


def test_symlink_outside_workspace_is_rejected(tmp_path):
    store = ContentStore(tmp_path / "data")
    from app.domain import new_id
    ws = new_id("workspace")
    root = store.workspace_root(ws)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / "files").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Host does not permit creating symlinks")
    with pytest.raises(AppError):
        store.path(ws, "files", "record.md")
