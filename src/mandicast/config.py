"""Settings from the environment and the git-ignored `.env` file. Secrets are SecretStr and never logged."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    data_gov_in_api_key: SecretStr | None = None
    database_url: SecretStr = Field(..., description="postgresql+psycopg://user:password@host:port/db")

    api_base_url: str = "https://api.data.gov.in/resource/35985678-0d79-46b4-9ed6-6f13308a1d24"
    resource_id: str = "35985678-0d79-46b4-9ed6-6f13308a1d24"
    user_agent: str = "MandiCast-ingest/0.2"
    page_size: int = 10_000
    request_pause_s: float = 1.0
    request_timeout_s: int = 300
    request_retries: int = 10

    raw_dir: Path = PROJECT_ROOT / "data" / "raw" / "agmarknet_variety_daily"
    manual_aliases_path: Path = PROJECT_ROOT / "config" / "manual_aliases.csv"
    commodities: list[str] = ["Onion", "Tomato", "Potato", "Wheat", "Soyabean"]
    # Daily runs re-fetch this many recent arrival dates, because mandis report late (Phase 1 profile).
    refetch_days: int = 7

    # Phase 4: serving models and the API
    models_dir: Path = PROJECT_ROOT / "models"
    mlflow_enabled: bool = True  # MLflow tracking + model registry mirror (tests turn it off)
    redis_url: SecretStr | None = Field(None, description="redis://:password@host:port/db; caching and rate limits")
    api_rate_limit_per_minute: int = Field(60, ge=1, le=100_000)
    api_cache_ttl_s: int = Field(3600, ge=0, le=86_400)
    api_cors_origins: str = "http://localhost:5173"  # comma-separated; the dashboard's origin (Phase 7)
    api_trust_forwarded_for: bool = False  # true only behind a reverse proxy you control
    api_statement_timeout_ms: int = Field(5000, ge=100, le=60_000)
    # Phase 8: without it, psycopg waits 130 s per attempt when the database host does not answer (measured on the dev
    # machine), so every request and health check hung for minutes during an outage.
    db_connect_timeout_s: int = Field(5, ge=1, le=120)
    # Least-privilege role for the API and the bot (`mandicast db-roles`, docs/15 §4)
    api_db_role: str = "mandicast_api"
    api_db_password: SecretStr | None = None

    # Phase 6: Telegram bot and alerts
    telegram_bot_token: SecretStr | None = None
    telegram_api_base: str = "https://api.telegram.org"
    dashboard_url: str = "http://localhost:5173"  # where /login links point (Phase 7 dashboard)
    max_alerts_per_user: int = Field(20, ge=1, le=200)
    session_days: int = Field(30, ge=1, le=365)
    login_code_minutes: int = Field(15, ge=1, le=60)
    notification_retention_days: int = Field(365, ge=30, le=3650)

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
