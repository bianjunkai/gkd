import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.captures import CaptureService
from app.db import AuditEvent, GroupRecord, Job, Proposal, Workspace
from app.domain import TaskDraft, json_hash
from app.errors import AppError
from app.extraction import Extraction, ExtractionService, consent_version, local_extract, strict_schema
from app.jobs import JobService
from app.proposals import ProposalService
from app.schemas import CaptureInput, GroupCreate
from app.workspace import WorkspaceService

RAW = "明天发送合同"
REFERENCE = datetime(2026, 9, 8, tzinfo=UTC)


@pytest.fixture
def external(system):
    settings, db, store, _, profile, changes = system
    settings.ai_provider = "responses"
    settings.ai_api_key = "unit-test-key-not-a-real-secret"
    settings.ai_model = "test-model"
    settings.ai_base_url = "https://provider.test/v1"
    ws = profile["workspace"]["id"]
    with db.session() as session:
        workspace = session.get(Workspace, ws)
        workspace.ai_enabled = True
        workspace.ai_consent_version = consent_version(settings)
    captures = CaptureService(db, store)
    workspace = WorkspaceService(db, store, changes)
    extraction = ExtractionService(db, store, settings)
    proposals = ProposalService(db, store, changes, workspace, captures, extraction)
    jobs = JobService(db, store, settings, changes)
    jobs.handlers["analysis"] = proposals.analyze_job
    return SimpleNamespace(settings=settings, db=db, ws=ws, captures=captures,
                           workspace=workspace, extraction=extraction, jobs=jobs)


def response(content=None):
    if content is None:
        content = Extraction(classification="single_task", group_title="合同跟进",
                             tasks=[TaskDraft(title="发送合同")]).model_dump_json()
    return httpx.Response(200, json={"status": "completed", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": content}]}],
        "usage": {"input_tokens": 100, "output_tokens": 30, "total_tokens": 130}})


def fake_http(monkeypatch, replies):
    calls = []

    def send(url, **options):
        calls.append({"url": url, **options})
        assert replies, "An unexpected extra provider request was attempted"
        result = replies.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(httpx, "post", send)
    return calls


def extract(external, mode="external"):
    return external.extraction.extract(external.ws, RAW, REFERENCE, "Asia/Shanghai", mode)


def queue_analysis(external, **extra):
    capture = external.captures.save(external.ws, CaptureInput(
        client_capture_id="external-capture-client", raw_text=RAW))
    payload = {"capture_id": capture["id"], "mode": "external", **extra}
    job = external.jobs.submit(external.ws, "analysis", payload, "external-analysis-key", json_hash(payload))
    return capture, job


def test_only_capture_and_explicit_target_metadata_are_sent(external, monkeypatch):
    private = GroupCreate(title="合同跟进", tags=["工作"], body="PRIVATE_BODY_NOT_FOR_MODEL",
                          tasks=[TaskDraft(title="PRIVATE_TASK_NOT_FOR_MODEL")])
    group = external.workspace.create(external.ws, private, "private-group-key", "group-hash")
    external.captures.save(external.ws, CaptureInput(
        client_capture_id="private-other-capture", raw_text="PRIVATE_OTHER_CAPTURE"))
    calls = fake_http(monkeypatch, [response()])
    capture, job = queue_analysis(external, target_group_id=group["group_ids"][0])
    assert external.jobs.run_once()
    assert external.jobs.get(external.ws, job["id"])["status"] == "succeeded"
    assert len(calls) == 1
    sent = calls[0]["json"]
    assert calls[0]["url"] == "https://provider.test/v1/responses"
    assert sent["store"] is False and "tools" not in sent
    payload = json.loads(sent["input"][1]["content"])
    assert set(payload) == {"raw_text", "reference_time", "timezone", "selected_target"}
    assert payload["raw_text"] == capture["raw_text"]
    assert payload["selected_target"] == {"title": "合同跟进", "tags": ["工作"]}
    serialized = json.dumps(sent, ensure_ascii=False)
    assert "PRIVATE_" not in serialized and external.ws not in serialized
    with external.db.session() as session:
        assert len(list(session.scalars(select(GroupRecord)))) == 1
        assert session.scalar(select(Proposal)).status == "pending_confirmation"
        audit = session.scalar(select(AuditEvent).where(AuditEvent.action == "ai_call"))
        assert RAW not in json.dumps(audit.details, ensure_ascii=False)
        assert audit.details["status"] == "validated"


def test_provider_change_requires_new_consent_and_disabled_ai_stays_local(external, monkeypatch):
    calls = fake_http(monkeypatch, [response()])
    external.settings.ai_model = "changed-model"
    with pytest.raises(AppError) as denied:
        extract(external)
    assert denied.value.code == "AI_CONSENT_REQUIRED" and not calls
    with external.db.session() as session:
        session.get(Workspace, external.ws).ai_consent_version = consent_version(external.settings)
    extract(external)
    with external.db.session() as session:
        session.get(Workspace, external.ws).ai_enabled = False
    with pytest.raises(AppError) as denied:
        extract(external)
    assert denied.value.code == "AI_CONSENT_REQUIRED" and len(calls) == 1
    assert extract(external, mode="local")[1]["provider"] == "local-rules"
    assert len(calls) == 1


