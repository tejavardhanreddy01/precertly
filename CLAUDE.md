# Precertly: guide for Claude Code

Precertly checks prior-authorization criteria against a FHIR R4 patient chart, cites evidence for every verdict, and drafts a PAS-shaped request for human approval. It is a public portfolio project: code quality, honest evals and clear commits matter as much as features.

Design spec (source of truth for scope and architecture): https://claude.ai/code/artifact/afc367f6-4fae-4200-ac8a-679032c62cbf

## Commands

```bash
uv sync                                         # install
uv run uvicorn precertly.api.main:app --reload  # API at :8000/docs
docker build -t precertly-api .                 # API image (CI job docker-build); run: docker run --rm -p 8000:8000 precertly-api
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
uv run python -m precertly.pas [--check]         # draft PAS request bundles + review envelopes in evals/pas/ (never submitted)
uv run python scripts/validate_pas.py [--tx]     # HL7 FHIR Validator on the drafts (offline; CI job pas-validate)
uv run python -m precertly.fhir <bundle-or-dir>  # chunk summary for FHIR bundles
docker compose up -d db                         # Postgres 16 + pgvector on localhost:5433
uv run alembic upgrade head                     # apply migrations (PRECERTLY_DATABASE_URL, or PRECERTLY_DB_HOST/_NAME/_USER/_PASSWORD[/_PORT])
uv run python -m precertly.ingest <path> [--estimate | --embed]  # --embed costs money; ask first
uv run python -m precertly.db.check             # read-only: pgvector version, table row counts, TLS (SELECT only; prints no connection values)
uv run python scripts/smoke_llm.py              # one real Bedrock call (costs money; ask first)
cd infra && uv sync --locked && npm ci          # CDK app: own Python env and lock, CLI pinned in package.json (Node 24)
cd infra && uv run pytest -q                    # CDK assertion tests
cd infra && npx cdk synth --no-lookups -c budgetEmail=ci@example.invalid -c imageTag=sha-000000000000  # no AWS calls (the budget email is required context and never committed); never bootstrap, deploy or look up context without the owner's go-ahead; deploys are by stack name from the owner's Mac, never --all
scripts/deploy.sh image [--yes] [--dry-run]        # owner's Mac only: build linux/arm64 and push sha-<12 hex> to ECR (touches AWS; ask first)
BUDGET_EMAIL=<address> scripts/deploy.sh stack <name> [--dry-run]  # one stack by name, never --all (touches AWS; ask first); --dry-run is safe
scripts/deploy.sh up <name> [--yes] [--dry-run]    # owner's Mac only: image first for precertly-app, then cdk diff, "Deploy? [y/N]" (always asked), then stack (touches AWS; ask first)
scripts/deploy.sh status [--dry-run]              # read-only AWS queries: stacks, service, task definition, NAT, RDS, ECR (still an AWS call; ask first)
scripts/deploy.sh smoke [--bedrock] [--dry-run]   # owner's Mac only: scale to 1, /health and /policies via ECS Exec, always back to 0; --bedrock adds one real call (touches AWS; ask first)
scripts/deploy.sh down [--dry-run]                # set desired count 0 and confirm no task runs (touches AWS; ask first)
scripts/deploy.sh tools migrate|load|check [--dry-run]  # one precertly-tools task on the data stack's database; always stopped afterwards; load asks first and --yes does not skip that (touches AWS and Bedrock; ask first)
shellcheck scripts/*.sh                         # CI lint job
```

## Layout

