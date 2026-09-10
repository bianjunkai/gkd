import json
import time
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .db import AuditEvent, CaptureRecord, Job, Proposal, Workspace
from .domain import TimeSpec, digest, new_id, now_iso, utc_timestamp
from .errors import AppError, conflict, not_found
from .schemas import CaptureInput
from .storage import check_id


class StoredCapture(CaptureInput):
    schema_version: Literal[1]
    id: str
    workspace_id: str
    created_at: str
    created_epoch: float = Field(gt=0, allow_inf_nan=False, strict=True)
    reference_timezone: str

    @field_validator("id", "workspace_id")
    @classmethod
    def valid_identity(cls, value, info):
        check_id(value)
        prefix = "capture-" if info.field_name == "id" else "workspace-"
        if not value.startswith(prefix):
            raise ValueError("Unexpected identity prefix")
        return value

    @field_validator("created_at")
    @classmethod
    def valid_timestamp(cls, value):
        return utc_timestamp(value)

    @field_validator("reference_timezone")
    @classmethod
    def valid_timezone(cls, value):
        return TimeSpec.valid_zone(value)

    @model_validator(mode="after")
    def matching_timestamps(self):
        if abs(datetime.fromisoformat(self.created_at).timestamp() - self.created_epoch) > 2:
            raise ValueError("Capture timestamps disagree")
        return self


def refresh_capture_status(session, workspace_id, capture_id):
    """Reflect all surviving proposals, without unarchiving or stopping an active analysis."""
    capture = session.get(CaptureRecord, capture_id)
    if not capture or capture.workspace_id != workspace_id or capture.deleted_at:
        return
    states = set(session.scalars(select(Proposal.status).where(
        Proposal.workspace_id == workspace_id, Proposal.capture_id == capture_id)))
    status = ("needs_confirmation" if states & {"pending_confirmation", "stale", "failed"}
              else "processed" if "applied" in states else "unprocessed")
    if capture.status == "archived":
        capture.previous_status = status
    elif capture.status != "processing":
        capture.status = status


