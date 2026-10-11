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

Some criteria have a `condition` in the policy file (for example, a rule that applies only to a repeat procedure). When the condition does not hold for a case, label the criterion `not_applicable` in `expected`; the scorer leaves it out of accuracy and false-approval counts. `not_applicable` is a label for scoring only: the agent's outcomes stay `met`, `not_met` and `insufficient`.

## Evidence stages

Every criterion has an `evidence_stage`, defined relative to the request being decided:

| Stage | Meaning |
| --- | --- |
| `chart` | In the patient's record when this request is made |
| `request` | A property of the requested service |
| `provider_attestation` | A provider or supplier attests to it |
| `post_service` | Exists only after the requested service is performed |
| `payer` | The payer's own coverage rules or jurisdiction |

The same fact can sit at different stages for different requests. PAP adherence data does not exist when the first device is requested, but it is chart evidence for a continued-coverage request, which is the only variant those criteria appear in.

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
