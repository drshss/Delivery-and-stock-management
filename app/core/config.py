"""Application configuration loaded from environment variables / .env file."""
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Insecure development defaults that MUST NOT be used in production.
_INSECURE_SECRET_KEY = "change-this-secret-key-in-production"
_INSECURE_ADMIN_PASSWORD = "admin123"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Runtime environment: "development" relaxes checks, "production" enforces them.
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"

    # App
    PROJECT_NAME: str = "Gas Cylinder Delivery & Stock Management"
    VERSION: str = "1.0.0"
    API_V1_PREFIX: str = "/api/v1"

    # Security
    SECRET_KEY: str = _INSECURE_SECRET_KEY
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30                # short-lived access token
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7      # 7-day refresh token

    # Login brute-force protection (per client IP, counts FAILED attempts).
    LOGIN_MAX_FAILED_ATTEMPTS: int = 10
    LOGIN_ATTEMPT_WINDOW_SECONDS: int = 300              # 5 minutes

    # Logging / observability
    LOG_LEVEL: str = "INFO"
    # "json" (structured, cloud-friendly) or "console" (human-readable).
    # Empty = auto: json in production, console otherwise.
    LOG_FORMAT: str = ""
    # Optional error tracking. Leave blank to disable; set to your Sentry DSN to enable
    # (requires `pip install sentry-sdk`).
    SENTRY_DSN: str = ""


    # Database — PostgreSQL in production, e.g.
    #   postgresql+psycopg://user:password@host:5432/dbname
    DATABASE_URL: str = "sqlite:///./delivery_app.db"
    # Connection-pool tuning (ignored for SQLite).
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_RECYCLE: int = 1800  # recycle connections after 30 min
    DB_POOL_TIMEOUT: int = 30

    # File uploads (delivery evidence) — stored in the database, capped by size.
    MAX_UPLOAD_SIZE_MB: int = 10

    # CORS — comma-separated list of allowed origins, or "*" for any.
    BACKEND_CORS_ORIGINS: str = "*"

    # Bootstrap admin
    FIRST_ADMIN_EMAIL: str = "admin@delivery.com"
    FIRST_ADMIN_PASSWORD: str = _INSECURE_ADMIN_PASSWORD
    FIRST_ADMIN_NAME: str = "System Admin"

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    @property
    def log_format(self) -> str:
        """Resolved log format: explicit LOG_FORMAT, else json in prod / console in dev."""
        fmt = self.LOG_FORMAT.strip().lower()
        if fmt in ("json", "console"):
            return fmt
        return "json" if self.is_production else "console"

    @property
    def cors_origins(self) -> list[str]:
        """Parse BACKEND_CORS_ORIGINS into a clean list of origins."""
        raw = self.BACKEND_CORS_ORIGINS.strip()
        if raw in ("", "*"):
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @model_validator(mode="after")
    def _enforce_production_safety(self) -> "Settings":
        """Fail fast if production is misconfigured with insecure defaults."""
        if not self.is_production:
            return self

        problems: list[str] = []
        if self.SECRET_KEY == _INSECURE_SECRET_KEY or len(self.SECRET_KEY) < 32:
            problems.append("SECRET_KEY must be set to a strong random value (>=32 chars)")
        if self.FIRST_ADMIN_PASSWORD == _INSECURE_ADMIN_PASSWORD or len(self.FIRST_ADMIN_PASSWORD) < 10:
            problems.append("FIRST_ADMIN_PASSWORD must be set to a strong value (>=10 chars)")
        if self.is_sqlite:
            problems.append("DATABASE_URL must point to PostgreSQL in production, not SQLite")
        if "*" in self.cors_origins:
            problems.append("BACKEND_CORS_ORIGINS must list explicit origins in production")

        if problems:
            raise ValueError(
                "Insecure production configuration:\n  - " + "\n  - ".join(problems)
            )
        return self


settings = Settings()

