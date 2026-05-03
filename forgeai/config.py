"""Application settings loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration values for ForgeAI."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://forgeai:forgeai_dev@localhost:5432/forgeai"


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance.

    Returns:
        Settings: Loaded application settings.
    """
    return Settings()
