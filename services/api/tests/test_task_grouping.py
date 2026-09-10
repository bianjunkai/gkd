import copy

import pytest

from app.db import GroupRecord, Proposal
from app.domain import TaskDraft
from app.errors import AppError
from app.grouping import suggested_group_title, validate_new_group_title
from test_api import api, analyze, confirm, group, save, version, write


@pytest.mark.parametrize("title,task", [
    ("发送合同.md", "发送合同"),
    (" SEND CONTRACT ", "send contract"),
    ("提交" + "验收内容" * 14 + "…", "提交" + "验收内容" * 20),
])
def test_task_title_variants_do_not_become_container_names(title, task):
    tasks = [TaskDraft(title=task)]
    assert suggested_group_title(title, tasks) == "日常任务"
    with pytest.raises(AppError) as denied:
        validate_new_group_title(title, tasks)
    assert denied.value.code == "TASK_GROUP_TITLE_REQUIRED"


def edit(client, proposal, actions, questions=None):
    response = client.patch("/api/proposals/" + proposal["id"], json={
        "proposal_revision": proposal["revision"], "actions": actions,
        "questions": proposal["questions"] if questions is None else questions})
    assert response.status_code == 200, response.text
    return response.json()


def test_tasks_use_a_named_workspace_container_and_reuse_it(api):
    client, app, profile = api
    first_capture = save(client, "明天发送合同")
    proposal = analyze(api, first_capture)
    action = proposal["actions"][0]
    assert action["type"] == "create" and action["group_title"] == "日常任务"
    assert action["group_title"] != action["tasks"][0]["title"]
    assert action["folder_id"] is None
    assert proposal["previews"][0]["path"] == "日常任务.md"
    assert client.get("/api/groups").json()["total"] == 0
    first = confirm(client, proposal)
    original = client.get("/api/groups/" + first["group_ids"][0]).json()
    second_capture = save(client, "周五提交方案")
    proposal = analyze(api, second_capture)
    assert proposal["actions"][0]["type"] == "append"
    assert proposal["actions"][0]["target_group_id"] == original["id"]
    assert client.get("/api/groups/" + original["id"]).json()["content_hash"] == original["content_hash"]
    second = confirm(client, proposal)
    assert second["group_ids"] == first["group_ids"]
    current = client.get("/api/groups/" + original["id"]).json()
    assert current["path"] == "日常任务.md" and current["folder_id"] is None
    assert [t["title"] for t in current["tasks"]] == ["明天发送合同", "周五提交方案"]
    assert current["tasks"][0]["id"] == original["tasks"][0]["id"]
    assert current["tasks"][1]["source_refs"] == [second_capture["id"]]
    assert client.get("/api/groups").json()["total"] == 1
    with app.state.db.session() as session:
        assert session.get(GroupRecord, original["id"]).workspace_id == profile["workspace"]["id"]
    assert write(client, "POST", "/api/changes/" + second["id"] + "/undo").status_code == 200
    restored = client.get("/api/groups/" + original["id"]).json()
    assert restored["tasks"] == original["tasks"]


def test_full_group_name_selects_existing_group_inside_folder(api):
    client, _, _ = api
    folder = write(client, "POST", "/api/folders", {"name": "产品项目"}).json()["folder_ids"][0]
    original = group(client, "春季发布", folder_id=folder, body="保留的发布背景")
    proposal = analyze(api, save(client, "明天提交春季发布的验收报告"))
    action = proposal["actions"][0]
    assert action["type"] == "append" and action["target_group_id"] == original["id"]
    assert action["folder_id"] == folder
    assert proposal["previews"][0]["path"] == "产品项目/春季发布.md"
    confirm(client, proposal)
    current = client.get("/api/groups/" + original["id"]).json()
    assert current["title"] == "春季发布" and "保留的发布背景" in current["body"]
    assert len(current["tasks"]) == 1 and client.get("/api/groups").json()["total"] == 1


def test_explicit_group_beats_a_matching_name_elsewhere(api):
    client, _, _ = api
    automatic = group(client, "春季发布")
    chosen = group(client, "本周行动")
    proposal = analyze(api, save(client, "明天提交春季发布报告"), target_group_id=chosen["id"])
    assert proposal["actions"][0]["target_group_id"] == chosen["id"]
    confirm(client, proposal)
    assert client.get("/api/groups/" + automatic["id"]).json()["tasks"] == []


