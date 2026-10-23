"""The in-process record of live runs.

The API runs as one uvicorn worker (one process, one event loop), so a dict is enough and the
single-flight check below is atomic: it never awaits. A second worker or a second task would
each have its own registry; the database advisory lock (limits.py) is what keeps the runs
apart across processes.
"""

from __future__ import annotations

import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from precertly.live.models import MESSAGES, LiveResult, Reason, RunState, RunStatus

KEEP_RUNS = 20
KEEP_FINISHED_FOR = timedelta(hours=1)


class RunActive(Exception):
    """A live run is already in progress in this process."""


@dataclass
class RunRecord:
    id: str
    actor: str
    case_id: str
    started_at: datetime
    status: RunStatus = RunStatus.WAKING_DATABASE
    reason: Reason | None = None
    finished_at: datetime | None = None
    timings: dict[str, float] = field(default_factory=dict)
    result: LiveResult | None = None
    # Every status the run has been in, in order. Tests assert on this, not on timing.
    transitions: list[RunStatus] = field(default_factory=list)
    _since: float | None = None

    def __post_init__(self) -> None:
        self.transitions.append(self.status)

    def enter(self, status: RunStatus, now: float) -> None:
        """Move to a new status; the time spent in the old one is added to its timing."""
        if self._since is not None:
            self.timings[self.status.value] = self.timings.get(self.status.value, 0.0) + max(
                now - self._since, 0.0
            )
        self.status = status
        self._since = now
        self.transitions.append(status)

    def state(self) -> RunState:
        return RunState(
            run_id=self.id,
            case_id=self.case_id,
            status=self.status,
            reason=self.reason,
            message=MESSAGES[self.reason] if self.reason else None,
            started_at=self.started_at,
            finished_at=self.finished_at,
            timings={k: round(v, 3) for k, v in self.timings.items()},
            result=self.result,
        )


class RunRegistry:
    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._clock = clock
        self._runs: OrderedDict[str, RunRecord] = OrderedDict()

    def active(self) -> RunRecord | None:
        return next((r for r in self._runs.values() if not r.status.finished), None)

    def create(self, actor: str, case_id: str, now: float) -> RunRecord:
        """A new run, or RunActive if one is in progress. Does not await, so it is atomic."""
        if self.active() is not None:
            raise RunActive
        self._prune()
        record = RunRecord(
            id=str(uuid.uuid4()), actor=actor, case_id=case_id, started_at=self._clock()
        )
        record._since = now
        self._runs[record.id] = record
        return record

    def get(self, run_id: str) -> RunRecord | None:
        return self._runs.get(run_id)

    def finish(
        self,
        record: RunRecord,
        status: RunStatus,
        now: float,
        *,
        reason: Reason | None = None,
        result: LiveResult | None = None,
    ) -> None:
        record.enter(status, now)
        record.reason = reason
        record.result = result
        record.finished_at = self._clock()

    def _prune(self) -> None:
        """Drop finished runs older than an hour, then the oldest finished runs beyond 20.
        A run in progress is never dropped."""
        cutoff = self._clock() - KEEP_FINISHED_FOR
        for run_id, record in list(self._runs.items()):
            if record.finished_at is not None and record.finished_at < cutoff:
                del self._runs[run_id]
        for run_id, record in list(self._runs.items()):
            if len(self._runs) < KEEP_RUNS:
                break
            if record.status.finished:
                del self._runs[run_id]
