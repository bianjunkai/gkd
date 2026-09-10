"""Keep task actions separate from their reusable Markdown containers."""
import re
import unicodedata

from .errors import AppError

DEFAULT_TASK_GROUP = "日常任务"


def title_key(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold()).strip()


def duplicates_task_title(title, tasks):
    title = title.strip()
    variants = [title, title[:-3]] if title.lower().endswith(".md") else [title]
    for value in variants:
        key = title_key(value)
        for task in tasks:
            task_key = title_key(task.title)
            if key == task_key:
                return True
            # Legacy local proposals shortened their first task to a 60-character title.
            if len(value) >= 40 and value.endswith("…") and task_key.startswith(title_key(value[:-1])):
                return True
    return False


def suggested_group_title(title, tasks):
    """Provider suggestions must not promote the first task to a document title."""
    return DEFAULT_TASK_GROUP if tasks and duplicates_task_title(title, tasks) else title.strip()


def validate_new_group_title(title, tasks, file_name=None):
    file_title = file_name[:-3] if file_name and file_name.lower().endswith(".md") else file_name
    if duplicates_task_title(title, tasks) or (file_title and duplicates_task_title(file_title, tasks)):
        raise AppError("TASK_GROUP_TITLE_REQUIRED", "任务不能直接作为文件标题。请选择已有任务组，或用主题为新任务组命名，例如「合同跟进」「日常任务」。", 422)


def mentions_title(text, title, *, path=False):
    """A complete name is evidence; a shared bigram alone is not a destination."""
    title = unicodedata.normalize("NFKC", title).strip().casefold()
    if len(title) < 2:
        return False
    left = r"(?<![a-z0-9_])" if re.match(r"[a-z0-9_]", title) else ""
    if path:
        left += r"(?<![/\\])"
    right = r"(?![a-z0-9_])" if re.search(r"[a-z0-9_]$", title) else ""
    return bool(re.search(left + re.escape(title) + right, unicodedata.normalize("NFKC", text).casefold()))
