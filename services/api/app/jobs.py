import logging
import threading
import time
from datetime import datetime

from sqlalchemy import and_, or_, select, update

from .db import CaptureRecord, Job, Workspace
from .domain import json_hash, new_id
from .errors import AppError, conflict, not_found

logger = logging.getLogger("gkd.jobs")


def job_json(job):
    return {"id": job.id, "kind": job.kind, "status": job.status, "attempts": job.attempts,
            "created_at": job.created_at, "finished_at": job.finished_at,
            "result": job.result, "error": job.error, "run_after": job.run_after}


class JobService:
    def __init__(self, db, store, settings, changes):
        self.db, self.store, self.settings, self.changes = db, store, settings, changes
        self.handlers = {}
        self.stop_event = threading.Event()
        self.thread = None

    def submit(self, ws, kind, payload, key, request_hash):
        with self.store.lock(ws), self.db.session() as session:
            self.changes.ensure_ready(session, ws)
            existing = session.scalar(select(Job).where(Job.workspace_id == ws, Job.request_key == key))
            if existing:
                if existing.request_hash != request_hash:
                    raise AppError("IDEMPOTENCY_MISMATCH", "同一请求标识不能用于不同内容。", 409)
                return job_json(existing)
            if kind == "analysis":
                capture = session.get(CaptureRecord, payload["capture_id"])
                if not capture or capture.workspace_id != ws or capture.deleted_at:
                    raise not_found()
                if capture.status == "processing":
                    raise AppError("JOB_IN_PROGRESS", "这条记录正在整理，请等待结果。", 409)
                if capture.status in {"processed", "needs_confirmation"} and not payload.get("reanalyze"):
                    raise AppError("REANALYZE_REQUIRED", "此记录已有整理结果；重新分析可能产生重复任务，请明确选择重新分析。", 409)
                if capture.status == "archived":
                    raise conflict("请先从归档恢复这条记录。")
                workspace = session.get(Workspace, ws)
                payload = {**payload, "timezone": workspace.timezone,
                           "reference_time": payload.get("reference_time") or datetime.fromtimestamp(capture.created_at).astimezone().isoformat(),
                           "previous_status": capture.status}
                capture.status = "processing"
            job = Job(id=new_id("job"), workspace_id=ws, kind=kind, payload=payload,
                      request_key=key, request_hash=request_hash, status="pending")
            session.add(job)
            session.flush()
            return job_json(job)

    def get(self, ws, job_id):
        with self.db.session() as session:
            job = session.get(Job, job_id)
            if not job or job.workspace_id != ws:
                raise not_found()
            return job_json(job)

    def list(self, ws, limit=30):
        with self.db.session() as session:
            return [job_json(j) for j in session.scalars(select(Job).where(Job.workspace_id == ws)
                    .order_by(Job.created_at.desc()).limit(limit))]

    def claim(self):
        now = time.time()
        eligible = or_(and_(Job.status == "pending", Job.run_after <= now),
                       and_(Job.status == "running", Job.lease_until < now))
        with self.db.session() as session:
            job_id = session.scalar(select(Job.id).where(eligible).order_by(Job.created_at, Job.id).limit(1))
            if not job_id:
                return None
            token = new_id("lease")
            changed = session.execute(update(Job).where(Job.id == job_id, eligible).values(
                status="running", lease_token=token, lease_until=now + max(180, self.settings.ai_timeout_seconds * 2 + 60),
                attempts=Job.attempts + 1))
            if changed.rowcount != 1:
                return None
            return session.get(Job, job_id)

    def run_once(self):
        job = self.claim()
        if not job:
            return False
        try:
            with self.db.session() as session:
                workspace = session.get(Workspace, job.workspace_id)
                if not workspace or workspace.deleted_at:
                    raise AppError("ACCOUNT_UNAVAILABLE", "账号或工作空间已不可用。", 403)
            if job.attempts > 3:
                raise AppError("JOB_RETRY_EXHAUSTED", "进程多次中断，请手动重新发起。", 503)
            result = self.handlers[job.kind](job)
            with self.store.lock(job.workspace_id), self.db.session() as session:
                current = session.get(Job, job.id)
                if current.lease_token == job.lease_token:
                    current.status, current.result, current.error = "succeeded", result, None
                    current.finished_at, current.lease_until = time.time(), None
        except Exception as exc:
            error = exc if isinstance(exc, AppError) else AppError("JOB_FAILED", "后台处理失败，原始数据已保留，请重试。", 503)
            logger.warning("job_failed job_id=%s code=%s", job.id, error.code)
            with self.store.lock(job.workspace_id), self.db.session() as session:
                current = session.get(Job, job.id)
                if current.lease_token != job.lease_token:
                    return True
                retry = error.retryable and current.attempts < 3
                current.status = "pending" if retry else "failed"
                current.run_after = time.time() + 2 ** current.attempts
                current.error = {"code": error.code, "message": error.message, "retryable": error.retryable}
                current.lease_until = None
                if not retry:
                    current.finished_at = time.time()
                    if job.kind == "analysis":
                        capture = session.get(CaptureRecord, job.payload["capture_id"])
                        if capture and capture.workspace_id == job.workspace_id and not capture.deleted_at and capture.status == "processing":
                            capture.status = "failed"
        return True

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                worked = self.run_once()
            except Exception:
                logger.error("worker_iteration_failed")
                worked = False
            if not worked:
                self.stop_event.wait(self.settings.worker_poll_seconds)

    def start(self):
        if self.thread is None or not self.thread.is_alive():
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._loop, name="gkd-job-worker", daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            # Keep the server/backup lock until all file writes have actually settled.
            # Model calls have bounded HTTP timeouts; force-kill recovery uses the journals.
            self.thread.join()
