from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "KPI & MIS Reporting Hub"
    database_url: str = "postgresql+psycopg2://kpi:kpi@localhost:5432/kpi_hub"

    # Celery / Redis
    redis_url: str = "redis://localhost:6379/0"
    celery_always_eager: bool = False          # True = run tasks inline (dev/tests, no Redis)
    consolidation_schedule_minutes: int = 60   # Celery beat: auto-consolidate open periods

    # Blob storage: Azure if connection string set, else local folder
    azure_storage_connection_string: str | None = None
    azure_container: str = "kpi-hub"
    local_storage_dir: str = "./storage"

    # LLM (optional; deterministic fallback used when not configured)
    llm_provider: str = "none"                 # none | openai | azure
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = None
    azure_openai_deployment: str | None = None
    azure_openai_api_version: str = "2024-10-21"

    max_upload_mb: int = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