- `src/precertly/policies/` – policy schema (Pydantic) and loader
- `src/precertly/api/` – FastAPI app
- `src/precertly/fhir/` – FHIR bundle → `Chunk`s (id `Type/id#n`; notes are verbatim slices with char offsets)
- `src/precertly/llm/` – Bedrock Converse client: structured output, tokens and latency per call
- `src/precertly/db/` – SQLAlchemy models (spec data model) and async session; `check.py` is the read-only database check run by `deploy.sh tools check`; `connect.py` holds the shared engine arguments (`PRECERTLY_DB_POOL`, `PRECERTLY_DB_CONNECT_TIMEOUT`) and the first-connect retry used by `get_engine()`, `migrations/env.py`, ingest and the eval runner; migrations in `migrations/`
- `src/precertly/ingest/` – bundle → `chart_chunks`; embeds only notes, conditions, procedures, medication requests; vectors cached by sha256
- `src/precertly/retrieval/` – `hybrid_search` (full-text + pgvector, RRF) and `observations` (by code and date)
- `src/precertly/judgment/` – per-criterion verdicts with verified citations, combined by the variant logic; only `chart` and `request` stage criteria are judged; `lines.py` splits evidence into line handles for the experimental prompt version 4 (`v4.py` holds its output schema); prompt version 3 is the default
- `src/precertly/evals/` – eval case schema, scorer, the runner (`python -m precertly.evals.run`), `compare`, `rules_check`, `prompt_check` and `rescore`
- `src/precertly/pas/` – draft Da Vinci PAS 2.2.1 request bundles and review envelopes from recorded verdicts; reviewed diagnosis map (`diagnosis_map.yaml`); never submitted, no verdicts in the bundle
- `src/precertly/settings.py` – `PRECERTLY_*` settings (model id, region, AWS profile); `sqlalchemy_url()` builds the database URL from `PRECERTLY_DATABASE_URL` or the `PRECERTLY_DB_*` parts
- `infra/` – AWS CDK app in Python, separate project (own `pyproject.toml`, `uv.lock`, `package.json` for the pinned CDK CLI); three environment-agnostic stacks (`precertly-base`, `precertly-app`, `precertly-data`); `precertly-base` (`base_stack.py`) holds the budget and the ECR repository (no IAM: the account denies `iam:*Provider*`), `precertly-app` (`app_stack.py`) holds the VPC (two public subnets and two isolated `database` subnets, no NAT or endpoints), the ECS cluster, the Fargate API service at desired count 0 and its task and execution roles, and requires the CDK context `imageTag`; `precertly-data` (`data_stack.py`, depends on `precertly-app`, which never refers to it) holds the Aurora Serverless v2 PostgreSQL cluster (minimum capacity 0), its generated secret, the DB and tools security groups, and the `precertly-tools` task definition with its own roles and log group; shared names in `names.py`
- `data/policies/*.yaml` – the 4 coverage policies as atomic criteria + all_of/any_of logic per variant
- `data/synthea/` – patient generator (output is git-ignored)
- `evals/` – labeled cases and scorers (see evals/README.md)
- `Dockerfile`, `.dockerignore` – the API image (bases pinned by multi-arch digest): editable `src/` layout (policies and bundles are found relative to the package file), non-root, port 8000, urllib health check; holds no credentials

## Rules

- Synthetic data only. Never add real patient data. Never commit bulk Synthea output (`data/synthea/output`). Curated eval fixtures in `evals/` (the trimmed template patient and the case bundles) are committed and must match `evals/build_cases.py`; rebuild them with that script, never by hand.
- Policy criteria come only from governing NCD, LCD or LCD-article text, never from MAC checklists, summary pages or memory. Each criterion carries a verbatim `quote`, the `source_document` and the `source_section`. Encode only thresholds the text states, exactly as stated; if the text is silent, leave it out and add a note. Criteria that apply only in some situations get a `condition`.
- After editing a policy, run `uv run python scripts/verify_policy_quotes.py` (needs network) and keep fetched documents out of the repo. Never copy CPT descriptors.
- Every verdict the agent produces must cite FHIR resource ids and verbatim quotes that are verified against the source text.
- A drafted PAS request is never submitted and never carries a verdict; every note cited by any verdict is attached, favourable or not. Placeholders stay in the project namespace: never an NPI, TIN or NAIC. X12 and CPT codes are codes only, with X12 codes only from the IG's examples.
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
- Reports: lead with the result and any decision needed, then at most 10 bullets of detail. Put long tables and full analyses in a file in the repo and link it instead of pasting.

## Working agreement

