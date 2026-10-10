"""Database fixtures. DB tests run against a real Postgres with pgvector.

Locally they skip when the test database is not reachable (start it with
`docker compose up -d db`); in CI an unreachable database is a failure.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

TEST_DATABASE_URL = os.environ.get(
    "PRECERTLY_TEST_DATABASE_URL",
    "postgresql+asyncpg://precertly:precertly@localhost:5433/precertly_test",
)

DbRunner = Callable[[Callable[[AsyncSession], Awaitable[Any]]], Any]


async def _reset_schema() -> None:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def migrated_db() -> str:
    """Empty test database migrated to head, so the tests exercise the real migration."""
    try:
        asyncio.run(_reset_schema())
    except OSError as error:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"test database not reachable ({error}); run `docker compose up -d db`")
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    config.attributes["configure_logging"] = False
    command.upgrade(config, "head")
    return TEST_DATABASE_URL


@pytest.fixture
def db(migrated_db: str) -> DbRunner:
    """Run an async function with a session; everything it writes is rolled back."""

    def run(work: Callable[[AsyncSession], Awaitable[Any]]) -> Any:
        async def scoped() -> Any:
            engine = create_async_engine(migrated_db, poolclass=NullPool)
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    try:
                        return await work(session)
                    finally:
                        await session.rollback()
            finally:
                await engine.dispose()

        return asyncio.run(scoped())

    return run
