"""One live run against the test database, with a fake model and a fake embedder.

DB tests skip locally without `docker compose up -d db` and always run in CI. A run commits
(it uses several sessions), so these tests truncate the tables they touch before and after.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from live_support import CASE, NOON, FakeClock, FakeLLM, live_env
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from precertly.db.models import (
    AuditLog,
    Case,
    ChartChunk,
    Criterion,
    Evidence,
    Policy,
    Run,
    Verdict,
)
from precertly.evals import load_cases
from precertly.export.build import chart_chunks
from precertly.live.limits import FINISH_ACTION, LOCK_KEY, REJECT_ACTION, START_ACTION

TABLES = "cases, chart_chunks, runs, audit_log, verdicts, evidence"


async def _truncate(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    finally:
        await engine.dispose()


@pytest.fixture
def url(migrated_db: str):
    asyncio.run(_truncate(migrated_db))
    yield migrated_db
    asyncio.run(_truncate(migrated_db))


def run_async(scenario) -> Any:
    return asyncio.run(scenario)


async def rows(env, entity) -> list[Any]:
    async with env.sessions() as session:
        return list(await session.scalars(select(entity).order_by(entity.id)))


async def count(env, entity) -> int:
    async with env.sessions() as session:
        return await session.scalar(select(func.count()).select_from(entity)) or 0


async def audit_actions(env) -> list[str]:
    return [row.action for row in await rows(env, AuditLog)]


async def seed_start(env, actor: str, at: datetime) -> None:
    async with env.sessions() as session:
        session.add(
            AuditLog(actor=actor, action=START_ACTION, entity="live_run", entity_id="seed", at=at)
        )
        await session.commit()


async def seed_tokens(env, tokens_in: int, tokens_out: int, at: datetime) -> None:
    async with env.sessions() as session:
        case = Case(patient_ref="Patient/seed", created_by="seed")
        session.add(case)
        await session.flush()
        session.add(
            Run(
                case_id=case.id,
                model="seed",
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                created_at=at,
            )
        )
        await session.commit()


def test_happy_path_writes_only_what_it_should(url, caplog):
    caplog.set_level(logging.INFO)

    async def scenario():
        llm = FakeLLM(cite=True)
        async with live_env(url, llm=llm) as env:
            state = await env.run()
            record = env.runner.registry.get(state["run_id"])
            return {
                "state": state,
                "transitions": [s.value for s in record.transitions],
                "runs": await rows(env, Run),
                "audit": await rows(env, AuditLog),
                "chunks": await count(env, ChartChunk),
                "cases": await count(env, Case),
                "forbidden": [
                    await count(env, Verdict),
                    await count(env, Evidence),
                    await count(env, Criterion),
                    await count(env, Policy),
                ],
                "calls": len(llm.prompts),
                "lock_free": await env.lock_is_free(),
                "model": env.settings.bedrock_model_id,
            }

    out = run_async(scenario())
    state = out["state"]
    assert state["status"] == "done" and state["reason"] is None
    # The order comes from the recorded transitions, not from timing.
    assert out["transitions"] == [
        "waking_database",
        "checking_limits",
        "ingesting",
        "judging",
        "done",
    ]
    assert set(state["timings"]) == {"waking_database", "checking_limits", "ingesting", "judging"}

    (run,) = out["runs"]
    assert out["calls"] > 0
    assert (run.input_tokens, run.output_tokens) == (100 * out["calls"], 20 * out["calls"])
    assert run.cost_usd is None and run.latency_ms and run.latency_ms > 0
    assert run.trace_id == state["run_id"] and run.model == out["model"]

    start, finish = out["audit"]
    assert (start.action, finish.action) == (START_ACTION, FINISH_ACTION)
    assert start.actor == finish.actor == "user-1"
    assert start.entity == "live_run" and start.entity_id == state["run_id"]
    assert start.details == {
        "case": CASE,
        "policy": "facet-joint-interventions",
        "variant": "initial-rfa",
    }
    assert finish.details["status"] == "done" and finish.details["case"] == CASE
    assert finish.details["input_tokens"] == run.input_tokens
    assert sum(finish.details["outcomes"].values()) == len(state["result"]["verdicts"])

    assert out["forbidden"] == [0, 0, 0, 0]
    assert out["chunks"] > 0 and out["cases"] == 1
    assert out["lock_free"]
    assert "user-1" in caplog.text


def keys_of(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in keys_of(v)}
    if isinstance(value, list):
        return {k for v in value for k in keys_of(v)}
    return set()


def test_result_has_the_notice_no_labels_and_exportable_chunk_ids(url):
    async def scenario():
        async with live_env(url, llm=FakeLLM(cite=True)) as env:
            return await env.run()

    state = run_async(scenario())
    result = state["result"]
    assert result["notice"] == (
        "Single unregistered run, not part of the eval, no accuracy claim. Results are not stored."
    )
    assert result["case_id"] == CASE and result["policy"] == "facet-joint-interventions"
    assert result["overall"] in {"met", "not_met", "insufficient"}
    assert {"reviewer_checklist", "verdicts", "input_tokens", "output_tokens", "latency_ms"} <= set(
        result
    )
    assert result["verdicts"] and result["reviewer_checklist"]
    assert {v["decided_by"] for v in result["verdicts"]} <= {"model", "rule", "rule+model"}

    banned = {"expected", "expected_overall", "correct", "label", "labels", "score", "notes"}
    assert not keys_of(state) & banned
    assert not any(re.search(r"correct|expected|accuracy_", k) for k in keys_of(state))

    # Chart citations use the id format of the exported chunks, so the site can highlight them.
    case = load_cases()[CASE]
    exported = {c.chunk_id for c in chart_chunks(case, date(2026, 10, 23))}
    citations = [c for v in result["verdicts"] for c in v["citations"]]
    chart = [c for c in citations if not c["chunk_id"].startswith("request.")]
    assert chart, "the fake model cites a chart item, so some verdict keeps a chart citation"
    for citation in citations:
        assert citation["chunk_id"] in exported
        assert citation["quote"] and citation["char_end"] > citation["char_start"] >= 0
    for citation in chart:
        assert re.fullmatch(r"[A-Za-z]+/[^#\s]+#\d+", citation["chunk_id"])


def test_database_wait_failure_fails_and_holds_no_lock(url):
    async def refuse(engine):
        raise ConnectionRefusedError("MARKER")

    async def scenario():
        async with live_env(url, wait_for_db=refuse) as env:
            state = await env.run()
            return (
                state,
                await audit_actions(env),
                await env.lock_is_free(),
                env.runner.registry.get(state["run_id"]),
            )

    state, audit, lock_free, record = run_async(scenario())
    assert state["status"] == "failed" and state["reason"] == "database_unavailable"
    assert "MARKER" not in str(state)
    assert audit == []  # the database was not reachable, so nothing was written
    assert lock_free
    assert [s.value for s in record.transitions] == ["waking_database", "failed"]


def test_the_lock_makes_a_second_run_wait_its_turn(url):
    async def scenario():
        async with live_env(url) as env:
            async with env.engine.connect() as holder:
                await holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": LOCK_KEY})
                blocked = await env.run()
                audit_blocked = await rows(env, AuditLog)
                await holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
            proceeded = await env.run()
            return blocked, audit_blocked, proceeded, env.runner.registry.get(blocked["run_id"])

    blocked, audit_blocked, proceeded, record = run_async(scenario())
    assert blocked["status"] == "rejected" and blocked["reason"] == "run_in_progress"
    assert [s.value for s in record.transitions] == [
        "waking_database",
        "checking_limits",
        "rejected",
    ]
    assert [a.action for a in audit_blocked] == [REJECT_ACTION]
    assert (
        audit_blocked[0].actor == "user-1"
        and audit_blocked[0].details["reason"] == "run_in_progress"
    )
    assert proceeded["status"] == "done"


def test_total_cap_rejects_and_writes_an_audit_row(url):
    async def scenario():
        async with live_env(url, live_max_runs_per_day=2) as env:
            await seed_start(env, "someone", NOON - timedelta(hours=3))
            await seed_start(env, "someone-else", NOON - timedelta(hours=2))
            state = await env.run()
            return state, await audit_actions(env), await count(env, Run)

    state, audit, runs = run_async(scenario())
    assert (state["status"], state["reason"]) == ("rejected", "daily_cap_total")
    assert audit.count(START_ACTION) == 2 and audit.count(REJECT_ACTION) == 1  # no new start row
    assert runs == 0


def test_user_cap_counts_only_that_actor(url):
    async def scenario():
        async with live_env(url, live_max_runs_per_user=1) as env:
            await seed_start(env, "user-1", NOON - timedelta(hours=1))
            mine = await env.run(actor="user-1")
            other = await env.run(actor="user-2")
            return mine, other

    mine, other = run_async(scenario())
    assert (mine["status"], mine["reason"]) == ("rejected", "daily_cap_user")
    assert other["status"] == "done"


def test_token_cap_counts_input_and_output_tokens(url):
    async def scenario():
        async with live_env(url, live_max_tokens_per_day=1000) as env:
            await seed_tokens(env, 600, 399, NOON - timedelta(hours=1))
            under = await env.run()  # 999 of 1000 so far
            # The run just made pushed the day over the cap.
            over = await env.run()
            return under, over

    under, over = run_async(scenario())
    assert under["status"] == "done"
    assert (over["status"], over["reason"]) == ("rejected", "daily_tokens")


def test_caps_are_checked_in_the_order_total_user_tokens(url):
    async def scenario():
        async with live_env(
            url, live_max_runs_per_day=1, live_max_runs_per_user=1, live_max_tokens_per_day=10
        ) as env:
            await seed_start(env, "user-1", NOON - timedelta(hours=1))
            await seed_tokens(env, 50, 50, NOON - timedelta(hours=1))
            first = await env.run()
        async with live_env(
            url, live_max_runs_per_day=5, live_max_runs_per_user=1, live_max_tokens_per_day=10
        ) as env:
            second = await env.run()
        async with live_env(
            url, live_max_runs_per_day=5, live_max_runs_per_user=5, live_max_tokens_per_day=10
        ) as env:
            third = await env.run()
        return first, second, third

    first, second, third = run_async(scenario())
    assert first["reason"] == "daily_cap_total"
    assert second["reason"] == "daily_cap_user"
    assert third["reason"] == "daily_tokens"


def test_the_utc_day_boundary_with_an_injected_clock(url):
    day = datetime(2026, 10, 23, tzinfo=UTC)
    before = day - timedelta(minutes=1)  # 23:59 UTC the day before
    midnight = day  # 00:00 UTC today

    async def scenario():
        async with live_env(
            url, live_max_runs_per_day=1, clock=FakeClock(day + timedelta(hours=9))
        ) as env:
            await seed_start(env, "someone", before)
            yesterday = await env.run()
        async with live_env(
            url, live_max_runs_per_day=2, clock=FakeClock(day + timedelta(hours=9))
        ) as env:
            # `before` is not counted, the run above (today) and the one at midnight are.
            await seed_start(env, "someone", midnight)
            at_midnight = await env.run()
        return yesterday, at_midnight

    yesterday, at_midnight = run_async(scenario())
    assert yesterday["status"] == "done"  # cap of 1, and the row from 23:59 did not count
    assert (at_midnight["status"], at_midnight["reason"]) == ("rejected", "daily_cap_total")


def test_token_day_boundary(url):
    day = datetime(2026, 10, 23, tzinfo=UTC)

    async def scenario():
        async with live_env(
            url, live_max_tokens_per_day=100, clock=FakeClock(day + timedelta(hours=9))
        ) as env:
            await seed_tokens(env, 100, 0, day - timedelta(seconds=1))
            yesterday = await env.run()
        return yesterday

    assert run_async(scenario())["status"] == "done"

    async def at_midnight():
        async with live_env(
            url, live_max_tokens_per_day=100, clock=FakeClock(day + timedelta(hours=9))
        ) as env:
            await seed_tokens(env, 100, 0, day)
            return await env.run()

    assert run_async(at_midnight())["reason"] == "daily_tokens"


def test_rejected_runs_do_not_count_but_failed_runs_do(url):
    async def scenario():
        async with live_env(url, live_max_runs_per_day=1) as env:
            async with env.engine.connect() as holder:
                await holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": LOCK_KEY})
                rejected = await env.run()
                await holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
            starts_after_rejection = (await audit_actions(env)).count(START_ACTION)
            env.embedder.error = RuntimeError("embedding service down")
            failed = await env.run()  # cap of 1: this is the first run that counts
            env.embedder.error = None
            after_failure = await env.run()
            return rejected, starts_after_rejection, failed, after_failure, await audit_actions(env)

    rejected, starts, failed, after_failure, audit = run_async(scenario())
    assert rejected["reason"] == "run_in_progress" and starts == 0
    assert (failed["status"], failed["reason"]) == ("failed", "embedding_failed")
    assert (after_failure["status"], after_failure["reason"]) == ("rejected", "daily_cap_total")
    assert audit.count(START_ACTION) == 1


def test_an_embedder_failure_mid_ingest_fails_the_run_and_leaves_no_lock(url, caplog):
    caplog.set_level(logging.DEBUG)

    async def scenario():
        async with live_env(url) as env:
            env.embedder.error = RuntimeError("MARKER-embed host=db.internal")
            failed = await env.run()
            lock_free = await env.lock_is_free()
            audit = await rows(env, AuditLog)
            env.embedder.error = None
            retry = await env.run()
            return failed, lock_free, audit, retry

    failed, lock_free, audit, retry = run_async(scenario())
    assert (failed["status"], failed["reason"]) == ("failed", "embedding_failed")
    assert lock_free
    assert [a.action for a in audit[:2]] == [START_ACTION, FINISH_ACTION]
    assert (
        audit[1].details["status"] == "failed" and audit[1].details["reason"] == "embedding_failed"
    )
    assert retry["status"] == "done"
    assert "MARKER" not in str(failed) and "MARKER" not in caplog.text


def test_a_model_failure_is_internal_error_with_no_error_text(url, caplog):
    caplog.set_level(logging.DEBUG)
    marker = "MARKER postgresql://precertly:hunter2@db.internal/precertly"

    async def scenario():
        async with live_env(url, llm=FakeLLM(fail=RuntimeError(marker))) as env:
            failed = await env.run()
            return (
                failed,
                await env.lock_is_free(),
                await rows(env, AuditLog),
                await count(env, Run),
            )

    failed, lock_free, audit, runs = run_async(scenario())
    assert (failed["status"], failed["reason"]) == ("failed", "internal_error")
    assert failed["message"] == "The run failed." and failed["result"] is None
    assert lock_free and runs == 0
    assert [a.action for a in audit] == [START_ACTION, FINISH_ACTION]
    assert audit[1].details == {"status": "failed", "reason": "internal_error", "case": CASE}
    for text_ in (str(failed), caplog.text, str(audit[1].details)):
        assert "MARKER" not in text_ and "hunter2" not in text_
    assert "RuntimeError" in caplog.text


def test_a_timeout_fails_the_run_and_releases_the_lock(url):
    async def scenario():
        async with live_env(url, llm=FakeLLM(delay=30), live_run_timeout_seconds=0.5) as env:
            began = asyncio.get_running_loop().time()
            failed = await env.run()
            elapsed = asyncio.get_running_loop().time() - began
            lock_free = await env.lock_is_free()
            audit = await rows(env, AuditLog)
            env.llm.delay = 0
            retry = await env.run()
            return failed, elapsed, lock_free, audit, retry

    failed, elapsed, lock_free, audit, retry = run_async(scenario())
    assert (failed["status"], failed["reason"]) == ("failed", "timeout")
    assert elapsed < 10
    assert lock_free
    assert audit[1].action == FINISH_ACTION and audit[1].details["reason"] == "timeout"
    assert retry["status"] == "done"


def test_cancelling_a_run_releases_the_lock(url):
    async def scenario():
        async with live_env(url, llm=FakeLLM(delay=30)) as env:
            response = await env.start()
            run_id = response.json()["run_id"]
            for _ in range(500):  # until the run is waiting on the model
                if env.llm.prompts:
                    break
                await asyncio.sleep(0.02)
            assert env.llm.prompts
            assert not await env.lock_is_free()  # held while the run is working
            for task in list(env.runner._tasks):
                task.cancel()
            await env.runner.idle()
            return await env.state(run_id), await env.lock_is_free()

    state, lock_free = run_async(scenario())
    assert (state["status"], state["reason"]) == ("failed", "cancelled")
    assert lock_free


def test_a_run_replaces_the_shared_case_chunks_without_a_second_case(url):
    async def scenario():
        async with live_env(url) as env:
            await env.run("facet-01")
            first = await count(env, ChartChunk)
            await env.run("facet-02")
            return (
                first,
                await count(env, ChartChunk),
                await count(env, Case),
                await count(env, Run),
            )

    first, second, cases, runs = run_async(scenario())
    assert first > 0 and second > 0
    assert cases == 1 and runs == 2


def test_session_is_not_left_open(url):
    """After a run no connection of the app is still in a transaction (nothing idle in it)."""

    async def scenario():
        async with live_env(url) as env:
            await env.run()
            async with env.engine.connect() as connection:
                return await connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                        "AND pid <> pg_backend_pid() AND state LIKE 'idle in transaction%'"
                    )
                )

    assert run_async(scenario()) == 0
