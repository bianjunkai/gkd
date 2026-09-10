from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from .domain import StrictModel, TaskDraft, TaskStatus, TimeSpec, Title


class RegisterInput(StrictModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=60)


class LoginInput(StrictModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=128)


class WechatInput(StrictModel):
    code: str = Field(min_length=1, max_length=256)


class WorkspaceInput(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    timezone: str | None = None
    ai_enabled: bool | None = None
    ai_consent_version: str | None = None


class CaptureInput(StrictModel):
    client_capture_id: str = Field(min_length=8, max_length=120)
    raw_text: str = Field(min_length=1, max_length=10000)
    source_type: Literal["text", "wechat"] = "text"

    @field_validator("raw_text")
    @classmethod
    def valid_text(cls, value):
        if not value.strip():
            raise ValueError("请填写内容")
        if len(value.encode("utf-8")) > 65536:
            raise ValueError("单条内容不能超过 64 KiB")
        return value  # Never normalize or trim the user's raw Capture.


class AnalysisInput(StrictModel):
    mode: Literal["local", "external"] = "local"
    reference_time: datetime | None = None
    target_group_id: str | None = None
    reanalyze: bool = False

    @field_validator("reference_time")
    @classmethod
    def aware_reference(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("参照时间必须包含时区")
        return value


class VersionInput(StrictModel):
    expected_revision: int = Field(ge=1)
    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class GroupCreate(StrictModel):
    title: Title
    folder_id: str | None = None
    file_name: str | None = None
    body: str = Field(default="", max_length=100000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    tasks: list[TaskDraft] = Field(default_factory=list, max_length=200)


class GroupUpdate(VersionInput):
    title: Title | None = None
    folder_id: str | None = None
    file_name: str | None = None
    body: str | None = Field(default=None, max_length=100000)
    tags: list[str] | None = Field(default=None, max_length=20)
    status: Literal["active", "done", "archived"] | None = None


class MarkdownEdit(VersionInput):
    markdown: str = Field(min_length=1, max_length=1024 * 1024)


class TaskPatch(StrictModel):
    title: Title | None = None
    description: str | None = Field(default=None, max_length=100000)
    status: TaskStatus | None = None
    owner: str | None = Field(default=None, max_length=100)
    scheduled: TimeSpec | None = None
    deadline: TimeSpec | None = None
    priority: Literal["low", "medium", "high"] | None = None
    location: str | None = Field(default=None, max_length=200)
    context: str | None = Field(default=None, max_length=200)
    tags: list[str] | None = Field(default=None, max_length=20)


class TaskUpdate(VersionInput):
    patch: TaskPatch


class TaskCreate(VersionInput):
    task: TaskDraft


class FolderCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    parent_id: str | None = None


class FolderUpdate(StrictModel):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    parent_id: str | None = None
    archived: bool | None = None


class Question(StrictModel):
    field_path: str = Field(max_length=100)
    message: str = Field(max_length=500)
    required: bool = False
    resolved: bool = False


class ProposalAction(StrictModel):
    action_id: str
    type: Literal["create", "append"]
    target_group_id: str | None = None
    folder_id: str | None = None
    group_title: Title
    file_name: str | None = None
    tasks: list[TaskDraft] = Field(default_factory=list, max_length=20)
    body_append: str = Field(default="", max_length=20000)
    evidence: dict[str, str] = Field(default_factory=dict, max_length=30)
    field_confidence: dict[str, Literal["high", "medium", "low"]] = Field(default_factory=dict, max_length=30)


class ProposalEdit(StrictModel):
    proposal_revision: int = Field(ge=1)
    actions: list[ProposalAction] = Field(min_length=1, max_length=20)
    questions: list[Question] = Field(default_factory=list, max_length=20)


class ProposalConfirm(StrictModel):
    proposal_revision: int = Field(ge=1)
    selected_action_ids: list[str] = Field(min_length=1, max_length=20)


class ProposalReject(StrictModel):
    proposal_revision: int = Field(ge=1)


class RestoreInput(VersionInput):
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class CaptureState(StrictModel):
    action: Literal["archive", "unarchive", "trash", "restore"]


class ExportInput(StrictModel):
    include_history: bool = False
