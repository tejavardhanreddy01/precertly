"""Tables from the design spec's data model.

Departures from the spec table, agreed in review:
- policies carry their variants and all_of/any_of logic as JSONB, because the nested logic
  trees do not fit a flat criteria.logic_group column. criteria stay flat rows.
- policies have a surrogate id plus a unique (key, version), so versions can coexist.
- chart_chunks keep what the chunker knows (index, date, offsets, codes, numeric value)
  and a generated tsvector. embedding is nullable: labs and vitals are not embedded.
- cases.procedure_code and policy_id are nullable until a request is attached.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIMENSIONS = 1024
OUTCOMES = ("met", "not_met", "insufficient")


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_N_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Policy(Base):
    """One version of a coverage policy."""

    __tablename__ = "policies"
    __table_args__ = (UniqueConstraint("key", "version"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    key: Mapped[str] = mapped_column(Text)  # e.g. "bariatric-surgery"
    version: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    document: Mapped[str] = mapped_column(Text)  # e.g. "NCD 100.1"
    source_url: Mapped[str] = mapped_column(Text)
    procedure_codes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    effective_date: Mapped[date | None]
    variants: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()


class Criterion(Base):
    """One atomic, checkable statement from a policy."""

    __tablename__ = "criteria"
    __table_args__ = (UniqueConstraint("policy_id", "key"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("policies.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(Text)  # criterion id within the policy file
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text, server_default="required")
    thresholds: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=sql_text("'{}'::jsonb")
    )


class Case(Base):
    """One prior-authorization request."""

    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = _uuid_pk()
    patient_ref: Mapped[str] = mapped_column(Text)  # "Patient/<id>" in the bundle
    procedure_code: Mapped[str | None] = mapped_column(Text)
    policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("policies.id"))
    status: Mapped[str] = mapped_column(Text, server_default="draft")
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class ChartChunk(Base):
    """Searchable chart evidence: one chunk of one FHIR resource."""

    __tablename__ = "chart_chunks"
    __table_args__ = (
        UniqueConstraint(
            "case_id",
            "fhir_resource_type",
            "fhir_resource_id",
            "chunk_index",
            name="uq_chart_chunks_resource_chunk",  # the generated name exceeds 63 chars
        ),
        Index(None, "case_id", "fhir_resource_type", "effective_date"),
        Index(None, "tsv", postgresql_using="gin"),
        Index(None, "codes", postgresql_using="gin"),
        Index(
            None,
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index(None, "embedding_sha256"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    fhir_resource_type: Mapped[str] = mapped_column(Text)
    fhir_resource_id: Mapped[str] = mapped_column(Text)
    chunk_index: Mapped[int]
    effective_date: Mapped[date | None]
    text: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int | None]
    char_end: Mapped[int | None]
    # "system|code" tokens, and the numeric value when the resource has one (Observations).
    codes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=sql_text("'{}'::text[]"))
    value: Mapped[float | None]
    unit: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    # sha256 of the exact text that was embedded, so re-ingest can reuse the vector.
    embedding_sha256: Mapped[str | None] = mapped_column(String(64))
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', text)", persisted=True)
    )


class Verdict(Base):
    """One judgment per criterion. Never a denial: a human decides."""

    __tablename__ = "verdicts"
    __table_args__ = (
        CheckConstraint(f"outcome IN {OUTCOMES}", name="outcome"),
        Index(None, "case_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    criterion_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("criteria.id"))
    outcome: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None]
    rationale: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    reviewer_override: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class Evidence(Base):
    """A verbatim quote from a chunk that backs a verdict."""

    __tablename__ = "evidence"
    __table_args__ = (Index(None, "verdict_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    verdict_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("verdicts.id", ondelete="CASCADE"))
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chart_chunks.id"))
    quote: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int | None]
    char_end: Mapped[int | None]


class Run(Base):
    """Cost and latency of one agent run."""

    __tablename__ = "runs"
    __table_args__ = (Index(None, "case_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    model: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(server_default="0")
    output_tokens: Mapped[int] = mapped_column(server_default="0")
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[float | None]
    trace_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class AuditLog(Base):
    """Every read and write, with the actor."""

    __tablename__ = "audit_log"
    __table_args__ = (Index(None, "entity", "entity_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    entity: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=sql_text("'{}'::jsonb"))
