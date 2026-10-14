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

**How it was run.** Both runs are from commit `206d985` with `--rules`. Run 1a was interrupted when the session that started it ended, after case 8; it was resumed with `--resume` for the last three cases, on the same commit, with the resume checks passing. Run 1b ran in one piece.

**The pair is not clean.** Run 1b has one verdict where the model returned no usable answer (facet-04, `no-diagnostic-at-level-of-successful-rfa`, judged by the model alone because no rule result applied). Both files are marked accordingly.

**Result against the success criteria:**

- (a) Held: all 38 decided rule results match their labels.
- (b) Held: the four named verdicts are wrong in all four baseline runs and right in both experiment runs, each decided by code.
- (c) Held: no false approval in either run that was absent from every baseline run. facet-05 is no longer a false approval in either run.
- (d) As expected: facet-11 and facet-01 are unchanged (wrong in all six runs). facet-06 is wrong in 1a and right in 1b, as it was in two of four baseline runs.

**A regression the criteria did not anticipate.** Two verdicts that are right in all four baseline runs are wrong in both experiment runs: facet-03 `second-diagnostic-2-weeks-after-first` and facet-03 `diagnostic-sessions-max-4`. Both are partial rule results. In each the model reached the right outcome and cited the computed fact as its evidence, using a handle that is not an evidence item ("COMPUTED FACT", "computed"). The verifier rejects any handle that was not in the prompt, so the verdict was invalidated to `insufficient`. The same thing happened in 7 of the 20 partial verdicts across the two runs. This is a defect in how the partial path combines a computed fact with citation checking, not a model reasoning error. It has not been fixed; any fix is a separate, pre-registered experiment. The prompts and answers are in `2026-10-13-experiment-1-partial-prompts.md`.

**Known limits of the rules:**

- A pain score is recognized only by its LOINC code (72514-3 numeric rating, 38214-3 visual analog). An observation without one of those codes is missing data to the rules: they return absent and the model judges.
- The injection codes do not separate diagnostic from therapeutic injections, or medial branch blocks from intraarticular ones, so rules count "facet injection sessions".
- Body-site levels are read from free text by a strict whole-string grammar and nothing else. This is the only place code reads free text to decide `met`.
- The two-score minimum for the prior-ablation rule is a conservative routing choice, not a policy threshold.

## Experiment 1.1: the computed fact as a citable item

**Change.** On a partial rule result, the computed fact is evidence item `[C1]` instead of a separate "COMPUTED FACT" section. The model may cite it like any other item, and its quote must be found in the computed statement. `PARTIAL_BRANCHES` in `src/precertly/judgment/rules.py` has one entry per partial return site. Each entry fixes which outcomes may rest on `[C1]` alone. Any other outcome needs at least one verified chart citation (an E item from a chart chunk; a `request.*` item does not count), or the verdict becomes `insufficient`. The partial prompt states these requirements for its instance, generated from the same entry. The sentence "You may answer without a citation if the computed fact alone settles the criterion" is gone.

The principle: an outcome that rests on something being documented needs a chart citation; an outcome that rests on nothing further being found may cite `[C1]` alone. The full table is printed by `python -m precertly.evals.rules_check --table`.

**What does not change.** The model, the system prompt, the model-only prompt (so the shadow calls are the same as in the baselines and experiment 1), retrieval, k, the fixtures, and every computed statement: rules version 2 reproduces all 48 computed citations in each experiment 1 run exactly. Prompt version 3 differs from version 2 only in the partial prompt. Rules version 2 differs from version 1 only by naming each partial branch.

**Also new in these runs.** Every model call records the sha256 of the exact prompt and system text, and the raw response, truncated at 8,000 characters with its original length. `python -m precertly.evals.prompt_check` rebuilds every prompt offline and requires the hashes to match. A dry run over all 11 cases with a fake Bedrock behind the real client passes it, and a DB test confirms that chunks rebuilt from a bundle match ingested chunks byte for byte.

**Success criteria, written before the run.** Two runs from one commit with `--rules`, compared verdict by verdict with experiment-1a/1b and baseline v2a/v2b.

- (a) `rules_check`: 38 decided results, none contradicting a label.
- (b) Right in both runs: facet-03 `second-diagnostic-2-weeks-after-first` (not_met), facet-03 and facet-04 `diagnostic-sessions-max-4` (met), facet-09 `rfa-sessions-max-2` (met).
- (c) Zero new false approvals at verdict or case level, against every baseline and experiment 1 run.
- (d) No verdict rests on `[C1]` alone where the table requires a chart citation. The verifier enforces this and a unit test covers every cell of the table. Each results file checks it again in `partial_review.c1_alone_where_chart_required`, which must be empty.
- (e) Must not regress; right in both runs: facet-04 `second-diagnostic-2-weeks-after-first` (met), facet-03 and facet-04 `first-diagnostic-80pct-relief` (met), facet-01 `two-mbbs-80pct-sustained-relief` (met), facet-10 `prior-rfa-50pct-6-months` (not_met).
- (f) `prompt_check` and `rules_check --recompute` pass on both results files.