- Deploys run from the owner's Mac. No GitHub Actions job may assume an AWS role or hold AWS keys.
- Never merge a PR. The owner merges manually with "Create a merge commit" after CI is green.
- Do not use interactive question popups. Put any decision at the top of the plan or report under "Decisions needed" with a recommendation, then stop.
- Plans are markdown the owner pastes into the reviewer chat. Write one consistent version, never a header that supersedes old text below it.
- Branch prefixes: `feat/`, `fix/`, `docs/`, `chore/` (see Conventions).
- If a task prompt contains an unfilled placeholder such as `<...>`, stop and report it instead of guessing.
- Update README.md after each push or merge to main in later phases.
- Never run Bedrock or AWS calls or other paid actions without the owner's explicit go-ahead.
- Edit PR bodies with `gh api -X PATCH repos/<owner>/<repo>/pulls/<n> -f body=...`: `gh pr edit` fails on the deprecated Projects (classic) GraphQL.
- Every PR in the AWS deployment sequence updates README.md and the Status section of this file in the same PR. Status states only what is true at that merge, with the date it was checked: nothing counts as deployed or running while the service is at zero tasks or the database is destroyed.
- Report style stays as in the Reports rule under Conventions.

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

- Experiment 2, line-level evidence handles: failed its pre-registration on (b), (c) and (f) and was reverted. Prompt version 3 is the default (`PRECERTLY_PROMPT_VERSION`); version 4 is experimental and opt-in (`--prompt-version 4`), kept so experiment 2 is reproducible. Kept for both: invalidations by kind, causes per difference in `compare`, free-text detection.

- Draft PAS 2.2.1 request bundles for the 11 facet cases (`evals/pas/`), validated with zero errors by the pinned HL7 FHIR Validator (CI job `pas-validate`); not ready to send (no covered diagnosis code, placeholder participants; open issue 5).

- Container image: the `docker-build` CI job builds the API image without pushing it, runs it, waits for the health check, requests `/health` and `/policies` and runs `alembic heads`. Base images are pinned by multi-arch digest; uv has its own `FROM` stage so Dependabot's Docker ecosystem follows it; Python minor and major image updates are ignored there and made by hand. Verified locally on arm64 and in CI on 2026-10-17. Not deployed at that date; see the 2026-10-18 entry below.

- Database connection from separate settings: `PRECERTLY_DATABASE_URL` wins; otherwise `PRECERTLY_DB_HOST`, `_NAME`, `_USER` and `_PASSWORD` are all required once any `PRECERTLY_DB_*` is set (`_PORT` defaults to 5432); with none set the local default is unchanged. The password is a secret in settings and stays out of repr and errors. Checked locally on 2026-10-17 against the compose database; nothing is deployed.

- CDK scaffold (`infra/`, checked 2026-10-17): three empty, environment-agnostic stacks with fixed availability zones `us-east-2a` and `us-east-2b`, assertion tests, and the CI job `infra-synth` (tests and `cdk synth --no-lookups`, no credentials). `infra/` has its own environment and lock, checked by the `typecheck` and `security` jobs and by Dependabot. The stacks were empty at that point; nothing was bootstrapped or deployed, and no AWS call was made for it.

- Base stack deployed, nothing running (`infra/`, checked 2026-10-18): `precertly-base` defines the annual budget alarms and the `precertly-api` ECR repository (two resources, no IAM), with assertion tests, including a guard that it has no OIDC provider, role or policy. This account's managed organization policy denies `iam:*Provider*`, so GitHub Actions cannot assume an AWS role; deploys run from the owner's Mac. Owner-confirmed on 2026-10-18 from AWS CLI checks (not made by Claude): the CDK is bootstrapped (toolkit stack, us-east-2) and `precertly-base` was deployed from the owner's Mac, stack `CREATE_COMPLETE`; ECR repository `precertly-api` is IMMUTABLE with scan on push and both lifecycle rules; one annual 60 USD budget has 3 ACTUAL alerts (20/40/55) and 1 FORECASTED at 100%; two older console-made monthly budgets, `precertly-25` and `precertly-50`, exist outside the stack. `scripts/deploy.sh image` pushed `sha-951a31b14a9a` (88 MB, arm64) and a second run skipped it as already pushed. Nothing runs: `precertly-app` and `precertly-data` are still empty, with no service and no database. The API is not deployed or reachable; use "deployable" for it until a smoke check passes.