def test_partial_name_is_not_used_as_an_automatic_target(api):
    client, _, _ = api
    existing = group(client, "客户合同跟进")
    proposal = analyze(api, save(client, "明天发送合同"))
    assert proposal["candidates"] and proposal["candidates"][0]["id"] == existing["id"]
    assert proposal["actions"][0]["type"] == "create"
    assert proposal["actions"][0]["group_title"] == "日常任务"


def test_duplicate_names_require_destination_confirmation(api):
    client, _, _ = api
    folders = [write(client, "POST", "/api/folders", {"name": name}).json()["folder_ids"][0]
               for name in ("项目甲", "项目乙")]
    groups = [group(client, "合同跟进", folder_id=folder) for folder in folders]
    proposal = analyze(api, save(client, "请更新合同跟进的资料"))
    assert any(q["field_path"] == "destination" and q["required"] and not q["resolved"] for q in proposal["questions"])
    denied = write(client, "POST", f"/api/proposals/{proposal['id']}/confirm", {
        "proposal_revision": proposal["revision"], "selected_action_ids": [proposal["actions"][0]["action_id"]]})
    assert denied.status_code == 422 and denied.json()["error"]["code"] == "CLARIFICATION_REQUIRED"
    actions = copy.deepcopy(proposal["actions"])
    actions[0].update(type="append", target_group_id=groups[1]["id"])
    proposal = edit(client, proposal, actions, [{**q, "resolved": True} for q in proposal["questions"]])
    assert proposal["previews"][0]["path"] == "项目乙/合同跟进.md"
    assert confirm(client, proposal)["group_ids"] == [groups[1]["id"]]
    assert client.get("/api/groups/" + groups[0]["id"]).json()["tasks"] == []


def test_full_path_disambiguates_same_named_groups(api):
    client, _, _ = api
    folders = [write(client, "POST", "/api/folders", {"name": name}).json()["folder_ids"][0]
               for name in ("项目甲", "项目乙")]
    groups = [group(client, "合同跟进", folder_id=folder) for folder in folders]
    proposal = analyze(api, save(client, "请更新项目乙/合同跟进.md 的资料"))
    assert proposal["actions"][0]["target_group_id"] == groups[1]["id"]
    assert not any(q["field_path"] == "destination" for q in proposal["questions"])


def test_folder_path_does_not_also_match_root_filename(api):
    client, _, _ = api
    group(client, "合同跟进")
    folder = write(client, "POST", "/api/folders", {"name": "客户项目"}).json()["folder_ids"][0]
    chosen = group(client, "合同跟进", folder_id=folder)
    proposal = analyze(api, save(client, "请更新客户项目/合同跟进.md 的记录"))
    assert proposal["actions"][0]["target_group_id"] == chosen["id"]
    assert not any(q["field_path"] == "destination" for q in proposal["questions"])


@pytest.mark.parametrize("reuse", [False, True])
def test_new_group_is_created_or_reused_only_in_selected_folder(api, reuse):
    client, _, _ = api
    root = group(client, "采购清单")
    folder = write(client, "POST", "/api/folders", {"name": "办公室"}).json()["folder_ids"][0]
    chosen = group(client, "采购清单", folder_id=folder) if reuse else None
    proposal = analyze(api, save(client, "购买一个键盘"))
    actions = copy.deepcopy(proposal["actions"])
    actions[0].update(type="create", target_group_id=None, group_title="采购清单", folder_id=folder)
    proposal = edit(client, proposal, actions)
    assert proposal["actions"][0]["type"] == ("append" if reuse else "create")
    change = confirm(client, proposal)
    current = client.get("/api/groups/" + change["group_ids"][0]).json()
    assert current["folder_id"] == folder and current["path"] == "办公室/采购清单.md"
    assert current["tasks"][0]["title"] == "购买一个键盘"
    if chosen:
        assert current["id"] == chosen["id"]
    assert client.get("/api/groups/" + root["id"]).json()["tasks"] == []


