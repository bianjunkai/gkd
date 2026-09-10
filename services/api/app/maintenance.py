from collections import Counter
from pathlib import PurePosixPath

from sqlalchemy import delete, select

from .db import AuditEvent, Folder, GroupRecord, TaskIndex
from .domain import new_id
from .errors import AppError
from .storage import checked_relative, parse_document


class MaintenanceService:
    def __init__(self, db, store, changes, captures, workspace):
        self.db, self.store, self.changes = db, store, changes
        self.captures, self.workspace = captures, workspace

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
