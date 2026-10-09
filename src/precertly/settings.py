"""Runtime settings, overridable with PRECERTLY_* environment variables or a .env file."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PRECERTLY_", env_file=".env", extra="ignore")

    bedrock_model_id: str = "amazon.nova-2-lite-v1:0"
    aws_region: str = "us-east-2"
    # Named profile for local use. Set PRECERTLY_AWS_PROFILE= (empty) to use the default
    # credential chain, e.g. a task role.
    aws_profile: str | None = "precertly"


@lru_cache
def get_settings() -> Settings:
    return Settings()
