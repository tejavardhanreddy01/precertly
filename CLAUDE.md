# Precertly: guide for Claude Code

Precertly checks prior-authorization criteria against a FHIR R4 patient chart, cites evidence for every verdict, and drafts a PAS-shaped request for human approval. It is a public portfolio project: code quality, honest evals and clear commits matter as much as features.

Design spec (source of truth for scope and architecture): https://claude.ai/code/artifact/afc367f6-4fae-4200-ac8a-679032c62cbf

## Commands

```bash
uv sync                                         # install
uv run uvicorn precertly.api.main:app --reload  # API at :8000/docs
uv run pytest -q                                # tests
uv run ruff check . && uv run ruff format .     # lint + format
data/synthea/generate.sh 10                     # synthetic FHIR patients (Java 11+)
uv run python -m precertly.fhir <bundle-or-dir>  # chunk summary for FHIR bundles
docker compose up -d db                         # Postgres 16 + pgvector on localhost:5433
uv run alembic upgrade head                     # apply migrations (URL from PRECERTLY_DATABASE_URL)
uv run python -m precertly.ingest <path> [--estimate | --embed]  # --embed costs money; ask first
uv run python scripts/smoke_llm.py              # one real Bedrock call (costs money; ask first)
```

## Layout

- `src/precertly/policies/` – policy schema (Pydantic) and loader
- `src/precertly/api/` – FastAPI app
- `src/precertly/fhir/` – FHIR bundle → `Chunk`s (id `Type/id#n`; notes are verbatim slices with char offsets)
- `src/precertly/llm/` – Bedrock Converse client: structured output, tokens and latency per call
- `src/precertly/db/` – SQLAlchemy models (spec data model) and async session; migrations in `migrations/`
- `src/precertly/ingest/` – bundle → `chart_chunks`; embeds only notes, conditions, procedures, medication requests; vectors cached by sha256
- `src/precertly/retrieval/` – `hybrid_search` (full-text + pgvector, RRF) and `observations` (by code and date)
- `src/precertly/settings.py` – `PRECERTLY_*` settings (model id, region, AWS profile)
- `data/policies/*.yaml` – the 4 coverage policies as atomic criteria + all_of/any_of logic per variant
- `data/synthea/` – patient generator (output is git-ignored)
- `evals/` – labeled cases and scorers (see evals/README.md)

## Rules

- Synthetic data only. Never add real patient data, and never commit generated Synthea output.
- Policy criteria must trace to a public source listed in the policy file. Don't invent thresholds; if a source is silent, leave it out and add a note.
- Every verdict the agent produces must cite FHIR resource ids and verbatim quotes that are verified against the source text.
- The agent never outputs a denial on its own: outcomes are met, not_met or insufficient, and a human decides.
- Schema changes go through an Alembic migration; `uv run alembic check` must report no drift.
- DB tests need `docker compose up -d db` locally (they skip otherwise) and always run in CI.
- Python 3.12, typed, Pydantic v2, async where I/O happens. Keep CI green: ruff + pytest.
- Small, focused commits with clear messages.

## Status

- Day 1 done: policy schema + 4 policies validated in CI, read-only policy API, Synthea script.
- Day 2 part 1 done: FHIR chunker, Bedrock client (mocked in tests), smoke script.
- Day 2 part 2 done: Postgres + pgvector, migrations, ingest with cached embeddings, hybrid search, observation lookups.
- Next (core pipeline): per-criterion judgment on Bedrock with structured output and quote verification → combine via variant logic → PAS-shaped Claim bundle.
