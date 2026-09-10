import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.config import Settings
from app.db import CaptureRecord, ChangeSet, GroupRecord, Job, Proposal, TaskIndex
from app.domain import new_id
from app.main import create_app


@pytest.fixture
def api(tmp_path):
    app = create_app(Settings(_env_file=None, data_root=tmp_path, run_worker=False))
    with TestClient(app) as client:
        response = client.post("/api/auth/register", json={"username": "builder", "password": "test-password-2026", "display_name": "开发同学"})
        assert response.status_code == 201, response.text
        profile = response.json()
        client.headers["Authorization"] = "Bearer " + profile["token"]
        yield client, app, profile


def write(client, method, path, data=None, key=None):
    return client.request(method, path, json=data, headers={"Idempotency-Key": key or new_id("request")})


def save(client, text="明天下午给李总发合同", client_id=None):
    response = client.post("/api/captures", json={"client_capture_id": client_id or new_id("client"), "raw_text": text, "source_type": "text"})
    assert response.status_code == 201, response.text
    return response.json()


def analyze(api, capture, **values):
    client, app, _ = api
    response = write(client, "POST", f"/api/captures/{capture['id']}/analyze", {"mode": "local", **values})
    assert response.status_code == 202, response.text
    job = response.json()
    assert app.state.jobs.run_once()
    completed = client.get("/api/jobs/" + job["id"]).json()
    assert completed["status"] == "succeeded", completed
    response = client.get("/api/proposals/" + completed["result"]["proposal_id"])
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client, proposal, ids=None, key=None):
    data = {"proposal_revision": proposal["revision"], "selected_action_ids": ids or [a["action_id"] for a in proposal["actions"]]}
    response = write(client, "POST", f"/api/proposals/{proposal['id']}/confirm", data, key)
    assert response.status_code == 200, response.text
    return response.json()


def group(client, title="合同跟进", **values):
    response = write(client, "POST", "/api/groups", {"title": title, **values})
    assert response.status_code == 201, response.text
    return client.get("/api/groups/" + response.json()["group_ids"][0]).json()


def version(g):
    return {"expected_revision": g["revision"], "expected_hash": g["content_hash"]}


def test_full_capture_confirmation_task_undo_loop(api):
    client, app, _ = api
    capture = save(client, "明天下午给李总发合同\n周五18点前提交方案")
    proposal = analyze(api, capture, reference_time="2026-09-07T10:00:00+08:00")
    assert proposal["classification"] == "multiple_tasks"
    assert proposal["provider"]["provider"] == "local-rules"
    assert client.get("/api/groups").json()["total"] == 0
    selected = [proposal["actions"][0]["action_id"]]
    body = {"proposal_revision": proposal["revision"], "selected_action_ids": selected}
    preview = write(client, "POST", f"/api/proposals/{proposal['id']}/preview", body).json()
    assert preview["task_count"] == preview["file_count"] == 1
    change = confirm(client, proposal, selected, "request-confirm-loop")
    assert confirm(client, proposal, selected, "request-confirm-loop")["id"] == change["id"]
    assert confirm(client, proposal, selected)["id"] == change["id"]
    g = client.get("/api/groups/" + change["group_ids"][0]).json()
    assert len(g["tasks"]) == 1
    task = g["tasks"][0]
    assert task["scheduled"] == {"date": "2026-09-08", "time": None, "period": "afternoon", "timezone": "Asia/Shanghai"}
    assert task["owner"] is None and task["deadline"] is None
    assert task["source_refs"] == [capture["id"]]
    assert client.get("/api/captures/" + capture["id"]).json()["status"] == "processed"
    done = write(client, "PATCH", f"/api/groups/{g['id']}/tasks/{task['id']}", {**version(g), "patch": {"status": "done"}})
    assert done.status_code == 200, done.text
    completed = client.get("/api/groups/" + g["id"]).json()
    assert completed["tasks"][0]["completed_at"] is not None
    assert "### " + task["title"] in completed["markdown"]
    assert "status: done" in completed["markdown"]
    assert write(client, "POST", f"/api/changes/{done.json()['id']}/undo").status_code == 200
    reopened = client.get("/api/groups/" + g["id"]).json()
    assert reopened["tasks"][0]["status"] == "next"
    assert reopened["revision"] > completed["revision"]
    assert client.get("/api/search", params={"q": "合同"}).json()["total"] >= 3


