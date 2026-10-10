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
- Policy criteria come only from governing NCD, LCD or LCD-article text, never from MAC checklists, summary pages or memory. Each criterion carries a verbatim `quote`, the `source_document` and the `source_section`. Encode only thresholds the text states, exactly as stated; if the text is silent, leave it out and add a note. Criteria that apply only in some situations get a `condition`.
- After editing a policy, run `uv run python scripts/verify_policy_quotes.py` (needs network) and keep fetched documents out of the repo. Never copy CPT descriptors.
- Every verdict the agent produces must cite FHIR resource ids and verbatim quotes that are verified against the source text.
- The agent never outputs a denial on its own: outcomes are met, not_met or insufficient, and a human decides.
- Schema changes go through an Alembic migration; `uv run alembic check` must report no drift.
- DB tests need `docker compose up -d db` locally (they skip otherwise) and always run in CI.
- Python 3.12, typed, Pydantic v2, async where I/O happens. Keep CI green: ruff + pytest.
- Small, focused commits with clear messages.

## Conventions

- Branches: `feat/`, `fix/`, `docs/` or `chore/` prefix, then a kebab-case name (`feat/criterion-judgment`).
- Commit subjects: imperative, sentence case, under 72 characters, no trailing period (`Add hybrid search with reciprocal rank fusion`).
- Commit bodies: explain why when it isn't obvious from the subject.
- PR titles follow the same style as commit subjects.
- No day or sprint labels anywhere: not in branches, commits, PR titles, docs or code.

## Status

Done:

- Policy schema, 4 policies validated in CI, read-only policy API.
- FHIR chunker, Bedrock Converse client (mocked in tests), smoke script.
- Postgres + pgvector, migrations, ingest with cached embeddings, hybrid search, observation lookups.
- Baseline numbers in `docs/benchmarks/ingest-and-retrieval.md` (dated 2026-10-10, not exactly reproducible).
- Synthea pinned to v4.0.0 with a sha256 check.
- All 4 policies re-encoded from governing NCD/LCD/article text, with verbatim quotes, sources and conditions; `scripts/verify_policy_quotes.py` checks them against the CMS Coverage API.

Next: add a required `evidence_stage` field to every criterion (`chart`, `request`, `provider_attestation`, `post_service`) as its own `feat/` PR; show the stage-grouped list for review before committing.

After that (core pipeline): per-criterion judgment on Bedrock with structured output and quote verification → combine via variant logic → PAS-shaped Claim bundle.
