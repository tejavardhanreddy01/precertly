# Precertly: guide for Claude Code

Precertly checks prior-authorization criteria against a FHIR R4 patient chart, cites evidence for every verdict, and drafts a PAS-shaped request for human approval. It is a public portfolio project: code quality, honest evals and clear commits matter as much as features.

Design spec (source of truth for scope and architecture): https://claude.ai/code/artifact/afc367f6-4fae-4200-ac8a-679032c62cbf

## Commands

```bash
uv sync                                         # install
uv run uvicorn precertly.api.main:app --reload  # API at :8000/docs
uv run pytest -q                                # tests
uv run ruff check . && uv run ruff format .     # lint + format
uv run pyright                                  # type check src/ (CI job: typecheck)
uv run pre-commit install                       # once per clone: ruff + pytest before every commit
data/synthea/generate.sh 10                     # synthetic FHIR patients (Java 11+)
uv run python evals/build_cases.py [--check]    # rebuild or verify the eval case bundles
uv run python -m precertly.evals.run --estimate  # size of an eval run; without --estimate it calls Bedrock (ask first)
uv run python -m precertly.evals.rules_check [--table | --recompute <results.json>]  # rules vs labels, offline
uv run python -m precertly.evals.prompt_check <results.json>  # rebuild every recorded prompt and check its hash
uv run python -m precertly.evals.rescore <results.json> --out <derived.json>  # re-check recorded answers with the current verifier, offline
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
- `src/precertly/judgment/` – per-criterion verdicts with verified quotes, combined by the variant logic; only `chart` and `request` stage criteria are judged
- `src/precertly/evals/` – eval case schema, scorer, the runner (`python -m precertly.evals.run`), `compare`, `rules_check`, `prompt_check` and `rescore`
- `src/precertly/settings.py` – `PRECERTLY_*` settings (model id, region, AWS profile)
- `data/policies/*.yaml` – the 4 coverage policies as atomic criteria + all_of/any_of logic per variant
- `data/synthea/` – patient generator (output is git-ignored)
- `evals/` – labeled cases and scorers (see evals/README.md)

## Rules

- Synthetic data only. Never add real patient data. Never commit bulk Synthea output (`data/synthea/output`). Curated eval fixtures in `evals/` (the trimmed template patient and the case bundles) are committed and must match `evals/build_cases.py`; rebuild them with that script, never by hand.
- Policy criteria come only from governing NCD, LCD or LCD-article text, never from MAC checklists, summary pages or memory. Each criterion carries a verbatim `quote`, the `source_document` and the `source_section`. Encode only thresholds the text states, exactly as stated; if the text is silent, leave it out and add a note. Criteria that apply only in some situations get a `condition`.
- After editing a policy, run `uv run python scripts/verify_policy_quotes.py` (needs network) and keep fetched documents out of the repo. Never copy CPT descriptors.
- Every verdict the agent produces must cite FHIR resource ids and verbatim quotes that are verified against the source text.
- The agent never outputs a denial on its own: outcomes are met, not_met or insufficient, and a human decides.
- A verdict whose quote is not found in the cited chunk, or a met / not_met verdict with no citation, becomes `insufficient`. Never weaken this to make a result look better.
- Schema changes go through an Alembic migration; `uv run alembic check` must report no drift.
- DB tests need `docker compose up -d db` locally (they skip otherwise) and always run in CI.
- Python 3.12, typed, Pydantic v2, async where I/O happens. Keep CI green: the `lint`, `typecheck`, `test` and `security` jobs.
- The pre-commit hook (ruff check, ruff format --check, pytest -q) must pass; don't bypass it with `--no-verify`.
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
- Required `evidence_stage` on every criterion (`chart`, `request`, `provider_attestation`, `post_service`, `payer`), defined relative to the request being decided.
- Annotation guideline (`evals/GUIDELINES.md`) and the facet case plan (`evals/cases/facet/PLAN.md`).
- Eleven facet eval cases (`evals/cases/facet/`), with bundles built by `evals/build_cases.py` and tests tying each case to the policy, its bundle and the case plan.
- The judgment step: per-criterion verdicts on Bedrock with verified quotes, combined by the variant logic; a deterministic rules layer (`judgment/rules.py`) that decides or fixes facts from coded data; the partial path, where a computed fact is cited as `[C1]` under per-branch citation rules (`PARTIAL_BRANCHES`).
- Eval runner with resume, shadow model-only calls and a partial-path review list; `compare` for marking run pairs; `rules_check` (offline, `--table`, `--recompute`); `prompt_check`, which rebuilds every recorded prompt against its hash. Results and pre-registered experiments are in `evals/results/NOTES.md` (noise floor 14 of 184).
- Verifier version 2: every conditional criterion names its `condition_evidence` (`chart` or `request.<field>`), and a not-applicable verdict must cite it, strictly both ways (open issue 4 closed; experiment 1.1 rescored offline with `evals.rescore`).

Next: experiment 2, line-level evidence handles (whole-note quotes always verify). Pre-register it in `evals/results/NOTES.md` before running.

After that: experiment 3, retrieval by evidence type (the facet-11 retrieval miss); then the PAS-shaped Claim bundle; then eval cases for PAP, spinal cord stimulator and bariatric surgery.