def test_capture_is_immutable_and_idempotent(api):
    client, app, profile = api
    text = "  原始文字\n\n不能改写。  "
    first = save(client, text, "capture-client-immutable")
    assert save(client, text, "capture-client-immutable")["id"] == first["id"]
    changed = client.post("/api/captures", json={"client_capture_id": "capture-client-immutable", "raw_text": text + "new"})
    assert changed.status_code == 409
    with app.state.db.session() as session:
        session.execute(delete(CaptureRecord).where(CaptureRecord.id == first["id"]))
    retried = save(client, text, "capture-client-immutable")
    assert retried["id"] == first["id"] and retried["raw_text"] == text


def test_reference_never_forces_a_task(api):
    client, _, _ = api
    capture = save(client, "资料：项目色板使用暖白色和墨绿色。")
    proposal = analyze(api, capture)
    assert proposal["classification"] == "reference"
    assert proposal["actions"][0]["tasks"] == []
    change = confirm(client, proposal)
    g = client.get("/api/groups/" + change["group_ids"][0]).json()
    assert g["tasks"] == [] and capture["raw_text"] in g["body"]


@pytest.mark.parametrize("archived", [False, True])
def test_undo_only_unprocesses_capture_after_all_applied_proposals_are_undone(api, archived):
    client, _, _ = api
    capture = save(client)
    # This regression needs two independent destinations. Default analysis now reuses
    # one container, where an older undo correctly conflicts with newer revisions.
    destinations = [group(client, "合同执行"), group(client, "客户沟通")]
    first = confirm(client, analyze(api, capture, target_group_id=destinations[0]["id"]))
    second = confirm(client, analyze(api, capture, reanalyze=True, target_group_id=destinations[1]["id"]))
    state_path = f"/api/captures/{capture['id']}/state"
    if archived:
        assert client.post(state_path, json={"action": "archive"}).status_code == 200
    assert write(client, "POST", f"/api/changes/{second['id']}/undo").status_code == 200
    current = client.get("/api/captures/" + capture["id"]).json()
    assert current["status"] == ("archived" if archived else "processed")
    assert write(client, "POST", f"/api/changes/{first['id']}/undo").status_code == 200
    if archived:
        assert client.post(state_path, json={"action": "unarchive"}).status_code == 200
    assert client.get("/api/captures/" + capture["id"]).json()["status"] == "unprocessed"


def test_rejecting_reanalysis_preserves_the_previously_applied_result(api):
    client, _, _ = api
    capture = save(client)
    confirm(client, analyze(api, capture))
    pending = analyze(api, capture, reanalyze=True)
    response = client.post(f"/api/proposals/{pending['id']}/reject", json={"proposal_revision": pending["revision"]})
    assert response.status_code == 200, response.text
    assert client.get("/api/captures/" + capture["id"]).json()["status"] == "processed"


