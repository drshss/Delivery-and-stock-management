"""Application configuration loaded from environment variables / .env file."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # App
    PROJECT_NAME: str = "Gas Cylinder Delivery & Stock Management"
    VERSION: str = "1.0.0"
    API_V1_PREFIX: str = "/api/v1"

    # Security
    SECRET_KEY: str = "change-this-secret-key-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1 day

    # Database
    DATABASE_URL: str = "sqlite:///./delivery_app.db"

    # File uploads (delivery evidence)
    UPLOAD_DIR: str = "uploads"
    MAX_UPLOAD_SIZE_MB: int = 10

    # CORS — allow the web app & android app to call the API
    BACKEND_CORS_ORIGINS: list[str] = ["*"]

    # Bootstrap admin
    FIRST_ADMIN_EMAIL: str = "admin@delivery.com"
    FIRST_ADMIN_PASSWORD: str = "admin123"
    FIRST_ADMIN_NAME: str = "System Admin"


settings = Settings()