- Deploy script (`scripts/deploy.sh`, checked 2026-10-18): `image` and `stack <name>`, each with `--dry-run`; the dry run is tested in the `test` job and the script is shellchecked in `lint`. CI never runs a real deploy. Say "deployable", not "deployed", until the service runs and a smoke check passes (`docs/deployment.md`).

- App stack deployed at zero and smoke-tested (`infra/app_stack.py`; deployment and smoke checks owner-confirmed 2026-10-18 from AWS CLI output, not run by Claude; the definition checked by synthesis and assertion tests): VPC with two public subnets and no NAT, endpoints or load balancer; a task security group with no inbound rules; cluster `precertly`; Fargate service `precertly-api` (ARM64, 256/512) at desired count 0 with ECS Exec; roles `precertly-ecs-task` and `precertly-ecs-execution`; log group `/precertly/api` (7 days). The task has no database and no secrets. `deploy.sh stack` passes `-c imageTag=<HEAD tag>`, and for `precertly-app` first checks the image is in ECR. Confirmed by the owner: `precertly-app` was deployed from the owner's Mac (VPC with 2 public subnets, no NAT or load balancer, cluster `precertly`, service `precertly-api` at desired count 0); one task was started, `/health` and `/policies` returned 200 through ECS Exec, logs reached CloudWatch `/precertly/api`, and the service was scaled back to zero. `scripts/smoke_llm.py` ran in the task via ECS Exec and made one real Bedrock call with the task role (`us.amazon.nova-2-lite-v1:0`, us-east-2, 1003/52 tokens, 801 ms) that returned a valid structured result. Titan embeddings were not called, so that permission is unverified. Say "deployed and smoke-tested at zero", not "live" or "reachable": there is no inbound path, no database and no judgment run against a chart. The ECS health check does not come from the image (ECS ignores the Dockerfile `HEALTHCHECK`); the explicit container health check in `app_stack.py` was not yet deployed then (it was on 2026-10-20, task definition revision 3; see the data stack entry). `precertly-data` was still empty then.

- Deploy automation (`scripts/deploy.sh`, checked 2026-10-18): new commands `up <name>`, `status`, `smoke [--bedrock]` and `down`, all with `--dry-run`; `BUDGET_EMAIL` can come from a gitignored `.env.local` (parsed, never sourced). `up` runs the image step for `precertly-app`, shows `cdk diff` with the email redacted and always asks "Deploy? [y/N]" (`--yes` skips neither); `smoke` refuses unless the service has no task and an EXIT/INT/TERM trap always scales it back to 0 and waits for no running task. These are tested with stub `aws`, `npx` and `docker` and with `--dry-run` only (`tests/test_deploy_script.py`, shellcheck clean); **none of the new commands has been run against real AWS**, and nothing new is deployed or verified by this change. The ECS Exec command string and the `--query` expressions are unconfirmed against the live API until the owner runs them.

- Smoke fix: ECS Exec readiness (`scripts/deploy.sh smoke`, checked 2026-10-18): the owner's first real `smoke` run failed with `InvalidParameterException` "execute command agent isn't running" because the checks ran the moment the task was RUNNING (the scale-back to 0 still worked). `smoke` now waits up to 120 s for the `ExecuteCommandAgent` managed agent to be RUNNING, retries an exec check up to 3 times only on `InvalidParameterException`, `TargetNotConnectedException` or "agent isn't running" (any other failure fails at once), and after the exec checks waits up to 120 s for the ECS health status HEALTHY, recording PASS or FAIL for each. The scale-back trap and the refuse-unless-at-zero guard are unchanged. This is tested with stub `aws` only (`tests/test_deploy_script.py`, shellcheck clean); it has not been run against real AWS since the fix, so it is unverified until the owner runs `smoke` again.

