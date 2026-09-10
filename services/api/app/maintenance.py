import hashlib
import json
import os
import time
import uuid
import zipfile
from collections import Counter
from pathlib import PurePosixPath

from sqlalchemy import delete, select

from .db import AuditEvent, CaptureRecord, ChangeSet, Folder, GroupRecord, Job, TaskIndex
from .domain import digest, new_id
from .errors import AppError, not_found
from .storage import checked_relative, parse_document


def file_hash(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


class MaintenanceService:
    def __init__(self, db, store, changes, captures, workspace):
        self.db, self.store, self.changes = db, store, changes
        self.captures, self.workspace = captures, workspace

    def export_job(self, job):
        ws = job.workspace_id
        with self.store.lock(ws), self.db.session() as session:
            self.changes.ensure_ready(session, ws)
            output = self.store.path(ws, "exports", job.id + ".zip")
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_name(".tmp-" + str(uuid.uuid4()))
            entries = []
            try:
                with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                    def add(path, content, object_id=None, schema_version=1):
                        content = content.encode("utf-8") if isinstance(content, str) else content
                        archive.writestr(path, content)
                        entries.append({"path": path, "id": object_id, "schema_version": schema_version,
                                        "sha256": digest(content), "bytes": len(content)})
                    for row in session.scalars(select(GroupRecord).where(GroupRecord.workspace_id == ws, GroupRecord.deleted_at.is_(None))):
                        group, _, text = self.workspace._load(ws, row)
                        add("markdown/" + row.path, text, row.id, group.schema_version)
                    for row in session.scalars(select(CaptureRecord).where(CaptureRecord.workspace_id == ws, CaptureRecord.deleted_at.is_(None))):
                        raw = self.captures._raw(ws, row)
                        add("captures/" + row.id + ".json", json.dumps({**raw, "status": row.status}, ensure_ascii=False, indent=2), row.id)
                    folders = [{"id": f.id, "name": f.name, "path": f.path, "parent_id": f.parent_id,
                                "archived": f.archived} for f in session.scalars(select(Folder).where(Folder.workspace_id == ws))]
                    add("folders.json", json.dumps(folders, ensure_ascii=False, indent=2))
                    if job.payload.get("include_history"):
                        histories, hashes = [], set()
                        for change in session.scalars(select(ChangeSet).where(ChangeSet.workspace_id == ws, ChangeSet.status == "committed")):
                            histories.append({"id": change.id, "kind": change.kind, "summary": change.summary,
                                              "created_at": change.created_at, "operations": change.operations, "undone_by": change.undone_by})
                            for op in change.operations:
                                hashes.update(s["content_hash"] for s in (op["before"], op["after"]) if s)
                        for value in sorted(hashes):
                            version_text = self.store.read_blob(ws, value)
                            version_group, _, _ = parse_document(version_text)
                            add("versions/" + value + ".md", version_text, version_group.id, version_group.schema_version)
                        add("history.json", json.dumps(histories, ensure_ascii=False, indent=2))
                    manifest = {"schema_version": 1, "workspace_id": ws, "export_id": job.id,
                                "created_at": time.time(), "includes_history": bool(job.payload.get("include_history")), "entries": entries}
                    archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                with temporary.open("rb+") as handle:
                    os.fsync(handle.fileno())
                os.replace(temporary, output)
            finally:
                temporary.unlink(missing_ok=True)
            session.add(AuditEvent(id=new_id("audit"), workspace_id=ws, action="export_finished", resource_id=job.id,
                                   details={"files": len(entries)}))
            return {"file_name": "guike-export.zip", "sha256": file_hash(output), "bytes": output.stat().st_size,
                    "expires_at": time.time() + 86400, "download_url": f"/api/exports/{job.id}/download"}

    def export_path(self, ws, job_id):
        with self.store.lock(ws), self.db.session() as session:
            job = session.get(Job, job_id)
            if not job or job.workspace_id != ws or job.kind != "export" or job.status != "succeeded":
                raise not_found()
            path = self.store.path(ws, "exports", job.id + ".zip")
            if not job.result or job.result["expires_at"] < time.time() or not path.exists():
                raise AppError("EXPORT_EXPIRED", "导出包已失效，请重新生成。", 410)
            if file_hash(path) != job.result["sha256"]:
                raise AppError("EXPORT_CORRUPTED", "导出包校验失败，请重新生成。", 503)
            return path

    def reindex_job(self, job):
        ws = job.workspace_id
        with self.store.lock(ws), self.db.session() as session:
            self.changes.ensure_ready(session, ws)
            root = self.store.workspace_root(ws) / "files"
            parsed, errors = [], []
            folders = {f.path.casefold(): f.id for f in session.scalars(select(Folder).where(Folder.workspace_id == ws))}
            for path in sorted(root.rglob("*.md")):
                relative = path.relative_to(root).as_posix()
                try:
                    checked_relative(relative)
                    self.store._within(path, self.store.workspace_root(ws))
                    text = path.read_text(encoding="utf-8")
                    group, _, _ = parse_document(text)
                    parent = str(PurePosixPath(relative).parent).casefold()
                    if parent != "." and parent not in folders:
                        raise AppError("UNKNOWN_FOLDER", "目录尚未注册；请先在应用内创建同路径目录。", 409)
                    record = session.get(GroupRecord, group.id)
                    if record and (record.workspace_id != ws or record.deleted_at):
                        raise AppError("IDENTITY_CONFLICT", "文件 ID 已被其他工作空间或回收站占用。", 409)
                    for task in group.tasks:
                        other = session.get(TaskIndex, task.id)
                        if other and other.workspace_id != ws:
                            raise AppError("IDENTITY_CONFLICT", "任务 ID 已被其他工作空间占用。", 409)
                    parsed.append((relative, group, text, folders.get(parent)))
                except (AppError, OSError, ValueError) as exc:
                    errors.append({"path": relative, "code": getattr(exc, "code", "FILE_UNREADABLE"),
                                   "message": getattr(exc, "message", "文件无法安全读取。")})
            group_counts = Counter(group.id for _, group, _, _ in parsed)
            task_counts = Counter(task.id for _, group, _, _ in parsed for task in group.tasks)
            path_counts = Counter(path.casefold() for path, _, _, _ in parsed)
            session.execute(delete(TaskIndex).where(TaskIndex.workspace_id == ws))
            # The derived active group index can be rebuilt; tombstones remain authoritative recovery metadata.
            session.execute(delete(GroupRecord).where(GroupRecord.workspace_id == ws, GroupRecord.deleted_at.is_(None)))
            session.flush()
            count, tasks = 0, 0
            for path, group, text, folder_id in parsed:
                if group_counts[group.id] > 1 or path_counts[path.casefold()] > 1 or any(task_counts[t.id] > 1 for t in group.tasks):
                    errors.append({"path": path, "code": "DUPLICATE_ID", "message": "文件、任务 ID 或路径重复，未建立索引。"})
                    continue
                content_hash = self.store.blob(ws, text)
                self.changes._index(session, ws, group.id, {"path": path, "folder_id": folder_id,
                    "content_hash": content_hash, "revision": group.revision, "deleted_at": None})
                session.flush()
                count += 1
                tasks += len(group.tasks)
            session.add(AuditEvent(id=new_id("audit"), workspace_id=ws, action="index_rebuilt", resource_id=job.id,
                                   details={"groups": count, "tasks": tasks, "error_count": len(errors)}))
            return {"groups": count, "tasks": tasks, "errors": errors,
                    "message": "索引已重建；内容文件未修改。"}

    def clean_expired_exports(self):
        with self.db.session() as session:
            expired = [(j.workspace_id, j.id) for j in session.scalars(select(Job).where(
                Job.kind == "export", Job.status == "succeeded")) if j.result and j.result["expires_at"] < time.time()]
        for ws, job_id in expired:
            with self.store.lock(ws):
                self.store.remove(ws, "exports", job_id + ".zip")
