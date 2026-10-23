"""Fakes and a small environment for the live run tests: no AWS, no network.

The database is the local test Postgres (see conftest.py); the model and the embedder are
fakes, and the app is the real one with live mode on.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from conftest import FakeEmbedder
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from precertly.api.main import create_app
from precertly.db.connect import wait_for_database
from precertly.judgment import ModelJudgment
from precertly.live import LiveDeps, LiveMountOptions, LiveRunner
from precertly.live.limits import LOCK_KEY
from precertly.llm import LLMUsage
from precertly.settings import Settings

NOON = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
CASE = "facet-01"
FINISHED = {"done", "failed", "rejected"}


class Response:
    def __init__(self, data: ModelJudgment) -> None:
        self.data = data
        self.usage = LLMUsage(model_id="fake", input_tokens=100, output_tokens=20, latency_ms=5)
        self.exchanges: list[Any] = []


class FakeLLM:
    """Answers every criterion 'insufficient' with no citation, which is a valid verdict.

    With cite=True it instead answers 'met' and quotes the first words of the first chart
    item in the prompt. delay makes each call slow; fail raises instead of answering.
    """

    def __init__(self, *, cite: bool = False, delay: float = 0.0, fail: Exception | None = None):
        self.cite, self.delay, self.fail = cite, delay, fail
        self.prompts: list[str] = []

    async def structured(self, prompt: str, schema: Any, **options: Any) -> Response:
        self.prompts.append(prompt)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail is not None:
            raise self.fail
        assert schema is ModelJudgment
        if self.cite:
            chart = re.search(r"\[(E\d+)\] \(chart[^)]*\)\n(.+)", prompt)
            if chart:
                quote = " ".join(chart.group(2).split()[:3])
                return Response(
                    ModelJudgment.model_validate(
                        {
                            "outcome": "met",
                            "confidence": 0.9,
                            "rationale": "stated in the note",
                            "citations": [{"evidence": chart.group(1), "quote": quote}],
                        }
                    )
                )
        return Response(
            ModelJudgment(outcome="insufficient", confidence=0.4, rationale="not enough evidence")
        )


class FailingEmbedder(FakeEmbedder):
    """Embeds like FakeEmbedder until `error` is set; then every call raises it."""

    def __init__(self) -> None:
        super().__init__()
        self.error: Exception | None = None

    async def embed_many(self, texts: Any) -> list[list[float]]:
        if self.error is not None:
            raise self.error
        return await super().embed_many(texts)


class FakeClock:
    def __init__(self, now: datetime = NOON) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Env:
    client: httpx.AsyncClient
    runner: LiveRunner
    engine: AsyncEngine
    sessions: async_sessionmaker
    llm: FakeLLM
    embedder: FailingEmbedder
    clock: FakeClock
    settings: Settings
    waits: list[str] = field(default_factory=list)

    async def start(self, case_id: str = CASE, actor: str = "user-1") -> httpx.Response:
        return await self.client.post(
            "/live/runs", json={"case_id": case_id}, headers={"x-precertly-actor": actor}
        )

    async def state(self, run_id: str, actor: str = "user-1") -> dict[str, Any]:
        response = await self.client.get(
            f"/live/runs/{run_id}", headers={"x-precertly-actor": actor}
        )
        assert response.status_code == 200, response.text
        return response.json()

    async def run(self, case_id: str = CASE, actor: str = "user-1") -> dict[str, Any]:
        """Start a run and wait for it to end; returns the final state."""
        response = await self.start(case_id, actor)
        assert response.status_code == 202, response.text
        run_id = response.json()["run_id"]
        await self.runner.idle()
        state = await self.state(run_id, actor)
        assert state["status"] in FINISHED
        return state

    async def lock_is_free(self) -> bool:
        async with self.engine.connect() as connection:
            got = await connection.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY})
            if got:
                await connection.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
            return bool(got)


@asynccontextmanager
async def live_env(
    database_url: str,
    *,
    llm: FakeLLM | None = None,
    wait_for_db: Callable[[AsyncEngine], Any] = wait_for_database,
    clock: FakeClock | None = None,
    **settings: Any,
) -> AsyncIterator[Env]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    llm, embedder, clock = llm or FakeLLM(), FailingEmbedder(), clock or FakeClock()
    deps = LiveDeps(engine, sessions, llm, embedder, wait_for_db=wait_for_db)
    config = Settings(_env_file=None, live_enabled=True, **settings)
    app = create_app(config, LiveMountOptions(make_deps=lambda: deps, clock=clock))
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield Env(client, app.state.live, engine, sessions, llm, embedder, clock, config)
    finally:
        await app.state.live.idle()
        await engine.dispose()