- Database connection behaviour for a database that scales to zero (`src/precertly/db/connect.py`, checked 2026-10-20): `PRECERTLY_DB_POOL` (`queue` default or `null`) and `PRECERTLY_DB_CONNECT_TIMEOUT` (default 60 s, positive) in settings; one helper builds the engine arguments for `get_engine()` and `migrations/env.py`; migrations, ingest and the eval runner probe the database once at the start with up to 3 attempts (2 s then 5 s) for transient connection failures only, never for authentication, permission or missing-database errors, and never re-running the work. `/health` makes no database call (tested). Checked by unit tests and against the local compose database. The tools task sets both (null pool, 60 s); on 2026-10-20 a `tools check` connected to the paused Aurora cluster within the 60 s timeout (owner-confirmed; the resume time was not measured).

- Data stack deployed and used through the tools task (`infra/data_stack.py`; owner-confirmed 2026-10-20 from the owner's AWS CLI and script output, us-east-2, not checked by Claude): Aurora Serverless v2 PostgreSQL 16.13 (configured 0 to 2 ACU, auto-pause 300 s, one `db.serverless` writer, `storage_encrypted=True` with no key given, not publicly accessible, 1-day backups, no deletion protection, everything removed with the stack) in two isolated subnets of the app VPC, a generated secret, a DB security group that takes port 5432 from the tools security group only, and the `precertly-tools` task definition with its own roles (the task role may only call `bedrock:InvokeModel` on Titan Text Embeddings V2) and log group `/precertly/tools`. Verified: `precertly-app` was updated first (additions only; task definition revision 3, image `sha-30bc9a4e4f79`, container health check set), then `precertly-data` was created (`CREATE_COMPLETE`), so the `Fn::GetStackOutput` cross-stack references resolved; `status` shows the three stacks complete, the service at desired 0, running 0, pending 0, Aurora available (16.13, 0.0 to 2.0 ACU), 0 NAT gateways and 4 images in ECR; `tools check` before the migration showed server 16.13, pgvector not installed, TLS yes, all 8 tables missing, exit 1 (expected), the first real `deploy.sh tools` run (`run-task`, log tail, stop and exit code all worked); `tools migrate` was run (output not pasted) and the later check shows all 8 tables and pgvector 0.8.1, so the master user ran `CREATE EXTENSION vector` and the task resolved the five secret JSON keys; `tools load` (all 29 bundles) exited 0 with 237 Titan embedding calls, 29,463 tokens and 0 retries, the first real Titan call, which works from the tools task role; the check after the load shows `cases` 1, `chart_chunks` 175 (only the last bundle, `scs-06`) and every other table empty; connections use TLS (`pg_stat_ssl`) but the server certificate is not verified (asyncpg default); the cluster paused about 5 minutes after the load (CloudWatch `ServerlessDatabaseCapacity` 0.0 from 16:25 local) and a later `tools check` connected to the paused cluster within the 60 s timeout. The offline estimate of 210 unique texts was low because the vector cache is the chunks currently stored in `chart_chunks`, all 29 bundles share one case and each ingest replaces its chunks, so each bundle reuses only the previous bundle's vectors; a simulation of the ingest logic gives exactly 237. Still unverified: the API task role's Titan permission, the resume time from a paused database, actual spend (Cost Explorer lags), the ECR scan of `sha-30bc9a4e4f79`, and the service in front of the database (the API task has no database and no secrets). The service is at zero tasks and the database holds only the last bundle: nothing is live.

Next: the AWS data phase is finished (2026-10-20). The next phase is the web and UI phase, starting with recorded mode: a static export script and the UI, with no AWS.

Experiments after launch, each pre-registered in `evals/results/NOTES.md` before running: experiment 2b (the model copies the quote, which must fall within the cited lines; 3-line cap), then experiment 3, retrieval by evidence type (the facet-11 retrieval miss). Do not loosen a verification check to recover verdicts.