def test_append_preserves_body_and_detects_preview_conflicts(api):
    client, _, _ = api
    original = group(client, body="# 工作记录\n\n保留的段落与 [链接](https://example.org)。\n", tasks=[{"title": "联系客户"}])
    proposal = analyze(api, save(client, "明天补充售后章节"), target_group_id=original["id"])
    changed = write(client, "PATCH", "/api/groups/" + original["id"], {**version(original), "tags": ["已修改"]})
    assert changed.status_code == 200, changed.text
    denied = write(client, "POST", f"/api/proposals/{proposal['id']}/confirm", {"proposal_revision": 1, "selected_action_ids": [proposal["actions"][0]["action_id"]]})
    assert denied.status_code == 409 and denied.json()["error"]["code"] == "PROPOSAL_STALE"
    edit = client.patch("/api/proposals/" + proposal["id"], json={"proposal_revision": 1, "actions": proposal["actions"], "questions": proposal["questions"]})
    assert edit.status_code == 200, edit.text
    latest = edit.json()
    stale = write(client, "POST", f"/api/proposals/{proposal['id']}/confirm", {"proposal_revision": 1, "selected_action_ids": [proposal["actions"][0]["action_id"]]})
    assert stale.status_code == 409
    assert client.get("/api/proposals/" + proposal["id"]).json()["status"] == "pending_confirmation"
    confirm(client, latest)
    result = client.get("/api/groups/" + original["id"]).json()
    assert len(result["tasks"]) == 2 and result["tags"] == ["已修改"]
    assert "保留的段落与 [链接](https://example.org)。" in result["body"]


def test_failed_external_ai_preserves_capture_and_allows_local_retry(api):
    client, app, _ = api
    capture = save(client)
    submitted = write(client, "POST", f"/api/captures/{capture['id']}/analyze", {"mode": "external"}).json()
    app.state.jobs.run_once()
    job = client.get("/api/jobs/" + submitted["id"]).json()
    assert job["status"] == "failed" and job["error"]["code"] == "AI_NOT_CONFIGURED"
    assert client.get("/api/captures/" + capture["id"]).json()["raw_text"] == capture["raw_text"]
    assert analyze(api, capture)["status"] == "pending_confirmation"


def test_job_restart_after_proposal_commit_does_not_duplicate(api):
    client, app, _ = api
    capture = save(client)
    queued = write(client, "POST", f"/api/captures/{capture['id']}/analyze", {"mode": "local"}).json()
    claimed = app.state.jobs.claim()
    first = app.state.proposals.analyze_job(claimed)
    with app.state.db.session() as session:
        session.get(Job, queued["id"]).lease_until = time.time() - 1
    assert app.state.jobs.run_once()
    assert client.get("/api/jobs/" + queued["id"]).json()["result"] == first
    with app.state.db.session() as session:
        assert len(list(session.scalars(select(Proposal)))) == 1


def test_rebuild_restores_indices_without_modifying_files(api):
    client, app, _ = api
    original = group(client, tasks=[{"title": "提交报告", "tags": ["发布"]}])
    with app.state.db.session() as session:
        session.execute(delete(TaskIndex))
        session.execute(delete(GroupRecord))
    job = write(client, "POST", "/api/index/rebuild").json()
    app.state.jobs.run_once()
    result = client.get("/api/jobs/" + job["id"]).json()
    assert result["status"] == "succeeded", result
    assert result["result"]["groups"] == 1 and result["result"]["tasks"] == 1
    assert client.get("/api/groups/" + original["id"]).json()["markdown"] == original["markdown"]


def test_trash_restore_and_version_restore(api):
    client, _, _ = api
    original = group(client, tasks=[{"title": "检查回收站"}])
    edited = write(client, "PATCH", "/api/groups/" + original["id"], {**version(original), "title": "修改后的标题"})
    assert edited.status_code == 200
    current = client.get("/api/groups/" + original["id"]).json()
    preview = client.get(f"/api/groups/{original['id']}/versions/{original['content_hash']}")
    assert preview.status_code == 200 and preview.json()["diff"]
    restored = write(client, "POST", f"/api/groups/{original['id']}/restore-version", {**version(current), "content_hash": original["content_hash"]})
    assert restored.status_code == 200, restored.text
    current = client.get("/api/groups/" + original["id"]).json()
    assert current["title"] == original["title"] and current["revision"] > original["revision"]
    assert write(client, "POST", f"/api/groups/{current['id']}/trash", version(current)).status_code == 200
    assert client.get("/api/groups/" + current["id"]).status_code == 404
    trash = client.get("/api/groups?trash=true").json()["items"][0]
    assert write(client, "POST", f"/api/groups/{current['id']}/restore", version(trash)).status_code == 200
    assert client.get("/api/groups/" + current["id"]).status_code == 200


