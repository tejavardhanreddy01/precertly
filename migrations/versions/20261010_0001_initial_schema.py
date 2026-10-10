"""initial schema

Revision ID: 0001
Revises: (none)

The eight tables from the design spec's data model, plus the pgvector extension.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=True),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(
        op.f("ix_audit_log_entity_entity_id"), "audit_log", ["entity", "entity_id"], unique=False
    )
    op.create_table(
        "policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("document", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("procedure_codes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("variants", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policies")),
        sa.UniqueConstraint("key", "version", name=op.f("uq_policies_key_version")),
    )
    op.create_table(
        "cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("patient_ref", sa.Text(), nullable=False),
        sa.Column("procedure_code", sa.Text(), nullable=True),
        sa.Column("policy_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.Text(), server_default="draft", nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["policies.id"], name=op.f("fk_cases_policy_id_policies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cases")),
    )
    op.create_table(
        "criteria",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), server_default="required", nullable=False),
        sa.Column(
            "thresholds",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["policies.id"],
            name=op.f("fk_criteria_policy_id_policies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_criteria")),
        sa.UniqueConstraint("policy_id", "key", name=op.f("uq_criteria_policy_id_key")),
    )
    op.create_table(
        "chart_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("fhir_resource_type", sa.Text(), nullable=False),
        sa.Column("fhir_resource_id", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column(
            "codes",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("value", sa.Double(), nullable=True),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("embedding_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', text)", persisted=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_chart_chunks_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chart_chunks")),
        sa.UniqueConstraint(
            "case_id",
            "fhir_resource_type",
            "fhir_resource_id",
            "chunk_index",
            name="uq_chart_chunks_resource_chunk",
        ),
    )
    op.create_index(
        op.f("ix_chart_chunks_case_id_fhir_resource_type_effective_date"),
        "chart_chunks",
        ["case_id", "fhir_resource_type", "effective_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_chart_chunks_codes"),
        "chart_chunks",
        ["codes"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        op.f("ix_chart_chunks_embedding"),
        "chart_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        op.f("ix_chart_chunks_embedding_sha256"), "chart_chunks", ["embedding_sha256"], unique=False
    )
    op.create_index(
        op.f("ix_chart_chunks_tsv"), "chart_chunks", ["tsv"], unique=False, postgresql_using="gin"
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("latency_ms", sa.Double(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_runs_case_id_cases"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runs")),
    )
    op.create_index(op.f("ix_runs_case_id"), "runs", ["case_id"], unique=False)
    op.create_table(
        "verdicts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("criterion_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Double(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("reviewer_override", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('met', 'not_met', 'insufficient')", name=op.f("ck_verdicts_outcome")
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_verdicts_case_id_cases"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["criterion_id"], ["criteria.id"], name=op.f("fk_verdicts_criterion_id_criteria")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verdicts")),
    )
    op.create_index(op.f("ix_verdicts_case_id"), "verdicts", ["case_id"], unique=False)
    op.create_table(
        "evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("verdict_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["chunk_id"], ["chart_chunks.id"], name=op.f("fk_evidence_chunk_id_chart_chunks")
        ),
        sa.ForeignKeyConstraint(
            ["verdict_id"],
            ["verdicts.id"],
            name=op.f("fk_evidence_verdict_id_verdicts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence")),
    )
    op.create_index(op.f("ix_evidence_verdict_id"), "evidence", ["verdict_id"], unique=False)


def downgrade() -> None:
    # Indexes go with their tables. The vector extension is left installed.
    for table in (
        "evidence",
        "verdicts",
        "runs",
        "chart_chunks",
        "criteria",
        "cases",
        "policies",
        "audit_log",
    ):
        op.drop_table(table)
