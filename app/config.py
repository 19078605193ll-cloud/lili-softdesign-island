from functools import lru_cache
import os

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "软件设计师 AI 智能刷题系统"
    app_env: str = "development"
    database_url: str = (
        "postgresql+psycopg://postgres:postgres@localhost:5432/software_designer"
    )
    sql_echo: bool = False
    import_storage_root: str = "var/imports"
    import_max_file_size_mb: int = 50
    pdf_render_dpi: int = 200
    import_use_local_ocr: bool = True
    libreoffice_path: str = "soffice"
    ai_base_url: str | None = None
    ai_api_key: str | None = None
    ai_vision_model: str | None = None
    ai_classification_model: str | None = None
    ai_text_model: str | None = None
    ai_tutor_model: str | None = None
    h5_enabled: bool = True
    learning_ai_enabled: bool = True
    ai_provider_name: str = "openai-compatible"
    ai_timeout_seconds: float = 120.0
    redis_url: str = "redis://127.0.0.1:6379/0"
    celery_broker_url: str = "redis://127.0.0.1:6379/1"
    public_origin: str = "http://127.0.0.1:8000"
    allow_insecure_http: bool = False
    session_ttl_seconds: int = 28800
    allowed_image_hosts: list[str] = ["cdn-mineru.openxlab.org.cn"]
    writes_enabled: bool = True
    tasks_enabled: bool = True
    learning_enabled: bool = True

    @property
    def secure_cookie(self) -> bool:
        return self.app_env == "production" and self.public_origin.startswith("https://")

    @property
    def session_cookie(self) -> str:
        return (
            "__Host-island-session"
            if self.secure_cookie
            else "island-session"
        )

    @model_validator(mode="after")
    def validate_production(self):
        if self.app_env == "production" and not self.public_origin.startswith(
            "https://"
        ) and not (self.allow_insecure_http and self.public_origin.startswith("http://")):
            raise ValueError("Production PUBLIC_ORIGIN must use HTTPS")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings(_env_file=None if os.environ.get("APP_ENV") == "test" else ".env")