def test_invalid_output_has_one_bounded_repair_and_each_call_consumes_budget(external, monkeypatch):
    calls = fake_http(monkeypatch, [response("{}"), response()])
    result, provenance = extract(external)
    assert result.tasks[0].title == "发送合同" and len(calls) == 2
    assert provenance["usage"]["total_tokens"] == 130
    assert external.extraction.usage(external.ws)["requests"] == 2
    with external.db.session() as session:
        events = list(session.scalars(select(AuditEvent).where(AuditEvent.action == "ai_call")
                                      .order_by(AuditEvent.created_at)))
        assert [event.details["status"] for event in events] == ["invalid", "validated"]


@pytest.mark.parametrize("malformed", [[], {"output": [None]}, {"output": [], "status": "incomplete"}])
def test_malformed_response_envelopes_fail_safely(external, monkeypatch, malformed):
    calls = fake_http(monkeypatch, [httpx.Response(200, json=malformed) for _ in range(2)])
    with pytest.raises(AppError) as failed:
        extract(external)
    assert failed.value.code == "AI_OUTPUT_INVALID" and len(calls) == 2


def test_unsupported_owner_evidence_is_rejected(external, monkeypatch):
    invented = Extraction(classification="single_task", group_title="合同", tasks=[
        TaskDraft(title="发送合同", owner="原文中不存在的人")]).model_dump_json()
    calls = fake_http(monkeypatch, [response(invented), response(invented)])
    with pytest.raises(AppError) as failed:
        extract(external)
    assert failed.value.code == "AI_OUTPUT_INVALID" and len(calls) == 2


def test_budget_can_stop_repair_without_an_extra_network_call(external, monkeypatch):
    external.settings.ai_daily_request_limit = 1
    calls = fake_http(monkeypatch, [response("{}")])
    with pytest.raises(AppError) as exhausted:
        extract(external)
    assert exhausted.value.code == "AI_BUDGET_EXCEEDED" and len(calls) == 1
    assert external.extraction.usage(external.ws)["requests"] == 1
    assert extract(external, mode="local")[0].tasks


@pytest.mark.parametrize("status,code", [(401, "AI_AUTH_FAILED"), (403, "AI_AUTH_FAILED"), (400, "AI_REQUEST_REJECTED")])
def test_permanent_provider_errors_are_not_automatically_retried(external, monkeypatch, status, code):
    calls = fake_http(monkeypatch, [httpx.Response(status, json={"private_error": "provider internals"})])
    with pytest.raises(AppError) as failed:
        extract(external)
    assert failed.value.code == code and not failed.value.retryable and len(calls) == 1
    assert "provider internals" not in failed.value.message


@pytest.mark.parametrize("provider_code,expected", [(401, "AI_AUTH_FAILED"), (1001, "AI_AUTH_FAILED"), (1113, "AI_REQUEST_REJECTED")])
def test_error_envelopes_with_http_200_are_classified_without_a_repair_call(external, monkeypatch, provider_code, expected):
    calls = fake_http(monkeypatch, [httpx.Response(200, json={
        "success": False, "code": provider_code, "msg": "令牌已过期或验证不正确"})])
    with pytest.raises(AppError) as failed:
        extract(external)
    assert failed.value.code == expected and not failed.value.retryable and len(calls) == 1
    assert "令牌" not in failed.value.message
    with external.db.session() as session:
        event = session.scalar(select(AuditEvent).where(
            AuditEvent.workspace_id == external.ws, AuditEvent.action == "ai_call"))
    assert event.details["status"] == "rejected" and event.details["provider_code"] == provider_code


def test_network_failure_retries_three_times_and_keeps_original_capture(external, monkeypatch):
    calls = fake_http(monkeypatch, [httpx.ReadTimeout("simulated timeout") for _ in range(3)])
    capture, queued = queue_analysis(external)
    for attempt in range(1, 4):
        assert external.jobs.run_once()
        job = external.jobs.get(external.ws, queued["id"])
        assert job["attempts"] == attempt
        assert job["status"] == ("pending" if attempt < 3 else "failed")
        with external.db.session() as session:
            session.get(Job, queued["id"]).run_after = 0
    assert not external.jobs.run_once() and len(calls) == 3
    assert job["error"]["code"] == "AI_NETWORK_ERROR"
    original = external.captures.get(external.ws, capture["id"])
    assert original["raw_text"] == RAW and original["status"] == "failed"
    with external.db.session() as session:
        assert list(session.scalars(select(Proposal))) == []
        assert list(session.scalars(select(GroupRecord))) == []


def test_structured_schema_has_no_optional_object_keys_or_defaults():
    def visit(value):
        if isinstance(value, dict):
            assert "default" not in value
            if value.get("type") == "object":
                assert value["additionalProperties"] is False
                assert set(value["required"]) == set(value["properties"])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(strict_schema())


def test_local_group_name_is_independent_of_first_task():
    result = local_extract("明天发送合同\n周五提交方案", REFERENCE, "Asia/Shanghai")
    assert result.group_title == "日常任务"
    assert result.group_title not in [task.title for task in result.tasks]
    assert len(result.tasks) == 2


def test_external_provider_cannot_promote_task_to_group_title(external, monkeypatch):
    content = Extraction(classification="single_task", group_title="发送合同", tasks=[TaskDraft(title="发送合同")]).model_dump_json()
    calls = fake_http(monkeypatch, [response(content)])
    result, _ = extract(external)
    assert result.group_title == "日常任务" and result.tasks[0].title == "发送合同"
    assert "group_title 是容纳多个任务的主题名称" in calls[0]["json"]["input"][0]["content"]
