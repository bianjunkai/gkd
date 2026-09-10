from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    data_root: Path = PROJECT_ROOT / ".data"
    database_url: str = ""
    enable_password_auth: bool = True
    run_worker: bool = True
    wechat_app_id: str = ""
    wechat_app_secret: str = ""
    ai_provider: str = "local"
    ai_base_url: str = "https://api.openai.com/v1"
    ai_model: str = ""
    ai_api_key: str = ""
    ai_timeout_seconds: float = Field(default=60, gt=0, le=180)
    ai_daily_request_limit: int = Field(default=100, ge=0, le=10000)
    session_days: int = Field(default=7, ge=1, le=30)
    allowed_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    max_workspace_bytes: int = 2 * 1024 * 1024 * 1024
    worker_poll_seconds: float = 0.3

    @model_validator(mode="after")
    def normalize_paths(self):
        if not self.data_root.is_absolute():
            self.data_root = PROJECT_ROOT / self.data_root
        self.data_root = self.data_root.resolve()
        if not self.database_url:
            self.database_url = f"sqlite:///{(self.data_root / 'gkd.sqlite3').as_posix()}"
        if self.database_url.startswith("postgresql://"):
            self.database_url = self.database_url.replace(
                "postgresql://", "postgresql+psycopg://", 1
            )
        if self.app_env == "production" and self.enable_password_auth:
            raise ValueError("Production requires ENABLE_PASSWORD_AUTH=false and WeChat login.")
        if self.ai_provider not in {"local", "responses"}:
            raise ValueError("AI_PROVIDER must be local or responses")
        return self

    @property
    def external_ai_ready(self) -> bool:
        return bool(self.ai_api_key and self.ai_model and self.ai_provider == "responses")
