"""python -m precertly.db.check: read-only, and it prints no connection values."""

from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from precertly.db import check
from precertly.db.check import Report, collect, run_check
from precertly.db.models import Base

SOURCE = Path(check.__file__)


def run(coro: Any) -> Any:
    async def inner() -> Any:
        return await coro

    return asyncio.run(inner())


def test_report_lines_and_result() -> None:
    ready = Report("16.13", "0.8.1", True, {"cases": 3, "runs": 0})
    assert ready.ok
    assert ready.lines() == [
        "server_version: 16.13",
        "pgvector: 0.8.1",
        "tls: yes",
        "table cases: 3",
        "table runs: 0",
        "result: ok",
    ]


def test_missing_extension_or_table_is_not_ready() -> None:
    assert not Report("16", None, None, {"cases": 0}).ok
    assert not Report("16", "0.8.1", False, {"cases": None}).ok
    lines = Report("16", None, None, {"cases": None}).lines()
    assert "pgvector: NOT INSTALLED" in lines
    assert "tls: unknown" in lines
    assert "table cases: MISSING" in lines
    assert lines[-1] == "result: NOT READY"


def test_only_select_and_show_statements_are_written() -> None:
    """Every SQL string in the module starts with SELECT, SHOW or the READ ONLY setting."""
    statements = [
        node.args[0].value
        for node in ast.walk(ast.parse(SOURCE.read_text()))
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "text"
        and isinstance(node.args[0], ast.Constant)
    ]
    assert statements
    for statement in statements:
        assert re.match(r"(SELECT|SHOW|SET TRANSACTION READ ONLY)\b", statement), statement


def test_collect_reports_the_migrated_database(migrated_db: str) -> None:
    async def go() -> Report:
        engine = create_async_engine(migrated_db, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                return await collect(connection)
        finally:
            await engine.dispose()

    report = run(go())
    assert report.vector_version
    assert set(report.tables) == {t.name for t in Base.metadata.sorted_tables}
    assert all(isinstance(n, int) for n in report.tables.values())
    assert isinstance(report.tls, bool)
    assert report.ok


def test_a_write_in_the_check_transaction_fails(migrated_db: str) -> None:
    async def go() -> None:
        engine = create_async_engine(migrated_db, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                await connection.execute(text("CREATE TEMP TABLE should_fail (x int)"))
        finally:
            await engine.dispose()

    with pytest.raises(Exception, match="read-only transaction"):
        run(go())


def test_run_check_leaves_nothing_open(migrated_db: str) -> None:
    async def go() -> Report:
        engine = create_async_engine(migrated_db, poolclass=NullPool)
        try:
            return await run_check(engine)
        finally:
            await engine.dispose()

    assert run(go()).ok


def test_output_has_no_connection_values(
    migrated_db: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "pa55w0rd-that-must-not-print"
    url = migrated_db.replace("precertly:precertly@", f"precertly:{secret}@", 1)
    # The test database accepts the real password only, so connect with it and inject the
    # secret as a second, unused setting that a careless print could echo.
    monkeypatch.setenv("PRECERTLY_DATABASE_URL", migrated_db)
    monkeypatch.setenv("PRECERTLY_DB_PASSWORD", secret)
    from precertly.settings import get_settings

    get_settings.cache_clear()
    try:
        assert check.main([]) == 0
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr()
    for value in (secret, "localhost", "5433", "precertly_test", "precertly:", url):
        assert value not in out.out + out.err


def test_unreachable_database_exits_2_and_names_no_host(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from precertly.settings import get_settings

    monkeypatch.setenv("PRECERTLY_DATABASE_URL", "postgresql+asyncpg://u:hunter2@127.0.0.1:1/x")
    monkeypatch.setenv("PRECERTLY_DB_CONNECT_TIMEOUT", "1")

    async def no_sleep(seconds: float) -> None:
        return None

    real = check.wait_for_database

    async def quick(engine: Any, **kw: Any) -> None:
        await real(engine, sleep=no_sleep)

    monkeypatch.setattr(check, "wait_for_database", quick)
    get_settings.cache_clear()
    try:
        assert check.main([]) == 2
    finally:
        get_settings.cache_clear()
    err = capsys.readouterr().err
    assert err.strip() == "cannot check the database: ConnectionRefusedError"
    assert "hunter2" not in err and "127.0.0.1" not in err


def test_arguments_are_refused(capsys: pytest.CaptureFixture[str]) -> None:
    assert check.main(["--write"]) == 2
