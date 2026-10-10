"""Database fixtures. DB tests run against a real Postgres with pgvector.

Locally they skip when the test database is not reachable (start it with
`docker compose up -d db`); in CI an unreachable database is a failure.
"""

from __future__ import annotations

import asyncio
import base64
import math
import os
import re
import zlib
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

TEST_DATABASE_URL = os.environ.get(
    "PRECERTLY_TEST_DATABASE_URL",
    "postgresql+asyncpg://precertly:precertly@localhost:5433/precertly_test",
)

DbRunner = Callable[[Callable[[AsyncSession], Awaitable[Any]]], Any]


async def _reset_schema() -> None:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def migrated_db() -> str:
    """Empty test database migrated to head, so the tests exercise the real migration."""
    try:
        asyncio.run(_reset_schema())
    except OSError as error:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"test database not reachable ({error}); run `docker compose up -d db`")
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    config.attributes["configure_logging"] = False
    command.upgrade(config, "head")
    return TEST_DATABASE_URL


@pytest.fixture
def db(migrated_db: str) -> DbRunner:
    """Run an async function with a session; everything it writes is rolled back."""

    def run(work: Callable[[AsyncSession], Awaitable[Any]]) -> Any:
        async def scoped() -> Any:
            engine = create_async_engine(migrated_db, poolclass=NullPool)
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    try:
                        return await work(session)
                    finally:
                        session.expunge_all()  # keep returned rows readable after rollback
                        await session.rollback()
            finally:
                await engine.dispose()

        return asyncio.run(scoped())

    return run


class FakeEmbedder:
    """Deterministic 1024-dim bag-of-words vectors: shared words give similar vectors."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def embed(self, text: str) -> list[float]:
        return (await self.embed_many([text]))[0]

    async def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        self.texts += texts
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        vector = [0.0] * 1024
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            vector[zlib.crc32(word.encode()) % 1024] += 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _observation(resource_id: str, loinc: str, name: str, value: float, unit: str, when: str):
    return {
        "resourceType": "Observation",
        "id": resource_id,
        "status": "final",
        "category": [{"coding": [{"code": "vital-signs"}]}],
        "code": {"coding": [{"system": "http://loinc.org", "code": loinc}], "text": name},
        "valueQuantity": {"value": value, "unit": unit},
        "effectiveDateTime": when,
    }


SLEEP_NOTE = (
    "Patient reports loud snoring and excessive daytime sleepiness.\n\n"
    "Polysomnography showed an apnea-hypopnea index of 22 events per hour. CPAP ordered."
)


@pytest.fixture
def sleep_bundle() -> dict[str, Any]:
    """Small synthetic chart: one note, coded resources and a few vitals."""
    resources = [
        {"resourceType": "Patient", "id": "pat-1", "gender": "male", "birthDate": "1968-04-02"},
        {
            "resourceType": "Condition",
            "id": "cond-osa",
            "code": {
                "coding": [{"system": "http://snomed.info/sct", "code": "78275009"}],
                "text": "Obstructive sleep apnea syndrome",
            },
            "onsetDateTime": "2026-02-10",
        },
        {
            "resourceType": "Condition",
            "id": "cond-knee",
            "code": {"text": "Osteoarthritis of knee"},
            "onsetDateTime": "2021-06-01",
        },
        {
            "resourceType": "Procedure",
            "id": "proc-psg",
            "status": "completed",
            "code": {"text": "Polysomnography sleep study"},
            "performedDateTime": "2026-02-01",
        },
        {
            "resourceType": "MedicationRequest",
            "id": "med-1",
            "status": "active",
            "medicationCodeableConcept": {"text": "Naproxen 500 MG Oral Tablet"},
            "authoredOn": "2025-09-15",
        },
        {
            "resourceType": "DocumentReference",
            "id": "note-1",
            "status": "current",
            "date": "2026-02-10T09:00:00Z",
            "content": [{"attachment": {"contentType": "text/plain", "data": _b64(SLEEP_NOTE)}}],
        },
        _observation("bmi-2024", "39156-5", "Body mass index", 33.9, "kg/m2", "2024-03-01"),
        _observation("bmi-2025", "39156-5", "Body mass index", 35.4, "kg/m2", "2025-03-01"),
        _observation("bmi-2026", "39156-5", "Body mass index", 36.2, "kg/m2", "2026-03-01"),
        _observation("weight-2026", "29463-7", "Body weight", 112.0, "kg", "2026-03-01"),
        {"resourceType": "Claim", "id": "claim-1"},
    ]
    return {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [{"fullUrl": f"urn:uuid:{r['id']}", "resource": r} for r in resources],
    }
