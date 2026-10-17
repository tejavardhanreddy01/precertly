# Evals

Hand-labeled cases with known answers: 29 labeled cases in four sets (table below). **Labeled** means a case file carries an expected label for every chart- and request-stage criterion of its variant (326 labeled criteria in all). **Scored** means a complete recorded run in [results/](results) has a verdict for every labeled criterion of the case. Only the 11 facet cases have been scored, so every measured number in this repository is facet-only; the PAP, bariatric and spinal cord stimulator bundles are tested offline only. Post-launch target, not built: 10 per procedure (40 in all), scored in CI on every PR that touches prompts, retrieval or models. Runs are made by hand for now, and their results and pre-registered experiments are in [results/NOTES.md](results/NOTES.md). The headline metric is the **false-approval rate**: scored criteria the agent marks `met` when the label is `not_met` or `insufficient`. Both labels lead to a non-affirmed decision in practice, so both count. Labeling rules are in [GUIDELINES.md](GUIDELINES.md).

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
  service_date: 2026-10-19
  procedure: thermal radiofrequency ablation
  technique: radiofrequency_ablation
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
    fact: Block 2 gave (7 - 2) / 7 = 71% relief.
    evidence: [Observation/pain-post-mbb2, DocumentReference/note-mbb2]
  - criterion: levels-1-to-2
    outcome: met
    fact: The request covers two levels.
    evidence: [request.levels]   # request-stage labels cite the request field
```

`tests/test_eval_cases.py` checks every case: all scored criteria are labeled, cited resources exist in the bundle and survive chunking, the computed overall outcome matches the case plan, and the committed bundle matches the build script.

Outcomes: `met`, `not_met`, `insufficient`.

Some criteria have a `condition` in the policy file (for example, a rule that applies only to a repeat procedure). When the condition does not hold for a case, label the criterion `not_applicable` in `expected`. The label is correct only if the agent also reports that the condition does not hold, and it counts in criterion accuracy. `not_applicable` is never an agent outcome: the agent's outcomes stay `met`, `not_met` and `insufficient`. The agent's "condition does not hold" stands only if it cites the evidence named by the criterion's `condition_evidence` (the chart, or a specific request field); otherwise it becomes `insufficient`.

## Running

```bash
uv run python -m precertly.evals.run --estimate   # calls, tokens and time; no AWS, no database
uv run python -m precertly.evals.run              # real run: needs the local database and AWS credentials
uv run python -m precertly.evals.run --prompt-version 4   # experimental line-handle prompt (experiment 2); 3 is the default
```

A real run ingests each case, judges every chart- and request-stage criterion, and writes all verdicts, rationales and citations to `evals/results/`. Where the label is not `not_applicable`, a criterion the agent finds not applicable counts as `met` when scoring, because that is its effect on the case outcome.

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

The counts below are the single source for the case counts in the docs. `tests/test_doc_counts.py` recomputes every cell from the case files, the PLAN.md type columns and the recorded runs, and checks the counts stated in the README files and `docs/QUALITY.md` against this table.

<!-- case-counts -->
| Set | Folder | Cases | Labeled criteria | Overall: met / not_met / insufficient | Clean / adversarial | Scored |
| --- | --- | --- | --- | --- | --- | --- |
| Facet | [`cases/facet`](cases/facet/PLAN.md) | 11 | 184 | 3 / 6 / 2 | 6 / 5 | 11 |
| PAP | [`cases/pap`](cases/pap/PLAN.md) | 6 | 54 | 2 / 2 / 2 | 2 / 4 | 0 |
| Bariatric | [`cases/bariatric`](cases/bariatric/PLAN.md) | 6 | 22 | 1 / 2 / 3 | 2 / 4 | 0 |
| SCS | [`cases/spinal-cord-stimulators`](cases/spinal-cord-stimulators/PLAN.md) | 6 | 66 | 1 / 3 / 2 | 3 / 3 | 0 |
| Total | | 29 | 326 | 7 / 13 / 9 | 13 / 16 | 11 |
<!-- /case-counts -->

The mix differs by set; there is no fixed 5 clean plus 5 adversarial per procedure. What each case set varies, from the Type column of its plan:

- **Facet** ([plan](cases/facet/PLAN.md)): clean cases (`facet-01`, `-07`), clean near misses (`-02`, `-03`), a clean request-stage case (`-08`) and a clean conditional case (`-09`). Adversarial: `facet-04`, `-05`, free text only (`-06`, `-10`) and a contradiction (`-11`).
- **PAP** ([plan](cases/pap/PLAN.md)): clean, meets (`pap-01`) and fails (`-02`). Adversarial: a near miss on a stated threshold (`-03`), a contradiction (`-04`), free text only on the alternative branch (`-05`) and missing evidence (`-06`).
- **Bariatric** ([plan](cases/bariatric/PLAN.md)): clean, meets (`bariatric-01`) and fails (`-02`). Adversarial: a near miss on a stated threshold (`-03`), missing evidence (`-04`, `-06`) and a contradiction (`-05`).
- **Spinal cord stimulators** ([plan](cases/spinal-cord-stimulators/PLAN.md)): clean, meets (`scs-01`) and fails (`-02`, `-06`). Adversarial: a near miss on a stated threshold (`-03`), missing evidence (`-04`) and a contradiction (`-05`).

Not covered by any case: a stale policy version (GUIDELINES rule 9 allows it) and prompt injection (listed under adversarial cases to add in [docs/QUALITY.md](../docs/QUALITY.md)). Similar-but-irrelevant evidence appears as background decoys in the bariatric and SCS bundles (see their plans), not as a case's changed fact.

## Metrics

| Metric | Target |
| --- | --- |
| False-approval rate | under 2% |
| Criterion accuracy | over 90% |
| Citation precision | over 90% |
| Judge agreement with human labels | report pooled Cohen's kappa and per-criterion raw agreement ([GUIDELINES.md](GUIDELINES.md#review-and-agreement)) |
| Cost and latency per case | report p50 / p95 |

Targets are starting goals. Publish the real numbers, including failures.
