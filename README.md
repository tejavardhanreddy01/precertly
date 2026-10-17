# Precertly

**Prior authorization, checked against the chart, with every answer cited.**

Precertly reads a patient chart (FHIR R4) and a requested procedure, checks each payer coverage criterion against the chart, cites the exact evidence for each verdict, and drafts a prior-authorization request shaped like the HL7 Da Vinci PAS format. A human reviews and approves. It never auto-denies.

> 🚧 **In active development.** Chart ingest, search, cited per-criterion verdicts and the eval harness work today, for facet joint interventions. The PAS-shaped request, the reviewer UI and the deployment are not built yet.

## Why

Under the CMS Interoperability and Prior Authorization rule (CMS-0057-F), affected payers must run FHIR-based prior-authorization APIs by **January 1, 2027**. Precertly is built against that world: FHIR in, standards-shaped requests out, and measured accuracy instead of claims.

## Procedures covered

| Procedure | Policy |
| --- | --- |
| Facet joint interventions (medial branch blocks, radiofrequency ablation) | LCD L38773 + billing article A58364 |
| Spinal cord stimulator (trial and permanent) | NCD 160.7 + LCD L36204 (Noridian JF) and article A57792 |
| PAP devices for obstructive sleep apnea | LCD L33718 + policy article A52467 |
| Bariatric surgery | NCD 100.1 |

Each policy is encoded in [`data/policies/`](data/policies) as atomic criteria plus AND/OR logic per request type. Every criterion quotes the governing NCD, LCD or article text it encodes, with the document version and the date it was retrieved; `scripts/verify_policy_quotes.py` re-checks the quotes against the CMS Coverage API. Synthetic data only: no real patient information is used anywhere.

## Run locally

```bash
uv sync
uv run uvicorn precertly.api.main:app --reload   # http://localhost:8000/docs
uv run pytest
uv run pyright                                   # type check
uv run pre-commit install                        # optional: run ruff and pytest before each commit
```

Chunk a FHIR bundle (or a directory of them) and see what the agent will be able to cite:

```bash
data/synthea/generate.sh 10                        # synthetic patients, needs Java 11+
uv run python -m precertly.fhir data/synthea/output/fhir
```

Store charts in Postgres (16, with pgvector) and search them:

```bash
docker compose up -d db                             # Postgres on localhost:5433
uv run alembic upgrade head
uv run python -m precertly.ingest data/synthea/output/fhir --estimate   # size of the embedding job, offline
uv run python -m precertly.ingest data/synthea/output/fhir              # one case per bundle, no AWS calls
uv run python -m precertly.ingest data/synthea/output/fhir --embed      # also embed new text (real Bedrock calls)
```

Only notes, conditions, procedures and medication requests are embedded (Titan Text Embeddings V2, 1024 dimensions). Labs and vitals are found by code and date instead. Vectors are cached by a hash of the embedded text, so re-ingesting a chart does not pay for the same text twice. Ingest is idempotent per patient: running it again replaces that patient's chunks instead of creating a second case. Search is hybrid: Postgres full-text plus pgvector, merged with reciprocal rank fusion.

First measured numbers (tokens, cost, time, latency and a hand-judged search) are in [docs/benchmarks/ingest-and-retrieval.md](docs/benchmarks/ingest-and-retrieval.md).

The LLM runs on Amazon Bedrock. Model, region and AWS profile come from `PRECERTLY_BEDROCK_MODEL_ID`, `PRECERTLY_AWS_REGION` and `PRECERTLY_AWS_PROFILE` (defaults: `us.amazon.nova-2-lite-v1:0`, `us-east-2`, `precertly`). `uv run python scripts/smoke_llm.py` makes one real call to check the setup; tests never touch the network. Database tests use the `precertly_test` database that compose creates and are skipped when it is not running.

Try `GET /requirements/64635` to see what a lumbar facet ablation request must show.

## How a criterion is judged

