"""Application settings loaded from environment variables."""

from functools import lru_cache
import logging

from pydantic_settings import BaseSettings, SettingsConfigDict

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)


class Settings(BaseSettings):
    """Configuration values for ForgeAI."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://forgeai:forgeai_dev@localhost:5432/forgeai"
    sandbox_image: str = "python:3.11-slim"
    sandbox_cpu_limit: float = 1.0
    sandbox_memory_limit: str = "256m"
    sandbox_timeout_low: int = 60
    sandbox_timeout_medium: int = 180
    sandbox_timeout_high: int = 600
    sandbox_working_dir: str = "/sandbox"
    drift_threshold: int = 40
    max_self_retries: int = 2
    redis_url: str = "redis://localhost:6379"
    chroma_host: str = "localhost"
    chroma_port: int = 8000
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "forgeai"
    minio_secret_key: str = "forgeai_dev"
    minio_bucket: str = "forgeai-checkpoints"
    minio_secure: bool = False
    task_memory_ttl: int = 86400


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance.

    Returns:
        Settings: Loaded application settings.
    """
    return Settings()
