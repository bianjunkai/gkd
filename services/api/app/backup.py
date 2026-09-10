"""Offline, local SQLite backups. No overwrite, live backup, encryption, or remote upload."""
import argparse
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Literal
from contextlib import closing

from pydantic import Field
from sqlalchemy.engine import make_url

from .config import Settings
from .db import Base
from .domain import StrictModel, now_iso
from .errors import AppError
from .locking import workspace_lock
from .storage import ContentStore, check_id, checked_relative

MAX_BYTES = 4 * 1024**3
MAX_FILES = 100_000
MANIFEST_LIMIT = 16 * 1024**2
CHUNK = 1024**2
SECTIONS = {"files", "captures", "versions", "journals", "trash"}


class Entry(StrictModel):
    path: str
    bytes: int = Field(ge=0, strict=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Manifest(StrictModel):
    format: Literal["gkd-local-backup"] = "gkd-local-backup"
    version: Literal[1] = 1
    created_at: str
    database: Literal["gkd.sqlite3"] = "gkd.sqlite3"
    entries: list[Entry] = Field(min_length=1, max_length=MAX_FILES)


def invalid(message):
    return AppError("BACKUP_INVALID", message, 422)


def safe_name(value):
    checked_relative(value)
    if value == "gkd.sqlite3":
        return value
    parts = PurePosixPath(value).parts
    if len(parts) < 4 or parts[0] != "workspaces" or parts[2] not in SECTIONS:
        raise invalid("备份包含不支持的数据路径。")
    check_id(parts[1])
    if not parts[1].startswith("workspace-"):
        raise invalid("备份的工作空间标识无效。")
    return value


def check_database(connection):
    if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise invalid("SQLite 完整性检查失败。")
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not set(Base.metadata.tables).issubset(tables):
        raise invalid("数据库与此版本的数据结构不兼容。")
    pending = connection.execute(
        "SELECT count(*) FROM change_sets WHERE status NOT IN ('committed', 'rolled_back')"
    ).fetchone()[0]
    if pending:
        raise invalid("存在未恢复的写入批次。请先启动服务完成恢复，正常停服后再备份。")


def add_file(archive, path, name):
    safe_name(name)
    size, checksum = 0, hashlib.sha256()
    with path.open("rb") as source, archive.open(name, "w", force_zip64=True) as output:
        for chunk in iter(lambda: source.read(CHUNK), b""):
            size += len(chunk)
            if size > MAX_BYTES:
                raise invalid("单个文件超过本地备份容量限制。")
            checksum.update(chunk)
            output.write(chunk)
    return Entry(path=name, bytes=size, sha256=checksum.hexdigest())


def create_backup(settings, destination):
    root = settings.data_root.resolve()
    output = Path(destination).absolute()
    if output.exists() or output.is_symlink():
        raise invalid("备份文件已存在，请使用新文件名；不会覆盖旧备份。")
    if output.resolve().is_relative_to(root):
        raise invalid("备份必须保存在 DATA_ROOT 之外。")
    url = make_url(settings.database_url)
    database = root / "gkd.sqlite3"
    if url.get_backend_name() != "sqlite" or Path(url.database or "").resolve() != database:
        raise invalid("此命令只支持 DATA_ROOT/gkd.sqlite3；PostgreSQL 需要独立备份方案。")
    if not database.is_file():
        raise invalid("没有可备份的数据库，请先启动并使用本地服务。")
    ContentStore._within(database, root)
    output.parent.mkdir(parents=True, exist_ok=True)
    with workspace_lock(root / "server.lock", timeout=0.2):
        with tempfile.TemporaryDirectory(prefix=".gkd-backup-", dir=output.parent) as temporary:
            stage = Path(temporary)
            snapshot = stage / "gkd.sqlite3"
            # The SQLite backup API includes committed WAL records; copying just the DB would not.
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(snapshot)) as copied:
                    source.backup(copied)
                    check_database(copied)
                    copied.execute("PRAGMA journal_mode=DELETE")
            archive_path = stage / "backup.zip"
            entries, total = [], 0
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
                entries.append(add_file(archive, snapshot, "gkd.sqlite3"))
                total += entries[0].bytes
                for path in sorted((root / "workspaces").rglob("*")):
                    ContentStore._within(path, root)
                    relative = path.relative_to(root).as_posix()
                    parts = PurePosixPath(relative).parts
                    if (not path.is_file() or len(parts) < 4 or parts[2] not in SECTIONS
                            or path.name.startswith(".tmp-")):
                        continue
                    entries.append(add_file(archive, path, relative))
                    total += entries[-1].bytes
                    if len(entries) > MAX_FILES or total > MAX_BYTES:
                        raise invalid("备份超过 4 GiB 或 100,000 个文件的本地 MVP 限制。")
                manifest = Manifest(created_at=now_iso(), entries=entries)
                encoded = manifest.model_dump_json(indent=2).encode("utf-8")
                if len(encoded) > MANIFEST_LIMIT:
                    raise invalid("备份清单超过大小限制。")
                archive.writestr("manifest.json", encoded)
            with archive_path.open("rb+") as handle:
                os.fsync(handle.fileno())
            # Same-volume hard link publishes the completed archive and fails if output exists.
            os.link(archive_path, output)
    return {"path": str(output), "files": len(entries), "content_bytes": total,
            "encrypted": False, "message": "备份包含账号与原文，请妥善保管；服务端 .env 不在备份中。"}