The verdicts in (b) are targets, not effect claims. An effect is claimed only under the rule above: wrong in every baseline run and right in every experiment run. Everything else is reported against the noise floor of 14 of 184. A run with an unusable model answer makes the pair not clean, as before.

**Reviewed by hand after the run.** `partial_review.chart_required_met` lists every met verdict that needed a chart citation, with the E items and quotes it cited. The verifier checks that a quote is in the item it cites, not that it supports the outcome, so relevance is reviewed by hand.

**For QUALITY.md, when it is added:**

- With real data, the stored prompts and raw responses contain patient information. A deployment needs them behind access controls and retention limits.
- Absence-based met on the partial path (rows 2a and 3a: no session beyond the coded count) depends on retrieval recall. The model sees the retrieved chunks, not the whole chart, so a session documented only in a chunk that was not retrieved goes unseen. Experiment 3 targets this.
- Row 7 (`same-day-blocks/listed`): "documentation of each listed block" for met is stated in the prompt only. The verifier requires at least one verified chart citation, not one per listed block, so a met citing documentation for only one of several same-day blocks would pass verification. No fixture exercises this row.

**How it was run.** Both runs are from commit `af3d7f3` with `--rules`, each in one piece. The compare command marks them as a clean pair: 0 unusable answers, evidence identical in 184 of 184 verdicts, and 3 of 184 verdicts differ.

**Result against the success criteria:**

- (a) Held: all 38 decided rule results match their labels.
- (b) Held: the four target verdicts are right in both runs, none invalidated.
- (c) Held: no new false approvals. The only one, facet-11 `conservative-care-failed` (verdict and case), is present in all eight runs.
- (d) Held: `partial_review.c1_alone_where_chart_required` is empty in both files.
- (e) Held: the five must-not-regress verdicts are right in both runs.
- (f) Held: `prompt_check` matched 194 of 194 model calls in each run; `rules_check --recompute` reproduced 48 of 48 computed citations in each.

**Partial-path invalidations: 0 of 20, down from 7 of 20 in experiment 1.** The model cited `[C1]` in 18 of the 20 partial verdicts. The four met verdicts that needed a chart citation (facet-01 two-blocks relief, facet-03 and facet-04 first-block relief, facet-04 interval exception) were read by hand; each citation supports its verdict.

| Pair | Criteria right | Cases right | False approvals (verdict / case) | Invalidated |
| --- | --- | --- | --- | --- |
| baseline v2a / v2b | 171, 168 of 184 | 7, 6 of 11 | 2 / 1, 3 / 2 | 7, 9 |
| experiment 1a / 1b | 172, 171 | 5, 6 | 2 / 2, 1 / 1 | 10, 12 |
| experiment 1.1a / 1.1b | 177, 176 | 7, 7 | 1 / 1, 1 / 1 | 5, 6 |

**Effects.** The effects claimed in experiment 1 stand: the four errors that were stable across every baseline run are fixed (facet-05 `two-mbbs-80pct-sustained-relief`, facet-09 `diagnostics-repeated-after-2-years`, facet-03 and facet-04 `pain-scale-after-each-diagnostic`), and the facet-05 false approval is removed. Experiment 1.1 claims no new effect. It repairs a defect that experiment 1 introduced: the four verdicts in (b) were right in the baselines. Every verdict wrong in a 1.1 run was judged by the model alone, with an unchanged prompt, and is within the noise band.

**The noise floor stays at 14 of 184**, the largest measured. This pair differed on 3.

**Open issues:**

1. **Whole-note quotes always verify.** In the four hand-read verdicts the model quoted entire notes. A quote containing the whole chunk passes verification however little of it bears on the outcome, so verification says less than it seems to. This is the target of experiment 2 (line-level evidence handles), measured as the share of verified quotes longer than one line: 14% and 19% in baseline v2a / v2b, 13% and 16% in experiment 1a / 1b, and 19% and 17% in 1.1a / 1.1b.
2. **facet-04 `no-diagnostic-at-level-of-successful-rfa`** is labeled not applicable (no previously successful RFA). The model said not_met in 1.1a, 1.1b and v2b, which flips the case outcome to not_met. The rule is absent here (no coded ablation), so this is the model's reasoning on a conditional criterion. Not fixed now.
3. **facet-11 false approval** (`conservative-care-failed`): the note that contradicts the others is not retrieved. This is the target of experiment 3 (retrieval by evidence type).
4. **High priority: "does not apply" needs no citation.** When the model judges that a criterion's condition does not hold, the verdict needs no citation (`citation_problem` in `judgment/judge.py`), and not applicable counts as satisfied in the variant logic. An unsupported "does not apply" can therefore satisfy a criterion and move a case toward approval. Found while checking `docs/QUALITY.md` invariant 1 against the code. Not fixed yet.
