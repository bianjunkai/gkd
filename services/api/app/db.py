import time
from contextlib import contextmanager

from sqlalchemy import JSON, Boolean, Float, Integer, String, Text, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import Settings


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    wechat_openid: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    deleted_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="我的工作空间")
    timezone: Mapped[str] = mapped_column(String(100), default="Asia/Shanghai")
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_consent_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    deleted_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class LoginSession(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(80), index=True)
    expires_at: Mapped[float] = mapped_column(Float)


class Folder(Base):
    __tablename__ = "folders"
    __table_args__ = (UniqueConstraint("workspace_id", "canonical_path"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    path: Mapped[str] = mapped_column(String(1200))
    canonical_path: Mapped[str] = mapped_column(String(1200))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class CaptureRecord(Base):
    __tablename__ = "capture_records"
    __table_args__ = (UniqueConstraint("workspace_id", "client_capture_id"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), index=True)
    client_capture_id: Mapped[str] = mapped_column(String(120))
    content_hash: Mapped[str] = mapped_column(String(64))
    source_type: Mapped[str] = mapped_column(String(30), default="text")
    status: Mapped[str] = mapped_column(String(40), default="unprocessed", index=True)
    search_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    deleted_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    previous_status: Mapped[str | None] = mapped_column(String(40), nullable=True)


class GroupRecord(Base):
    __tablename__ = "group_index"
    __table_args__ = (UniqueConstraint("workspace_id", "canonical_path"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), index=True)
    folder_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    path: Mapped[str] = mapped_column(String(1200))
    canonical_path: Mapped[str] = mapped_column(String(1200))
    title: Mapped[str] = mapped_column(String(300))
    revision: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30))
    search_text: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)
    deleted_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class TaskIndex(Base):
    __tablename__ = "task_index"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), index=True)
    group_id: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(30), index=True)
    data: Mapped[dict] = mapped_column(JSON)


class Proposal(Base):
    __tablename__ = "proposals"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), index=True)
    capture_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending_confirmation")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class ChangeSet(Base):
    __tablename__ = "change_sets"
    __table_args__ = (UniqueConstraint("workspace_id", "request_key"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), index=True)
    request_key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(60))
    summary: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(40), default="prepared")
    operations: Mapped[list] = mapped_column(JSON)
    effects: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    undone_by: Mapped[str | None] = mapped_column(String(80), nullable=True)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("workspace_id", "request_key"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), index=True)
    request_key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    run_after: Mapped[float] = mapped_column(Float, default=time.time)
    lease_until: Mapped[float | None] = mapped_column(Float, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(80), nullable=True)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), index=True)
    action: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str] = mapped_column(String(80))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Database:
    def __init__(self, settings: Settings):
        settings.data_root.mkdir(parents=True, exist_ok=True)
        sqlite = settings.database_url.startswith("sqlite")
        self.engine = create_engine(
            settings.database_url,
            connect_args={"check_same_thread": False, "timeout": 30} if sqlite else {},
            pool_pre_ping=True,
        )
        if sqlite:
            @event.listens_for(self.engine, "connect")
            def sqlite_pragmas(connection, _):
                cursor = connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=FULL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
        self.factory = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self):
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self):
        with self.factory() as session:
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def dispose(self):
        self.engine.dispose()
