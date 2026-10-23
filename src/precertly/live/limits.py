"""The one-run-at-a-time lock and the daily caps, both kept in the database.

The lock is a transaction-level advisory lock on one fixed key, taken on a dedicated
connection whose transaction stays open for the whole run: it is released when that
transaction ends, however the run ends, and by the database itself if the connection is
lost. All 29 eval bundles share one patient, hence one case row, so two runs at once would
replace each other's chart chunks.

The caps count rows the run itself writes: the live_run.start audit row (written before the
work, so a run that fails afterwards still counts) and the runs table (tokens).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from precertly.db.models import AuditLog, Run
from precertly.live.models import Reason

START_ACTION = "live_run.start"
REJECT_ACTION = "live_run.rejected"
FINISH_ACTION = "live_run.finish"
ENTITY = "live_run"

# The first 8 bytes of sha256("precertly:live-run"), as a signed 64-bit integer (what
# pg_try_advisory_xact_lock takes). Derived from a fixed name so the value is not a magic
# number, and written out because it must never change while a deployed version runs:
# tests/test_live_limits.py recomputes it.
LOCK_KEY = 6342687505388282813


def utc_day(now: datetime) -> tuple[datetime, datetime]:
    """Start (inclusive) and end (exclusive) of the UTC day that contains `now`."""
    start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


async def try_lock(connection: AsyncConnection) -> bool:
    """Take the run lock for the connection's current transaction, without waiting."""
    return bool(
        await connection.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": LOCK_KEY})
    )


async def limit_reached(
    connection: AsyncConnection,
    *,
    actor: str,
    now: datetime,
    max_runs: int,
    max_runs_per_user: int,
    max_tokens: int,
) -> Reason | None:
    """The first cap that is already reached today, in the order total, user, tokens."""
    day_start, day_end = utc_day(now)
    started_today = (
        AuditLog.action == START_ACTION,
        AuditLog.at >= day_start,
        AuditLog.at < day_end,
    )
    total = await connection.scalar(
        select(func.count()).select_from(AuditLog).where(*started_today)
    )
    if (total or 0) >= max_runs:
        return Reason.DAILY_CAP_TOTAL
    mine = await connection.scalar(
        select(func.count()).select_from(AuditLog).where(*started_today, AuditLog.actor == actor)
    )
    if (mine or 0) >= max_runs_per_user:
        return Reason.DAILY_CAP_USER
    tokens = await connection.scalar(
        select(func.coalesce(func.sum(Run.input_tokens + Run.output_tokens), 0)).where(
            Run.created_at >= day_start, Run.created_at < day_end
        )
    )
    if (tokens or 0) >= max_tokens:
        return Reason.DAILY_TOKENS
    return None
