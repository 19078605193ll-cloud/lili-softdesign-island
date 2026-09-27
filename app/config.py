from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    ai_provider_name: str = "openai-compatible"
    ai_timeout_seconds: float = 120.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
