import hashlib
import hmac
import secrets
import time
import unicodedata

import httpx
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from .config import Settings
from .db import Database, Folder, LoginSession, User, Workspace
from .domain import digest, new_id
from .errors import AppError
from .storage import ContentStore


def password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    value = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1, dklen=32)
    return salt + ":" + value.hex()


def password_matches(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        salt, expected = stored.split(":")
        value = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(value.hex(), expected)
    except (ValueError, TypeError):
        return False


def workspace_json(workspace: Workspace) -> dict:
    return {
        "id": workspace.id,
        "name": workspace.name,
        "timezone": workspace.timezone,
        "ai_enabled": workspace.ai_enabled,
    }


class AuthService:
    def __init__(self, db: Database, store: ContentStore, settings: Settings):
        self.db, self.store, self.settings = db, store, settings

    def _local_enabled(self):
        if not self.settings.enable_password_auth:
            raise AppError("AUTH_METHOD_DISABLED", "当前环境请使用微信登录。", 403)

    def register(self, username: str, password: str, display_name: str) -> dict:
        self._local_enabled()
        username = unicodedata.normalize("NFKC", username.strip()).casefold()
        if not 3 <= len(username) <= 80 or not all(c.isalnum() or c in "_.@-" for c in username):
            raise AppError("INPUT_INVALID", "账号应为 3—80 个字母、数字或常用账号字符。", 422)
        if not 8 <= len(password) <= 128:
            raise AppError("INPUT_INVALID", "密码长度应为 8—128 个字符。", 422)
        if not display_name.strip() or len(display_name.strip()) > 60:
            raise AppError("INPUT_INVALID", "请填写 1—60 个字符的称呼。", 422)
        try:
            with self.db.session() as session:
                existing = session.scalar(select(User).where(User.username == username))
                if existing:
                    raise AppError("USERNAME_EXISTS", "该账号已存在，请直接登录。", 409)
                user = User(
                    id=new_id("user"),
                    username=username,
                    display_name=display_name.strip(),
                    password_hash=password_hash(password),
                )
                session.add(user)
                workspace = self._create_workspace(session, user)
                return self._new_session(session, user, workspace)
        except IntegrityError as exc:
            raise AppError("USERNAME_EXISTS", "该账号已存在，请直接登录。", 409) from exc

    def _create_workspace(self, session, user: User) -> Workspace:
        workspace = Workspace(id=new_id("workspace"), user_id=user.id)
        session.add(workspace)
        session.flush()
        root = self.store.workspace_root(workspace.id)
        for name in ("files", "captures", "versions", "journals", "exports", "trash"):
            (root / name).mkdir(exist_ok=True)
        for name in ("工作", "个人"):
            folder = Folder(
                id=new_id("folder"),
                workspace_id=workspace.id,
                name=name,
                path=name,
                canonical_path=name.casefold(),
            )
            session.add(folder)
        return workspace

    def _new_session(self, session, user: User, workspace: Workspace) -> dict:
        token = secrets.token_urlsafe(32)
        session.add(
            LoginSession(
                token_hash=digest(token),
                user_id=user.id,
                expires_at=time.time() + self.settings.session_days * 86400,
            )
        )
        return {
            "token": token,
            "user": {"id": user.id, "display_name": user.display_name, "username": user.username},
            "workspace": workspace_json(workspace),
        }

    def login(self, username: str, password: str) -> dict:
        self._local_enabled()
        username = unicodedata.normalize("NFKC", username.strip()).casefold()
        with self.db.session() as session:
            user = session.scalar(select(User).where(User.username == username))
            if not user or user.deleted_at or not password_matches(password, user.password_hash):
                raise AppError("INVALID_CREDENTIALS", "账号或密码不正确。", 401)
            workspace = session.scalar(select(Workspace).where(Workspace.user_id == user.id))
            if not workspace or workspace.deleted_at:
                raise AppError("WORKSPACE_DELETED", "工作空间已删除。", 403)
            return self._new_session(session, user, workspace)

    def wechat_login(self, code: str) -> dict:
        if not self.settings.wechat_app_id or not self.settings.wechat_app_secret:
            raise AppError("WECHAT_NOT_CONFIGURED", "尚未配置小程序 AppID 和服务端密钥。", 503)
        try:
            response = httpx.get(
                "https://api.weixin.qq.com/sns/jscode2session",
                params={
                    "appid": self.settings.wechat_app_id,
                    "secret": self.settings.wechat_app_secret,
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
                timeout=15,
            )
            response.raise_for_status()
            result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AppError("WECHAT_UNAVAILABLE", "微信登录暂时不可用，请重新尝试。", 503, retryable=True) from exc
        openid = result.get("openid")
        if not isinstance(openid, str) or result.get("errcode"):
            raise AppError("WECHAT_LOGIN_FAILED", "微信登录凭据已失效，请重新登录。", 401)
        with self.db.session() as session:
            user = session.scalar(select(User).where(User.wechat_openid == openid))
            if user and user.deleted_at:
                raise AppError("ACCOUNT_DELETED", "该账号已删除。", 403)
            if not user:
                user = User(
                    id=new_id("user"),
                    username="wx_" + digest(openid),
                    display_name="微信用户",
                    wechat_openid=openid,
                )
                session.add(user)
                workspace = self._create_workspace(session, user)
            else:
                workspace = session.scalar(select(Workspace).where(Workspace.user_id == user.id))
            if not workspace or workspace.deleted_at:
                raise AppError("WORKSPACE_DELETED", "工作空间已删除。", 403)
            return self._new_session(session, user, workspace)

    def identity(self, token: str) -> tuple[User, Workspace]:
        if not token or len(token) > 256:
            raise AppError("SESSION_EXPIRED", "请先登录。", 401)
        with self.db.session() as session:
            login = session.get(LoginSession, digest(token))
            if not login or login.expires_at < time.time():
                raise AppError("SESSION_EXPIRED", "登录已过期，请重新登录。", 401)
            user = session.get(User, login.user_id)
            workspace = session.scalar(select(Workspace).where(Workspace.user_id == login.user_id))
            if not user or user.deleted_at or not workspace or workspace.deleted_at:
                raise AppError("SESSION_EXPIRED", "当前账号或工作空间已不可用。", 401)
            return user, workspace

    def logout(self, token: str):
        with self.db.session() as session:
            session.execute(delete(LoginSession).where(LoginSession.token_hash == digest(token)))