def test_tenant_boundary_covers_content_jobs_history_and_writes(api):
    client, _, profile = api
    capture = save(client)
    proposal = analyze(api, capture)
    change = confirm(client, proposal)
    g = client.get("/api/groups/" + change["group_ids"][0]).json()
    job = client.get("/api/jobs").json()[0]
    folder = client.get("/api/folders").json()[0]
    other = client.post("/api/auth/register", json={"username": "another", "password": "different-password", "display_name": "另一个账号"}).json()
    client.headers["Authorization"] = "Bearer " + other["token"]
    paths = ["/api/captures/" + capture["id"], "/api/proposals/" + proposal["id"], "/api/groups/" + g["id"],
             "/api/jobs/" + job["id"], "/api/changes/" + change["id"],
             f"/api/groups/{g['id']}/versions/{g['content_hash']}"]
    for path in paths:
        assert client.get(path).status_code == 404, path
    assert client.get("/api/search?q=合同").json()["total"] == 0
    assert client.get("/api/tasks").json()["total"] == 0
    assert write(client, "PATCH", "/api/groups/" + g["id"], {**version(g), "title": "越权修改"}).status_code == 404
    assert write(client, "PATCH", "/api/folders/" + folder["id"], {"expected_revision": folder["revision"], "name": "越权目录"}).status_code == 404
    assert write(client, "POST", "/api/changes/" + change["id"] + "/undo").status_code == 404


@pytest.mark.parametrize("name", ["../escape", "CON", "a/b", "a\\b", "bad.", "/absolute"])
def test_unsafe_folder_names_are_rejected(api, name):
    client, _, _ = api
    assert write(client, "POST", "/api/folders", {"name": name}).status_code == 422


def test_folder_move_keeps_ids_and_invalidates_proposals(api):
    client, _, _ = api
    folder_id = write(client, "POST", "/api/folders", {"name": "研发"}).json()["folder_ids"][0]
    original = group(client, folder_id=folder_id, tasks=[{"title": "提交验收"}])
    proposal = analyze(api, save(client), target_group_id=original["id"])
    moved = write(client, "PATCH", "/api/folders/" + folder_id, {"expected_revision": 1, "name": "产品研发"})
    assert moved.status_code == 200, moved.text
    g = client.get("/api/groups/" + original["id"]).json()
    assert g["path"].startswith("产品研发/")
    assert g["tasks"][0]["id"] == original["tasks"][0]["id"]
    assert client.get("/api/proposals/" + proposal["id"]).json()["status"] == "stale"
    assert write(client, "POST", "/api/changes/" + moved.json()["id"] + "/undo").status_code == 200
    assert client.get("/api/groups/" + original["id"]).json()["path"].startswith("研发/")


def rebuild(api):
    client, app, _ = api
    job = write(client, "POST", "/api/index/rebuild").json()
    assert app.state.jobs.run_once()
    result = client.get("/api/jobs/" + job["id"]).json()
    assert result["status"] == "succeeded", result
    return result["result"]


def test_restore_uses_current_folder_path_after_rename(api):
    client, _, _ = api
    folder = write(client, "POST", "/api/folders", {"name": "原目录"}).json()["folder_ids"][0]
    original = group(client, folder_id=folder, tasks=[{"title": "提交报告"}])
    assert write(client, "POST", f"/api/groups/{original['id']}/trash", version(original)).status_code == 200
    assert write(client, "PATCH", "/api/folders/" + folder, {"expected_revision": 1, "name": "新目录"}).status_code == 200
    trashed = client.get("/api/groups?trash=true").json()["items"][0]
    assert write(client, "POST", f"/api/groups/{original['id']}/restore", version(trashed)).status_code == 200
    restored = client.get("/api/groups/" + original["id"]).json()
    assert restored["path"] == "新目录/合同跟进.md"
    assert rebuild(api)["errors"] == []
    assert client.get("/api/groups/" + original["id"]).status_code == 200


