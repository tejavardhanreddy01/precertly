# Evals

Forty hand-labeled cases (10 per procedure) with known answers, scored on every PR that touches prompts, retrieval or models. The headline metric is the **false-approval rate**: scored criteria the agent marks `met` when the label is `not_met` or `insufficient`. Both labels lead to a non-affirmed decision in practice, so both count. Labeling rules are in [GUIDELINES.md](GUIDELINES.md).

## Case format

One YAML file per case in `evals/cases/<policy>/`, plus its FHIR bundle in `evals/bundles/`. Bundles are not edited by hand: `evals/build_cases.py` adds each case's history to a trimmed Synthea patient (`evals/template/patient.json`), so the expected outcome is known by construction and a bundle can always be rebuilt.

```bash
uv run python evals/build_cases.py           # rebuild evals/bundles/
uv run python evals/build_cases.py --check   # fail if a committed bundle is stale
```

A case file, shortened (see `evals/cases/facet/facet-02.yaml` for a full one):

```yaml
id: facet-02
policy: facet-joint-interventions
policy_version: { LCD L38773: "32", Article A58364: "42" }
variant: initial-rfa
request:
  date: 2026-10-05
  codes: ["64635", "64636"]
  region: lumbar
  levels: [L4-5, L5-S1]
bundle: bundles/facet-02.json
expected_overall: not_met        # checked against the outcome computed from the labels
changed_labels:                  # why a label differs from the clean template
  - criterion: two-mbbs-80pct-sustained-relief
    quote: with each one providing a consistent minimum of 80% sustained relief
    why: The second block reached 71%.
expected:                        # one label per chart- or request-stage criterion
  - criterion: two-mbbs-80pct-sustained-relief
    outcome: not_met
    fact: Block 2 gave 71% relief (NRS 7/10 to 2/10).
    evidence: [Observation/pain-post-mbb2, DocumentReference/note-mbb2]
  - criterion: levels-1-to-2
    outcome: met
    fact: The request covers two levels.
    evidence: []
```

`tests/test_eval_cases.py` checks every case: all scored criteria are labeled, cited resources exist in the bundle and survive chunking, the computed overall outcome matches the case plan, and the committed bundle matches the build script.

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
