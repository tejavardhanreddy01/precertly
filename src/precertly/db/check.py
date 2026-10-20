"""Read-only check of the configured database: python -m precertly.db.check

Prints the pgvector version, whether each table exists and its row count, and whether this
connection uses TLS. SELECT and SHOW only, in a read-only transaction. It prints no host,
user, database name, password or URL, so its output can go to a log group. Exits 1 if pgvector
is not installed or a table is missing (for example before the migrations have run), 2 if the
database cannot be reached.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from precertly.db.connect import engine_kwargs, wait_for_database
from precertly.db.models import Base
from precertly.settings import get_settings


@dataclass
class Report:
    server_version: str
    vector_version: str | None
    tls: bool | None
    # Table name -> row count, or None when the table does not exist.
    tables: dict[str, int | None] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.vector_version is not None and all(n is not None for n in self.tables.values())

    def lines(self) -> list[str]:
        out = [
            f"server_version: {self.server_version}",
            f"pgvector: {self.vector_version or 'NOT INSTALLED'}",
            f"tls: {'unknown' if self.tls is None else 'yes' if self.tls else 'no'}",
        ]
        out += [
            f"table {name}: {'MISSING' if rows is None else rows}"
            for name, rows in self.tables.items()
        ]
        out.append(f"result: {'ok' if self.ok else 'NOT READY'}")
        return out


async def collect(connection: AsyncConnection) -> Report:
    """Run the checks on an open connection. SELECT and SHOW statements only."""
    version = (await connection.execute(text("SHOW server_version"))).scalar_one()
    vector = (
        await connection.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))
    ).scalar_one_or_none()
    tls = (
        await connection.execute(text("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()"))
    ).scalar_one_or_none()
    report = Report(server_version=str(version), vector_version=vector, tls=tls)
    for table in Base.metadata.sorted_tables:
        exists = (
            await connection.execute(
                text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f'public."{table.name}"'}
            )
        ).scalar_one()
        report.tables[table.name] = (
            (await connection.execute(select(func.count()).select_from(table))).scalar_one()
            if exists
            else None
        )
    return report


async def run_check(engine: AsyncEngine) -> Report:
    await wait_for_database(engine)
    async with engine.connect() as connection:
        # Any write in this transaction fails, whatever the database role may do.
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        report = await collect(connection)
        await connection.rollback()
    return report


async def _main() -> int:
    settings = get_settings()
    engine = create_async_engine(settings.sqlalchemy_url(), **engine_kwargs(settings))
    try:
        try:
            report = await run_check(engine)
        except Exception as error:
            # The class only: a message could carry a host or a user name.
            print(f"cannot check the database: {type(error).__name__}", file=sys.stderr)
            return 2
    finally:
        await engine.dispose()
    print("\n".join(report.lines()))
    return 0 if report.ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    if argv:
        print("usage: python -m precertly.db.check (no arguments)", file=sys.stderr)
        return 2
    return asyncio.run(_main())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
