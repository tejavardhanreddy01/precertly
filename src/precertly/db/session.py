"""Async engine and session factory for the configured database."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from precertly.db.connect import engine_kwargs
from precertly.settings import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(settings.sqlalchemy_url(), **engine_kwargs(settings))


def session_factory(engine: AsyncEngine | None = None) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine or get_engine(), expire_on_commit=False)
