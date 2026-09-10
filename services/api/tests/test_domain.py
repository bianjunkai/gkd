from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.domain import Task, TimeSpec, task_view


def test_date_only_deadline_expires_at_next_local_midnight():
    task = Task(title="提交方案", deadline=TimeSpec(date="2026-09-08"))
    assert not task_view(task, datetime(2026, 9, 8, 15, 59, tzinfo=UTC))["is_overdue"]
    assert task_view(task, datetime(2026, 9, 8, 16, 0, tzinfo=UTC))["is_overdue"]


def test_planned_date_is_not_a_deadline():
    task = Task(title="整理资料", scheduled=TimeSpec(date="2026-09-01", period="afternoon"))
    assert not task_view(task, datetime(2026, 9, 8, tzinfo=UTC))["is_overdue"]
    assert task.scheduled.time is None


def test_no_ambiguous_or_nonexistent_local_time():
    for day, clock in [("2026-03-08", "02:30"), ("2026-11-01", "01:30")]:
        with pytest.raises(ValidationError):
            TimeSpec(date=day, time=clock, timezone="America/New_York")


def test_time_is_serialized_at_minute_precision():
    assert TimeSpec(date="2026-09-08", time="14:30").model_dump(mode="json")["time"] == "14:30"


def test_unknown_deadline_is_not_filled():
    task = Task(title="联系客户")
    assert task.deadline is None
    assert task.owner is None
    with pytest.raises(ValidationError):
        Task(title="联系客户", deadline=TimeSpec(date="2026-09-08", period="afternoon"))


@pytest.mark.parametrize("field", ["created_at", "updated_at", "completed_at"])
def test_audit_timestamps_require_explicit_utc(field):
    with pytest.raises(ValidationError):
        Task(title="发送合同", **{field: "2026-09-08T12:00:00"})
    with pytest.raises(ValidationError):
        Task(title="发送合同", **{field: "2026-09-08T12:00:00+08:00"})