Every criterion has an `evidence_stage`. Only `chart` and `request` criteria get a verdict; the rest (provider attestations, post-service and payer facts) go to the reviewer as a checklist.

1. **Rules first.** Where coded data settles a criterion (conditions, session counts, intervals, pain-score relief), a deterministic rule in [`judgment/rules.py`](src/precertly/judgment/rules.py) decides it, or fixes a computed fact the model must work from. A rule never decides `not_met` from missing data; missing coded data goes to the model.
2. **Then the model.** The rest are judged on Bedrock from the retrieved chunks, through one forced output schema and no other tools.
3. **Then verification.** Every quote must be found in the chunk it cites. A verdict whose quote does not verify, a `met` / `not_met` with no citation, or a "condition does not hold" that does not cite the evidence its policy names (the chart, or a specific request field), becomes `insufficient`.
4. **Then the logic.** The policy's `all_of` / `any_of` logic combines the verdicts in code, never by the model.

Outcomes are `met`, `not_met` or `insufficient`. Precertly never denies; a human decides. The invariants, threat model and test strategy behind this are in [docs/QUALITY.md](docs/QUALITY.md), including the gaps that are known and not yet fixed.

## Evals

Eleven labeled facet cases ([`evals/cases/facet/`](evals/cases/facet)) are built on a trimmed Synthea patient by [`evals/build_cases.py`](evals/build_cases.py), so each expected answer is known by construction. Labeling rules are in [evals/GUIDELINES.md](evals/GUIDELINES.md) and the case format in [evals/README.md](evals/README.md).

```bash
uv run python evals/build_cases.py --check                  # committed bundles match the build script
uv run python -m precertly.evals.run --estimate             # size of a run: no AWS, no database
uv run python -m precertly.evals.run --rules                # real run: local database and Bedrock (costs money)
uv run python -m precertly.evals.compare --mark <a> <b>     # compare two runs from the same commit
uv run python -m precertly.evals.rules_check                # rule results against the labels, offline
uv run python -m precertly.evals.prompt_check <results.json>  # rebuild every recorded prompt, check its hash
uv run python -m precertly.evals.rescore <results.json> --out <derived.json>  # re-check recorded answers offline
```

Latest pair (experiment 1.1, Amazon Nova 2 Lite, with rules), from [evals/results/NOTES.md](evals/results/NOTES.md):

| Metric | Run a | Run b |
| --- | --- | --- |
| Criteria right | 177 of 184 | 176 of 184 |
| Cases right | 7 of 11 | 7 of 11 |
| False approvals (verdict / case) | 1 / 1 | 1 / 1 |

Experiment 2 (line-level evidence handles) failed its pre-registration (145 and 160 of 184 criteria right: the model's first-words echo often names the line before the handle it cites) and was reverted. Prompt version 3 is the default; version 4 is experimental and opt-in (`--prompt-version 4` or `PRECERTLY_PROMPT_VERSION=4`).

Two runs on the same commit differ on up to 14 of 184 verdicts, so an effect is claimed only for a verdict that is wrong in every baseline run and right in every experiment run. The remaining false approval (facet-11) is a retrieval miss. Experiments are pre-registered in the notes before they run, and failures are reported with the rest.

## Roadmap

- [x] Policy criteria for 4 procedures, validated in CI
- [x] Chart ingest and hybrid search (Postgres full-text + pgvector)
- [x] Cited per-criterion verdicts on Bedrock, with deterministic rules and quote verification
- [ ] Line-level evidence handles (experiment 2) and retrieval by evidence type (experiment 3)
- [ ] PAS-shaped request bundle
- [ ] Eval harness: 40 labeled cases (11 facet cases so far), CI gate
- [ ] AWS deployment (AgentCore, ECS, RDS, CDK)
- [ ] Reviewer UI, MCP server, demo mode

## License

MIT

CPT® is a registered trademark of the American Medical Association. No CPT descriptors are included in this repository.