@pytest.mark.parametrize("added", ["file", "folder", "moved_file"])
def test_undo_folder_rename_rejects_later_subtree_changes(api, added):
    client, app, profile = api
    folder = write(client, "POST", "/api/folders", {"name": "原目录"}).json()["folder_ids"][0]
    group(client, folder_id=folder)
    renamed = write(client, "PATCH", "/api/folders/" + folder, {"expected_revision": 1, "name": "新目录"}).json()
    if added == "file":
        group(client, "后增文件", folder_id=folder)
    elif added == "folder":
        assert write(client, "POST", "/api/folders", {"name": "后增目录", "parent_id": folder}).status_code == 201
    else:
        other = group(client, "移入文件")
        assert write(client, "PATCH", "/api/groups/" + other["id"], {**version(other), "folder_id": folder}).status_code == 200
    usage = app.state.store.usage(profile["workspace"]["id"])
    response = write(client, "POST", "/api/changes/" + renamed["id"] + "/undo")
    assert response.status_code == 409, response.text
    assert app.state.store.usage(profile["workspace"]["id"]) == usage
    assert next(f for f in client.get("/api/folders").json() if f["id"] == folder)["path"] == "新目录"
    assert rebuild(api)["errors"] == []


@pytest.mark.parametrize("archived", [False, True])
def test_undo_redo_keeps_capture_and_proposal_states_in_sync(api, archived):
    client, _, _ = api
    capture = save(client)
    proposal = analyze(api, capture)
    change = confirm(client, proposal)
    if archived:
        assert client.post(f"/api/captures/{capture['id']}/state", json={"action": "archive"}).status_code == 200
    for index in range(4):
        response = write(client, "POST", "/api/changes/" + change["id"] + "/undo")
        assert response.status_code == 200, response.text
        change = response.json()
        applied = index % 2 == 1
        assert client.get("/api/tasks").json()["total"] == int(applied)
        assert client.get("/api/proposals/" + proposal["id"]).json()["status"] == ("applied" if applied else "rejected")
        current = client.get("/api/captures/" + capture["id"]).json()
        assert current["status"] == ("archived" if archived else "processed" if applied else "unprocessed")
    if archived:
        assert client.post(f"/api/captures/{capture['id']}/state", json={"action": "unarchive"}).json()["status"] == "processed"


