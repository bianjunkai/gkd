import errno
import logging
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, OperationalError

from .auth import AuthService, workspace_json
from .captures import CaptureService
from .changes import ChangeEngine
from .config import PROJECT_ROOT, Settings
from .db import Database, Workspace
from .domain import TimeSpec, json_hash, new_id
from .errors import AppError
from .extraction import ExtractionService, consent_version
from .jobs import JobService
from .locking import workspace_lock
from .maintenance import MaintenanceService
from .proposals import ProposalService
from .schemas import (AnalysisInput, CaptureInput, CaptureState, FolderCreate, FolderUpdate,
                      GroupCreate, GroupUpdate, LoginInput, MarkdownEdit, ProposalConfirm, ProposalEdit, ProposalReject,
                      RegisterInput, RestoreInput, TaskCreate, TaskUpdate, VersionInput, WechatInput, WorkspaceInput)
from .storage import ContentStore
from .workspace import WorkspaceService

logger = logging.getLogger("gkd.api")


def create_app(settings: Settings | None = None):
    settings = settings or Settings()
    db = Database(settings)
    store = ContentStore(settings.data_root, settings.max_workspace_bytes)
    auth = AuthService(db, store, settings)
    changes = ChangeEngine(db, store)
    captures = CaptureService(db, store)
    workspace = WorkspaceService(db, store, changes)
    extraction = ExtractionService(db, store, settings)
    proposals = ProposalService(db, store, changes, workspace, captures, extraction)
    jobs = JobService(db, store, settings, changes)
    maintenance = MaintenanceService(db, store, changes, captures, workspace)
    jobs.handlers = {"analysis": proposals.analyze_job, "reindex": maintenance.reindex_job}

    @asynccontextmanager
    async def lifespan(app):
        # One local API process per DATA_ROOT. The same lock protects offline backup/restore.
        with workspace_lock(settings.data_root / "server.lock", timeout=0.2):
            db.initialize()
            app.state.recovery_errors = changes.recover_all()
            app.state.capture_errors = captures.recover()
            if settings.run_worker:
                jobs.start()
            try:
                yield
            finally:
                jobs.stop()
                db.dispose()

    app = FastAPI(title="归刻 · Markdown GTD API", version="0.1.0", lifespan=lifespan)
    for key, value in {"settings": settings, "db": db, "store": store, "auth": auth, "changes": changes,
                       "captures": captures, "workspace": workspace, "proposals": proposals,
                       "jobs": jobs, "maintenance": maintenance, "extraction": extraction}.items():
        setattr(app.state, key, value)
    app.add_middleware(CORSMiddleware, allow_origins=[s.strip() for s in settings.allowed_origins.split(",") if s.strip()],
                       allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
                       expose_headers=["X-Request-ID"], allow_credentials=False)
    attempts = defaultdict(deque)

    def error_response(request, error):
        return JSONResponse(status_code=error.status, content={"error": {"code": error.code, "message": error.message,
            "details": error.details, "retryable": error.retryable}, "request_id": getattr(request.state, "request_id", None)})

    @app.middleware("http")
    async def request_boundary(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        request.state.request_id = supplied if re.fullmatch(r"[A-Za-z0-9_-]{8,80}", supplied) else new_id("request")
        if request.url.path.startswith("/api/auth/") and request.method == "POST":
            address = request.client.host if request.client else "unknown"
            queue = attempts[address]
            while queue and queue[0] < time.time() - 300:
                queue.popleft()
            if len(queue) >= 30:
                return error_response(request, AppError("RATE_LIMITED", "登录尝试过于频繁，请稍后再试。", 429))
            queue.append(time.time())
        if request.method in {"POST", "PATCH"}:
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 1024 * 1024:
                    return error_response(request, AppError("PAYLOAD_TOO_LARGE", "请求超过 1 MiB，请拆分内容。", 413))
                chunks.append(chunk)
            request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(AppError)
    async def app_error(request, error):
        return error_response(request, error)

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def validation_error(request, error):
        fields = [{"field": ".".join(map(str, issue["loc"])), "message": issue["msg"]} for issue in error.errors()]
        return error_response(request, AppError("INPUT_INVALID", "输入内容未通过校验，请检查字段。", 422, details={"fields": fields}))

    @app.exception_handler(IntegrityError)
    async def duplicate_error(request, error):
        return error_response(request, AppError("DATA_CONFLICT", "数据标识或目标位置冲突，请刷新重试。", 409))

    @app.exception_handler(OperationalError)
    async def database_error(request, error):
        logger.error("database_unavailable request_id=%s", request.state.request_id)
        return error_response(request, AppError("DATABASE_UNAVAILABLE", "数据库暂时不可用，操作未确认成功，请稍后重试。", 503, retryable=True))

    @app.exception_handler(OSError)
    async def disk_error(request, error):
        message = "磁盘空间不足，请保留本机草稿并联系维护者。" if error.errno == errno.ENOSPC else "存储暂时不可用，操作未确认成功。"
        return error_response(request, AppError("STORAGE_UNAVAILABLE", message, 507 if error.errno == errno.ENOSPC else 503))

    @app.exception_handler(Exception)
    async def unexpected_error(request, error):
        logger.error("request_failed request_id=%s type=%s", request.state.request_id, type(error).__name__)
        return error_response(request, AppError("INTERNAL_ERROR", "操作未完成，请稍后重试；已保存的原文不会被自动删除。", 500))

    def token(authorization: str = Header(default="")):
        if not authorization.startswith("Bearer "):
            raise AppError("SESSION_EXPIRED", "请先登录。", 401)
        return authorization[7:]

    def identity(value: str = Depends(token)):
        return auth.identity(value)

    def ws_id(current=Depends(identity)):
        return current[1].id

    def key(idempotency_key: str = Header(alias="Idempotency-Key")):
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,160}", idempotency_key):
            raise AppError("INPUT_INVALID", "请提供有效的 Idempotency-Key。", 422)
        return idempotency_key

    def fingerprint(operation, data=None):
        return json_hash({"operation": operation, "body": data.model_dump(mode="json", exclude_unset=True) if hasattr(data, "model_dump") else data})

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.1.0", "password_auth": settings.enable_password_auth,
                "wechat_configured": bool(settings.wechat_app_id and settings.wechat_app_secret),
                "external_ai_configured": settings.external_ai_ready, "local_parser": "rules-v1"}

    @app.post("/api/auth/register", status_code=201)
    def register(data: RegisterInput):
        return auth.register(data.username, data.password, data.display_name)

    @app.post("/api/auth/login")
    def login(data: LoginInput):
        return auth.login(data.username, data.password)

    @app.post("/api/auth/wechat")
    def wechat(data: WechatInput):
        return auth.wechat_login(data.code)

    @app.post("/api/auth/logout")
    def logout(value: str = Depends(token)):
        auth.logout(value)
        return {"ok": True}

    @app.get("/api/me")
    def me(current=Depends(identity)):
        user, ws = current
        enabled = ws.ai_enabled and ws.ai_consent_version == consent_version(settings)
        return {"user": {"id": user.id, "username": user.username, "display_name": user.display_name},
                "workspace": {**workspace_json(ws), "ai_enabled": enabled},
                "ai": {"configured": settings.external_ai_ready, "provider": urlparse(settings.ai_base_url).hostname,
                       "model": settings.ai_model, "consent_version": consent_version(settings), "usage": extraction.usage(ws.id),
                       "disclosure": "仅发送当前原文、参照时间、时区和你明确选择的任务组标题与标签；不发送整个工作空间。外部服务的留存和训练政策须由维护者核实。关闭开关只能阻止后续请求。"},
                "storage": {"bytes": store.usage(ws.id), "limit": settings.max_workspace_bytes}}

    @app.patch("/api/workspace")
    def update_workspace(data: WorkspaceInput, ws=Depends(ws_id)):
        with store.lock(ws), db.session() as session:
            row = session.get(Workspace, ws)
            if data.name is not None:
                if not data.name.strip():
                    raise AppError("INPUT_INVALID", "名称不能为空。", 422)
                row.name = data.name.strip()
            if data.timezone is not None:
                TimeSpec(date=date.today(), timezone=data.timezone)
                row.timezone = data.timezone
            if data.ai_enabled is not None:
                if data.ai_enabled and (not settings.external_ai_ready or data.ai_consent_version != consent_version(settings)):
                    raise AppError("AI_CONSENT_REQUIRED", "请先配置外部 AI，并确认当前数据处理说明。", 409)
                row.ai_enabled = data.ai_enabled
                row.ai_consent_version = consent_version(settings) if data.ai_enabled else None
            return workspace_json(row)

    @app.post("/api/captures", status_code=201)
    def save_capture(data: CaptureInput, ws=Depends(ws_id)):
        return captures.save(ws, data)

    @app.get("/api/captures")
    def list_captures(ws=Depends(ws_id), status: str | None = None, offset: int = Query(0, ge=0),
                      limit: int = Query(50, ge=1, le=100), trash: bool = False):
        return captures.list(ws, status, offset, limit, trash)

    @app.get("/api/captures/{capture_id}")
    def get_capture(capture_id: str, ws=Depends(ws_id)):
        return captures.get(ws, capture_id)

    @app.post("/api/captures/{capture_id}/state")
    def capture_state(capture_id: str, data: CaptureState, ws=Depends(ws_id)):
        return captures.change_state(ws, capture_id, data.action)

    @app.post("/api/captures/{capture_id}/analyze", status_code=202)
    def analyze(capture_id: str, data: AnalysisInput, ws=Depends(ws_id), request_key=Depends(key)):
        if data.target_group_id:
            workspace.get(ws, data.target_group_id)
        return jobs.submit(ws, "analysis", {**data.model_dump(mode="json"), "capture_id": capture_id}, request_key,
                           fingerprint("analyze:" + capture_id, data))

    @app.get("/api/jobs")
    def list_jobs(ws=Depends(ws_id)):
        return jobs.list(ws)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, ws=Depends(ws_id)):
        return jobs.get(ws, job_id)

    @app.get("/api/proposals/{proposal_id}")
    def get_proposal(proposal_id: str, ws=Depends(ws_id)):
        return proposals.get(ws, proposal_id)

    @app.patch("/api/proposals/{proposal_id}")
    def edit_proposal(proposal_id: str, data: ProposalEdit, ws=Depends(ws_id)):
        return proposals.edit(ws, proposal_id, data)

    @app.post("/api/proposals/{proposal_id}/preview")
    def preview_proposal(proposal_id: str, data: ProposalConfirm, ws=Depends(ws_id)):
        return proposals.preview_selection(ws, proposal_id, data)

    @app.post("/api/proposals/{proposal_id}/confirm")
    def confirm_proposal(proposal_id: str, data: ProposalConfirm, ws=Depends(ws_id), request_key=Depends(key)):
        return proposals.confirm(ws, proposal_id, data, request_key, fingerprint("confirm:" + proposal_id, data))

    @app.post("/api/proposals/{proposal_id}/reject")
    def reject_proposal(proposal_id: str, data: ProposalReject, ws=Depends(ws_id)):
        return proposals.reject(ws, proposal_id, data.proposal_revision)

    @app.get("/api/folders")
    def list_folders(ws=Depends(ws_id)):
        return workspace.folders(ws)

    @app.post("/api/folders", status_code=201)
    def create_folder(data: FolderCreate, ws=Depends(ws_id), request_key=Depends(key)):
        return workspace.create_folder(ws, data, request_key, fingerprint("folder:create", data))

    @app.patch("/api/folders/{folder_id}")
    def update_folder(folder_id: str, data: FolderUpdate, ws=Depends(ws_id), request_key=Depends(key)):
        return workspace.update_folder(ws, folder_id, data, request_key, fingerprint("folder:update:" + folder_id, data))

    @app.get("/api/groups")
    def groups(ws=Depends(ws_id), folder_id: str | None = None, status: str | None = None,
               offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), trash: bool = False):
        return workspace.list_groups(ws, folder_id, status, offset, limit, trash)

    @app.get("/api/group-options")
    def group_options(ws=Depends(ws_id), q: str = Query("", max_length=200),
                      offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
        return workspace.group_options(ws, q, offset, limit)

    @app.post("/api/groups", status_code=201)
    def create_group(data: GroupCreate, ws=Depends(ws_id), request_key=Depends(key)):
        return workspace.create(ws, data, request_key, fingerprint("group:create", data))

    @app.get("/api/groups/{group_id}")
    def group(group_id: str, ws=Depends(ws_id)):
        return workspace.get(ws, group_id)

    @app.patch("/api/groups/{group_id}")
    def update_group(group_id: str, data: GroupUpdate, ws=Depends(ws_id), request_key=Depends(key)):
        return workspace.update(ws, group_id, data, request_key, fingerprint("group:update:" + group_id, data))

    @app.post("/api/groups/{group_id}/tasks")
    def create_task(group_id: str, data: TaskCreate, ws=Depends(ws_id), request_key=Depends(key)):
        return workspace.mutate_task(ws, group_id, None, data, request_key, fingerprint("task:create:" + group_id, data))

    @app.post("/api/groups/{group_id}/markdown/preview")
    def preview_markdown(group_id: str, data: MarkdownEdit, ws=Depends(ws_id)):
        return workspace.preview_markdown(ws, group_id, data)

    @app.patch("/api/groups/{group_id}/markdown")
    def save_markdown(group_id: str, data: MarkdownEdit, ws=Depends(ws_id), request_key=Depends(key)):
        return workspace.save_markdown(ws, group_id, data, request_key, fingerprint("markdown:update:" + group_id, data))

    @app.patch("/api/groups/{group_id}/tasks/{task_id}")
    def update_task(group_id: str, task_id: str, data: TaskUpdate, ws=Depends(ws_id), request_key=Depends(key)):
        return workspace.mutate_task(ws, group_id, task_id, data, request_key, fingerprint("task:update:" + group_id + ":" + task_id, data))

    @app.post("/api/groups/{group_id}/trash")
    def trash_group(group_id: str, data: VersionInput, ws=Depends(ws_id), request_key=Depends(key)):
        return workspace.trash_or_restore(ws, group_id, data, request_key, fingerprint("group:trash:" + group_id, data))

    @app.post("/api/groups/{group_id}/restore")
    def restore_group(group_id: str, data: VersionInput, ws=Depends(ws_id), request_key=Depends(key)):
        return workspace.trash_or_restore(ws, group_id, data, request_key, fingerprint("group:restore:" + group_id, data), restore=True)

    @app.get("/api/tasks")
    def tasks(ws=Depends(ws_id), view: str = "all", status: str | None = None, owner: str | None = None,
              tag: str | None = None, group_id: str | None = None, folder_id: str | None = None,
              source_ref: str | None = None, q: str | None = Query(None, max_length=200),
              scheduled_from: date | None = None, scheduled_to: date | None = None,
              deadline_from: date | None = None, deadline_to: date | None = None,
              include_completed: bool = False, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100)):
        filters = dict(view=view, status=status, owner=owner, tag=tag, group_id=group_id, folder_id=folder_id,
                       source_ref=source_ref, q=q, include_completed=include_completed, offset=offset, limit=limit)
        for name, value in (("scheduled_from", scheduled_from), ("scheduled_to", scheduled_to),
                            ("deadline_from", deadline_from), ("deadline_to", deadline_to)):
            filters[name] = str(value) if value else None
        return workspace.tasks(ws, filters)

    @app.get("/api/search")
    def search(q: str = Query(min_length=1, max_length=200), kind: str | None = None, ws=Depends(ws_id),
               offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        return workspace.search(ws, q, kind, offset, limit)

    @app.get("/api/changes")
    def history(group_id: str | None = None, ws=Depends(ws_id)):
        return workspace.history(ws, group_id)

    @app.get("/api/changes/{change_id}")
    def change(change_id: str, ws=Depends(ws_id)):
        return workspace.change_detail(ws, change_id)

    @app.post("/api/changes/{change_id}/undo")
    def undo(change_id: str, ws=Depends(ws_id), request_key=Depends(key)):
        return changes.undo(ws, change_id, request_key, fingerprint("undo:" + change_id))

    @app.get("/api/groups/{group_id}/versions/{content_hash}")
    def preview_version(group_id: str, content_hash: str, ws=Depends(ws_id)):
        return workspace.version_preview(ws, group_id, content_hash)

    @app.post("/api/groups/{group_id}/restore-version")
    def restore_version(group_id: str, data: RestoreInput, ws=Depends(ws_id), request_key=Depends(key)):
        return workspace.restore_version(ws, group_id, data, request_key, fingerprint("version:restore:" + group_id, data))

    @app.post("/api/index/rebuild", status_code=202)
    def rebuild(ws=Depends(ws_id), request_key=Depends(key)):
        return jobs.submit(ws, "reindex", {}, request_key, fingerprint("reindex"))

    web_root = PROJECT_ROOT / "apps" / "web" / "dist"
    if web_root.is_dir():
        app.mount("/", StaticFiles(directory=web_root, html=True), name="web")
    else:
        @app.get("/")
        def index():
            return {"message": "API 已启动。开发网页请运行 npm run dev；构建网页请运行 npm run build。", "docs": "/docs"}
    return app
