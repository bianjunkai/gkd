import hashlib
import json
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta
from datetime import date as LocalDate
from datetime import time as LocalTime
from enum import StrEnum
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


def new_id(kind: str) -> str:
    return f"{kind}-{uuid.uuid4()}"


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def digest(content: str | bytes) -> str:
    return hashlib.sha256(content.encode("utf-8") if isinstance(content, str) else content).hexdigest()


def json_hash(value: object) -> str:
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def utc_timestamp(value: str | None):
    if value is None:
        return None
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None or moment.utcoffset() != timedelta(0):
        raise ValueError("审计时间必须是带时区的 UTC 时间戳")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeSpec(StrictModel):
    date: LocalDate
    time: LocalTime | None = None
    period: Literal["morning", "afternoon", "evening"] | None = None
    timezone: str = "Asia/Shanghai"

    @field_serializer("time")
    def serialize_time(self, value: LocalTime | None):
        return value.strftime("%H:%M") if value else None

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("请选择有效时区") from exc
        return value

    @model_validator(mode="after")
    def check_local_time(self):
        if self.time and self.period:
            raise ValueError("具体时间与上午/下午不能同时设置")
        if self.time and (self.time.tzinfo or self.time.second or self.time.microsecond):
            raise ValueError("时间精确到分钟，时区使用 timezone 字段")
        if self.time:
            local = datetime.combine(self.date, self.time)
            zone = ZoneInfo(self.timezone)
            first, second = local.replace(tzinfo=zone, fold=0), local.replace(tzinfo=zone, fold=1)
            if first.utcoffset() != second.utcoffset():
                raise ValueError("该时刻不存在或存在夏令时歧义，请重新选择")
            if first.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != local:
                raise ValueError("该本地时刻不存在，请重新选择")
        return self

    def boundary(self) -> datetime:
        zone = ZoneInfo(self.timezone)
        if self.time:
            return datetime.combine(self.date, self.time, zone)
        return datetime.combine(self.date + timedelta(days=1), LocalTime.min, zone)

    def is_today(self, moment: datetime) -> bool:
        return self.date == moment.astimezone(ZoneInfo(self.timezone)).date()


class TaskStatus(StrEnum):
    INBOX = "inbox"
    NEXT = "next"
    ACTIVE = "active"
    WAITING = "waiting"
    SCHEDULED = "scheduled"
    SOMEDAY = "someday"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


TERMINAL = {TaskStatus.DONE, TaskStatus.CANCELLED, TaskStatus.ARCHIVED}
Title = Annotated[str, Field(min_length=1, max_length=200)]


class TaskDraft(StrictModel):
    title: Title
    description: str = Field(default="", max_length=100000)
    status: TaskStatus = TaskStatus.NEXT
    owner: str | None = Field(default=None, max_length=100)
    scheduled: TimeSpec | None = None
    deadline: TimeSpec | None = None
    priority: Literal["low", "medium", "high"] = "medium"
    location: str | None = Field(default=None, max_length=200)
    context: str | None = Field(default=None, max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("任务标题不能为空")
        return value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        result = list(dict.fromkeys(unicodedata.normalize("NFC", v.strip()) for v in values))
        if any(not v or len(v) > 40 for v in result):
            raise ValueError("标签长度应为 1—40 个字符")
        return result

    @model_validator(mode="after")
    def check_task(self):
        if self.deadline and self.deadline.period:
            raise ValueError("截止时间需选择日期或具体时间")
        if self.status == TaskStatus.SCHEDULED and not self.scheduled:
            raise ValueError("已安排状态需要计划时间")
        return self


class Task(TaskDraft):
    model_config = ConfigDict(extra="allow")
    id: str = Field(default_factory=lambda: new_id("task"))
    parent_task_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    completed_at: str | None = None
    archived_from_status: TaskStatus | None = None

    @field_validator("created_at", "updated_at", "completed_at")
    @classmethod
    def valid_timestamps(cls, value):
        return utc_timestamp(value)

    @model_validator(mode="after")
    def consistent_status(self):
        if self.status == TaskStatus.DONE and not self.completed_at:
            self.completed_at = self.updated_at
        if self.status != TaskStatus.DONE and self.status != TaskStatus.ARCHIVED:
            self.completed_at = None
        return self


class Group(StrictModel):
    model_config = ConfigDict(extra="allow")
    schema_version: Literal[1, 2] = 2
    id: str = Field(default_factory=lambda: new_id("group"))
    title: Title
    status: Literal["active", "done", "archived"] = "active"
    revision: int = Field(default=1, ge=1)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    tags: list[str] = Field(default_factory=list)
    tasks: list[Task] = Field(default_factory=list, max_length=200)

    @field_validator("created_at", "updated_at")
    @classmethod
    def valid_timestamps(cls, value):
        return utc_timestamp(value)

    @field_validator("title")
    @classmethod
    def valid_title(cls, value):
        return TaskDraft.strip_title(value)

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, values):
        if len(values) > 20:
            raise ValueError("最多 20 个标签")
        return TaskDraft.normalize_tags(values)

    @model_validator(mode="after")
    def unique_tasks(self):
        if len({t.id for t in self.tasks}) != len(self.tasks):
            raise ValueError("同一文件内出现重复 Task ID")
        if self.status == "done" and any(t.status not in TERMINAL for t in self.tasks):
            raise ValueError("仍有未完成任务，不能标记整个任务组完成")
        return self


def task_view(task: Task, moment: datetime | None = None) -> dict:
    moment = moment or datetime.now(UTC)
    active = task.status not in TERMINAL
    overdue = bool(active and task.deadline and moment >= task.deadline.boundary())
    today = bool(
        active
        and not overdue
        and (
            (task.scheduled and task.scheduled.is_today(moment))
            or (task.deadline and task.deadline.is_today(moment))
        )
    )
    return {**task.model_dump(mode="json"), "is_overdue": overdue, "is_today": today}
