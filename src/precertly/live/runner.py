"""One live run: wake the database, take the lock, check the caps, ingest, judge, record.

A run writes exactly this: the cases and chart_chunks rows that ingest_bundle writes, one
runs row, and audit_log rows. It never writes verdicts, evidence, policies or criteria: a
verdict row needs a criteria row, evidence points at chart_chunks ids that every ingest
deletes and recreates, and ingest refuses to re-ingest a case that has verdict rows. The
result is held in memory only (registry.py).

Status order: waking_database, checking_limits, ingesting, judging, then done. A run can also
end rejected (another run holds the lock, or a daily cap is reached) or failed. The terminal
status is set only after the lock transaction has ended, so a request that arrives right after
a run is shown as finished never meets a lock that is about to be released.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from precertly.db.connect import wait_for_database
from precertly.db.models import AuditLog, Run
from precertly.evals.cases import EvalCase
from precertly.fhir import load_bundle
from precertly.ingest import ingest_bundle
from precertly.judgment import CaseJudgment, ChartEvidence, StructuredChart, judge_case
from precertly.judgment.judge import StructuredLLM
from precertly.live import limits
from precertly.live.models import (
    LiveChecklistItem,
    LiveCitation,
    LiveResult,
    LiveVerdict,
    Reason,
    RunStatus,
)
from precertly.live.registry import RunRecord, RunRegistry
from precertly.policies import Policy
from precertly.settings import Settings

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


class LiveEmbedder(Protocol):
    """Documents (ingest) and queries (retrieval) go through the same embedder."""

    async def embed(self, text: str) -> list[float]: ...

    async def embed_many(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class LiveDeps:
    """What a run needs from outside. Built when a run starts, never at import time."""

    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    llm: StructuredLLM
    embedder: LiveEmbedder
    # Waits for a paused database to resume; tests replace it.
    wait_for_db: Callable[[AsyncEngine], Awaitable[None]] = wait_for_database


class ProductionDeps:
    """The real engine and Bedrock clients, created on the first run and then reused, so the
    request pacing (which lives in the client) carries over from one run to the next."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._deps: LiveDeps | None = None

    def __call__(self) -> LiveDeps:
        if self._deps is None:
            from precertly.db import get_engine, session_factory
            from precertly.llm import BedrockClient, BedrockEmbedder

            engine = get_engine()
            self._deps = LiveDeps(
                engine=engine,
                sessions=session_factory(engine),
                llm=BedrockClient(self._settings),
                embedder=BedrockEmbedder(self._settings),
            )
        return self._deps


@dataclass(frozen=True)
class Outcome:
    status: RunStatus
    reason: Reason | None = None
    result: LiveResult | None = None


def failed(reason: Reason) -> Outcome:
    return Outcome(RunStatus.FAILED, reason)


def rejected(reason: Reason) -> Outcome:
    return Outcome(RunStatus.REJECTED, reason)


def build_result(
    case: EvalCase, judgment: CaseJudgment, latency_ms: float
) -> tuple[LiveResult, int, int]:
    """The result shown to the user, and the tokens the run used. Expected labels and every
    correct flag are left out on purpose: next to a live verdict they would be an accuracy
    claim."""
    input_tokens = sum(v.usage.input_tokens for v in judgment.verdicts)
    output_tokens = sum(v.usage.output_tokens for v in judgment.verdicts)
    result = LiveResult(
        case_id=case.id,
        policy=judgment.policy,
        variant=judgment.variant,
        overall=judgment.overall,
        verdicts=[
            LiveVerdict(
                criterion=v.criterion,
                outcome=v.outcome,
                applicable=v.applicable,
                decided_by=v.decided_by,
                confidence=v.confidence,
                rationale=v.rationale,
                citations=[
                    LiveCitation(
                        chunk_id=c.chunk_id,
                        quote=c.quote,
                        char_start=c.char_start,
                        char_end=c.char_end,
                    )
                    for c in v.citations
                ],
            )
            for v in judgment.verdicts
        ],
        reviewer_checklist=[
            LiveChecklistItem(criterion=i.criterion, evidence_stage=i.evidence_stage, text=i.text)
            for i in judgment.reviewer_checklist
        ],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=round(latency_ms, 1),
    )
    return result, input_tokens, output_tokens


