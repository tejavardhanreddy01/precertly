# Evals

Forty hand-labeled cases (10 per procedure) with known answers, scored on every PR that touches prompts, retrieval or models. The headline metric is the **false-approval rate**: criteria marked met when the label says not met.

## Case format

One YAML file per case in `evals/cases/`, plus the edited FHIR bundle in `evals/bundles/`. Build each case by copying a Synthea bundle and editing it so the expected outcome is known by construction.

```yaml
id: facet-rfa-014
policy: facet-joint-interventions
variant: initial-rfa
procedure: "64635"
bundle: bundles/facet-rfa-014.json
expected:
  - criterion: two-mbbs-80pct-relief
    outcome: not_met          # second block gave 70% relief
    evidence: ["Observation/pain-post-mbb2"]
  - criterion: conservative-care-3-months
    outcome: met
    evidence: ["Procedure/pt-0141"]
  - criterion: no-untreated-radiculopathy
    outcome: insufficient     # note mentions leg pain, no workup
tags: [adversarial, near-miss-threshold]
```

Outcomes: `met`, `not_met`, `insufficient`.

## Mix per procedure

- 5 clean cases: clearly meets or clearly fails.
- 5 adversarial: near-miss threshold, contradictory notes, evidence only in free text, missing data, stale policy version, similar-but-irrelevant evidence.

## Metrics

| Metric | Target |
| --- | --- |
| False-approval rate | under 2% |
| Criterion accuracy | over 90% |
| Citation precision | over 90% |
| Judge agreement with human labels | report Cohen's kappa |
| Cost and latency per case | report p50 / p95 |

Targets are starting goals. Publish the real numbers, including failures.
