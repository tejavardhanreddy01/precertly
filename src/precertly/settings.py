"""Runtime settings, overridable with PRECERTLY_* environment variables or a .env file."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

# Matches docker-compose.yml (host port 5433).
DEFAULT_DATABASE_URL = "postgresql+asyncpg://precertly:precertly@localhost:5433/precertly"
DATABASE_DRIVER = "postgresql+asyncpg"
DEFAULT_DB_PORT = 5432


class DatabaseSettingsError(ValueError):
    """The database settings are incomplete or unusable. Never carries a password."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PRECERTLY_", env_file=".env", extra="ignore")

    # Inference profile id: Bedrock rejects the bare model id for on-demand use.
    bedrock_model_id: str = "us.amazon.nova-2-lite-v1:0"
    aws_region: str = "us-east-2"
    # Named profile for local use. Set PRECERTLY_AWS_PROFILE= (empty) to use the default
    # credential chain, e.g. a task role.
    aws_profile: str | None = "precertly"
    # Paces Converse calls to the account's requests-per-minute quota for the model
    # (20 on this account for the Nova 2 Lite inference profile). Set empty to disable.
    llm_requests_per_minute: int | None = 20

    embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    embedding_dimensions: int = 1024
    # Paces embedding calls to the account's on-demand quota. New accounts are often held
    # to 60 requests/minute (the AWS default is 6000); raise this to match yours, or set
    # it empty to disable pacing.
    embedding_requests_per_minute: int | None = 60

    # Judgment: chart chunks retrieved per criterion.
    judgment_top_k: int = 8
    # Judgment prompt. 3 is the default; 4 (line-level evidence handles) is experimental
    # and opt-in: experiment 2 in evals/results/NOTES.md, which failed its pre-registration.
    prompt_version: Literal["3", "4"] = "3"

    # Database connection, in order of precedence (see sqlalchemy_url): database_url, then
    # the separate parts, then the local compose default. The URL and password are secrets,
    # so they stay out of repr and logs.
    database_url: SecretStr | None = None
    db_host: str | None = None
    db_port: int | None = None
    db_name: str | None = None
    db_user: str | None = None
    db_password: SecretStr | None = None

    def sqlalchemy_url(self) -> URL:
        """The database URL for create_async_engine and Alembic.

        Pass the URL object itself: str() of it hides the password, and the driver reads the
        real one from the object. Blank values count as unset.
        """
        url = _text(self.database_url)
        if url:
            try:
                return make_url(url)
            except Exception:
                # SQLAlchemy's message is safe today, but this one is ours to keep safe.
                raise DatabaseSettingsError(
                    "PRECERTLY_DATABASE_URL is not a valid SQLAlchemy URL"
                ) from None

        host, name, user = _text(self.db_host), _text(self.db_name), _text(self.db_user)
        password = _text(self.db_password)
        if not (host or name or user or password or self.db_port is not None):
            return make_url(DEFAULT_DATABASE_URL)

        missing = [
            f"PRECERTLY_DB_{part}"
            for part, value in (
                ("HOST", host),
                ("NAME", name),
                ("USER", user),
                ("PASSWORD", password),
            )
            if not value
        ]
        if missing:
            raise DatabaseSettingsError(
                "PRECERTLY_DATABASE_URL is not set and some of the separate database settings "
                f"are, so these are also required: {', '.join(missing)} "
                f"(PRECERTLY_DB_PORT defaults to {DEFAULT_DB_PORT})"
            )
        return URL.create(
            DATABASE_DRIVER,
            username=user,
            password=password,
            host=host,
            port=self.db_port if self.db_port is not None else DEFAULT_DB_PORT,
            database=name,
        )


def _text(value: str | SecretStr | None) -> str | None:
    """The plain text of a setting, or None when it is unset or blank."""
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return value if value and value.strip() else None


@lru_cache
def get_settings() -> Settings:
    return Settings()
