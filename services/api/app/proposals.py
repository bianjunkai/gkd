import copy
import difflib
import re
from datetime import datetime

from sqlalchemy import select

from .captures import refresh_capture_status
from .changes import changeset_json, snapshot
from .db import AuditEvent, CaptureRecord, ChangeSet, Folder, GroupRecord, Proposal
from .domain import Group, Task, TaskDraft, json_hash, new_id, now_iso
from .errors import AppError, conflict, not_found
from .grouping import DEFAULT_TASK_GROUP, mentions_title, suggested_group_title, title_key, validate_new_group_title
from .schemas import ProposalAction, Question
from .storage import START, END, TASK_START, TASK_END, check_id, parse_document, render_document
from .workspace import folder_chain


class ProposalService:
    def __init__(self, db, store, changes, workspace, captures, extraction):
        self.db, self.store, self.changes = db, store, changes
        self.workspace, self.captures, self.extraction = workspace, captures, extraction

    @staticmethod
    def public(row):
        return {"id": row.id, "revision": row.revision, "status": row.status,
                "capture_id": row.capture_id, "created_at": row.created_at,
                **{k: v for k, v in row.data.items() if not k.startswith("_")}}

    def _row(self, session, workspace_id, proposal_id):
        row = session.get(Proposal, proposal_id)
        if not row or row.workspace_id != workspace_id:
            raise not_found()
        return row

    def candidates(self, workspace_id, raw_text):
        with self.db.session() as session:
            result = []
            for row in session.scalars(select(GroupRecord).where(GroupRecord.workspace_id == workspace_id,
                                       GroupRecord.deleted_at.is_(None), GroupRecord.status != "archived")):
                if any(f.archived for f in folder_chain(session, workspace_id, row.folder_id, writable=False)):
                    continue
                tokens = set(re.findall(r"[a-zA-Z]{3,}|[\u4e00-\u9fff]{2}", row.title))
                shared = [t for t in tokens if t.casefold() in raw_text.casefold()]
                if shared:
                    result.append({"id": row.id, "title": row.title, "path": row.path,
                                   "reason": "标题与原文共同包含：" + "、".join(shared[:3]), "score": len(shared)})
            return sorted(result, key=lambda r: (-r["score"], r["id"]))[:5]

    def _writable_groups(self, session, ws):
        return [row for row in session.scalars(select(GroupRecord).where(
            GroupRecord.workspace_id == ws, GroupRecord.deleted_at.is_(None), GroupRecord.status != "archived"))
            if not any(f.archived for f in folder_chain(session, ws, row.folder_id, writable=False))]

    def _suggest_target(self, session, ws, raw_text, group_title):
        available = self._writable_groups(session, ws)
        # A full path disambiguates identically named groups in different projects.
        for matched in (
            [g for g in available if mentions_title(raw_text, g.path, path=True)],
            [g for g in available if mentions_title(raw_text, g.title)],
            [g for g in available if title_key(g.title) == title_key(group_title)
             and (group_title != DEFAULT_TASK_GROUP or g.folder_id is None)],
        ):
            if matched:
                return (matched[0], False) if len(matched) == 1 else (None, True)
        return None, False

    def analyze_job(self, job):
        ws, payload = job.workspace_id, job.payload
        proposal_id = job.id.replace("job-", "proposal-", 1)
        with self.store.lock(ws), self.db.session() as session:
            existing = session.get(Proposal, proposal_id)
            if existing:
                return {"proposal_id": existing.id}
            self.changes.ensure_ready(session, ws)
        capture = self.captures.get(ws, payload["capture_id"])
        if capture["deleted_at"] or capture["status"] == "archived":
            raise AppError("CAPTURE_UNAVAILABLE", "这条原始记录已删除或归档。", 409)
        target = None
        target_id = payload.get("target_group_id")
        if payload.get("target_group_id"):
            group = self.workspace.get(ws, payload["target_group_id"])
            target = {"title": group["title"], "tags": group["tags"]}
        reference = datetime.fromisoformat(payload["reference_time"].replace("Z", "+00:00"))
        result, provenance = self.extraction.extract(ws, capture["raw_text"], reference,
                                                    payload["timezone"], payload["mode"], target)
        result.group_title = suggested_group_title(result.group_title, result.tasks)
        if not target and result.tasks:
            with self.store.lock(ws), self.db.session() as session:
                self.changes.ensure_ready(session, ws)
                suggested, ambiguous = self._suggest_target(session, ws, capture["raw_text"], result.group_title)
                if suggested:
                    target_id, target = suggested.id, {"title": suggested.title}
                elif ambiguous:
                    if len(result.questions) >= 20:
                        raise AppError("TOO_MANY_QUESTIONS", "归属和内容有较多待澄清项，请分段整理。", 422)
                    result.questions.append(Question(field_path="destination", required=True,
                        message="匹配到多个任务组。请在「保存到哪里」选择具体任务组，或明确新建的位置和名称，再勾选确认。"))
        actions = []
        for index, task in enumerate(result.tasks):
            evidence = {e.field: e.quote for e in result.evidence if e.task_index == index}
            confidence = {e.field: e.confidence for e in result.evidence if e.task_index == index}
            actions.append(ProposalAction(action_id=new_id("action"),
                           type="append" if target else "create", target_group_id=target_id,
                           group_title=target["title"] if target else result.group_title,
                           tasks=[task], evidence=evidence, field_confidence=confidence))
        if not actions:
            actions.append(ProposalAction(action_id=new_id("action"),
                           type="append" if target else "create", target_group_id=target_id,
                           group_title=target["title"] if target else result.group_title,
                           body_append=capture["raw_text"], evidence={"body": capture["raw_text"][:1000]},
                           field_confidence={"body": "medium"}))
        candidates = self.candidates(ws, capture["raw_text"])
        with self.store.lock(ws), self.db.session() as session:
            self.changes.ensure_ready(session, ws)
            row = session.get(CaptureRecord, capture["id"])
            if not row or row.deleted_at:
                raise not_found()
            existing = session.get(Proposal, proposal_id)
            if existing:
                return {"proposal_id": existing.id}
            prepared = self._prepare(session, ws, actions, capture["id"])
            prepared.update(classification=result.classification, questions=[q.model_dump() for q in result.questions],
                            reference_time=payload["reference_time"], timezone=payload["timezone"],
                            provider=provenance, candidates=candidates)
            for old in session.scalars(select(Proposal).where(Proposal.workspace_id == ws,
                                       Proposal.capture_id == row.id, Proposal.status.in_(["pending_confirmation", "stale", "failed"]))):
                old.status = "rejected"
            proposal = Proposal(id=proposal_id, workspace_id=ws, capture_id=row.id,
                                data=prepared, revision=1, status="pending_confirmation")
            session.add(proposal)
            row.status = "needs_confirmation"
            session.add(AuditEvent(id=new_id("audit"), workspace_id=ws, action="analysis_finished",
                                   resource_id=proposal_id, details={"job_id": job.id, "provider": provenance["provider"]}))
            return {"proposal_id": proposal.id}

    def _prepare(self, session, ws, actions, capture_id):
        if sum(len(a.tasks) for a in actions) > 20:
            raise AppError("TOO_MANY_ACTIONS", "单次提案最多包含 20 项任务。", 422)
        if len(set(a.action_id for a in actions)) != len(actions):
            raise AppError("INPUT_INVALID", "动作标识不能重复。", 422)
        data = {"actions": [], "targets": [], "folders": [], "_bindings": {}, "_capture_id": capture_id}
        creates, seen_targets, seen_folders, reserved = {}, set(), set(), set()
        reusable = self._writable_groups(session, ws) if any(a.type == "create" and a.tasks and not a.file_name for a in actions) else []
        for action in actions:
            check_id(action.action_id)
            if not action.action_id.startswith("action-"):
                raise AppError("INPUT_INVALID", "动作 ID 不合法。", 422)
            if any(marker in action.body_append for marker in (START, END, TASK_START, TASK_END)):
                raise AppError("INPUT_INVALID", "追加内容不能包含托管区标记。", 422)
            if not action.tasks and not action.body_append.strip():
                raise AppError("EMPTY_ACTION", "动作必须包含任务或正文内容。", 422)
            if action.type == "create":
                if action.target_group_id:
                    raise AppError("INPUT_INVALID", "新建动作不能指定现有任务组。", 422)
                validate_new_group_title(action.group_title, action.tasks, action.file_name)
                if action.tasks and not action.file_name:
                    matching = [g for g in reusable
                                if g.folder_id == action.folder_id and title_key(g.title) == title_key(action.group_title)]
                    if len(matching) > 1:
                        raise AppError("TARGET_REQUIRED", "同一位置存在多个同名任务组，请明确选择要追加的文件。", 422)
                    if matching:
                        action = action.model_copy(update={"type": "append", "target_group_id": matching[0].id})
            if action.type == "append":
                if not action.target_group_id:
                    raise AppError("TARGET_REQUIRED", "请选择要追加的任务组。", 422)
                row = self.workspace._row(session, ws, action.target_group_id)
                group, _, _ = self.workspace._load(ws, row)
                if group.status == "archived":
                    raise conflict("目标任务组已归档，请先恢复。")
                binding = {"group_id": row.id, "folder_id": row.folder_id, "path": row.path}
                action = action.model_copy(update={"folder_id": row.folder_id, "group_title": group.title})
                if row.id not in seen_targets:
                    data["targets"].append(snapshot(row))
                    seen_targets.add(row.id)
            else:
                if action.target_group_id:
                    raise AppError("INPUT_INVALID", "新建动作不能指定现有任务组。", 422)
                key = json_hash({"folder": action.folder_id, "title": action.group_title, "name": action.file_name})
                if key not in creates:
                    path = self.workspace.path_for(session, ws, action.folder_id, action.file_name or action.group_title,
                                                   auto_suffix=action.file_name is None, reserved=reserved)
                    reserved.add(path.casefold())
                    creates[key] = {"group_id": new_id("group"), "folder_id": action.folder_id, "path": path}
                binding = copy.deepcopy(creates[key])
            for folder in folder_chain(session, ws, binding["folder_id"]):
                if folder.id not in seen_folders:
                    data["folders"].append({"id": folder.id, "path": folder.path, "revision": folder.revision})
                    seen_folders.add(folder.id)
            binding["task_ids"] = [new_id("task") for _ in action.tasks]
            data["_bindings"][action.action_id] = binding
            data["actions"].append(action.model_dump(mode="json"))
        if len({b["group_id"] for b in data["_bindings"].values()}) > 5:
            raise AppError("TOO_MANY_FILES", "单次提案最多影响 5 个文件。", 422)
        documents = self._documents(session, ws, data, [a.action_id for a in actions])
        data["previews"] = [self._preview(d) for d in documents]
        return data

    def _documents(self, session, ws, data, selected):
        targets = {target["id"]: target for target in data["targets"]}
        documents = {}
        for action in data["actions"]:
            if action["action_id"] not in selected:
                continue
            if action["type"] == "create":
                # Pending proposals created by an older server must obey the same rule.
                validate_new_group_title(action["group_title"], [TaskDraft.model_validate(t) for t in action["tasks"]], action.get("file_name"))
            binding = data["_bindings"][action["action_id"]]
            gid = binding["group_id"]
            if gid not in documents:
                if gid in targets:
                    row = self.workspace._row(session, ws, gid)
                    if snapshot(row) != targets[gid]:
                        raise AppError("PROPOSAL_STALE", "目标文件已变化，请重新预览。", 409)
                    group, _, original = self.workspace._load(ws, row)
                    group.revision += 1
                else:
                    row, original = None, ""
                    group = Group(id=gid, title=action["group_title"])
                documents[gid] = {"group": group, "row": row, "original": original,
                                  "previous": original, "path": binding["path"], "folder_id": binding["folder_id"]}
            doc = documents[gid]
            for task, task_id in zip(action["tasks"], binding["task_ids"], strict=True):
                doc["group"].tasks.append(Task(**task, id=task_id, source_refs=[data["_capture_id"]] if data["_capture_id"] else []))
                if doc["group"].status == "done":
                    doc["group"].status = "active"
            if action["body_append"].strip():
                if not doc["previous"]:
                    doc["previous"] = render_document(Group(id=gid, title=doc["group"].title))
                doc["previous"] = doc["previous"].rstrip() + "\n\n" + action["body_append"] + "\n"
        for doc in documents.values():
            doc["group"].schema_version = 2
            doc["group"].updated_at = now_iso()
            doc["text"] = render_document(doc["group"], previous=doc["previous"] or None)
        return list(documents.values())

    @staticmethod
    def _preview(doc):
        return {"group_id": doc["group"].id, "path": doc["path"], "title": doc["group"].title,
                "before_revision": doc["row"].revision if doc["row"] else None,
                "will_reopen": bool(doc["row"] and doc["row"].status == "done" and doc["group"].status == "active"),
                "diff": "".join(difflib.unified_diff(doc["original"].splitlines(True), doc["text"].splitlines(True), fromfile="确认前", tofile="确认后"))}

    def _is_stale(self, session, ws, row):
        for target in row.data["targets"]:
            current = session.get(GroupRecord, target["id"])
            if not current or current.workspace_id != ws or snapshot(current) != target:
                return True
            try:
                self.workspace._load(ws, current)
            except AppError:
                return True
        for target in row.data["folders"]:
            current = session.get(Folder, target["id"])
            if not current or current.workspace_id != ws or current.archived or current.path != target["path"] or current.revision != target["revision"]:
                return True
        for action in row.data["actions"]:
            if action["type"] == "create":
                path = row.data["_bindings"][action["action_id"]]["path"]
                if self.store.path(ws, "files", path).exists():
                    return True
        return False

    def get(self, ws, proposal_id):
        with self.store.lock(ws), self.db.session() as session:
            self.changes.ensure_ready(session, ws)
            row = self._row(session, ws, proposal_id)
            if row.status == "pending_confirmation" and self._is_stale(session, ws, row):
                row.status = "stale"
            return self.public(row)

    def edit(self, ws, proposal_id, data):
        with self.store.lock(ws), self.db.session() as session:
            self.changes.ensure_ready(session, ws)
            row = self._row(session, ws, proposal_id)
            if row.revision != data.proposal_revision or row.status not in {"pending_confirmation", "stale", "failed"}:
                raise AppError("PROPOSAL_STALE", "提案已变化或已结束，请刷新后操作。", 409)
            if row.capture_id:
                capture = session.get(CaptureRecord, row.capture_id)
                if not capture or capture.deleted_at:
                    raise not_found()
            prepared = self._prepare(session, ws, data.actions, row.capture_id)
            row.data = {**row.data, **prepared, "questions": [q.model_dump() for q in data.questions]}
            row.revision += 1
            row.status = "pending_confirmation"
            session.add(AuditEvent(id=new_id("audit"), workspace_id=ws, action="proposal_edited", resource_id=row.id,
                                   details={"revision": row.revision}))
            return self.public(row)

    def preview_selection(self, ws, proposal_id, data):
        with self.store.lock(ws), self.db.session() as session:
            self.changes.ensure_ready(session, ws)
            row = self._row(session, ws, proposal_id)
            self._check_confirmation(session, ws, row, data)
            documents = self._documents(session, ws, row.data, data.selected_action_ids)
            return {"previews": [self._preview(doc) for doc in documents], "file_count": len(documents),
                    "task_count": sum(len(a["tasks"]) for a in row.data["actions"] if a["action_id"] in data.selected_action_ids)}

    def _check_confirmation(self, session, ws, row, data):
        if row.revision != data.proposal_revision or row.status not in {"pending_confirmation", "failed"}:
            raise AppError("PROPOSAL_STALE", "该修订已失效，请重新预览。", 409)
        ids = data.selected_action_ids
        if len(ids) != len(set(ids)) or not set(ids).issubset({a["action_id"] for a in row.data["actions"]}):
            raise AppError("INPUT_INVALID", "选中的动作不属于本次提案。", 422)
        if any(q["required"] and not q["resolved"] for q in row.data["questions"]):
            raise AppError("CLARIFICATION_REQUIRED", "请先处理必需的澄清项。", 422)
        if row.capture_id:
            capture = session.get(CaptureRecord, row.capture_id)
            if not capture or capture.workspace_id != ws or capture.deleted_at:
                raise not_found()
        if self._is_stale(session, ws, row):
            raise AppError("PROPOSAL_STALE", "目标文件或目录已变化，请保存修订、重新预览。", 409)

    def confirm(self, ws, proposal_id, data, key, request_hash):
        with self.store.lock(ws):
            try:
                with self.db.session() as session:
                    repeated = self.changes.existing(session, ws, key, request_hash)
                    if repeated:
                        return repeated
                    self.changes.ensure_ready(session, ws)
                    row = self._row(session, ws, proposal_id)
                    if row.status == "applied":
                        if row.revision != data.proposal_revision or set(row.data["selected_action_ids"]) != set(data.selected_action_ids):
                            raise conflict("该提案已应用，不能再次应用不同的动作集合。")
                        return changeset_json(session.get(ChangeSet, row.data["change_set_id"]))
                    self._check_confirmation(session, ws, row, data)
                    documents = self._documents(session, ws, row.data, data.selected_action_ids)
                    operations = [self.changes.operation(ws, doc["row"], doc["group"], path=doc["path"],
                                  folder_id=doc["folder_id"], previous=doc["previous"] or None) for doc in documents]
                    effects = {"proposal_id": row.id, "proposal_revision": row.revision,
                               "selected_action_ids": data.selected_action_ids}
                return self.changes.execute(ws, key, request_hash, "proposal_confirmed", f"确认整理：{len(documents)} 个文件", operations, effects)
            except AppError as exc:
                if exc.code == "PROPOSAL_STALE":
                    with self.db.session() as session:
                        row = self._row(session, ws, proposal_id)
                        if row.revision == data.proposal_revision and row.status in {"pending_confirmation", "failed"} and self._is_stale(session, ws, row):
                            row.status = "stale"
                raise

    def reject(self, ws, proposal_id, revision):
        with self.store.lock(ws), self.db.session() as session:
            self.changes.ensure_ready(session, ws)
            row = self._row(session, ws, proposal_id)
            if row.revision != revision or row.status == "applied":
                raise conflict()
            row.status = "rejected"
            if row.capture_id:
                refresh_capture_status(session, ws, row.capture_id)
            return self.public(row)