class CaptureService:
    def __init__(self, db, store):
        self.db, self.store = db, store

    @staticmethod
    def file_name(client_capture_id):
        return digest(client_capture_id) + ".json"

    def _read_raw(self, workspace_id, name):
        try:
            path = self.store.path(workspace_id, "captures", name)
            if path.stat().st_size > 128 * 1024:
                raise ValueError("Capture file exceeds its format limit")
            data = StoredCapture.model_validate_json(path.read_text(encoding="utf-8")).model_dump()
            if data["workspace_id"] != workspace_id or name != self.file_name(data["client_capture_id"]):
                raise ValueError("Capture file identity mismatch")
        except OSError as exc:
            raise AppError("CAPTURE_UNAVAILABLE", "原始记录暂时不可用，请检查存储或备份。", 503) from exc
        except (ValueError, TypeError, AppError) as exc:
            raise AppError("STORAGE_CORRUPTED", "原始记录校验失败，已保留文件并停止操作。", 503) from exc
        return data

    def _raw(self, workspace_id, row):
        data = self._read_raw(workspace_id, self.file_name(row.client_capture_id))
        if (data["id"] != row.id or data["source_type"] != row.source_type
                or digest(data["raw_text"]) != row.content_hash or data["created_epoch"] != row.created_at):
            raise AppError("STORAGE_CORRUPTED", "原始记录校验失败，已停止操作。", 503)
        return data

    def _view(self, workspace_id, row):
        data = self._raw(workspace_id, row)
        return {**data, "status": row.status, "deleted_at": row.deleted_at}

    def _listing_view(self, workspace_id, row):
        try:
            return self._view(workspace_id, row)
        except AppError as exc:
            if exc.code not in {"CAPTURE_UNAVAILABLE", "STORAGE_CORRUPTED"}:
                raise
            # Display verified index metadata only; never substitute cached text for
            # unreadable source content or pass that content to analysis.
            return {"id": row.id, "workspace_id": workspace_id, "client_capture_id": row.client_capture_id,
                    "source_type": row.source_type, "raw_text": "", "status": row.status,
                    "created_at": datetime.fromtimestamp(row.created_at, UTC).isoformat(),
                    "deleted_at": row.deleted_at, "reference_timezone": None,
                    "integrity_error": {"code": exc.code, "message": exc.message}}

    def save(self, workspace_id, data: CaptureInput):
        name = self.file_name(data.client_capture_id)
        with self.store.lock(workspace_id), self.db.session() as session:
            workspace = session.get(Workspace, workspace_id)
            if not workspace or workspace.deleted_at:
                raise not_found()
            previous = session.scalar(select(CaptureRecord).where(
                CaptureRecord.workspace_id == workspace_id,
                CaptureRecord.client_capture_id == data.client_capture_id,
            ))
            if previous:
                if previous.content_hash != digest(data.raw_text) or previous.source_type != data.source_type:
                    raise AppError("IDEMPOTENCY_MISMATCH", "这条提交标识已用于其他内容。", 409)
                if previous.deleted_at:
                    raise conflict("原提交已在回收站，请先恢复，不能重复创建。")
                return self._view(workspace_id, previous)
            path = self.store.path(workspace_id, "captures", name)
            if path.exists():
                # A crash after the durable file write but before the DB commit is recoverable.
                raw = self._read_raw(workspace_id, name)
                if raw["raw_text"] != data.raw_text or raw["source_type"] != data.source_type:
                    raise AppError("IDEMPOTENCY_MISMATCH", "原提交已持久化，但内容与重试不一致。", 409)
                if session.get(CaptureRecord, raw["id"]):
                    raise AppError("STORAGE_CORRUPTED", "原始记录 ID 已被占用，已保留文件并停止操作。", 503)
            else:
                raw = {"schema_version": 1, "id": new_id("capture"), "workspace_id": workspace_id,
                       **data.model_dump(), "created_at": now_iso(), "created_epoch": time.time(),
                       "reference_timezone": workspace.timezone}
                text = json.dumps(raw, ensure_ascii=False, indent=2)
                self.store.check_capacity(workspace_id, len(text.encode("utf-8")))
                self.store.write(workspace_id, "captures", name, text)
            row = CaptureRecord(id=raw["id"], workspace_id=workspace_id,
                                client_capture_id=data.client_capture_id, source_type=data.source_type,
                                content_hash=digest(data.raw_text), search_text=data.raw_text,
                                created_at=raw["created_epoch"], status="unprocessed")
            session.add(row)
            session.add(AuditEvent(id=new_id("audit"), workspace_id=workspace_id,
                                   action="capture_saved", resource_id=row.id))
            session.flush()
            return self._view(workspace_id, row)

    def get(self, workspace_id, capture_id, *, allow_deleted=False):
        with self.store.lock(workspace_id), self.db.session() as session:
            row = session.get(CaptureRecord, capture_id)
            if not row or row.workspace_id != workspace_id or (row.deleted_at and not allow_deleted):
                raise not_found()
            result = self._view(workspace_id, row)
            result["proposals"] = [{"id": p.id, "revision": p.revision, "status": p.status}
                                   for p in session.scalars(select(Proposal).where(
                                       Proposal.workspace_id == workspace_id, Proposal.capture_id == row.id)
                                       .order_by(Proposal.created_at.desc()))]
            result["jobs"] = [{"id": j.id, "status": j.status, "error": j.error}
                              for j in session.scalars(select(Job).where(
                                  Job.workspace_id == workspace_id, Job.kind == "analysis")
                                  .order_by(Job.created_at.desc())) if j.payload.get("capture_id") == row.id][:5]
            return result

    def list(self, workspace_id, status=None, offset=0, limit=50, trash=False):
        with self.store.lock(workspace_id), self.db.session() as session:
            query = select(CaptureRecord).where(CaptureRecord.workspace_id == workspace_id)
            query = query.where(CaptureRecord.deleted_at.is_not(None) if trash else CaptureRecord.deleted_at.is_(None))
            if status:
                query = query.where(CaptureRecord.status == status)
            elif not trash:
                query = query.where(CaptureRecord.status != "archived")
            rows = list(session.scalars(query.order_by(CaptureRecord.created_at.desc(), CaptureRecord.id)))
            return {"items": [self._listing_view(workspace_id, r) for r in rows[offset:offset + limit]],
                    "total": len(rows), "offset": offset, "limit": limit}

    def change_state(self, workspace_id, capture_id, action):
        with self.store.lock(workspace_id), self.db.session() as session:
            row = session.get(CaptureRecord, capture_id)
            if not row or row.workspace_id != workspace_id:
                raise not_found()
            if row.status == "processing":
                raise conflict("请等待正在进行的整理结束后再操作。")
            if action == "trash":
                row.deleted_at = row.deleted_at or time.time()
                for p in session.scalars(select(Proposal).where(Proposal.capture_id == row.id,
                                                               Proposal.workspace_id == workspace_id,
                                                               Proposal.status.in_(["pending_confirmation", "stale", "failed"]))):
                    p.status = "rejected"
            elif action == "restore":
                if row.deleted_at is not None:
                    row.deleted_at = None
                    refresh_capture_status(session, workspace_id, row.id)
            elif row.deleted_at:
                raise not_found()
            elif action == "archive" and row.status != "archived":
                row.previous_status, row.status = row.status, "archived"
            elif action == "unarchive" and row.status == "archived":
                row.status = row.previous_status or "unprocessed"
                row.previous_status = None
                refresh_capture_status(session, workspace_id, row.id)
            session.add(AuditEvent(id=new_id("audit"), workspace_id=workspace_id,
                                   action="capture_" + action, resource_id=row.id))
            return self._listing_view(workspace_id, row)

    def recover(self):
        errors = []
        with self.db.session() as session:
            ids = list(session.scalars(select(Workspace.id).where(Workspace.deleted_at.is_(None))))
        for ws in ids:
            root = self.store.workspace_root(ws)
            with self.store.lock(ws), self.db.session() as session:
                for path in sorted((root / "captures").glob("*.json")):
                    try:
                        # Each record has a savepoint: one invalid row must not poison the batch.
                        with session.begin_nested():
                            raw = self._read_raw(ws, path.name)
                            existing = session.get(CaptureRecord, raw["id"])
                            if existing:
                                if existing.workspace_id != ws or existing.client_capture_id != raw["client_capture_id"]:
                                    raise ValueError("Capture ID already belongs to another record")
                                self._raw(ws, existing)
                                continue
                            session.add(CaptureRecord(id=raw["id"], workspace_id=ws,
                                                      client_capture_id=raw["client_capture_id"],
                                                      content_hash=digest(raw["raw_text"]), search_text=raw["raw_text"],
                                                      source_type=raw["source_type"], created_at=raw["created_epoch"],
                                                      status="unprocessed"))
                            session.flush()
                    except (ValueError, OSError, AppError, IntegrityError):
                        errors.append({"workspace_id": ws, "path": path.name})
        return errors
