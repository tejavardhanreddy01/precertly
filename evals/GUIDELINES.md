# Annotation guideline

How expected outcomes in `evals/cases/` are labeled. Every label must be reproducible by a second annotator who reads only this guideline, the policy file and the case's bundle.

The answer key is the policy file in `data/policies/`, which encodes the governing NCD, LCD and article text with verbatim quotes. Labels are decided by that text. When the text is silent, the case is built so the question does not arise; annotators do not fill gaps with clinical judgment.

## Outcomes

| Label | Use when | CMS parallel |
| --- | --- | --- |
| `met` | The record contains evidence that satisfies the criterion as quoted | — |
| `not_met` | The record contains evidence that the criterion is not satisfied | "Medical necessity" error: documentation shows the service does not meet coverage rules |
| `insufficient` | The record does not let you decide: the evidence is absent, incomplete, or contradictory | "Insufficient documentation" error: documentation is insufficient to determine whether the claim is payable |
| `not_applicable` | The criterion has a `condition` in the policy file and the condition does not hold for this case | — |

`not_met` and `insufficient` both lead to a non-affirmed prior-authorization decision in practice, with the provider free to resubmit. Neither is a soft outcome.

`not_applicable` is a label, not an agent outcome. The agent outputs `met`, `not_met` or `insufficient`, and for a criterion with a `condition` it also reports whether the condition holds. A `not_applicable` label is correct only if the agent reports that the condition does not hold, and it counts in criterion accuracy like any other label. An earlier version of this guideline accepted any agent outcome here; that was wrong, because the overall outcome depends on these criteria and a wrong verdict on one changes the case.

Going the other way, if the agent reports that a condition does not hold where the label is `met`, `not_met` or `insufficient`, the verdict is scored as `met`, because that is its effect on the case outcome. Against a `not_met` or `insufficient` label that is a false approval.

## What gets labeled

Label every criterion in the case's variant whose `evidence_stage` is `chart` or `request`. These are the criteria the agent is scored on.

Criteria at `provider_attestation`, `post_service` and `payer` are not labeled. The pipeline lists them, by stage, as a checklist for the human reviewer; they never become agent verdicts and never enter the metrics.

Label the full set for the variant, not only the criteria a case is designed to vary. The scorer compares every labeled criterion.

## Rules

1. **Thresholds are read exactly as quoted.** "At least 80%" is met by 80%. "A minimum of 2 weeks" is met by 14 days and not by 13. "No more than four (4)" is met by four.
2. **Do not build cases on undefined boundaries.** Where the text gives no precise boundary (for example, "consistent" relief, "duration of relief consistent with the agent used", or "minimum of 3 months" against the separate "≥12 weeks" definition of chronic pain), cases sit clearly on one side: two months or six months, never eleven or thirteen weeks. Near-miss cases are built only on boundaries the text states exactly.
3. **Exclusion criteria need an assessment.** A criterion phrased as an absence ("absence of untreated radiculopathy", "no non-facet pathology") is:
   - `met` when the record documents an assessment that addresses it with no positive finding;
   - `not_met` when the record documents the excluded condition;
   - `insufficient` when the record never addresses it, or raises it without resolving it.
   Silence is not evidence of absence.
4. **Contradictory records are `insufficient`.** When two documents in the record disagree on a fact a criterion depends on, and nothing in the record resolves the conflict, the label is `insufficient`. This follows CMS's requirement that documentation support the claim. It is our reading, not a rule CMS states for this case.
5. **A documented fact that violates a requirement is `not_met`, not `insufficient`.** If the record shows pain was measured on two different scales, the same-scale criterion is `not_met`; any criterion that needs a comparable before-and-after value becomes `insufficient`, because the comparison cannot be made.
6. **Documented exceptions count when the policy allows them.** If the quote provides for an exception that "must be clearly documented" (for example, the two-week interval between diagnostic procedures), a clearly documented exception makes the criterion `met`.
7. **Request-stage criteria are judged from the request.** Levels, spinal region, procedure code and setting come from the case's `request` block, not the chart.
8. **Dates are relative to the request date** in the case file.
9. **Policy version is fixed per case.** Each case records the policy version it was labeled against. A stale-version case states both versions and is labeled against the version in force on the request date.

## Overall outcome

The case-level outcome is derived from the variant's `all_of`/`any_of` logic over the labeled criteria, with `not_applicable` treated as satisfied:

1. `not_met` if the logic cannot be satisfied because of `not_met` criteria;
2. otherwise `insufficient` if it cannot be satisfied without resolving `insufficient` criteria;
3. otherwise `met`, with the attestation checklist still pending for the human reviewer.

The overall outcome is computed, never hand-labeled, so it cannot drift from the criterion labels.

## Evidence

- For `met` and `not_met`, list every resource reference that establishes the label (`Observation/abc`).
- For `insufficient`, list the resources that show the gap or the contradiction, if any.
- For `not_applicable`, list nothing.

## Case design

- Each case starts from a clean template patient whose record clearly meets every scored criterion for its variant, then changes one or two facts. The `notes` field says what was changed and why each changed label follows from the quoted text.
- Bundles are trimmed Synthea patients: the history relevant to the request plus realistic unrelated noise, a few hundred resources rather than thousands.
- Clinical facts are written the way they appear in real charts: a mix of coded resources and free-text notes, with some evidence present only in free text.
- No real patient data, ever.

## Review and agreement

- A second annotator labels each case from this guideline, the policy file and the bundle, without seeing the first annotator's labels.
- Agreement is reported per criterion as Cohen's kappa. Disagreements are adjudicated, and the reason is recorded in the case's `notes`.
- Until a second annotator has reviewed the set, the published results state: **single annotator; not clinically validated.**

## Sources

- Policy text: the governing documents listed in each file under `data/policies/`.
- CMS error categories: [Introduction to the Comprehensive Error Rate Testing (CERT) Program](https://www.cms.gov/files/document/introduction-comprehensive-error-rate-testing-cert-program.pdf).
- Non-affirmed decisions and resubmission: [CGS, non-affirmative prior authorization decisions](https://www.cgsmedicare.com/jc/pa/non-affirmative-decision.html).
