"""Engine arguments and the first-connect probe, shared by every place that opens the database.

Aurora Serverless v2 scaled to zero pauses only while no client connection is open, and a
paused cluster takes about 15 s to resume (30 s or more after a long pause). So the pool must
be able to hold no idle connections, the connect timeout must be long, and the first connect
is retried when the cluster is still waking up.
"""

from __future__ import annotations

import asyncio
import errno
import logging
from collections.abc import Awaitable, Callable, Iterator, Sequence
from typing import Any

from asyncpg import exceptions as pg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool

from precertly.settings import Settings

logger = logging.getLogger(__name__)

# Seconds to wait before the second and the third attempt: three attempts in total.
RETRY_DELAYS: tuple[float, ...] = (2.0, 5.0)

_REFUSED_OR_RESET = frozenset({errno.ECONNREFUSED, errno.ECONNRESET, errno.ECONNABORTED})
# asyncpg reports a refused connection to a host with several addresses (localhost, an IPv6
# and an IPv4 address) as a plain OSError("Multiple exceptions: ...") that has no errno.
_REFUSED_OR_RESET_TEXT = ("connect call failed", "connection refused", "connection reset")
_STARTING_UP_TEXT = "database system is starting up"

# SQLSTATE of errors that retrying cannot fix: class 28 (invalid authorization, which includes a
# wrong password), 3D000 (database does not exist) and 42501 (insufficient privilege).
_PERMANENT_SQLSTATE_CLASSES = ("28",)
_PERMANENT_SQLSTATES = frozenset({"3D000", "42501"})


def engine_kwargs(settings: Settings) -> dict[str, Any]:
    """Keyword arguments for create_async_engine, from the settings.

    "queue" keeps SQLAlchemy's default pool (with pre-ping). "null" opens a connection per
    use and closes it afterwards, so nothing idle keeps a scaled-to-zero database awake.
    """
    kwargs: dict[str, Any] = {"connect_args": {"timeout": settings.db_connect_timeout}}
    if settings.db_pool == "null":
        kwargs["poolclass"] = NullPool
    else:
        kwargs["pool_pre_ping"] = True
    return kwargs


def _chain(error: BaseException) -> Iterator[BaseException]:
    """The error and everything it wraps: SQLAlchemy's `orig`, then the cause and context."""
    seen: set[int] = set()
    pending: list[BaseException | None] = [error]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        pending.extend((getattr(current, "orig", None), current.__cause__, current.__context__))


def _permanent(error: BaseException) -> bool:
    sqlstate = getattr(error, "sqlstate", None) or getattr(error, "pgcode", None)
    if not isinstance(sqlstate, str):
        return False
    return sqlstate in _PERMANENT_SQLSTATES or sqlstate.startswith(_PERMANENT_SQLSTATE_CLASSES)


def _transient_one(error: BaseException) -> bool:
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    if isinstance(error, (pg.CannotConnectNowError, pg.ConnectionDoesNotExistError)):
        return True
    if isinstance(error, OSError):
        if error.errno in _REFUSED_OR_RESET:
            return True
        # Name resolution failures are not on the list, and carry no errno of interest.
        text_ = str(error).lower()
        return error.errno is None and any(part in text_ for part in _REFUSED_OR_RESET_TEXT)
    return _STARTING_UP_TEXT in str(error).lower()


def is_transient_connect_error(error: BaseException) -> bool:
    """True for a failure that is expected to clear while the database wakes up.

    Connect timeouts, a refused or reset connection, "database system is starting up" and a
    connection that does not exist, also when SQLAlchemy has wrapped them. Authentication,
    permission and missing-database errors are never transient, even if something transient
    appears elsewhere in the chain.
    """
    members = list(_chain(error))
    if any(_permanent(member) for member in members):
        return False
    return any(_transient_one(member) for member in members)


def _names(error: BaseException) -> str:
    """Class names only: a message could carry a host or a statement, never worth logging."""
    return " <- ".join(type(member).__name__ for member in _chain(error))


async def retry_transient[T](
    probe: Callable[[], Awaitable[T]],
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    delays: Sequence[float] = RETRY_DELAYS,
) -> T:
    """Run `probe`; retry it only for transient connection errors, then re-raise the last one.

    The probe must be a bare connectivity check. Work that must run once (a migration, an
    ingest) is never passed in here.
    """
    attempts = len(delays) + 1
    for attempt, delay in enumerate((*delays, None), start=1):
        try:
            return await probe()
        except Exception as error:
            if delay is None or not is_transient_connect_error(error):
                raise
            logger.warning(
                "database connection attempt %d of %d failed (%s); retrying in %.0f s",
                attempt,
                attempts,
                _names(error),
                delay,
            )
            await sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


async def wait_for_database(
    engine: AsyncEngine,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Open one connection and run SELECT 1, retrying while the database wakes up."""

    async def probe() -> None:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    await retry_transient(probe, sleep=sleep)
