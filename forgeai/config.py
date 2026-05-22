"""Application settings loaded from environment variables."""

from functools import lru_cache
import logging

from pydantic import Field
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
    frontend_sandbox_image: str = Field(
        default="forgeai-frontend-sandbox:latest",
        validation_alias="FRONTEND_SANDBOX_IMAGE",
    )
    frontend_sandbox_network: str = Field(
        default="bridge",
        validation_alias="FRONTEND_SANDBOX_NETWORK",
    )
    frontend_sandbox_memory_limit: str = Field(
        default="1g",
        validation_alias="FRONTEND_SANDBOX_MEMORY_LIMIT",
    )
    drift_threshold: int = 40
    max_self_retries: int = 2
    redis_url: str = "redis://localhost:6379"
    chroma_host: str = "localhost"
    chroma_port: int = 8001
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "forgeai"
    minio_secret_key: str = "forgeai_dev"
    minio_bucket: str = "forgeai-checkpoints"
    minio_secure: bool = False
    task_memory_ttl: int = 86400

    anthropic_api_key: str = ""
    pool_low_default: str = Field(
        default="claude-haiku-4-5-20251001",
        validation_alias="MODEL_LOW_DEFAULT",
    )
    pool_low_escalated: str = Field(
        default="claude-sonnet-4-6",
        validation_alias="MODEL_LOW_ESCALATED",
    )
    pool_medium_default: str = Field(
        default="claude-sonnet-4-6",
        validation_alias="MODEL_MEDIUM_DEFAULT",
    )
    pool_medium_escalated: str = Field(
        default="claude-sonnet-4-6",
        validation_alias="MODEL_MEDIUM_ESCALATED",
    )
    pool_high_default: str = Field(
        default="claude-sonnet-4-6",
        validation_alias="MODEL_HIGH_DEFAULT",
    )
    pool_high_escalated: str = Field(
        default="claude-opus-4-6",
        validation_alias="MODEL_HIGH_ESCALATED",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance.

    Returns:
        Settings: Loaded application settings.
    """
    return Settings()
