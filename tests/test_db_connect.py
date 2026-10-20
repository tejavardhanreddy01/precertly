"""Engine arguments, the first-connect retry and the places that must use them."""

from __future__ import annotations

import asyncio
import errno
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from asyncpg import exceptions as pg
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.pool import NullPool, QueuePool

from precertly.api.main import app
from precertly.db import connect, session
from precertly.db.connect import (
    RETRY_DELAYS,
    engine_kwargs,
    is_transient_connect_error,
    retry_transient,
    wait_for_database,
)
from precertly.settings import Settings, get_settings

ROOT = Path(__file__).resolve().parent.parent


def make_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for name in ("DB_POOL", "DB_CONNECT_TIMEOUT"):
        monkeypatch.delenv(f"PRECERTLY_{name}", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(f"PRECERTLY_{name}", value)
    return Settings(_env_file=None)


# --- settings -------------------------------------------------------------------------------


def test_defaults_keep_todays_behaviour(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(monkeypatch)
    assert settings.db_pool == "queue"
    assert settings.db_connect_timeout == 60.0


def test_pool_and_timeout_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(monkeypatch, DB_POOL="null", DB_CONNECT_TIMEOUT="45.5")
    assert settings.db_pool == "null"
    assert settings.db_connect_timeout == 45.5


@pytest.mark.parametrize("value", ["", "pool", "NULL", "none"])
def test_invalid_pool_fails_with_a_clear_message(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    with pytest.raises(ValueError, match="PRECERTLY_DB_POOL must be 'queue' or 'null'"):
        make_settings(monkeypatch, DB_POOL=value)


@pytest.mark.parametrize("value", ["0", "-1", "-0.5", "abc", "", "nan", "inf"])
def test_invalid_timeout_fails_with_a_clear_message(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    with pytest.raises(ValueError, match="PRECERTLY_DB_CONNECT_TIMEOUT must be a positive number"):
        make_settings(monkeypatch, DB_CONNECT_TIMEOUT=value)


# --- engine arguments ------------------------------------------------------------------------


def test_queue_mode_keeps_the_default_pool_and_sets_the_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs = engine_kwargs(make_settings(monkeypatch, DB_CONNECT_TIMEOUT="30"))
    assert kwargs == {"connect_args": {"timeout": 30.0}, "pool_pre_ping": True}


def test_null_mode_uses_nullpool_and_sets_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    kwargs = engine_kwargs(make_settings(monkeypatch, DB_POOL="null"))
    assert kwargs == {"connect_args": {"timeout": 60.0}, "poolclass": NullPool}


def test_get_engine_builds_the_engine_from_the_shared_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Settings] = []
    real = session.engine_kwargs

    def spy(settings: Settings) -> dict[str, Any]:
        calls.append(settings)
        return real(settings)

    monkeypatch.setenv("PRECERTLY_DB_POOL", "null")
    get_settings.cache_clear()
    session.get_engine.cache_clear()
    monkeypatch.setattr(session, "engine_kwargs", spy)
    try:
        engine = session.get_engine()
        assert len(calls) == 1
        assert isinstance(engine.pool, NullPool)
    finally:
        get_settings.cache_clear()
        session.get_engine.cache_clear()


def test_get_engine_queue_mode_pools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRECERTLY_DB_POOL", raising=False)
    get_settings.cache_clear()
    session.get_engine.cache_clear()
    try:
        assert isinstance(session.get_engine().pool, QueuePool)
    finally:
        get_settings.cache_clear()
        session.get_engine.cache_clear()


def test_migrations_use_the_shared_helper_and_the_probe(
    migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """env.py is executed afresh by every alembic command, so it sees these patches."""
    calls: list[str] = []
    real_kwargs, real_wait = connect.engine_kwargs, connect.wait_for_database

    def kwargs_spy(settings: Settings) -> dict[str, Any]:
        calls.append("engine_kwargs")
        return real_kwargs(settings)

    async def wait_spy(engine: Any, **kw: Any) -> None:
        calls.append("wait_for_database")
        await real_wait(engine, **kw)

    monkeypatch.setattr(connect, "engine_kwargs", kwargs_spy)
    monkeypatch.setattr(connect, "wait_for_database", wait_spy)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", migrated_db)
    config.attributes["configure_logging"] = False
    command.current(config)
    assert calls == ["engine_kwargs", "wait_for_database"]


def test_only_the_shared_helper_users_create_engines() -> None:
    """A second create_async_engine call would be a second copy of the pool settings."""
    allowed = {
        ROOT / "src/precertly/db/session.py",
        ROOT / "src/precertly/db/check.py",
        ROOT / "migrations/env.py",
    }
    found = {
        path
        for folder in ("src", "migrations", "scripts")
        for path in (ROOT / folder).rglob("*.py")
        if re.search(r"\bcreate_async_engine\(", path.read_text())
    }
    assert found == allowed
    for path in allowed:
        assert "engine_kwargs(" in path.read_text(), path


# --- classification --------------------------------------------------------------------------


def wrapped(orig: BaseException) -> OperationalError:
    return OperationalError("connect", None, orig)


def refused() -> OSError:
    return ConnectionRefusedError(errno.ECONNREFUSED, "Connect call failed")


TRANSIENT: list[Callable[[], BaseException]] = [
    lambda: TimeoutError(),
    lambda: refused(),
    lambda: ConnectionResetError(errno.ECONNRESET, "reset"),
    lambda: OSError("Multiple exceptions: [Errno 61] Connect call failed ('::1', 5432)"),
    lambda: pg.CannotConnectNowError("the database system is starting up"),
    lambda: pg.ConnectionDoesNotExistError("connection was closed in the middle of operation"),
    lambda: wrapped(pg.CannotConnectNowError("the database system is starting up")),
    lambda: wrapped(pg.ConnectionDoesNotExistError("gone")),
    lambda: wrapped(TimeoutError()),
    lambda: Exception("FATAL: the database system is starting up"),
]
PERMANENT: list[Callable[[], BaseException]] = [
    lambda: pg.InvalidPasswordError("password authentication failed"),
    lambda: pg.InvalidAuthorizationSpecificationError("role does not exist"),
    lambda: pg.InvalidCatalogNameError('database "x" does not exist'),
    lambda: pg.InsufficientPrivilegeError("permission denied"),
    lambda: wrapped(pg.InvalidPasswordError("password authentication failed")),
    lambda: wrapped(pg.InvalidCatalogNameError("no such database")),
    lambda: wrapped(pg.InsufficientPrivilegeError("denied")),
    lambda: OSError(errno.ENOENT, "no such file"),
    lambda: ValueError("bad url"),
]


@pytest.mark.parametrize("make", TRANSIENT)
def test_transient_errors_are_recognised(make: Callable[[], BaseException]) -> None:
    assert is_transient_connect_error(make())


@pytest.mark.parametrize("make", PERMANENT)
def test_permanent_errors_are_not(make: Callable[[], BaseException]) -> None:
    assert not is_transient_connect_error(make())


def test_a_permanent_error_wins_over_a_transient_one_in_its_chain() -> None:
    try:
        try:
            raise TimeoutError()
        except TimeoutError:
            raise pg.InvalidPasswordError("password authentication failed") from None
    except pg.InvalidPasswordError as error:
        assert not is_transient_connect_error(error)


def test_real_asyncpg_errors_through_sqlalchemy_classify_as_expected() -> None:
    dbapi = DBAPIError("connect", None, pg.CannotConnectNowError("starting up"))
    assert is_transient_connect_error(dbapi)


# --- retry -----------------------------------------------------------------------------------


class Probe:
    """A fake connector: raises the queued errors in order, then succeeds."""

    def __init__(self, *errors: BaseException) -> None:
        self.errors = list(errors)
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "connected"


class Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def run(coro: Awaitable[Any]) -> Any:
    async def inner() -> Any:
        return await coro

    return asyncio.run(inner())


def test_backoff_is_two_then_five_seconds() -> None:
    assert RETRY_DELAYS == (2.0, 5.0)


def test_success_on_the_first_try_does_not_sleep() -> None:
    probe, sleeps = Probe(), Sleeps()
    assert run(retry_transient(probe, sleep=sleeps)) == "connected"
    assert (probe.calls, sleeps.delays) == (1, [])


def test_two_transient_failures_then_success() -> None:
    probe, sleeps = Probe(TimeoutError(), refused()), Sleeps()
    assert run(retry_transient(probe, sleep=sleeps)) == "connected"
    assert (probe.calls, sleeps.delays) == (3, [2.0, 5.0])


def test_three_transient_failures_raise_the_original_error() -> None:
    errors = [TimeoutError("one"), refused(), pg.CannotConnectNowError("third")]
    probe, sleeps = Probe(*errors), Sleeps()
    with pytest.raises(pg.CannotConnectNowError) as raised:
        run(retry_transient(probe, sleep=sleeps))
    assert raised.value is errors[-1]
    assert (probe.calls, sleeps.delays) == (3, [2.0, 5.0])


def test_an_authentication_error_is_not_retried() -> None:
    error = wrapped(pg.InvalidPasswordError("password authentication failed"))
    probe, sleeps = Probe(error), Sleeps()
    with pytest.raises(OperationalError) as raised:
        run(retry_transient(probe, sleep=sleeps))
    assert raised.value is error
    assert (probe.calls, sleeps.delays) == (1, [])


@pytest.mark.parametrize("make", PERMANENT)
def test_no_permanent_error_is_retried(make: Callable[[], BaseException]) -> None:
    error = make()
    probe, sleeps = Probe(error), Sleeps()
    with pytest.raises(type(error)) as raised:
        run(retry_transient(probe, sleep=sleeps))
    assert raised.value is error
    assert (probe.calls, sleeps.delays) == (1, [])


def test_the_work_is_never_rerun() -> None:
    """The caller passes only a probe. Work after it runs once, however many probes failed."""
    work: list[str] = []

    async def start() -> None:
        await retry_transient(Probe(TimeoutError(), TimeoutError()), sleep=Sleeps())
        work.append("migration")

    run(start())
    assert work == ["migration"]


def test_each_retry_is_logged_without_the_message(caplog: pytest.LogCaptureFixture) -> None:
    secret = "postgresql://user:hunter2@db/x"
    probe = Probe(TimeoutError(secret), TimeoutError(secret))
    with caplog.at_level(logging.WARNING, logger="precertly.db.connect"):
        run(retry_transient(probe, sleep=Sleeps()))
    lines = [r.getMessage() for r in caplog.records]
    assert lines == [
        "database connection attempt 1 of 3 failed (TimeoutError); retrying in 2 s",
        "database connection attempt 2 of 3 failed (TimeoutError); retrying in 5 s",
    ]
    assert "hunter2" not in caplog.text


def test_wait_for_database_probes_with_select_one(db: Any) -> None:
    """Against the real test database: one connection opens and the probe returns."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from tests.conftest import TEST_DATABASE_URL

    async def go() -> None:
        engine = create_async_engine(TEST_DATABASE_URL, **engine_kwargs(Settings(_env_file=None)))
        try:
            await wait_for_database(engine, sleep=Sleeps())
        finally:
            await engine.dispose()

    run(go())


def test_wait_for_database_retries_a_refused_connection() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    sleeps = Sleeps()

    async def go() -> None:
        engine = create_async_engine(
            "postgresql+asyncpg://u:p@127.0.0.1:1/x",
            poolclass=NullPool,
            connect_args={"timeout": 2},
        )
        try:
            await wait_for_database(engine, sleep=sleeps)
        finally:
            await engine.dispose()

    with pytest.raises(OSError):
        run(go())
    assert sleeps.delays == [2.0, 5.0]


# --- call sites ------------------------------------------------------------------------------


class Sentinel(Exception):
    pass


class FakeEngine:
    disposed = False

    async def dispose(self) -> None:
        self.disposed = True


def test_ingest_probes_before_any_work(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from precertly.ingest import __main__ as ingest_main

    engine = FakeEngine()

    async def refuse(_: Any, **kw: Any) -> None:
        raise Sentinel

    monkeypatch.setattr(ingest_main, "get_engine", lambda: engine)
    monkeypatch.setattr(ingest_main, "session_factory", lambda: None)
    monkeypatch.setattr(ingest_main, "wait_for_database", refuse)
    # The path does not exist, so reaching load_bundle would raise something else.
    with pytest.raises(Sentinel):
        run(ingest_main._ingest([tmp_path / "missing.json"], None, False))
    assert engine.disposed


def test_eval_runner_probes_before_any_case(monkeypatch: pytest.MonkeyPatch) -> None:
    from precertly.evals import run as eval_run

    engine = FakeEngine()

    async def refuse(_: Any, **kw: Any) -> None:
        raise Sentinel

    monkeypatch.setattr(eval_run, "get_engine", lambda: engine)
    monkeypatch.setattr(eval_run, "session_factory", lambda: None)
    monkeypatch.setattr(eval_run, "BedrockClient", lambda settings: None)
    monkeypatch.setattr(eval_run, "BedrockEmbedder", lambda settings: None)
    monkeypatch.setattr(eval_run, "wait_for_database", refuse)
    saved: list[Any] = []
    with pytest.raises(Sentinel):
        run(eval_run._run([object()], saved.append))  # type: ignore[list-item]
    assert saved == [] and engine.disposed


# --- /health ---------------------------------------------------------------------------------


def test_health_makes_no_database_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("/health touched the database")

    async def forbidden_async(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("/health touched the database")

    session.get_engine.cache_clear()
    monkeypatch.setattr(session, "create_async_engine", forbidden)
    monkeypatch.setattr("asyncpg.connect", forbidden_async)
    monkeypatch.setattr("asyncpg.create_pool", forbidden)
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert session.get_engine.cache_info().currsize == 0


def test_pool_and_timeout_alone_keep_the_local_default_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """They are connection behaviour, not connection parts, so they do not require a host."""
    for name in ("DATABASE_URL", "DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_PORT"):
        monkeypatch.delenv(f"PRECERTLY_{name}", raising=False)
    settings = make_settings(monkeypatch, DB_POOL="null", DB_CONNECT_TIMEOUT="30")
    assert settings.sqlalchemy_url().host == "localhost"
