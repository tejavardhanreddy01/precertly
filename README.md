# Precertly

**Prior authorization, checked against the chart, with every answer cited.**

Precertly reads a patient chart (FHIR R4) and a requested procedure, checks each payer coverage criterion against the chart, cites the exact evidence for each verdict, and drafts a prior-authorization request shaped like the HL7 Da Vinci PAS format. A human reviews and approves. It never auto-denies.

> 🚧 **In active development.** Live demo, eval scorecard and architecture diagram land here as they ship.

## Why

Under the CMS Interoperability and Prior Authorization rule (CMS-0057-F), affected payers must run FHIR-based prior-authorization APIs by **January 1, 2027**. Precertly is built against that world: FHIR in, standards-shaped requests out, and measured accuracy instead of claims.

## Procedures covered

| Procedure | Policy |
| --- | --- |
| Facet joint interventions (medial branch blocks, radiofrequency ablation) | LCD L38773 |
| Spinal cord stimulator (trial and permanent) | NCD 160.7 + MAC documentation rules |
| PAP devices for obstructive sleep apnea | LCD L33718 |
| Bariatric surgery | NCD 100.1 |

Each policy is encoded in [`data/policies/`](data/policies) as atomic criteria plus AND/OR logic per request type, with its public sources and a verification date. Synthetic data only: no real patient information is used anywhere.

## Run locally

```bash
uv sync
uv run uvicorn precertly.api.main:app --reload   # http://localhost:8000/docs
uv run pytest
```

Try `GET /requirements/64635` to see what a lumbar facet ablation request must show.

## Roadmap

- [x] Policy criteria for 4 procedures, validated in CI
- [ ] Agent pipeline: evidence search and cited verdicts (Bedrock)
- [ ] Eval harness: 40 labeled cases, CI gate
- [ ] AWS deployment (AgentCore, ECS, RDS, CDK)
- [ ] Reviewer UI, MCP server, demo mode

## License

MIT
