"""Alembic environment: async engine, URL from settings unless the caller sets one."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine

from precertly.db.connect import engine_kwargs, wait_for_database
from precertly.db.models import Base
from precertly.settings import get_settings

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    # Keep loggers that already exist (precertly.db.connect logs each connection retry).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str | URL:
    return config.get_main_option("sqlalchemy.url") or get_settings().sqlalchemy_url()


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url(), **engine_kwargs(get_settings()))
    try:
        # Only the connectivity probe is retried; the migrations below run once.
        await wait_for_database(engine)
        async with engine.connect() as connection:
            await connection.run_sync(_run)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