@pytest.mark.parametrize("unavailable", ["archived", "archived_parent", "trashed"])
def test_unavailable_groups_are_never_reused(api, unavailable):
    client, _, _ = api
    folder = write(client, "POST", "/api/folders", {"name": "旧项目"}).json()["folder_ids"][0] if unavailable == "archived_parent" else None
    original = group(client, "日常任务", folder_id=folder)
    if unavailable == "archived":
        assert write(client, "PATCH", "/api/groups/" + original["id"], {**version(original), "status": "archived"}).status_code == 200
    elif unavailable == "trashed":
        assert write(client, "POST", "/api/groups/" + original["id"] + "/trash", version(original)).status_code == 200
    else:
        assert write(client, "PATCH", "/api/folders/" + folder, {"expected_revision": 1, "archived": True}).status_code == 200
    proposal = analyze(api, save(client, "更新日常任务的资料"))
    assert proposal["actions"][0]["type"] == "create"
    change = confirm(client, proposal)
    assert original["id"] not in change["group_ids"]
    current = client.get("/api/groups/" + change["group_ids"][0]).json()
    assert current["folder_id"] is None


@pytest.mark.parametrize("field", ["title", "file_name"])
def test_manual_creation_does_not_promote_task_to_file_title(api, field):
    client, _, _ = api
    data = {"title": "合同跟进", "tasks": [{"title": "发送合同"}], field: "发送合同.md" if field == "file_name" else "发送合同"}
    response = write(client, "POST", "/api/groups", data)
    assert response.status_code == 422 and response.json()["error"]["code"] == "TASK_GROUP_TITLE_REQUIRED"
    assert client.get("/api/groups").json()["total"] == 0
    assert client.get("/api/changes").json() == []


def test_proposal_cannot_name_new_group_after_a_task(api):
    client, _, _ = api
    proposal = analyze(api, save(client, "明天发送合同"))
    actions = copy.deepcopy(proposal["actions"])
    actions[0]["group_title"] = actions[0]["tasks"][0]["title"]
    response = client.patch("/api/proposals/" + proposal["id"], json={
        "proposal_revision": proposal["revision"], "actions": actions, "questions": proposal["questions"]})
    assert response.status_code == 422 and response.json()["error"]["code"] == "TASK_GROUP_TITLE_REQUIRED"
    assert client.get("/api/groups").json()["total"] == 0
    assert client.get("/api/proposals/" + proposal["id"]).json()["revision"] == proposal["revision"]


def test_legacy_pending_task_named_file_is_rejected_without_writes(api):
    client, app, _ = api
    proposal = analyze(api, save(client, "明天发送合同"))
    with app.state.db.session() as session:
        row = session.get(Proposal, proposal["id"])
        data = copy.deepcopy(row.data)
        data["actions"][0]["group_title"] = data["actions"][0]["tasks"][0]["title"]
        row.data = data
    response = write(client, "POST", f"/api/proposals/{proposal['id']}/confirm", {
        "proposal_revision": 1, "selected_action_ids": [proposal["actions"][0]["action_id"]]})
    assert response.status_code == 422 and response.json()["error"]["code"] == "TASK_GROUP_TITLE_REQUIRED"
    assert client.get("/api/groups").json()["total"] == 0
    assert client.get("/api/changes").json() == []


def test_concurrent_new_container_proposals_rebind_before_confirmation(api):
    client, _, _ = api
    first = analyze(api, save(client, "发送合同"))
    second = analyze(api, save(client, "提交方案"))
    created = confirm(client, first)
    assert client.get("/api/proposals/" + second["id"]).json()["status"] == "stale"
    second = edit(client, second, second["actions"])
    assert second["actions"][0]["type"] == "append"
    assert confirm(client, second)["group_ids"] == created["group_ids"]
    assert client.get("/api/groups").json()["total"] == 1


def test_matching_does_not_cross_workspaces(api):
    client, _, profile = api
    original = group(client, "日常任务")
    registered = client.post("/api/auth/register", json={"username": "separate-user", "password": "separate-test-password", "display_name": "独立用户"}).json()
    client.headers["Authorization"] = "Bearer " + registered["token"]
    proposal = analyze(api, save(client, "更新日常任务的资料"))
    assert proposal["actions"][0]["type"] == "create"
    assert original["id"] not in confirm(client, proposal)["group_ids"]
    client.headers["Authorization"] = "Bearer " + profile["token"]
    assert client.get("/api/groups/" + original["id"]).json()["tasks"] == []
