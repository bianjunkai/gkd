import html
import io
import json
import math
import os
import re
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath

import yaml
from pydantic import ValidationError
from yaml.tokens import AliasToken, AnchorToken, TagToken

from .domain import Group, Task, digest
from .errors import AppError
from .locking import workspace_lock

START = "<!-- gtd:tasks:start -->"
END = "<!-- gtd:tasks:end -->"
TASK_START = "<!-- gtd:task:start -->"
TASK_END = "<!-- gtd:task:end -->"
TASK_FENCE = "```gtd-task"
ID_PATTERN = re.compile(r"^[a-z]+-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)
ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def check_id(value: str):
    if not ID_PATTERN.fullmatch(value):
        raise AppError("INPUT_INVALID", "对象 ID 不合法。", 422)


def filename(value: str, *, sanitize: bool = False) -> str:
    value = unicodedata.normalize("NFC", value)
    if sanitize:
        value = ILLEGAL.sub("_", value).strip(" .")[:100]
        while len(value.encode("utf-8")) > 240:
            value = value[:-1]
        if RESERVED.match(value):
            value = "_" + value
        return value or "未命名"
    if (
        not value
        or value != value.strip(" .")
        or value in {".", ".."}
        or ILLEGAL.search(value)
        or RESERVED.match(value)
        or len(value) > 100
        or len(value.encode("utf-8")) > 240
    ):
        raise AppError("INVALID_PATH", "名称包含不支持的字符，或超过长度限制。", 422)
    return value


def checked_relative(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or "\\" in value or value != path.as_posix():
        raise AppError("INVALID_PATH", "只允许工作空间内的相对路径。", 422)
    if len(path.parts) > 33 or len(value.encode("utf-8")) > 1024:
        raise AppError("INVALID_PATH", "目录层级或路径长度超过限制。", 422)
    for part in path.parts:
        filename(part)
    return path.as_posix()


class StrictLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        keys = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in keys:
                raise ValueError("YAML 属性键必须唯一且为文本")
            keys.add(key)
        return super().construct_mapping(node, deep=deep)


class ReadableDumper(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


class YamlCodec:
    scan = staticmethod(yaml.scan)

    @staticmethod
    def load(value):
        return yaml.load(value, Loader=StrictLoader)

    @staticmethod
    def dump(value, stream):
        yaml.dump(
            value, stream, Dumper=ReadableDumper,
            allow_unicode=True, sort_keys=False, width=100, indent=2,
        )


def _yaml():
    return YamlCodec()


def _check_tree(value, depth=0):
    if depth > 24:
        raise ValueError("YAML 嵌套过深")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("属性键必须是文本")
            _check_tree(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check_tree(item, depth + 1)
    elif value is not None and not isinstance(value, str | int | float | bool | date | datetime):
        raise ValueError("不支持的 YAML 类型")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("不支持无限或非数字数值")


def safe_mapping(value: str) -> dict:
    parser = _yaml()
    if any(isinstance(token, AliasToken | AnchorToken | TagToken) for token in parser.scan(value)):
        raise ValueError("不接受 YAML 锚点、别名或自定义类型")
    data = parser.load(value)
    if not isinstance(data, dict):
        raise ValueError("属性必须是 YAML 映射")
    _check_tree(data)
    return data


@dataclass
class DocumentBlock:
    markdown: str
    task: Task | None = None


def _task_block(raw: str, *, draft=False) -> Task:
    lines = raw.splitlines(keepends=True)
    if len(lines) < 5 or not lines[1].startswith("### ") or lines[2].strip() != TASK_FENCE:
        raise ValueError("任务块需要 ### 标题，随后是 gtd-task 属性代码块")
    close = next((i for i in range(3, len(lines) - 1) if lines[i].strip() == "```"), None)
    if close is None:
        raise ValueError("任务属性代码块没有结束标记")
    metadata = safe_mapping("".join(lines[3:close]))
    required = {"status"} if draft else {"id", "status", "created_at", "updated_at"}
    if not required.issubset(metadata):
        raise ValueError("任务块缺少稳定 ID、状态或审计时间；新任务请先预览再保存")
    if {"title", "description"}.intersection(metadata):
        raise ValueError("任务标题只写在 ### 标题行，描述只写在属性代码块之后")
    title = html.unescape(re.sub(r"\\([\\`*_{}\[\]<>()#+.!|~-])", r"\1", lines[1][4:].rstrip("\r\n")))
    description = "".join(lines[close + 1:-1]).strip("\r\n")
    task = Task.model_validate({**metadata, "title": title, "description": description})
    check_id(task.id)
    if not task.id.startswith("task-"):
        raise ValueError("Task ID 前缀错误")
    return task


def document_blocks(body: str, *, draft=False) -> list[DocumentBlock]:
    """Recognize top-level task boundaries, never examples inside fenced code."""
    blocks, chunk = [], []
    in_task, fence_char, fence_size = False, None, 0
    for line_number, line in enumerate(body.splitlines(keepends=True), 1):
        value = line.rstrip("\r\n")
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", value)
        if fence_char:
            chunk.append(line)
            if fence and fence[1][0] == fence_char and len(fence[1]) >= fence_size and not fence[2].strip():
                fence_char, fence_size = None, 0
            continue
        if fence and (fence[1][0] != "`" or "`" not in fence[2]):
            fence_char, fence_size = fence[1][0], len(fence[1])
            chunk.append(line)
            continue
        if value == TASK_START:
            if in_task:
                raise ValueError(f"正文第 {line_number} 行：任务块不能嵌套")
            if chunk:
                blocks.append(DocumentBlock("".join(chunk)))
            chunk, in_task = [line], True
        elif value == TASK_END:
            if not in_task:
                raise ValueError(f"正文第 {line_number} 行：任务块缺少开始标记")
            chunk.append(line)
            raw = "".join(chunk)
            blocks.append(DocumentBlock(raw, _task_block(raw, draft=draft)))
            chunk, in_task = [], False
        elif "<!-- gtd:task:" in value or value in {START, END}:
            raise ValueError(f"正文第 {line_number} 行：任务标记无效，应独占一行；示例请放在代码围栏内")
        else:
            chunk.append(line)
    if in_task:
        raise ValueError("任务块缺少结束标记，或内部代码围栏未闭合")
    if chunk:
        blocks.append(DocumentBlock("".join(chunk)))
    return blocks


def render_task(task: Task) -> str:
    title = html.escape(task.title, quote=False).replace("\r", "&#13;").replace("\n", "&#10;")
    title = re.sub(r"([\\`*_{}\[\]()#+.!|~-])", r"\\\1", title)
    metadata = task.model_dump(mode="json", exclude={"title", "description"})
    stream = io.StringIO()
    _yaml().dump(metadata, stream)
    description = "\n\n" + task.description.strip("\r\n") if task.description else ""
    return f"{TASK_START}\n### {title}\n{TASK_FENCE}\n{stream.getvalue()}```{description}\n{TASK_END}\n"


def serialize_block_document(group: Group, body: str) -> str:
    """Encode an already-ordered body. All callers still publish via ChangeEngine."""
    if group.schema_version != 2:
        raise ValueError("Block documents require schema 2")
    stream = io.StringIO()
    _yaml().dump(group.model_dump(mode="json", exclude={"tasks"}), stream)
    result = "---\n" + stream.getvalue() + "---\n\n" + body
    parse_document(result)
    return result


def parse_document(content: str, *, draft=False) -> tuple[Group, str, dict]:
    if len(content.encode("utf-8")) > 1024 * 1024:
        raise AppError("DOCUMENT_TOO_LARGE", "单个 Markdown 文件不能超过 1 MiB。", 413)
    lines = content.removeprefix("\ufeff").splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise AppError("SCHEMA_INVALID", "文件缺少 YAML 属性头。", 422)
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        raise AppError("SCHEMA_INVALID", "YAML 属性头没有结束标记。", 422)
    header, body = "".join(lines[1:close]), "".join(lines[close + 1 :]).lstrip("\r\n")
    try:
        metadata = safe_mapping(header)
        required = {"schema_version", "id", "title", "status", "revision", "created_at", "updated_at"}
        if not required.issubset(metadata):
            raise ValueError("文件缺少必需属性")
        if metadata["schema_version"] == 2:
            if "tasks" in metadata:
                raise ValueError("格式 2 的任务只存在于正文任务块，属性头不能再包含 tasks")
            tasks = [block.task for block in document_blocks(body, draft=draft) if block.task]
            group = Group.model_validate({**metadata, "tasks": tasks})
        else:
            if not isinstance(metadata.get("tasks"), list):
                raise ValueError("旧格式 tasks 必须是数组")
            for item in metadata["tasks"]:
                if not isinstance(item, dict) or not {"id", "title", "status", "created_at", "updated_at"}.issubset(item):
                    raise ValueError("Task 缺少稳定 ID 或必需属性")
            group = Group.model_validate(metadata)
        check_id(group.id)
        if not group.id.startswith("group-"):
            raise ValueError("Group ID 前缀错误")
        for task in group.tasks:
            check_id(task.id)
            if not task.id.startswith("task-"):
                raise ValueError("Task ID 前缀错误")
        if group.schema_version == 1 and (body.count(START) != body.count(END) or body.count(START) > 1):
            raise ValueError("托管任务区标记不完整或重复")
        if group.schema_version == 1 and START in body and body.index(START) > body.index(END):
            raise ValueError("托管任务区标记顺序错误")
        return group, body, metadata
    except AppError:
        raise
    except (ValueError, TypeError, ValidationError) as exc:
        message = "任务或文档属性无效，请检查状态、日期及必需字段。" if isinstance(exc, ValidationError) else str(exc)
        raise AppError("SCHEMA_INVALID", "Markdown 格式无效：" + message, 422) from exc
    except Exception as exc:
        raise AppError("SCHEMA_INVALID", "YAML 无法安全解析。", 422) from exc


def editable_body(body: str) -> str:
    if START not in body:
        return body
    before, _, tail = body.partition(START)
    _, _, after = tail.partition(END)
    return (before.rstrip() + "\n\n" + after.lstrip()).strip() + "\n"


def render_document(group: Group, *, previous: str | None = None, body: str | None = None) -> str:
    if group.schema_version == 2:
        return _render_block_document(group, previous=previous, body=body)
    if previous:
        original, previous_body, metadata = parse_document(previous)
    else:
        original, metadata = None, {}
        previous_body = f"# {group.title}\n\n{START}\n{END}\n\n## 工作记录\n\n"
    if body is not None:
        if START in body or END in body:
            raise AppError("INPUT_INVALID", "正文编辑不能包含托管任务区标记。", 422)
        previous_body = body.rstrip() + f"\n\n{START}\n{END}\n"
    for key, value in group.model_dump(mode="json").items():
        metadata[key] = value
    stream = io.StringIO()
    _yaml().dump(metadata, stream)
    task_lines = []
    for task in group.tasks:
        pattern = r"([\\*_\[\]<>]" + "|" + chr(96) + ")"
        title = re.sub(pattern, r"\\\1", task.title).replace("\n", " ")
        check = "x" if task.status == "done" else " "
        task_lines.append(f"- [{check}] {title} <!-- task:{task.id} -->")
    managed = START + "\n## Tasks\n\n" + "\n".join(task_lines) + "\n" + END
    if START in previous_body:
        before, _, tail = previous_body.partition(START)
        _, _, after = tail.partition(END)
        body_result = before + managed + after
    else:
        body_result = previous_body.rstrip() + "\n\n" + managed + "\n"
    if original and body_result.startswith(f"# {original.title}\n"):
        body_result = f"# {group.title}\n" + body_result[len(f"# {original.title}\n") :]
    result = "---\n" + stream.getvalue() + "---\n\n" + body_result
    parse_document(result)
    return result


def _render_block_document(group: Group, *, previous: str | None = None, body: str | None = None) -> str:
    if previous:
        original, previous_body, metadata = parse_document(previous)
        if original.schema_version == 1:
            managed = "\n".join(render_task(t) for t in original.tasks)
            if START in previous_body:
                before, _, tail = previous_body.partition(START)
                _, _, after = tail.partition(END)
                previous_body = before + managed.rstrip("\n") + after
            else:
                previous_body = previous_body.rstrip() + "\n\n" + managed
    else:
        original, metadata = None, {}
        previous_body = f"# {group.title}\n\n"
    if body is not None:
        if any(marker in body for marker in (START, END, TASK_START, TASK_END)):
            raise AppError("INPUT_INVALID", "普通正文不能包含任务标记；请使用 Markdown 源码编辑来调整任务块。", 422)
        previous_body = body.rstrip() + "\n"
    try:
        blocks = document_blocks(previous_body)
    except (ValueError, TypeError, ValidationError) as exc:
        raise AppError("SCHEMA_INVALID", "Markdown 任务块无效：" + str(exc), 422) from exc
    pending = {t.id: t for t in group.tasks}
    parts = []
    for block in blocks:
        if block.task is None:
            parts.append(block.markdown)
        else:
            task = pending.pop(block.task.id, None)
            if task:
                parts.append(block.markdown if task == block.task else render_task(task))
    body_result = "".join(parts)
    if pending:
        body_result = body_result.rstrip("\r\n") + "\n\n" + "\n".join(render_task(t) for t in pending.values())
    if original and body_result.startswith(f"# {original.title}\n"):
        body_result = f"# {group.title}\n" + body_result[len(f"# {original.title}\n"):]
    metadata.update(group.model_dump(mode="json", exclude={"tasks"}))
    metadata.pop("tasks", None)
    stream = io.StringIO()
    _yaml().dump(metadata, stream)
    result = "---\n" + stream.getvalue() + "---\n\n" + body_result
    parse_document(result)
    return result


class ContentStore:
    def __init__(self, root: Path, max_workspace_bytes: int = 2 * 1024 * 1024 * 1024):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_workspace_bytes = max_workspace_bytes

    def workspace_root(self, workspace_id: str) -> Path:
        check_id(workspace_id)
        path = self.root / "workspaces" / workspace_id
        self._within(path, self.root)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def usage(self, workspace_id: str) -> int:
        root = self.workspace_root(workspace_id)
        total = 0
        for path in root.rglob("*"):
            self._within(path, root)
            if path.is_file():
                total += path.stat().st_size
        return total

    def check_capacity(self, workspace_id: str, additional_bytes: int):
        if self.usage(workspace_id) + additional_bytes > self.max_workspace_bytes:
            raise AppError("CAPACITY_EXCEEDED", "工作空间容量不足。原有内容仍可直接查看。", 507)

    @staticmethod
    def _within(path: Path, root: Path):
        if not path.resolve().is_relative_to(root.resolve()):
            raise AppError("INVALID_PATH", "路径不能越出工作空间。", 422)
        current = path
        while current != root and current != current.parent:
            if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
                raise AppError("INVALID_PATH", "工作空间不接受软链接或目录联接。", 422)
            current = current.parent

    def path(self, workspace_id: str, section: str, relative: str) -> Path:
        if section not in {"files", "captures", "versions", "journals", "trash"}:
            raise ValueError("Unknown storage section")
        root = self.workspace_root(workspace_id)
        result = root / section / checked_relative(relative)
        self._within(result, root)
        return result

    @contextmanager
    def lock(self, workspace_id: str, timeout: float = 10):
        check_id(workspace_id)
        directory = self.root / "locks"
        directory.mkdir(exist_ok=True)
        with workspace_lock(directory / f"{workspace_id}.lock", timeout):
            yield

    @staticmethod
    def atomic_bytes(path: Path, content: bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".tmp-{uuid.uuid4()}"
        try:
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            if os.name != "nt":
                fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        finally:
            temporary.unlink(missing_ok=True)

    def write(self, workspace_id: str, section: str, relative: str, content: str):
        self.atomic_bytes(self.path(workspace_id, section, relative), content.encode("utf-8"))

    def read(self, workspace_id: str, section: str, relative: str) -> str:
        return self.path(workspace_id, section, relative).read_text(encoding="utf-8")

    def remove(self, workspace_id: str, section: str, relative: str):
        self.path(workspace_id, section, relative).unlink(missing_ok=True)

    def blob(self, workspace_id: str, content: str) -> str:
        hash_value = digest(content)
        path = self.path(workspace_id, "versions", hash_value + ".md")
        if not path.exists():
            self.atomic_bytes(path, content.encode("utf-8"))
        elif digest(path.read_bytes()) != hash_value:
            raise AppError("STORAGE_CORRUPTED", "版本对象校验失败，已停止写入。", 503)
        return hash_value

    def read_blob(self, workspace_id: str, hash_value: str) -> str:
        if not re.fullmatch("[0-9a-f]{64}", hash_value):
            raise AppError("INPUT_INVALID", "版本标识不合法。", 422)
        content = self.read(workspace_id, "versions", hash_value + ".md")
        if digest(content) != hash_value:
            raise AppError("STORAGE_CORRUPTED", "历史版本校验失败。", 503)
        return content

    def journal(self, workspace_id: str, changeset_id: str, data: dict):
        check_id(changeset_id)
        self.write(workspace_id, "journals", changeset_id + ".json", json.dumps(data, ensure_ascii=False))