@pytest.mark.parametrize("archived", [False, True])
def test_capture_restore_reconciles_surviving_applied_proposals(api, archived):
    client, _, _ = api
    capture = save(client)
    confirm(client, analyze(api, capture))
    pending = analyze(api, capture, reanalyze=True)
    path = f"/api/captures/{capture['id']}/state"
    if archived:
        assert client.post(path, json={"action": "archive"}).status_code == 200
    assert client.post(path, json={"action": "trash"}).status_code == 200
    restored = client.post(path, json={"action": "restore"}).json()
    assert restored["status"] == ("archived" if archived else "processed")
    if archived:
        assert client.post(path, json={"action": "unarchive"}).json()["status"] == "processed"
    assert client.get("/api/proposals/" + pending["id"]).json()["status"] == "rejected"
    response = write(client, "POST", f"/api/captures/{capture['id']}/analyze", {"mode": "local"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "REANALYZE_REQUIRED"


@pytest.mark.parametrize("damage", ["malformed", "missing"])
def test_corrupt_indexed_capture_is_isolated_and_can_be_trashed(api, damage):
    client, app, profile = api
    broken, valid = save(client), save(client, "有效原文")
    ws = profile["workspace"]["id"]
    name = app.state.captures.file_name(broken["client_capture_id"])
    if damage == "malformed":
        app.state.store.write(ws, "captures", name, "[]")
    else:
        app.state.store.remove(ws, "captures", name)
    response = client.get("/api/captures")
    assert response.status_code == 200, response.text
    records = {c["id"]: c for c in response.json()["items"]}
    assert records[valid["id"]]["raw_text"] == valid["raw_text"]
    assert records[broken["id"]]["integrity_error"]["code"] in {"STORAGE_CORRUPTED", "CAPTURE_UNAVAILABLE"}
    assert records[broken["id"]]["raw_text"] == ""
    assert client.get("/api/captures/" + broken["id"]).status_code == 503
    state_path = f"/api/captures/{broken['id']}/state"
    assert client.post(state_path, json={"action": "trash"}).status_code == 200
    assert client.get("/api/captures").json()["total"] == 1
    assert client.get("/api/captures?trash=true").json()["items"][0]["integrity_error"]
    assert client.post(state_path, json={"action": "restore"}).status_code == 200
    if damage == "malformed":
        assert app.state.store.read(ws, "captures", name) == "[]"


def test_file_folder_namespace_conflicts_are_rejected_in_both_directions(api):
    client, _, _ = api
    group(client, "冲突")
    assert write(client, "POST", "/api/folders", {"name": "冲突.md"}).status_code == 409
    folder = write(client, "POST", "/api/folders", {"name": "空目录.md"}).json()["folder_ids"][0]
    assert write(client, "POST", "/api/groups", {"title": "显式文件名", "file_name": "空目录.md"}).status_code == 409
    suffixed = group(client, "空目录")
    assert suffixed["path"] == "空目录 (2).md"
    assert write(client, "PATCH", "/api/folders/" + folder, {"name": "冲突.md", "expected_revision": 1}).status_code == 409


def test_case_only_file_rename_and_undo_preserve_content(api):
    client, app, profile = api
    original = group(client, "Case", file_name="Contract.md", tasks=[{"title": "提交合同"}])
    response = write(client, "PATCH", "/api/groups/" + original["id"], {**version(original), "file_name": "contract.md"})
    assert response.status_code == 200, response.text
    renamed = client.get("/api/groups/" + original["id"]).json()
    assert renamed["path"] == "contract.md"
    assert renamed["tasks"][0]["id"] == original["tasks"][0]["id"]
    root = app.state.store.workspace_root(profile["workspace"]["id"]) / "files"
    assert [p.name for p in root.glob("*.md")] == ["contract.md"]
    assert write(client, "POST", "/api/changes/" + response.json()["id"] + "/undo").status_code == 200
    assert [p.name for p in root.glob("*.md")] == ["Contract.md"]
    assert rebuild(api)["errors"] == []


def test_body_edit_quota_includes_file_and_snapshot_without_leaking_blobs(api):
    client, app, profile = api
    original = group(client)
    ws = profile["workspace"]["id"]
    app.state.store.max_workspace_bytes = 100000
    usage = app.state.store.usage(ws)
    with app.state.db.session() as session:
        changes_before = list(session.scalars(select(ChangeSet.id)))
    response = write(client, "PATCH", "/api/groups/" + original["id"], {**version(original), "body": "中" * 20000})
    assert response.status_code == 507, response.text
    assert app.state.store.usage(ws) == usage
    assert client.get("/api/groups/" + original["id"]).json()["markdown"] == original["markdown"]
    with app.state.db.session() as session:
        assert list(session.scalars(select(ChangeSet.id))) == changes_before
    app.state.store.max_workspace_bytes = 150000
    accepted = write(client, "PATCH", "/api/groups/" + original["id"], {**version(original), "body": "中" * 20000})
    assert accepted.status_code == 200, accepted.text
    assert app.state.store.usage(ws) <= app.state.store.max_workspace_bytes


def test_group_options_paginate_all_targets_without_reading_markdown(api, monkeypatch):
    client, app, _ = api
    groups = [group(client, f"目标 {index:03d}", tasks=[{"title": "任务正文不应随选项加载"}]) for index in range(103)]
    with app.state.db.session() as session:
        for row in session.scalars(select(GroupRecord)):
            row.updated_at = 1.0
    expected_ids = sorted(g["id"] for g in groups)

    def no_content_reads(*args, **kwargs):
        raise AssertionError("Destination options must only read indexed metadata")

    monkeypatch.setattr(app.state.store, "read", no_content_reads)
    seen = []
    for offset in range(0, 103, 20):
        response = client.get("/api/group-options", params={"offset": offset, "limit": 20})
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["total"] == 103 and page["offset"] == offset and page["limit"] == 20
        for item in page["items"]:
            assert set(item) == {"id", "title", "path", "folder_id"}
        seen.extend(item["id"] for item in page["items"])
    assert seen == expected_ids
    assert client.get("/api/group-options", params={"q": "  目标 102  "}).json()["items"][0]["id"] == groups[-1]["id"]
    assert client.get("/api/group-options", params={"offset": 200}).json()["items"] == []


def test_group_options_search_paths_and_exclude_unwritable_or_foreign_targets(api):
    client, _, _ = api
    root = write(client, "POST", "/api/folders", {"name": "ClientDocs"}).json()["folder_ids"][0]
    child = write(client, "POST", "/api/folders", {"name": "子目录", "parent_id": root}).json()["folder_ids"][0]
    visible = group(client, "有效合同", folder_id=child)
    done = group(client, "Completed")
    archived = group(client, "归档组")
    trashed = group(client, "删除组")
    assert write(client, "PATCH", "/api/groups/" + done["id"], {**version(done), "status": "done"}).status_code == 200
    assert write(client, "PATCH", "/api/groups/" + archived["id"], {**version(archived), "status": "archived"}).status_code == 200
    assert write(client, "POST", "/api/groups/" + trashed["id"] + "/trash", version(trashed)).status_code == 200
    assert {g["id"] for g in client.get("/api/group-options").json()["items"]} == {visible["id"], done["id"]}
    assert client.get("/api/group-options", params={"q": "clientdocs/子目录"}).json()["items"][0]["id"] == visible["id"]
    assert client.get("/api/group-options", params={"q": "completed"}).json()["items"][0]["id"] == done["id"]
    assert write(client, "PATCH", "/api/folders/" + root, {"expected_revision": 1, "archived": True}).status_code == 200
    assert [g["id"] for g in client.get("/api/group-options").json()["items"]] == [done["id"]]
    other = client.post("/api/auth/register", json={"username": "option-reader", "password": "different-password", "display_name": "另一用户"}).json()
    client.headers["Authorization"] = "Bearer " + other["token"]
    assert client.get("/api/group-options").json()["total"] == 0
    assert client.get("/api/group-options", params={"q": "Completed"}).json()["items"] == []
    assert client.get("/api/group-options", headers={"Authorization": ""}).status_code == 401
    for params in ({"offset": -1}, {"limit": 101}, {"limit": 0}, {"q": "x" * 201}):
        assert client.get("/api/group-options", params=params).status_code == 422


def test_legacy_undo_chain_reconciles_proposal_status(api):
    client, app, _ = api
    capture = save(client)
    proposal = analyze(api, capture)
    change = confirm(client, proposal)
    for index in range(4):
        with app.state.db.session() as session:
            row = session.get(ChangeSet, change["id"])
            row.effects = {k: v for k, v in row.effects.items() if k != "proposal_states"}
        response = write(client, "POST", "/api/changes/" + change["id"] + "/undo")
        assert response.status_code == 200, response.text
        change = response.json()
        applied = index % 2 == 1
        assert client.get("/api/proposals/" + proposal["id"]).json()["status"] == ("applied" if applied else "rejected")
        assert client.get("/api/captures/" + capture["id"]).json()["status"] == ("processed" if applied else "unprocessed")


def test_case_only_folder_rename_reindex_preserves_folder_identity(api):
    client, _, _ = api
    folder = write(client, "POST", "/api/folders", {"name": "Client"}).json()["folder_ids"][0]
    original = group(client, folder_id=folder)
    response = write(client, "PATCH", "/api/folders/" + folder, {"expected_revision": 1, "name": "client"})
    assert response.status_code == 200, response.text
    assert rebuild(api)["errors"] == []
    current = client.get("/api/groups/" + original["id"]).json()
    assert current["folder_id"] == folder
    assert current["path"].casefold() == "client/合同跟进.md"
