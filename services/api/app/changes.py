import copy
import json
import time
from collections.abc import Callable
from pathlib import PurePosixPath

from sqlalchemy import delete, select

from .captures import refresh_capture_status
from .db import AuditEvent, ChangeSet, Database, Folder, GroupRecord, Proposal, TaskIndex, Workspace
from .domain import Group, digest, new_id, now_iso
from .errors import AppError, conflict, not_found
from .storage import ContentStore, parse_document, render_document

SETTLED = {"committed", "rolled_back"}


def snapshot(record: GroupRecord | None) -> dict | None:
    if record is None:
        return None
    return {
        "id": record.id,
        "folder_id": record.folder_id,
        "path": record.path,
        "revision": record.revision,
        "content_hash": record.content_hash,
        "deleted_at": record.deleted_at,
    }


def changeset_json(record: ChangeSet) -> dict:
    return {
        "id": record.id,
        "kind": record.kind,
        "summary": record.summary,
        "status": record.status,
        "created_at": record.created_at,
        "group_ids": [op["group_id"] for op in record.operations],
        "undone_by": record.undone_by,
        "folder_ids": [item["id"] for item in record.effects.get("folders", [])],
    }


class ChangeEngine:
    def __init__(self, db: Database, store: ContentStore):
        self.db, self.store = db, store
        # Fault injection is only assigned by tests; it is never configurable over HTTP.
        self.fault_hook: Callable[[str, str], None] | None = None

    def fault(self, phase: str, changeset_id: str):
        if self.fault_hook:
            self.fault_hook(phase, changeset_id)

    def existing(self, session, workspace_id: str, key: str, request_hash: str):
        row = session.scalar(select(ChangeSet).where(
            ChangeSet.workspace_id == workspace_id, ChangeSet.request_key == key
        ))
        if row is None:
            return None
        if row.request_hash != request_hash:
            raise AppError("IDEMPOTENCY_MISMATCH", "同一请求标识不能用于不同内容。", 409)
        if row.status == "committed":
            return changeset_json(row)
        if row.status == "rolled_back":
            raise AppError("CHANGE_ROLLED_BACK", "上次变更已恢复，请刷新后重新提交。", 409)
        raise AppError("WORKSPACE_RECOVERING", "该次变更尚在恢复。", 503, retryable=True)

    def ensure_ready(self, session, workspace_id: str):
        workspace = session.get(Workspace, workspace_id)
        if not workspace or workspace.deleted_at:
            raise not_found()
        pending = session.scalar(
            select(ChangeSet.id).where(
                ChangeSet.workspace_id == workspace_id, ChangeSet.status.not_in(SETTLED)
            ).limit(1)
        )
        if pending:
            raise AppError(
                "WORKSPACE_RECOVERING", "工作空间正在恢复，请稍后再试。", 503, retryable=True
            )

    def operation(
        self,
        workspace_id: str,
        before: GroupRecord | None,
        after_group: Group,
        *,
        path: str,
        folder_id: str | None,
        previous: str | None = None,
        body: str | None = None,
        deleted: bool = False,
    ) -> dict:
        if before:
            original = self.store.read_blob(workspace_id, before.content_hash)
            if digest(original) != before.content_hash:
                raise conflict()
        text = render_document(after_group, previous=previous, body=body)
        after_hash = digest(text)
        return {
            # Transient content is published only after the whole batch passes preflight.
            # It is never included in the persisted Change Set or journal.
            "_content": text,
            "group_id": after_group.id,
            "before": snapshot(before),
            "after": {
                "id": after_group.id,
                "folder_id": folder_id,
                "path": path,
                "revision": after_group.revision,
                "content_hash": after_hash,
                "deleted_at": time.time() if deleted else None,
            },
        }

    def _verify(self, session, workspace_id: str, operations: list[dict], effects: dict):
        ids = [op["group_id"] for op in operations]
        paths = [op["after"]["path"].casefold() for op in operations
                 if op["after"] and op["after"]["deleted_at"] is None]
        if len(ids) != len(set(ids)) or len(paths) != len(set(paths)):
            raise conflict("同一批次不能重复写入同一个文件或目标位置。")
        for operation in operations:
            before, after = operation["before"], operation["after"]
            current = session.get(GroupRecord, operation["group_id"])
            if current and current.workspace_id != workspace_id:
                raise not_found()
            if snapshot(current) != before:
                raise conflict()
            if after and (after["id"] != operation["group_id"] or
                          after["revision"] != (before["revision"] + 1 if before else 1)):
                raise conflict("文件 ID 或版本序列不一致。")
            if before and before["deleted_at"] is None:
                try:
                    actual = self.store.read(workspace_id, "files", before["path"])
                except FileNotFoundError as exc:
                    raise conflict("文件已被移动或删除，请先检查工作空间。") from exc
                if digest(actual) != before["content_hash"]:
                    raise conflict("检测到文件被外部修改，已停止覆盖。")
            if after and after["deleted_at"] is None:
                occupied = session.scalar(select(GroupRecord).where(
                    GroupRecord.workspace_id == workspace_id,
                    GroupRecord.canonical_path == after["path"].casefold(),
                    GroupRecord.id != operation["group_id"],
                ))
                if occupied:
                    raise conflict("目标位置已有同名文件，请选择其他名称。")
                if after["folder_id"]:
                    folder = session.get(Folder, after["folder_id"])
                    if not folder or folder.workspace_id != workspace_id:
                        raise not_found()
                target = self.store.path(workspace_id, "files", after["path"])
                if target.exists() and not (before and before["deleted_at"] is None and
                                            self._same_location(workspace_id, before["path"], after["path"])):
                    raise conflict("目标位置已有同名文件，请选择其他名称。")
        for update in effects.get("folders", []):
            row = session.get(Folder, update["id"])
            before = update["before"]
            if before is None:
                if row:
                    raise conflict()
                continue
            if not row or row.workspace_id != workspace_id:
                raise not_found()
            if any(getattr(row, key) != value for key, value in before.items()):
                raise conflict("文件夹已发生变化，请重新操作。")
        self._verify_namespace(session, workspace_id, operations, effects)
        for update in effects.get("proposal_states", []):
            proposal = session.get(Proposal, update["id"])
            if not proposal or proposal.workspace_id != workspace_id:
                raise not_found()
            if proposal.revision != update["revision"] or proposal.status != update["before"]:
                raise conflict("关联提案已有后续变化，请重新核对。")
        proposal_id = effects.get("proposal_id")
        if proposal_id:
            proposal = session.get(Proposal, proposal_id)
            if not proposal or proposal.workspace_id != workspace_id:
                raise not_found()
            if proposal.revision != effects["proposal_revision"] or proposal.status not in {"pending_confirmation", "applying", "failed"}:
                raise AppError("PROPOSAL_STALE", "该提案已失效，请重新整理。", 409)
            for target in proposal.data.get("targets", []):
                row = session.get(GroupRecord, target["id"])
                if not row or row.workspace_id != workspace_id or snapshot(row) != target:
                    raise AppError("PROPOSAL_STALE", "目标文件已发生变化，请重新预览。", 409)
            for target in proposal.data.get("folders", []):
                row = session.get(Folder, target["id"])
                if not row or row.workspace_id != workspace_id or row.revision != target["revision"] or row.path != target["path"] or row.archived:
                    raise AppError("PROPOSAL_STALE", "目标目录已发生变化，请重新预览。", 409)

    def _same_location(self, workspace_id, first, second):
        if first == second:
            return True
        if first.casefold() != second.casefold():
            return False
        try:
            return self.store.path(workspace_id, "files", first).samefile(
                self.store.path(workspace_id, "files", second))
        except FileNotFoundError:
            return False

    def _verify_namespace(self, session, workspace_id, operations, effects):
        folders = {f.id: {"path": f.path, "parent_id": f.parent_id} for f in
                   session.scalars(select(Folder).where(Folder.workspace_id == workspace_id))}
        changed = {f["id"] for f in effects.get("folders", [])}
        for update in effects.get("folders", []):
            folders[update["id"]] = update["after"]
        paths = [f["path"].casefold() for f in folders.values()]
        if len(paths) != len(set(paths)):
            raise conflict("目标目录已有同名文件夹。")
        folder_paths = set(paths)

        def check_parent(path, folder_id):
            if folder_id and folder_id not in folders:
                raise not_found()
            parent = folders[folder_id]["path"] if folder_id else "."
            if str(PurePosixPath(path).parent).casefold() != parent.casefold():
                raise conflict("目录存在后续新增或移动的内容，请刷新后重新操作。")

        for folder_id, state in folders.items():
            if folder_id in changed or state["parent_id"] in changed:
                check_parent(state["path"], state["parent_id"])
            if folder_id in changed:
                occupied = session.scalar(select(GroupRecord.id).where(
                    GroupRecord.workspace_id == workspace_id, GroupRecord.deleted_at.is_(None),
                    GroupRecord.canonical_path == state["path"].casefold()))
                target = self.store.path(workspace_id, "files", state["path"])
                if occupied or (target.exists() and not target.is_dir()):
                    raise conflict("目标位置已有同名文件，不能创建或移动文件夹。")

        states = {op["group_id"]: op["after"] for op in operations}
        if changed:
            for row in session.scalars(select(GroupRecord).where(
                GroupRecord.workspace_id == workspace_id, GroupRecord.folder_id.in_(changed),
                GroupRecord.deleted_at.is_(None))):
                states.setdefault(row.id, snapshot(row))
        for state in states.values():
            if not state or state["deleted_at"] is not None:
                continue
            if state["path"].casefold() in folder_paths:
                raise conflict("目标位置已有同名文件夹，请选择其他名称。")
            check_parent(state["path"], state["folder_id"])
            for parent in PurePosixPath(state["path"]).parents:
                if str(parent) == ".":
                    break
                target = self.store.path(workspace_id, "files", str(parent))
                if target.exists() and not target.is_dir():
                    raise conflict("目标目录的位置被文件占用，请先修正目录。")

    def execute(
        self,
        workspace_id: str,
        request_key: str,
        request_hash: str,
        kind: str,
        summary: str,
        operations: list[dict],
        effects: dict | None = None,
    ) -> dict:
        effects = effects or {}
        drafts = operations
        operations = [{key: op[key] for key in ("group_id", "before", "after")} for op in drafts]
        with self.store.lock(workspace_id):
            with self.db.session() as session:
                existing = session.scalar(select(ChangeSet).where(
                    ChangeSet.workspace_id == workspace_id, ChangeSet.request_key == request_key
                ))
                if existing:
                    if existing.request_hash != request_hash:
                        raise AppError("IDEMPOTENCY_MISMATCH", "同一请求标识不能用于不同内容。", 409)
                    if existing.status == "committed":
                        return changeset_json(existing)
                    if existing.status != "rolled_back":
                        raise AppError("WORKSPACE_RECOVERING", "该次变更尚在恢复。", 503, retryable=True)
                    # A rolled-back batch is immutable. A retry gets a distinct batch key.
                    raise AppError("CHANGE_ROLLED_BACK", "上次变更已恢复，请刷新后重新提交。", 409)
                self.ensure_ready(session, workspace_id)
                self._verify(session, workspace_id, operations, effects)
                cs = ChangeSet(
                    id=new_id("change"),
                    workspace_id=workspace_id,
                    request_key=request_key,
                    request_hash=request_hash,
                    kind=kind,
                    summary=summary,
                    operations=operations,
                    effects=effects,
                    status="prepared",
                )
                journal = {
                    "id": cs.id, "workspace_id": workspace_id, "operations": operations, "effects": effects
                }
                contents, peak_bytes = {}, 0
                for draft in drafts:
                    before, after = draft["before"], draft["after"]
                    text = (draft.get("_content") if "_content" in draft else
                            self.store.read_blob(workspace_id, after["content_hash"])) if after else ""
                    if after:
                        if digest(text) != after["content_hash"]:
                            raise conflict("写入内容与版本校验和不一致。")
                        contents[after["content_hash"]] = text
                    before_bytes = len(self.store.read_blob(workspace_id, before["content_hash"]).encode("utf-8")) if before and before["deleted_at"] is None else 0
                    after_bytes = len(text.encode("utf-8")) if after and after["deleted_at"] is None else 0
                    # Reserve atomic-write and rollback headroom without assuming old files
                    # have already been removed. Snapshots and journals consume quota too.
                    peak_bytes += max(before_bytes, after_bytes)
                for value, text in contents.items():
                    if not self.store.path(workspace_id, "versions", value + ".md").exists():
                        peak_bytes += len(text.encode("utf-8"))
                peak_bytes += len(json.dumps(journal, ensure_ascii=False).encode("utf-8"))
                self.store.check_capacity(workspace_id, peak_bytes)
                for text in contents.values():
                    self.store.blob(workspace_id, text)
                self.store.journal(workspace_id, cs.id, journal)
                session.add(cs)
                session.flush()
                cs_id = cs.id
            self.fault("prepared", cs_id)
            try:
                for index, operation in enumerate(operations):
                    self._write_operation(workspace_id, operation, forward=True)
                    self.fault(f"file_{index}", cs_id)
                with self.db.session() as session:
                    cs = session.get(ChangeSet, cs_id)
                    cs.status = "files_applied"
                self.fault("files_applied", cs_id)
                with self.db.session() as session:
                    for operation in operations:
                        self._index(session, workspace_id, operation["group_id"], operation["after"])
                    self._effects(session, workspace_id, effects, cs_id)
                    cs = session.get(ChangeSet, cs_id)
                    cs.status = "committed"
                    session.add(AuditEvent(
                        id=new_id("audit"), workspace_id=workspace_id, action=kind,
                        resource_id=cs_id, details={"summary": summary}
                    ))
                    session.flush()
                    result = changeset_json(cs)
                    self.fault("before_commit", cs_id)
                self.fault("committed", cs_id)
                return result
            except Exception:
                self._recover_locked(workspace_id, cs_id)
                raise

    def _write_operation(self, workspace_id: str, operation: dict, *, forward: bool):
        old = operation["before"] if forward else operation["after"]
        new = operation["after"] if forward else operation["before"]
        old_active = old is not None and old["deleted_at"] is None
        new_active = new is not None and new["deleted_at"] is None
        same_location = old_active and new_active and self._same_location(workspace_id, old["path"], new["path"])
        if new_active:
            content = self.store.read_blob(workspace_id, new["content_hash"])
            self.store.write(workspace_id, "files", new["path"], content)
        if old_active and not same_location:
            self.store.remove(workspace_id, "files", old["path"])

    def _index(self, session, workspace_id: str, group_id: str, state: dict | None):
        session.execute(delete(TaskIndex).where(TaskIndex.group_id == group_id, TaskIndex.workspace_id == workspace_id))
        record = session.get(GroupRecord, group_id)
        if state is None:
            if record:
                session.delete(record)
            return
        text = self.store.read_blob(workspace_id, state["content_hash"])
        group, body, _ = parse_document(text)
        if group.id != group_id:
            raise AppError("STORAGE_CORRUPTED", "文件身份与索引不一致。", 503)
        if record is None:
            record = GroupRecord(id=group_id, workspace_id=workspace_id)
            session.add(record)
        record.folder_id = state["folder_id"]
        record.path = state["path"]
        record.canonical_path = (
            f"__trash__/{group_id}" if state["deleted_at"] else state["path"].casefold()
        )
        record.title = group.title
        record.content_hash = state["content_hash"]
        record.revision = state["revision"]
        record.status = group.status
        record.deleted_at = state["deleted_at"]
        record.search_text = group.title + "\n" + body + "\n" + " ".join(group.tags)
        record.updated_at = time.time()
        if state["deleted_at"] is None:
            for task in group.tasks:
                existing = session.get(TaskIndex, task.id)
                if existing and existing.group_id != group_id:
                    raise AppError("DUPLICATE_TASK_ID", "不同文件出现了同一个 Task ID。", 409)
                session.add(TaskIndex(
                    id=task.id, workspace_id=workspace_id, group_id=group_id,
                    title=task.title, status=task.status, data=task.model_dump(mode="json")
                ))

    def _effects(self, session, workspace_id: str, effects: dict, cs_id: str):
        if effects.get("proposal_id"):
            proposal = session.get(Proposal, effects["proposal_id"])
            proposal.status = "applied"
            proposal.data = {**proposal.data, "change_set_id": cs_id,
                             "selected_action_ids": effects.get("selected_action_ids", [])}
            if proposal.capture_id:
                refresh_capture_status(session, workspace_id, proposal.capture_id)
        for update in effects.get("folders", []):
            row = session.get(Folder, update["id"])
            if row is None:
                row = Folder(id=update["id"], workspace_id=workspace_id)
                session.add(row)
            for key, value in update["after"].items():
                setattr(row, key, value)
        if effects.get("undo_of"):
            previous = session.get(ChangeSet, effects["undo_of"])
            if not previous or previous.workspace_id != workspace_id or previous.undone_by:
                raise conflict("这次变更已经撤销。")
            previous.undone_by = cs_id
        for update in effects.get("proposal_states", []):
            proposal = session.get(Proposal, update["id"])
            proposal.status = update["after"]
            if proposal.capture_id:
                refresh_capture_status(session, workspace_id, proposal.capture_id)

    def _recover_locked(self, workspace_id: str, cs_id: str):
        with self.db.session() as session:
            cs = session.get(ChangeSet, cs_id)
            if not cs or cs.status in SETTLED:
                return
            operations = copy.deepcopy(cs.operations)
        try:
            for op in operations:
                allowed = {s["content_hash"] for s in (op["before"], op["after"]) if s}
                for state in (op["before"], op["after"]):
                    if not state or state["deleted_at"]:
                        continue
                    path = self.store.path(workspace_id, "files", state["path"])
                    if path.exists() and digest(path.read_bytes()) not in allowed:
                        raise conflict("恢复中检测到额外的外部编辑，已保留现场。")
            for op in reversed(operations):
                self._write_operation(workspace_id, op, forward=False)
            with self.db.session() as session:
                cs = session.get(ChangeSet, cs_id)
                cs.status = "rolled_back"
                proposal_id = cs.effects.get("proposal_id")
                if proposal_id:
                    proposal = session.get(Proposal, proposal_id)
                    if proposal and proposal.status != "applied":
                        proposal.status = "failed"
        except Exception:
            with self.db.session() as session:
                cs = session.get(ChangeSet, cs_id)
                cs.status = "recovery_required"
            raise

    def recover_all(self) -> list[dict]:
        with self.db.session() as session:
            pending = [
                (cs.workspace_id, cs.id)
                for cs in session.scalars(select(ChangeSet).where(ChangeSet.status.not_in(SETTLED)))
            ]
        errors = []
        for workspace_id, cs_id in pending:
            try:
                with self.store.lock(workspace_id):
                    self._recover_locked(workspace_id, cs_id)
            except Exception:
                errors.append({"workspace_id": workspace_id, "change_set_id": cs_id})
        return errors

    def undo(self, workspace_id: str, cs_id: str, request_key: str, request_hash: str) -> dict:
        with self.store.lock(workspace_id):
            with self.db.session() as session:
                repeated = self.existing(session, workspace_id, request_key, request_hash)
                if repeated:
                    return repeated
                self.ensure_ready(session, workspace_id)
                original = session.get(ChangeSet, cs_id)
                if not original or original.workspace_id != workspace_id:
                    raise not_found()
                if original.undone_by:
                    return changeset_json(session.get(ChangeSet, original.undone_by))
                if original.status != "committed":
                    raise conflict("只能撤销已成功的变更。")
                proposal_states = self._undo_proposal_states(session, workspace_id, original)
                operations = copy.deepcopy(original.operations)
                reversed_ops = []
                for operation in operations:
                    current = session.get(GroupRecord, operation["group_id"])
                    if snapshot(current) != operation["after"]:
                        raise conflict("这次变更后已有其他编辑，请从版本历史预览恢复。")
                    before = operation["before"]
                    current_text = self.store.read_blob(workspace_id, current.content_hash)
                    source = self.store.read_blob(workspace_id, before["content_hash"]) if before else current_text
                    group, _, _ = parse_document(source)
                    group.revision = current.revision + 1
                    group.updated_at = now_iso()
                    reversed_ops.append(self.operation(
                        workspace_id, current, group,
                        path=before["path"] if before else current.path,
                        folder_id=before["folder_id"] if before else current.folder_id,
                        previous=source,
                        deleted=before is None or before["deleted_at"] is not None,
                    ))
                folder_effects = []
                for change in original.effects.get("folders", []):
                    row = session.get(Folder, change["id"])
                    if not row or row.workspace_id != workspace_id or any(getattr(row, k) != v for k, v in change["after"].items()):
                        raise conflict("文件夹已有后续变更，请先处理冲突。")
                    if change["before"] is None:
                        children = session.scalar(select(Folder.id).where(
                            Folder.workspace_id == workspace_id, Folder.parent_id == row.id))
                        files = session.scalar(select(GroupRecord.id).where(
                            GroupRecord.workspace_id == workspace_id, GroupRecord.folder_id == row.id,
                            GroupRecord.deleted_at.is_(None)))
                        if children or files:
                            raise conflict("文件夹已有内容，不能撤销创建。")
                    restored = {**(change["before"] or change["after"]), "revision": row.revision + 1}
                    if change["before"] is None:
                        restored["archived"] = True
                    folder_effects.append({"id": row.id, "before": change["after"], "after": restored})
            return self.execute(
                workspace_id, request_key, request_hash, "undo", "撤销：" + original.summary,
                reversed_ops, {"undo_of": cs_id, "folders": folder_effects, "proposal_states": proposal_states}
            )

    def _undo_proposal_states(self, session, workspace_id, original):
        if "proposal_states" in original.effects:
            return [{**item, "before": item["after"], "after": item["before"]}
                    for item in original.effects["proposal_states"]]
        # Also support histories recorded before reversible proposal effects existed.
        base, depth, seen = original, 0, set()
        while base.effects.get("undo_of"):
            if base.id in seen:
                raise conflict("撤销历史存在循环，已停止操作。")
            seen.add(base.id)
            base = session.get(ChangeSet, base.effects["undo_of"])
            if not base or base.workspace_id != workspace_id:
                raise not_found()
            depth += 1
        proposal_id = base.effects.get("proposal_id")
        if not proposal_id:
            return []
        proposal = session.get(Proposal, proposal_id)
        if not proposal or proposal.workspace_id != workspace_id:
            raise not_found()
        before = "applied" if depth % 2 == 0 else "rejected"
        return [{"id": proposal.id, "revision": proposal.revision, "before": before,
                 "after": "rejected" if before == "applied" else "applied"}]