def inspect_archive(archive):
    infos = archive.infolist()
    if len(infos) > MAX_FILES + 1 or sum(info.file_size for info in infos) > MAX_BYTES + MANIFEST_LIMIT:
        raise invalid("备份超过解压容量或文件数量限制。")
    names = [info.filename for info in infos]
    if len({name.casefold() for name in names}) != len(names) or "manifest.json" not in names:
        raise invalid("备份存在重复路径或缺少清单。")
    for info in infos:
        mode = info.external_attr >> 16
        if (info.is_dir() or stat.S_IFMT(mode) not in {0, stat.S_IFREG} or info.flag_bits & 1
                or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
            raise invalid("不接受目录、链接、加密项或不支持的压缩格式。")
        if info.filename != "manifest.json":
            safe_name(info.filename)
    if archive.getinfo("manifest.json").file_size > MANIFEST_LIMIT:
        raise invalid("备份清单过大。")
    manifest = Manifest.model_validate_json(archive.read("manifest.json"))
    indexed = {entry.path: entry for entry in manifest.entries}
    if (len(indexed) != len(manifest.entries) or set(indexed) != set(names) - {"manifest.json"}
            or manifest.database not in indexed):
        raise invalid("备份清单与实际文件不一致。")
    if sum(entry.bytes for entry in manifest.entries) > MAX_BYTES:
        raise invalid("备份超过解压容量限制。")
    for entry in manifest.entries:
        if entry.bytes != archive.getinfo(entry.path).file_size:
            raise invalid("备份文件大小与清单不一致。")
    return manifest


def restore_backup(archive_path, destination):
    target = Path(destination).absolute()
    if target == target.parent or target.exists() or target.is_symlink():
        raise invalid("只允许恢复到尚不存在的新目录；不会覆盖现有数据。")
    if not target.parent.is_dir():
        raise invalid("请先创建恢复目录的父目录。")
    with zipfile.ZipFile(archive_path) as archive:
        manifest = inspect_archive(archive)
        with tempfile.TemporaryDirectory(prefix=".gkd-restore-", dir=target.parent) as temporary:
            stage = Path(temporary)
            data = stage / "data"
            data.mkdir()
            for entry in manifest.entries:
                path = data / safe_name(entry.path)
                ContentStore._within(path, data)
                path.parent.mkdir(parents=True, exist_ok=True)
                size, checksum = 0, hashlib.sha256()
                with archive.open(entry.path) as source, path.open("xb") as output:
                    for chunk in iter(lambda: source.read(CHUNK), b""):
                        size += len(chunk)
                        if size > entry.bytes:
                            raise invalid("解压后的文件超过清单大小。")
                        checksum.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if size != entry.bytes or checksum.hexdigest() != entry.sha256:
                    raise invalid("备份校验和不匹配，恢复未生效。")
            with closing(sqlite3.connect(data / "gkd.sqlite3")) as connection:
                check_database(connection)
                # A restored backup must not resurrect old bearer sessions.
                connection.execute("DELETE FROM sessions")
                connection.commit()
                connection.execute("PRAGMA journal_mode=DELETE")
            if target.exists() or target.is_symlink():
                raise invalid("目标目录已出现，恢复已停止。")
            data.rename(target)
    return {"path": str(target.resolve()), "files": len(manifest.entries),
            "message": "恢复完成。旧会话已失效；请设置 DATA_ROOT 到此目录并重新登录。"}


def main():
    parser = argparse.ArgumentParser(description="归刻本地 SQLite 停服备份与恢复（未加密）")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="停止 API 后，备份到一个新 ZIP 文件")
    create.add_argument("--output", type=Path, required=True)
    restore = commands.add_parser("restore", help="校验备份并恢复到一个不存在的新目录")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = (create_backup(Settings(), args.output) if args.command == "create"
                  else restore_backup(args.archive, args.destination))
    except (AppError, OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as exc:
        message = ("API 或其他维护进程仍在运行，请先正常停服。"
                   if isinstance(exc, AppError) and exc.code == "WORKSPACE_BUSY"
                   else exc.message if isinstance(exc, AppError) else str(exc))
        parser.exit(1, message + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