class LiveRunner:
    def __init__(
        self,
        settings: Settings,
        cases: Mapping[str, EvalCase],
        policies: Mapping[str, Policy],
        make_deps: Callable[[], LiveDeps],
        *,
        registry: RunRegistry | None = None,
        clock: Callable[[], datetime] = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.cases = cases
        self.policies = policies
        self._make_deps = make_deps
        self.clock = clock
        self.monotonic = monotonic
        self.registry = registry or RunRegistry(clock)
        # Strong references: the event loop keeps only weak ones to running tasks.
        self._tasks: set[asyncio.Task[None]] = set()

    def start(self, actor: str, case_id: str) -> RunRecord:
        """Register a run and start it in the background. Raises RunActive if one is in
        progress in this process, and creates nothing then. Does no I/O."""
        record = self.registry.create(actor, case_id, self.monotonic())
        task = asyncio.create_task(self._run(record), name=f"live-run-{record.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return record

    async def idle(self) -> None:
        """Wait until every run started so far has ended (for shutdown and for tests)."""
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def _enter(self, record: RunRecord, status: RunStatus) -> None:
        record.enter(status, self.monotonic())
        logger.info("live run %s actor %.8s: %s", record.id, record.actor, status.value)

    async def _run(self, record: RunRecord) -> None:
        deps: LiveDeps | None = None
        timeout: asyncio.Timeout | None = None
        outcome = failed(Reason.INTERNAL_ERROR)
        try:
            deps = self._make_deps()
            timeout = asyncio.timeout(self.settings.live_run_timeout_seconds)
            async with timeout:
                outcome = await self._execute(record, deps)
        except asyncio.CancelledError:
            outcome = failed(Reason.CANCELLED)
            raise
        except Exception as error:
            expired = isinstance(error, TimeoutError) and timeout is not None and timeout.expired()
            outcome = failed(Reason.TIMEOUT if expired else Reason.INTERNAL_ERROR)
            # The class name only: the message could carry a host or a statement.
            logger.error("live run %s failed: %s", record.id, type(error).__name__)
        finally:
            await self._finish(record, deps, outcome)

    async def _finish(self, record: RunRecord, deps: LiveDeps | None, outcome: Outcome) -> None:
        try:
            if (
                outcome.status is RunStatus.FAILED
                and outcome.reason is not Reason.DATABASE_UNAVAILABLE
                and deps is not None
            ):
                try:
                    await self._audit(
                        deps,
                        record,
                        limits.FINISH_ACTION,
                        {
                            "status": "failed",
                            "reason": outcome.reason.value if outcome.reason else None,
                            "case": record.case_id,
                        },
                    )
                except Exception as error:
                    logger.error(
                        "live run %s: audit row not written: %s", record.id, type(error).__name__
                    )
        finally:
            self.registry.finish(
                record,
                outcome.status,
                self.monotonic(),
                reason=outcome.reason,
                result=outcome.result,
            )
            logger.info(
                "live run %s actor %.8s: %s%s",
                record.id,
                record.actor,
                outcome.status.value,
                f" ({outcome.reason.value})" if outcome.reason else "",
            )

    async def _audit(
        self, deps: LiveDeps, record: RunRecord, action: str, details: dict[str, Any]
    ) -> None:
        """One audit row, committed on its own short session."""
        async with deps.sessions() as session:
            session.add(
                AuditLog(
                    actor=record.actor,
                    action=action,
                    entity=limits.ENTITY,
                    entity_id=record.id,
                    at=self.clock(),
                    details=details,
                )
            )
            await session.commit()

    async def _reject(self, deps: LiveDeps, record: RunRecord, reason: Reason) -> Outcome:
        await self._audit(
            deps, record, limits.REJECT_ACTION, {"reason": reason.value, "case": record.case_id}
        )
        return rejected(reason)

    async def _execute(self, record: RunRecord, deps: LiveDeps) -> Outcome:
        settings = self.settings
        case = self.cases[record.case_id]

        try:
            await deps.wait_for_db(deps.engine)
        except Exception as error:
            logger.error("live run %s: database wait: %s", record.id, type(error).__name__)
            return failed(Reason.DATABASE_UNAVAILABLE)

        self._enter(record, RunStatus.CHECKING_LIMITS)
        # One connection and one transaction for the whole run: the advisory lock lives as
        # long as the transaction. Leaving this block rolls it back, whatever happened.
        async with deps.engine.connect() as lock_connection:
            if not await limits.try_lock(lock_connection):
                return await self._reject(deps, record, Reason.RUN_IN_PROGRESS)
            capped = await limits.limit_reached(
                lock_connection,
                actor=record.actor,
                now=self.clock(),
                max_runs=settings.live_max_runs_per_day,
                max_runs_per_user=settings.live_max_runs_per_user,
                max_tokens=settings.live_max_tokens_per_day,
            )
            if capped is not None:
                return await self._reject(deps, record, capped)
            # Committed on another session while the lock transaction stays open, so it
            # survives a failure below and counts toward the caps even if the run fails.
            await self._audit(
                deps,
                record,
                limits.START_ACTION,
                {"case": case.id, "policy": case.policy, "variant": case.variant},
            )

            self._enter(record, RunStatus.INGESTING)
            began = self.monotonic()
            bundle = load_bundle(case.bundle_path())
            async with deps.sessions() as session:
                ingested = await ingest_bundle(
                    session,
                    bundle,
                    embedder=deps.embedder,
                    created_by="live",
                    commit_batches=True,
                )
                if ingested.embedding_error:
                    # Its text is "ClassName: message"; the message is not ours to show or log.
                    return failed(Reason.EMBEDDING_FAILED)
                await session.commit()

                self._enter(record, RunStatus.JUDGING)
                evidence = ChartEvidence(
                    session, ingested.case_id, deps.embedder, k=settings.judgment_top_k
                )
                judgment = await judge_case(
                    deps.llm,
                    self.policies[case.policy],
                    case.variant,
                    case.request,
                    evidence,
                    structured=StructuredChart.from_bundle(bundle),
                    shadow=False,
                    prompt_version=settings.prompt_version,
                )
            latency_ms = (self.monotonic() - began) * 1000

            # Tokens are summed from the verdicts: each carries the usage of all its model
            # calls, retries included, and a rule-decided verdict carries none.
            result, input_tokens, output_tokens = build_result(case, judgment, latency_ms)
            async with deps.sessions() as session:
                session.add(
                    Run(
                        case_id=ingested.case_id,
                        model=settings.bedrock_model_id,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        # Left empty on purpose: the model's per-token price is not verified.
                        cost_usd=None,
                        latency_ms=latency_ms,
                        trace_id=record.id,
                        created_at=self.clock(),
                    )
                )
                session.add(
                    AuditLog(
                        actor=record.actor,
                        action=limits.FINISH_ACTION,
                        entity=limits.ENTITY,
                        entity_id=record.id,
                        at=self.clock(),
                        details={
                            "status": "done",
                            "case": case.id,
                            "overall": result.overall.value,
                            "outcomes": dict(
                                Counter(o.value for o in judgment.outcomes().values())
                            ),
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "latency_ms": round(latency_ms, 1),
                        },
                    )
                )
                await session.commit()
            await lock_connection.rollback()
        return Outcome(RunStatus.DONE, result=result)
