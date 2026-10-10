"""Runtime settings, overridable with PRECERTLY_* environment variables or a .env file."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PRECERTLY_", env_file=".env", extra="ignore")

    # Inference profile id: Bedrock rejects the bare model id for on-demand use.
    bedrock_model_id: str = "us.amazon.nova-2-lite-v1:0"
    aws_region: str = "us-east-2"
    # Named profile for local use. Set PRECERTLY_AWS_PROFILE= (empty) to use the default
    # credential chain, e.g. a task role.
    aws_profile: str | None = "precertly"

    embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    embedding_dimensions: int = 1024
    # Paces embedding calls to the account's on-demand quota. New accounts are often held
    # to 60 requests/minute (the AWS default is 6000); raise this to match yours, or set
    # it empty to disable pacing.
    embedding_requests_per_minute: int | None = 60

    # Matches docker-compose.yml (host port 5433).
    database_url: str = "postgresql+asyncpg://precertly:precertly@localhost:5433/precertly"


@lru_cache
def get_settings() -> Settings:
    return Settings()
