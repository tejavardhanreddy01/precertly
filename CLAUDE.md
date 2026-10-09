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
```

## Layout

- `src/precertly/policies/` – policy schema (Pydantic) and loader
- `src/precertly/api/` – FastAPI app
- `data/policies/*.yaml` – the 4 coverage policies as atomic criteria + all_of/any_of logic per variant
- `data/synthea/` – patient generator (output is git-ignored)
- `evals/` – labeled cases and scorers (see evals/README.md)

## Rules

- Synthetic data only. Never add real patient data, and never commit generated Synthea output.
- Policy criteria must trace to a public source listed in the policy file. Don't invent thresholds; if a source is silent, leave it out and add a note.
- Every verdict the agent produces must cite FHIR resource ids and verbatim quotes that are verified against the source text.
- The agent never outputs a denial on its own: outcomes are met, not_met or insufficient, and a human decides.
- Python 3.12, typed, Pydantic v2, async where I/O happens. Keep CI green: ruff + pytest.
- Small, focused commits with clear messages.

## Status

- Day 1 done: policy schema + 4 policies validated in CI, read-only policy API, Synthea script.
- Next (core pipeline): FHIR bundle → text chunks with resource ids → hybrid search (Postgres FTS + pgvector) → per-criterion judgment on Bedrock with structured output and quote verification → combine via variant logic → PAS-shaped Claim bundle.
