import copy
import difflib
import time
from datetime import UTC, datetime
from pathlib import PurePosixPath

from sqlalchemy import select

from .changes import changeset_json, snapshot
from .db import CaptureRecord, ChangeSet, Folder, GroupRecord, TaskIndex
from .domain import Group, Task, TaskStatus, TERMINAL, digest, new_id, now_iso, task_view
from .errors import AppError, conflict, not_found
from .grouping import validate_new_group_title
from .storage import (checked_relative, document_blocks, editable_body, filename, parse_document,
                      render_document, render_task, serialize_block_document)


def folder_state(row):
    return {key: getattr(row, key) for key in ("name", "parent_id", "path", "canonical_path", "revision", "archived")}


def folder_chain(session, workspace_id, folder_id, *, writable=True):
    chain, seen = [], set()
    while folder_id:
        if folder_id in seen:
            raise AppError("STORAGE_CORRUPTED", "目录结构存在循环。", 503)
        seen.add(folder_id)
        row = session.get(Folder, folder_id)
        if not row or row.workspace_id != workspace_id:
            raise not_found()
        if writable and row.archived:
            raise conflict("目标目录已归档，请先恢复目录。")
        chain.append(row)
        folder_id = row.parent_id
    return chain


class WorkspaceService:
    def __init__(self, db, store, changes):
        self.db, self.store, self.changes = db, store, changes

    def _row(self, session, workspace_id, group_id, *, deleted=False):
        self.changes.ensure_ready(session, workspace_id)
        row = session.get(GroupRecord, group_id)
        if not row or row.workspace_id != workspace_id or (row.deleted_at and not deleted):
            raise not_found()
        return row

    def _load(self, workspace_id, row):
        if row.deleted_at:
            text = self.store.read_blob(workspace_id, row.content_hash)
        else:
            try:
                text = self.store.read(workspace_id, "files", row.path)
            except FileNotFoundError as exc:
                raise conflict("文件已被外部移动或删除，请运行索引检查。") from exc
            if digest(text) != row.content_hash:
                raise conflict("检测到外部编辑，请先检查并重建索引，当前操作不会覆盖文件。")
        group, body, _ = parse_document(text)
        if group.id != row.id or group.revision != row.revision:
            raise AppError("STORAGE_CORRUPTED", "文件身份或版本与索引不一致。", 503)
        return group, body, text

    @staticmethod
    def _version(row, data):
        if row.revision != data.expected_revision or row.content_hash != data.expected_hash:
            raise conflict()

    def path_for(self, session, workspace_id, folder_id, suggested, *, current_id=None, auto_suffix=False, reserved=None):
        chain = folder_chain(session, workspace_id, folder_id)
        name = filename(suggested, sanitize=auto_suffix)
        if not name.lower().endswith(".md"):
            name = name[:96]
            while len(name.encode("utf-8")) > 237:
                name = name[:-1]
            name += ".md"
        name = filename(name)
        prefix = chain[0].path + "/" if chain else ""
        base = name[:-3]
        for i in range(1, 10001):
            suffix = ".md" if i == 1 else f" ({i}).md"
            stem = base
            while len(stem + suffix) > 100 or len((stem + suffix).encode("utf-8")) > 240:
                stem = stem[:-1]
            candidate = checked_relative(prefix + stem + suffix)
            other = session.scalar(select(GroupRecord).where(
                GroupRecord.workspace_id == workspace_id,
                GroupRecord.canonical_path == candidate.casefold(),
            ))
            file_exists = self.store.path(workspace_id, "files", candidate).exists()
            folder_exists = session.scalar(select(Folder.id).where(
                Folder.workspace_id == workspace_id, Folder.canonical_path == candidate.casefold()))
            owned = other is not None and other.id == current_id
            if not folder_exists and not (other and not owned) and not (file_exists and not owned) and candidate.casefold() not in (reserved or set()):
                return candidate
            if not auto_suffix:
                raise conflict("目标位置已有同名文件，请选择其他名称。")
        raise conflict("同名文件过多，请选择其他名称。")

    def _result(self, workspace_id, row, *, include_source=True):
        group, body, text = self._load(workspace_id, row)
        tasks = [task_view(task) for task in group.tasks]
        denominator = sum(t.status not in {TaskStatus.CANCELLED, TaskStatus.ARCHIVED} for t in group.tasks)
        result = {**group.model_dump(mode="json"), "path": row.path, "folder_id": row.folder_id,
                  "content_hash": row.content_hash, "deleted_at": row.deleted_at,
                  "tasks": tasks, "progress": {"done": sum(t.status == TaskStatus.DONE for t in group.tasks),
                                                 "total": denominator}}
        if include_source:
            # Old files are projected in memory; opening or reindexing never migrates them.
            editable = render_document(group.model_copy(update={"schema_version": 2}), previous=text) if group.schema_version == 1 else text
            _, projected_body, _ = parse_document(editable)
            blocks = [{"type": "task", "task_id": block.task.id} if block.task else
                      {"type": "markdown", "markdown": block.markdown} for block in document_blocks(projected_body)]
            result.update(body=editable_body(body) if group.schema_version == 1 else body,
                          markdown=text, editable_markdown=editable, blocks=blocks)
        return result

    def get(self, workspace_id, group_id, *, deleted=False):
        with self.store.lock(workspace_id), self.db.session() as session:
            return self._result(workspace_id, self._row(session, workspace_id, group_id, deleted=deleted))

    def list_groups(self, workspace_id, folder_id=None, status=None, offset=0, limit=50, trash=False):
        with self.store.lock(workspace_id), self.db.session() as session:
            self.changes.ensure_ready(session, workspace_id)
            query = select(GroupRecord).where(GroupRecord.workspace_id == workspace_id)
            query = query.where(GroupRecord.deleted_at.is_not(None) if trash else GroupRecord.deleted_at.is_(None))
            if folder_id:
                if folder_id == "root":
                    query = query.where(GroupRecord.folder_id.is_(None))
                else:
                    folder_chain(session, workspace_id, folder_id, writable=False)
                    query = query.where(GroupRecord.folder_id == folder_id)
            if status:
                query = query.where(GroupRecord.status == status)
            elif not trash:
                query = query.where(GroupRecord.status != "archived")
            rows = list(session.scalars(query.order_by(GroupRecord.updated_at.desc(), GroupRecord.id)))
            if not trash and status != "archived":
                rows = [r for r in rows if not any(f.archived for f in folder_chain(session, workspace_id, r.folder_id, writable=False))]
            return {"items": [self._result(workspace_id, row, include_source=False) for row in rows[offset:offset + limit]],
                    "total": len(rows), "offset": offset, "limit": limit}

    def create(self, workspace_id, data, key, request_hash):
        with self.store.lock(workspace_id):
            with self.db.session() as session:
                repeated = self.changes.existing(session, workspace_id, key, request_hash)
                if repeated:
                    return repeated
                self.changes.ensure_ready(session, workspace_id)
                validate_new_group_title(data.title, data.tasks, data.file_name)
                path = self.path_for(session, workspace_id, data.folder_id, data.file_name or data.title, auto_suffix=data.file_name is None)
                tags = Task.model_validate({"title": "tags", "tags": data.tags}).tags
                group = Group(title=data.title.strip(), tags=tags,
                              tasks=[Task(**t.model_dump()) for t in data.tasks])
                op = self.changes.operation(workspace_id, None, group, path=path, folder_id=data.folder_id, body=data.body or None)
            return self.changes.execute(workspace_id, key, request_hash, "group_created", "创建：" + group.title, [op])

    def group_options(self, workspace_id, query="", offset=0, limit=20):
        """Page through writable destinations without loading Markdown or task bodies."""
        query = query.strip().casefold()
        with self.store.lock(workspace_id), self.db.session() as session:
            self.changes.ensure_ready(session, workspace_id)
            rows = session.scalars(select(GroupRecord).where(
                GroupRecord.workspace_id == workspace_id, GroupRecord.deleted_at.is_(None),
                GroupRecord.status != "archived").order_by(GroupRecord.updated_at.desc(), GroupRecord.id))
            items = []
            for row in rows:
                if query and query not in (row.title + "\n" + row.path).casefold():
                    continue
                if any(f.archived for f in folder_chain(session, workspace_id, row.folder_id, writable=False)):
                    continue
                items.append({"id": row.id, "title": row.title, "path": row.path, "folder_id": row.folder_id})
            return {"items": items[offset:offset + limit], "total": len(items), "offset": offset, "limit": limit}

    def update(self, workspace_id, group_id, data, key, request_hash):
        with self.store.lock(workspace_id):
            with self.db.session() as session:
                repeated = self.changes.existing(session, workspace_id, key, request_hash)
                if repeated:
                    return repeated
                row = self._row(session, workspace_id, group_id)
                self._version(row, data)
                group, _, original = self._load(workspace_id, row)
                patch = data.model_dump(exclude_unset=True, exclude={"expected_revision", "expected_hash", "folder_id", "file_name", "body"})
                for field in ("title", "tags", "status"):
                    if field in patch and patch[field] is None:
                        raise AppError("INPUT_INVALID", "标题、状态和标签不能设为 null。", 422)
                if "tags" in patch:
                    patch["tags"] = Task.model_validate({"title": "tags", "tags": patch["tags"]}).tags
                group = Group.model_validate({**group.model_dump(), **patch, "revision": group.revision + 1, "updated_at": now_iso()})
                folder_id = data.folder_id if "folder_id" in data.model_fields_set else row.folder_id
                path = self.path_for(session, workspace_id, folder_id,
                                     data.file_name or PurePosixPath(row.path).name, current_id=row.id)
                op = self.changes.operation(workspace_id, row, group, path=path, folder_id=folder_id,
                                            previous=original, body=data.body)
            return self.changes.execute(workspace_id, key, request_hash, "group_updated", "编辑：" + group.title, [op])

    def mutate_task(self, workspace_id, group_id, task_id, data, key, request_hash):
        with self.store.lock(workspace_id):
            with self.db.session() as session:
                repeated = self.changes.existing(session, workspace_id, key, request_hash)
                if repeated:
                    return repeated
                row = self._row(session, workspace_id, group_id)
                self._version(row, data)
                folder_chain(session, workspace_id, row.folder_id)
                group, _, original = self._load(workspace_id, row)
                if group.status == "archived":
                    raise conflict("任务组已归档，请先恢复。")
                if task_id is None:
                    task = Task(**data.task.model_dump())
                    group.tasks.append(task)
                else:
                    current = next((t for t in group.tasks if t.id == task_id), None)
                    if current is None:
                        raise not_found()
                    patch = data.patch.model_dump(exclude_unset=True)
                    for field in ("title", "description", "status", "priority", "tags"):
                        if field in patch and patch[field] is None:
                            raise AppError("INPUT_INVALID", "必需字段不能设为 null。", 422)
                    if patch.get("status") == TaskStatus.ARCHIVED and current.status != TaskStatus.ARCHIVED:
                        patch["archived_from_status"] = current.status
                    if current.status == TaskStatus.ARCHIVED and patch.get("status") and patch["status"] != TaskStatus.ARCHIVED:
                        patch["archived_from_status"] = None
                    if patch.get("status") == TaskStatus.DONE and current.status != TaskStatus.DONE:
                        patch["completed_at"] = now_iso()
                    task = Task.model_validate({**current.model_dump(), **patch, "updated_at": now_iso()})
                    group.tasks = [task if t.id == task_id else t for t in group.tasks]
                if task.status not in TERMINAL and group.status == "done":
                    group.status = "active"
                group.schema_version = 2
                group.revision += 1
                group.updated_at = now_iso()
                Group.model_validate(group.model_dump())
                op = self.changes.operation(workspace_id, row, group, path=row.path, folder_id=row.folder_id, previous=original)
            return self.changes.execute(workspace_id, key, request_hash,
                                        "task_completed" if task.status == "done" else "task_updated",
                                        ("完成：" if task.status == "done" else "保存任务：") + task.title, [op])

    def _prepare_markdown(self, session, workspace_id, row, data):
        self._version(row, data)
        folder_chain(session, workspace_id, row.folder_id)
        original, _, before = self._load(workspace_id, row)
        if original.status == "archived":
            raise conflict("任务组已归档，请先恢复再编辑 Markdown。")
        proposed, body, _ = parse_document(data.markdown, draft=True)
        if proposed.schema_version != 2:
            raise AppError("SCHEMA_INVALID", "源码编辑使用格式 2，请重新打开编辑器获取兼容转换后的内容。", 422)
        if proposed.id != original.id:
            raise AppError("IDENTITY_CONFLICT", "不能修改任务组的稳定 ID；移动文件请使用项目设置。", 422)
        timestamp = now_iso()
        existing = {t.id: t for t in original.tasks}
        readonly = {"created_at", "updated_at", "completed_at", "archived_from_status", "source_refs"}
        updated_tasks, changed_ids = [], []
        for draft in proposed.tasks:
            old = existing.get(draft.id)
            occupied = session.get(TaskIndex, draft.id)
            if occupied and (occupied.group_id != row.id or occupied.workspace_id != workspace_id):
                raise AppError("IDENTITY_CONFLICT", "任务 ID 已被其他文件占用。复制任务时请移除新块的 id，再预览。", 409)
            values = draft.model_dump(exclude=readonly)
            values.update(created_at=old.created_at if old else timestamp,
                          updated_at=timestamp, source_refs=old.source_refs if old else [])
            if old:
                values["completed_at"] = old.completed_at
                values["archived_from_status"] = old.archived_from_status
            if draft.status == TaskStatus.ARCHIVED and old and old.status != TaskStatus.ARCHIVED:
                values["archived_from_status"] = old.status
            elif draft.status != TaskStatus.ARCHIVED:
                values["archived_from_status"] = None
            if draft.status == TaskStatus.DONE and (not old or old.status != TaskStatus.DONE):
                values["completed_at"] = timestamp
            task = Task.model_validate(values)
            if old and task.model_dump(exclude={"updated_at"}) == old.model_dump(exclude={"updated_at"}):
                task.updated_at = old.updated_at
            elif old:
                changed_ids.append(task.id)
            updated_tasks.append(task)
        proposed = Group.model_validate({**proposed.model_dump(), "tasks": updated_tasks,
            "created_at": original.created_at, "updated_at": timestamp, "revision": original.revision + 1})
        tasks = iter(updated_tasks)
        # Task blocks are normalized, while every ordinary content segment stays in place.
        normalized_body = "".join(render_task(next(tasks)) if block.task else block.markdown
                                  for block in document_blocks(body, draft=True))
        normalized = serialize_block_document(proposed, normalized_body)
        ids = {t.id for t in updated_tasks}
        summary = {"added": len(ids - existing.keys()), "updated": len(changed_ids), "removed": len(existing.keys() - ids)}
        return proposed, before, normalized, summary

    def preview_markdown(self, workspace_id, group_id, data):
        with self.store.lock(workspace_id), self.db.session() as session:
            row = self._row(session, workspace_id, group_id)
            group, before, normalized, summary = self._prepare_markdown(session, workspace_id, row, data)
            return {"markdown": normalized, "summary": summary, "tasks": [task_view(t) for t in group.tasks],
                    "expected_revision": row.revision, "expected_hash": row.content_hash,
                    "diff": "".join(difflib.unified_diff(before.splitlines(True), normalized.splitlines(True),
                                                        fromfile="保存前", tofile="保存后"))}

    def save_markdown(self, workspace_id, group_id, data, key, request_hash):
        with self.store.lock(workspace_id):
            with self.db.session() as session:
                repeated = self.changes.existing(session, workspace_id, key, request_hash)
                if repeated:
                    return repeated
                row = self._row(session, workspace_id, group_id)
                group, _, normalized, _ = self._prepare_markdown(session, workspace_id, row, data)
                op = self.changes.operation(workspace_id, row, group, path=row.path,
                                            folder_id=row.folder_id, previous=normalized)
            return self.changes.execute(workspace_id, key, request_hash, "markdown_updated",
                                        "编辑 Markdown：" + group.title, [op])

    def trash_or_restore(self, workspace_id, group_id, data, key, request_hash, *, restore=False):
        with self.store.lock(workspace_id):
            with self.db.session() as session:
                repeated = self.changes.existing(session, workspace_id, key, request_hash)
                if repeated:
                    return repeated
                row = self._row(session, workspace_id, group_id, deleted=True)
                self._version(row, data)
                if bool(row.deleted_at) != restore:
                    raise conflict("该记录的回收站状态已经变化。")
                group, _, original = self._load(workspace_id, row)
                path = self.path_for(session, workspace_id, row.folder_id, PurePosixPath(row.path).name, current_id=row.id) if restore else row.path
                group.revision += 1
                group.updated_at = now_iso()
                op = self.changes.operation(workspace_id, row, group, path=path, folder_id=row.folder_id,
                                            previous=original, deleted=not restore)
            return self.changes.execute(workspace_id, key, request_hash, "group_restored" if restore else "group_trashed",
                                        ("恢复：" if restore else "移入回收站：") + group.title, [op])

    def folders(self, workspace_id):
        with self.store.lock(workspace_id), self.db.session() as session:
            self.changes.ensure_ready(session, workspace_id)
            return [{"id": row.id, **folder_state(row),
                     "effective_archived": any(f.archived for f in folder_chain(session, workspace_id, row.id, writable=False))}
                    for row in session.scalars(select(Folder).where(Folder.workspace_id == workspace_id).order_by(Folder.path))]

    def create_folder(self, workspace_id, data, key, request_hash):
        with self.store.lock(workspace_id):
            with self.db.session() as session:
                repeated = self.changes.existing(session, workspace_id, key, request_hash)
                if repeated:
                    return repeated
                self.changes.ensure_ready(session, workspace_id)
                parents = folder_chain(session, workspace_id, data.parent_id)
                name = filename(data.name)
                path = checked_relative((parents[0].path + "/" if parents else "") + name)
                if session.scalar(select(Folder.id).where(Folder.workspace_id == workspace_id, Folder.canonical_path == path.casefold())):
                    raise conflict("同一位置已有这个文件夹。")
                after = {"name": name, "parent_id": data.parent_id, "path": path,
                         "canonical_path": path.casefold(), "revision": 1, "archived": False}
                effect = {"id": new_id("folder"), "before": None, "after": after}
            return self.changes.execute(workspace_id, key, request_hash, "folder_created", "新建文件夹：" + name, [], {"folders": [effect]})

    def update_folder(self, workspace_id, folder_id, data, key, request_hash):
        with self.store.lock(workspace_id):
            with self.db.session() as session:
                repeated = self.changes.existing(session, workspace_id, key, request_hash)
                if repeated:
                    return repeated
                self.changes.ensure_ready(session, workspace_id)
                chain = folder_chain(session, workspace_id, folder_id, writable=False)
                root = chain[0]
                if root.revision != data.expected_revision:
                    raise conflict()
                parent_id = data.parent_id if "parent_id" in data.model_fields_set else root.parent_id
                parents = folder_chain(session, workspace_id, parent_id)
                if any(p.id == root.id for p in parents):
                    raise AppError("FOLDER_CYCLE", "不能把文件夹移入自身或其子目录。", 422)
                name = filename(data.name or root.name)
                new_prefix = checked_relative((parents[0].path + "/" if parents else "") + name)
                rows = list(session.scalars(select(Folder).where(Folder.workspace_id == workspace_id)))
                moved = [f for f in rows if f.id == root.id or f.path.startswith(root.path + "/")]
                target_paths = {f.id: new_prefix + f.path[len(root.path):] for f in moved}
                occupied = {f.canonical_path for f in rows if f.id not in target_paths}
                effects = []
                for row in moved:
                    checked_relative(target_paths[row.id])
                    if target_paths[row.id].casefold() in occupied:
                        raise conflict("目标目录已有同名文件夹。")
                    after = {**folder_state(row), "path": target_paths[row.id],
                             "canonical_path": target_paths[row.id].casefold(), "revision": row.revision + 1}
                    if row.id == root.id:
                        after.update(name=name, parent_id=parent_id)
                        if data.archived is not None:
                            after["archived"] = data.archived
                    effects.append({"id": row.id, "before": folder_state(row), "after": after})
                operations = []
                if new_prefix != root.path:
                    records = session.scalars(select(GroupRecord).where(GroupRecord.workspace_id == workspace_id,
                                               GroupRecord.folder_id.in_(target_paths), GroupRecord.deleted_at.is_(None)))
                    for record in records:
                        group, _, text = self._load(workspace_id, record)
                        group.revision += 1
                        group.updated_at = now_iso()
                        path = checked_relative(target_paths[record.folder_id] + "/" + PurePosixPath(record.path).name)
                        operations.append(self.changes.operation(workspace_id, record, group, path=path,
                                                                 folder_id=record.folder_id, previous=text))
            return self.changes.execute(workspace_id, key, request_hash, "folder_updated", "更新文件夹：" + name, operations, {"folders": effects})

    def tasks(self, workspace_id, filters):
        with self.store.lock(workspace_id), self.db.session() as session:
            self.changes.ensure_ready(session, workspace_id)
            records = {r.id: r for r in session.scalars(select(GroupRecord).where(
                GroupRecord.workspace_id == workspace_id, GroupRecord.deleted_at.is_(None), GroupRecord.status != "archived"))}
            available = {gid: r for gid, r in records.items()
                         if not any(f.archived for f in folder_chain(session, workspace_id, r.folder_id, writable=False))}
            items = []
            for row in session.scalars(select(TaskIndex).where(TaskIndex.workspace_id == workspace_id)):
                group = available.get(row.group_id)
                if not group:
                    continue
                task = Task.model_validate(row.data)
                view = task_view(task)
                selected = filters.get("view", "all")
                if selected == "today" and not view["is_today"]:
                    continue
                if selected == "overdue" and not view["is_overdue"]:
                    continue
                if selected not in {"all", "today", "overdue"} and task.status != selected:
                    continue
                if selected == "all" and task.status in TERMINAL and not filters.get("include_completed"):
                    continue
                if filters.get("status") and task.status not in filters["status"].split(","):
                    continue
                if filters.get("owner") and task.owner != filters["owner"]:
                    continue
                if filters.get("tag") and not set(filters["tag"].split(",")).intersection(task.tags):
                    continue
                if filters.get("group_id") and group.id != filters["group_id"]:
                    continue
                if filters.get("folder_id") and group.folder_id != filters["folder_id"]:
                    continue
                if filters.get("source_ref") and filters["source_ref"] not in task.source_refs:
                    continue
                if filters.get("q") and filters["q"].casefold() not in (task.title + " " + task.description + " " + " ".join(task.tags)).casefold():
                    continue
                date_mismatch = False
                for field in ("scheduled", "deadline"):
                    date_value = getattr(task, field)
                    for op in ("from", "to"):
                        bound = filters.get(field + "_" + op)
                        if bound and (not date_value or (str(date_value.date) < bound if op == "from" else str(date_value.date) > bound)):
                            date_mismatch = True
                if date_mismatch:
                    continue
                items.append({**view, "group_id": group.id, "group_title": group.title, "folder_id": group.folder_id,
                              "group_revision": group.revision, "group_hash": group.content_hash, "path": group.path})
            priority = {"high": 0, "medium": 1, "low": 2}
            items.sort(key=lambda t: (Task.model_validate({k: v for k, v in t.items() if k in Task.model_fields}).deadline.boundary().timestamp()
                                     if t["deadline"] else float("inf"), priority[t["priority"]],
                                     -datetime.fromisoformat(t["updated_at"].replace("Z", "+00:00")).timestamp(), t["id"]))
            offset, limit = filters.get("offset", 0), filters.get("limit", 100)
            return {"items": items[offset:offset + limit], "total": len(items), "offset": offset, "limit": limit}

    def search(self, workspace_id, query, kind=None, offset=0, limit=50):
        query = query.strip().casefold()
        if not query:
            return {"items": [], "total": 0, "offset": offset, "limit": limit}
        with self.store.lock(workspace_id), self.db.session() as session:
            self.changes.ensure_ready(session, workspace_id)
            hits = []
            sources = [("group", GroupRecord), ("task", TaskIndex), ("capture", CaptureRecord)]
            for object_type, model in sources:
                if kind and kind != object_type:
                    continue
                for row in session.scalars(select(model).where(model.workspace_id == workspace_id)):
                    if getattr(row, "deleted_at", None):
                        continue
                    if object_type == "task":
                        content = row.title + " " + row.data.get("description", "") + " " + " ".join(row.data.get("tags", []))
                    else:
                        content = row.search_text
                    position = content.casefold().find(query)
                    if position < 0:
                        continue
                    hits.append({"type": object_type, "id": row.id, "title": getattr(row, "title", content[:80]),
                                 "snippet": content[max(0, position - 30):position + len(query) + 100],
                                 "group_id": row.group_id if object_type == "task" else None})
            hits.sort(key=lambda r: (r["type"], r["id"]))
            return {"items": hits[offset:offset + limit], "total": len(hits), "offset": offset, "limit": limit}

    def history(self, workspace_id, group_id=None):
        with self.store.lock(workspace_id), self.db.session() as session:
            self.changes.ensure_ready(session, workspace_id)
            if group_id:
                self._row(session, workspace_id, group_id, deleted=True)
            rows = session.scalars(select(ChangeSet).where(ChangeSet.workspace_id == workspace_id,
                                   ChangeSet.status == "committed").order_by(ChangeSet.created_at.desc()))
            result = []
            for row in rows:
                if group_id and not any(op["group_id"] == group_id for op in row.operations):
                    continue
                result.append({**changeset_json(row), "versions": [{"group_id": op["group_id"],
                    "before_hash": op["before"]["content_hash"] if op["before"] else None,
                    "after_hash": op["after"]["content_hash"] if op["after"] else None} for op in row.operations]})
                if len(result) >= 100:
                    break
            return result

    def change_detail(self, workspace_id, change_id):
        with self.store.lock(workspace_id), self.db.session() as session:
            self.changes.ensure_ready(session, workspace_id)
            row = session.get(ChangeSet, change_id)
            if not row or row.workspace_id != workspace_id:
                raise not_found()
            differences = []
            for op in row.operations:
                before = self.store.read_blob(workspace_id, op["before"]["content_hash"]) if op["before"] else ""
                after = self.store.read_blob(workspace_id, op["after"]["content_hash"]) if op["after"] else ""
                differences.append({"group_id": op["group_id"], "before": op["before"], "after": op["after"],
                                    "diff": "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile="变更前", tofile="变更后"))})
            return {**changeset_json(row), "differences": differences}

    def version_preview(self, workspace_id, group_id, content_hash):
        with self.store.lock(workspace_id), self.db.session() as session:
            row = self._row(session, workspace_id, group_id, deleted=True)
            authorized = content_hash == row.content_hash
            for change in session.scalars(select(ChangeSet).where(ChangeSet.workspace_id == workspace_id, ChangeSet.status == "committed")):
                authorized |= any(op["group_id"] == group_id and any(s and s["content_hash"] == content_hash
                                  for s in (op["before"], op["after"])) for op in change.operations)
            if not authorized:
                raise not_found()
            text = self.store.read_blob(workspace_id, content_hash)
            group, body, _ = parse_document(text)
            if group.id != group_id:
                raise not_found()
            current = self.store.read_blob(workspace_id, row.content_hash)
            return {"group": group.model_dump(mode="json"), "markdown": text, "content_hash": content_hash,
                    "expected_revision": row.revision, "expected_hash": row.content_hash,
                    "diff": "".join(difflib.unified_diff(current.splitlines(True), text.splitlines(True), fromfile="当前版本", tofile="恢复内容"))}

    def restore_version(self, workspace_id, group_id, data, key, request_hash):
        with self.store.lock(workspace_id):
            with self.db.session() as session:
                repeated = self.changes.existing(session, workspace_id, key, request_hash)
                if repeated:
                    return repeated
                row = self._row(session, workspace_id, group_id)
                self._version(row, data)
                folder_chain(session, workspace_id, row.folder_id)
                preview = self.version_preview(workspace_id, group_id, data.content_hash)
                group, _, _ = parse_document(preview["markdown"])
                group.revision = row.revision + 1
                group.updated_at = now_iso()
                op = self.changes.operation(workspace_id, row, group, path=row.path, folder_id=row.folder_id, previous=preview["markdown"])
            return self.changes.execute(workspace_id, key, request_hash, "version_restored", "恢复历史内容：" + group.title, [op])
