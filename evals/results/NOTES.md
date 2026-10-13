# Results notes

Notes that apply across the results files in this directory. Each file records its own git sha, model, prompt version, retrieval and judgment settings.

## Noise floor

Two runs from the same commit and settings do not give the same verdicts, even though the evidence shown to the model is identical in both.

| Pair | Commit | Verdicts that differ | Evidence identical |
| --- | --- | --- | --- |
| `baseline-v1a` / `baseline-v1b` | `64856c8` | 3 of 184 | 184 of 184 |
| `baseline-v2a` / `baseline-v2b` | `2240d86` | 14 of 184 | 184 of 184 |

**The noise floor is 14 of 184 verdicts, not 3.** The first pair was lucky. Of the 14 differences in the v2 pair, 8 are the same answer from the model with a quote that verified in only one run, 3 are a changed outcome, and 3 are a changed applicability call. One pair also differs by one case outcome and one false approval.

**How effects are claimed.** An effect is claimed only for a verdict that is wrong in every baseline run and right in every experiment run. Everything else is reported as within the noise band, whichever way it moved. Totals from a single pair are not evidence of an effect.

## Baseline errors present in all four baseline runs

| Case | Criterion | Cause |
| --- | --- | --- |
| facet-11 | `conservative-care-failed` (false approval) | Retrieval miss: the note that contradicts the others is not among the chunks shown |
| facet-05 | `two-mbbs-80pct-sustained-relief` (false approval) | The model converts between pain scales |
| facet-09 | `diagnostics-repeated-after-2-years` | The model applies a conditional criterion whose condition does not hold |
| facet-03 | `pain-scale-after-each-diagnostic` | Retrieval miss |
| facet-04 | `pain-scale-after-each-diagnostic` | Reasoning and citation format |
| facet-01 | `chronic-axial-pain-functional-deficit` | Quote formation |

## Experiment 1: deterministic rules

**Change:** criteria with a rule in `src/precertly/judgment/rules.py` are decided by code, or judged by the model with a computed fact fixed. Model, prompt, retrieval and k are unchanged. The fixtures gained LOINC 97909-6 on the disability score, so the experiment is compared with its own shadow calls first and with baseline v2 for reference only.

**Shadow calls:** every criterion a rule decides or informs also gets the plain model-only call, with the same prompt and evidence as the baseline. Each experiment run therefore contains its own model-only verdicts on the same fixtures.

**Success criteria, written before the run:**

- (a) Offline check: every decided rule result matches its label (`python -m precertly.evals.rules_check`).
- (b) Right in both runs: facet-05 `two-mbbs-80pct-sustained-relief` (insufficient), facet-09 `diagnostics-repeated-after-2-years` (not applicable), facet-03 and facet-04 `pain-scale-after-each-diagnostic` (met).
- (c) Zero new false approvals in either run.
- (d) facet-11, facet-06 and facet-01 are expected unchanged. Everything else is reported as within the noise band.

**Known limits of the rules:**

- A rating scale with no code is treated as a possible pain scale, so a pain score recorded on a different, uncoded scale is seen as a mismatch. Another uncoded questionnaire in the same encounter would be read the same way. That can only make a result stricter or send it to the model.
- The injection codes do not separate diagnostic from therapeutic injections, or medial branch blocks from intraarticular ones, so rules count "facet injection sessions".
- Body-site levels are read from free text by a strict whole-string grammar and nothing else. This is the only place code reads free text to decide `met`.
- The two-score minimum for the prior-ablation rule is a conservative routing choice, not a policy threshold.
