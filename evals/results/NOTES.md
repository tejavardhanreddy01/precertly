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

1. **Whole-note quotes always verify.** (Experiment 2: with line handles, 1.4% and 2.4% of citations span more than one line, but the first-words echo it needs fails often; see below.) In the four hand-read verdicts the model quoted entire notes. A quote containing the whole chunk passes verification however little of it bears on the outcome, so verification says less than it seems to. This is the target of experiment 2 (line-level evidence handles), measured as the share of verified quotes longer than one line: 14% and 19% in baseline v2a / v2b, 13% and 16% in experiment 1a / 1b, and 19% and 17% in 1.1a / 1.1b.
2. **facet-04 `no-diagnostic-at-level-of-successful-rfa`** is labeled not applicable (no previously successful RFA). The model said not_met in 1.1a, 1.1b and v2b, which flips the case outcome to not_met. The rule is absent here (no coded ablation), so this is the model's reasoning on a conditional criterion. Not fixed now.
3. **facet-11 false approval** (`conservative-care-failed`): the note that contradicts the others is not retrieved. This is the target of experiment 3 (retrieval by evidence type).
4. ~~**High priority: "does not apply" needs no citation.**~~ Closed by verifier version 2; see [Verifier version 2](#verifier-version-2-not-applicable-needs-condition-evidence) below.

## Verifier version 2: not applicable needs condition evidence

**Change.** Closes open issue 4. Every criterion with a `condition` now names its `condition_evidence` in the policy file: `chart`, or the request field the condition is about (`request.same_day_blocks` for `same-day-blocks-justified`, `request.technique` for `ia-diagnostic-only-if-mbb-not-possible`; the other six conditional criteria across the four policies are `chart`). A model verdict that the condition does not hold must cite that evidence, strictly in both directions: a chart condition needs a verified chart citation and a request field alone does not count; a request condition needs a verified citation of that field and a chart citation alone does not count. Anything else becomes `insufficient`, with the reason in `invalidated`. The check runs on the model-alone path and the partial path; a rule-decided not applicable is unchanged. The prompt, model, retrieval, rules and fixtures are unchanged.

**Why no Bedrock run.** The prompt is unchanged (prompt version 3; `prompt_check` rebuilds all 194 recorded prompts in each 1.1 file with the edited policy files), so a fresh pair would re-ask the same questions and measure only noise. The exact effect on these answers is obtained by rescoring them.

**Method.** `python -m precertly.evals.rescore` re-checks the recorded answers offline, with no model calls. The new check comes after all earlier ones, so it is applied only to verdicts that passed them; an already invalidated verdict stays as it was. It refuses a file with a different prompt or rules version, an incomplete file, or one already at verifier version 2. The derived files are `2026-10-14-experiment-1.1a-rescored-issue4.json` and `-1.1b-rescored-issue4.json`, rescored at commit `40b377b` from a clean tree. Each records its source path and sha256, the source pair, verifier versions 1 → 2 and every changed verdict. The originals are not edited, and a test reproduces both derived files from them.

**Result.** It matches the offline replay made before the change, verdict for verdict:

- No primary verdict changes in either run: the 5 model not-applicable verdicts per run (all `no-diagnostic-at-level-of-successful-rfa`, facet-03, 06, 07, 08, 11) cite chart chunks and stand. Criteria right, cases right and false approvals are unchanged (177 and 176 of 184; 7 of 11 each; 1 / 1 each).
- Shadow (model-only) `ia-diagnostic-only-if-mbb-not-possible` becomes `insufficient` where the model cited nothing: facet-06 and facet-11 in 1.1a; facet-06, facet-07 and facet-11 in 1.1b. All are labeled not applicable, so each is now a wrong criterion verdict.
- Model-only facet-11 moves from `met` to `insufficient` in both runs, which removes the model-only case-level false approval. This is right at case level for an unrelated reason: the criterion that changed is labeled not applicable. No effect is claimed.
- Model-only totals: criteria right 167 → 165 (1.1a) and 168 → 165 (1.1b); cases right 6 → 7 in both.
- The summary counts the new invalidations under `quote_invalidations` (8 → 10 and 8 → 11). They are missing condition citations, not quotes that failed to verify.
- `rules_check`: 38 decided results, none contradicting a label. `prompt_check`: 194 of 194 calls in each derived file. `rules_check --recompute`: 48 of 48 computed citations in each.

**Deferred.** Criterion (d) of the draft pre-registration (every not-applicable verdict carries the citation it needs, by scan) holds here by construction, since these files are verified by the rule being tested. It is checked on new answers in the next fresh pair, experiment 2.

**Watch.** facet-04 `no-diagnostic-at-level-of-successful-rfa` (labeled not applicable) is not touched: the model said `not_met`, applicable, in both runs. Open issue 2 stands.

**What this does not establish.** The check requires a citation of the right kind, not one that proves the condition does not hold. All 10 surviving primary verdicts cite the plan to ablate "if two blocks confirm the diagnosis", which implies no prior ablation without stating it. An absence cannot be proven by a quote; this is recorded in `docs/QUALITY.md` known limitations.

## Experiment 2: line-level evidence handles

Pre-registered before any code for it was written. Design reviewed separately; the prototype figures below were measured offline on the committed fixtures and the recorded 1.1 answers.

**Change.** Prompt version 4 and verifier version 3. Each evidence chunk is split deterministically into lines, each with a handle such as `[E3.2]`. Notes are split on newlines (blank lines get no handle), and a line longer than 160 characters is further split at sentence ends; coded resources, request fields and `[C1]` are one line each. The model cites handles only (a single line, or a range of at most 3 adjacent lines of one item) and the verifier extracts the cited text as the quote, so a quote can no longer be misquoted or invented. With each citation the model echoes the first three words of the first cited line (all of its words if it has fewer), compared after NFKC normalization, case-insensitively, ignoring punctuation and a leading bullet marker; a mismatch invalidates the verdict as `handle_mismatch`. This also applies to `[C1]`. An unknown handle still invalidates. Invalidations are counted by kind (`quote_not_found`, `unknown_handle`, `missing_citation`, `chart_citation_required`, `condition_evidence`, `citation_too_long`, `handle_mismatch`, `condition_contradicts_computed`), so the issue 4 invalidations are no longer counted as quote failures.

**What does not change.** Model, retrieval, k, rules (version 2), fixtures and the condition-evidence check from verifier version 2.

**Why.** Open issue 1: a quote containing a whole note verifies however little of it bears on the outcome. In 1.1, every quote that was found nowhere (10 of 742 citations) joined two bullet lines and dropped their markers, and 6 citations put a quote from one item under another item's handle; the echo keeps that second error detectable once quotes are extracted by code.

**Prototype measurements.** Of the 726 verified 1.1 quotes, 619 touch at most 3 lines under this segmentation. Rebuilding all 388 recorded 1.1 calls with the v4 prototype gives about 4,110 input tokens per call (1.1: 3,449), about 797,000 per run (1.1: 669,066), and about 25% fewer output tokens because quotes are no longer copied.

**Success criteria, written before the run.** Two runs from one commit with `--rules`, marked as a pair with `compare`, compared verdict by verdict with 1.1a / 1.1b as rescored with verifier version 2, and with every earlier run for reference.

- (a) `rules_check`: 38 decided results, none contradicting a label.
- (b) `quote_not_found` at most 1 per run (0 by construction; only `[C1]` can still produce one), `unknown_handle` 0, and `handle_mismatch` at most 1 per run, each listed. All invalidations reported by kind, for the primary and model-only views. 1.1 had 5 and 6 primary quote failures.
- (c) Verdict differences between the two runs caused by a citation verifying in one run only: 0 by construction (v2: 8 of 14 differences). Total differences reported with the cause of each; no noise-reduction effect claimed from one pair.
- (d) Zero new false approvals, at verdict and case level, against every baseline, experiment 1 and 1.1 run. The facet-11 retrieval miss is expected to remain (experiment 3).
- (e) Every not-applicable verdict carries the citation it needs, checked by a scan of both files (deferred from verifier version 2).
- (f) Must not regress; right in both runs: facet-03 `second-diagnostic-2-weeks-after-first` (not_met), facet-03 and facet-04 `diagnostic-sessions-max-4` (met), facet-09 `rfa-sessions-max-2` (met); facet-04 `second-diagnostic-2-weeks-after-first` (met), facet-03 and facet-04 `first-diagnostic-80pct-relief` (met), facet-01 `two-mbbs-80pct-sustained-relief` (met), facet-10 `prior-rfa-50pct-6-months` (not_met).
- (g) Citation precision reported, no effect claimed (it is measured per resource, not per line). Also reported for open issue 1: lines per citation and the share of citations spanning more than one line.

**Watch.** facet-04 `no-diagnostic-at-level-of-successful-rfa` (open issue 2). Whether not-applicable verdicts on `no-diagnostic-at-level-of-successful-rfa` now cite a line that states the absence (such as "No prior radiofrequency ablation or spinal injections.") instead of the plan line; reported by hand, no effect claimed.

**Also required.** `prompt_check` and `rules_check --recompute` pass on both files. An effect is claimed only for a verdict wrong in every earlier run and right in both experiment runs.

### Amendment: after a one-case canary, before any pair run

**The canary.** Before starting the pair, case facet-01 was run alone at commit `e07da1f` (17 model calls) to check real token counts against the estimate: 4,155 input tokens per call, against about 4,110 estimated. It also produced 10 `handle_mismatch` invalidations (9 primary, 1 shadow) in one case, against a target of at most 1 per run, so the run was stopped there. The file is kept as evidence in `2026-10-16-experiment-2-canary-superseded.json`. It used the superseded verifier and is not part of any result.

**What it found.**

1. The echo rule was stricter than intended. In 8 of the 10 invalidations the model cited the right line and echoed a correct prefix of fewer than three words (`- Pain: 7/10`, where the model counts the bullet as a word; `Conservative management:`; `Spinal region:`; `Procedure/mbb1: relief`). The other 2 were real misnumberings, both `E9.9` echoed as `Pain and` when that heading is `E9.8`, which is what the echo is for.
2. Radiology report text was segmented as a coded resource. `DiagnosticReport/mri-lumbar#1` is a verbatim slice of the report's presentedForm, but only DocumentReference chunks were treated as free text, so the whole report was one line. The model cited `E15.15` for the Impression row, a handle that did not exist.

**Changes, made after this one case and before any pair run.**

1. Echo: the echo must be a prefix of the line's words, at least 2 words long, or every word of the line's first row if that row has fewer. On the 1.1a prompts, a near-miss handle (an adjacent line, or the same line of a neighbouring item) passes a two-word echo 9.4% of the time and a three-word echo 8.3%. Replayed offline on the canary's answers, the 8 correct citations pass and both misnumberings are still caught. The prompt text is unchanged ("the first three words ... all of its words if it has fewer"), and prompt version 4 is unchanged; verifier version 3 is reworded before any result was recorded with it.
2. Free text is recognized from the chunker's character offsets (`EvidenceChunk.free_text`, set where `char_start` is set, in retrieval and in `prompt_check`), not from the resource type. Radiology report text is now split into lines like notes. This changes the rendered prompts of criteria that are shown a report, under the same prompt version 4 template.

**The line-length threshold.** Recomputed over all 228 free-text chunks in the 11 bundles (54 distinct texts: 51 notes, 3 radiology reports): 552 distinct non-empty lines, median 42 characters, 90th percentile 194, maximum 548; 478 at or under 160 and 74 over it. The distribution barely moves (notes alone: 537 lines, median 41, 90th percentile 194, 466 at or under 160), so `MAX_LINE_CHARS` stays 160.

**Unchanged.** Every success criterion above stands as written, including (b)'s target of at most 1 `handle_mismatch` per run, which is reported against as written. Both runs of the pair are fresh runs from the commit with these changes; neither resumes the canary.

### Results

**How it was run.** Both runs are fresh runs from commit `fb48d8c` with `--rules`, each in one piece: `2026-10-16-experiment-2a.json` and `-2b.json`. The compare command marks them as a clean pair: 0 unusable answers, evidence identical in 184 of 184 verdicts. 798,078 input tokens per run (1.1: 669,066) and about 40,500 output (1.1: about 53,000). `prompt_check` matched 194 of 194 model calls in each run, and `rules_check --recompute` reproduced 48 of 48 computed citations in each.

| Run | Criteria right | Cases right | False approvals (verdict / case) | Invalidated (primary) | Citation precision |
| --- | --- | --- | --- | --- | --- |
| 1.1a / 1.1b (verifier 2) | 177, 176 of 184 | 7, 7 of 11 | 1 / 1, 1 / 1 | 5, 6 (all quote not found) | 74.4%, 74.8% |
| 2a / 2b | 145, 160 of 184 | 8, 8 of 11 | 2 / 0, 1 / 0 | 32, 22 | 69.2%, 70.8% |

**Result against the success criteria:**

- (a) Held: 38 decided rule results, none contradicting a label.
- (b) **Failed.** `quote_not_found` 0 and 0, and `unknown_handle` 0 and 0, as intended. But `handle_mismatch` is 29 and 20 (target: at most 1 per run), and `citation_too_long` 3 and 2. Model-only view: `handle_mismatch` 41 and 34, `citation_too_long` 3 and 2, `condition_evidence` 3 and 1.
- (c) **Failed.** 19 of 184 verdicts differ between the runs (1.1 pair: 3). 16 of them are a citation verifying in one run only: 14 `handle_mismatch` and 2 `citation_too_long`. The criterion assumed extraction makes this impossible, but the echo and the cap are themselves checks that can pass in one run and fail in the other. The other 3: two changed model outcomes (facet-06 and facet-11 `no-diagnostic-at-level-of-successful-rfa`) and one changed applicability (facet-08, same criterion). No noise effect is claimed either way.
- (d) Held: no false approval, at verdict or case level, that was absent from every earlier run. Verdict-level false approvals: facet-06 `no-untreated-radiculopathy` (2a only, seen in earlier runs) and facet-11 `conservative-care-failed` (both runs, all runs). The case-level false approval on facet-11 is gone in both runs only because another facet-11 verdict was invalidated (`chronic-axial-pain-functional-deficit` in 2a, `pain-scale-at-baseline` in 2b); that is right for the wrong reason and is not an effect.
- (e) Held: every not-applicable verdict (30 in 2a and 35 in 2b, primary and shadow) carries the citation it needs: a computed citation for a rule, or the criterion's `condition_evidence` for the model.
- (f) **Failed for one of nine.** facet-03 `first-diagnostic-80pct-relief` is insufficient in both runs (`handle_mismatch`). The other eight hold.
- (g) Citation precision 69.2% and 70.8% (1.1: 74.4% and 74.8%), no effect claimed. Open issue 1's measure: 1.4% and 2.4% of verified citations span more than one line (4 of 279, 7 of 291). Whole-note quotes are gone.

**Effects and regressions.** No verdict is wrong in all 8 earlier runs and right in both experiment runs, so no effect is claimed. 19 verdicts are right in all 8 earlier runs and wrong in both experiment runs. 18 of them are invalidations (`pain-scale-at-baseline` in all 11 cases, `scales-documented` in 3, `no-generalized-pain-indication` in 2, `chronic-axial-pain-functional-deficit` and `first-diagnostic-80pct-relief` in 1 each). One is the model's reasoning: facet-03 `no-diagnostic-at-level-of-successful-rfa` is not_met in both runs (label not applicable).

**Why the echo fails.** In run 2a, 417 of 483 line citations have a matching echo. Of the 66 that do not, 37 echo the line immediately before the cited handle, 27 echo words that start no line of the item, and 2 are further off. The systematic case is `pain-scale-at-baseline`: the model cites `E9.9` ("- Pain: 7/10 on the Numerical Rating Scale ...") and echoes "Pain and", the heading on `E9.8`, in every case. The model appears to read a handle as labelling the text before it, as a citation marker does. Which of the two it meant cannot be told from the answer, so the verifier is right to refuse it. The 27 non-matching echoes are mostly one word ("- Pain", "Levels:", "Today:"), below the two-word minimum, and "Technique: medial", which splits `medial_branch_block` at the underscores.

**Conclusion.** Line handles do what they were meant to for quotes: no quote was misquoted or invented, and citations are now one line, not whole notes. But the first-words echo, which is needed to catch a wrong line number, disagrees with the cited handle in 14% of line citations, roughly six times the quote failures it replaced. As a whole, experiment 2 is worse than 1.1 on criteria right (145 and 160 against 177 and 176) and on run-to-run agreement, and it fails (b), (c) and (f). Any change to the echo, the handle layout or the cap is a separate, pre-registered experiment. The check is not to be loosened to recover these verdicts.

**Watch items.**

- facet-04 `no-diagnostic-at-level-of-successful-rfa` (labeled not applicable) is not_met, applicable, in both runs, as in 1.1. Open issue 2 stands.
- Not-applicable verdicts on `no-diagnostic-at-level-of-successful-rfa` now cite "No prior diagnostic facet procedures." (facet-07 in 2a; facet-06, 07, 08 and 11 in 2b), usually alongside the plan line. That line states an absence, but of prior diagnostic facet procedures, not of a prior ablation. No verdict cited "No prior radiofrequency ablation or spinal injections." No effect is claimed.
