from precertly.evals import load_cases
from precertly.evals.scoring import score_case, summarize
from precertly.judgment import CaseJudgment, CriterionVerdict, Outcome, VerifiedCitation
from precertly.llm import LLMUsage

CASES = load_cases()
USAGE = LLMUsage(model_id="fake", input_tokens=1000, output_tokens=100, latency_ms=50)


def _verdict(label, outcome=None, *, applicable=True, cite=None, invalidated=None):
    """A verdict that agrees with the label unless told otherwise."""
    if outcome is None:
        outcome = "met" if label.outcome is Outcome.NOT_APPLICABLE else label.outcome.value
    resources = label.evidence if cite is None else cite
    return CriterionVerdict(
        criterion=label.criterion,
        outcome=outcome,
        applicable=applicable,
        confidence=0.9,
        rationale="r",
        citations=[
            VerifiedCitation(
                chunk_id=f"{r}#0" if "/" in r else r, quote="q", char_start=0, char_end=1
            )
            for r in resources
        ],
        invalidated=invalidated,
        model_outcome=outcome,
        usage=USAGE,
    )


def _judgment(case, overrides=None, overall=None):
    overrides = overrides or {}
    verdicts = [overrides.get(label.criterion) or _verdict(label) for label in case.expected]
    return CaseJudgment(
        policy=case.policy,
        variant=case.variant,
        verdicts=verdicts,
        overall=overall or case.expected_overall,
        reviewer_checklist=[],
    )


def test_a_judgment_that_matches_every_label_scores_perfectly():
    scores = [score_case(case, _judgment(case)) for case in CASES.values()]
    summary = summarize(scores)
    assert summary.cases == summary.cases_correct == 11
    assert summary.criterion_accuracy == 1.0
    assert (summary.false_approvals, summary.case_false_approvals) == (0, 0)
    assert summary.citation_precision == 1.0
    # not_applicable labels are left out of the accuracy count
    labels = [label for case in CASES.values() for label in case.expected]
    not_applicable = sum(label.outcome is Outcome.NOT_APPLICABLE for label in labels)
    assert summary.scored_criteria == len(labels) - not_applicable
    assert summary.llm_calls == len(labels)
    assert summary.input_tokens == 1000 * len(labels)


def test_marking_a_failed_criterion_met_is_a_false_approval():
    case = CASES["facet-02"]
    label = next(x for x in case.expected if x.criterion == "two-mbbs-80pct-sustained-relief")
    wrong = {label.criterion: _verdict(label, "met")}
    score = score_case(case, _judgment(case, wrong, overall=Outcome.MET))
    summary = summarize([score])
    assert (summary.false_approvals, summary.negative_labels) == (1, 1)
    assert summary.false_approval_rate == 1.0
    assert summary.case_false_approvals == 1
    assert summary.criteria_correct == summary.scored_criteria - 1


def test_calling_a_failed_criterion_not_applicable_is_also_a_false_approval():
    case = CASES["facet-03"]
    label = next(x for x in case.expected if x.criterion == "second-diagnostic-2-weeks-after-first")
    skipped = {label.criterion: _verdict(label, "met", applicable=False, cite=[])}
    summary = summarize([score_case(case, _judgment(case, skipped))])
    assert summary.false_approvals == 1


def test_too_cautious_is_wrong_but_not_a_false_approval():
    case = CASES["facet-01"]
    label = case.expected[0]
    cautious = {label.criterion: _verdict(label, "insufficient", cite=[], invalidated="x")}
    summary = summarize([score_case(case, _judgment(case, cautious, overall=Outcome.INSUFFICIENT))])
    assert (summary.false_approvals, summary.cases_correct) == (0, 0)
    assert summary.criteria_correct == summary.scored_criteria - 1
    assert summary.invalidated_verdicts == 1


def test_citation_precision_counts_resources_outside_the_label():
    case = CASES["facet-01"]
    label = next(x for x in case.expected if x.criterion == "disability-scale-at-baseline")
    extra = {label.criterion: _verdict(label, cite=["Observation/odi-baseline", "Condition/other"])}
    score = score_case(case, _judgment(case, extra))
    scored = next(c for c in score.criteria if c.criterion == label.criterion)
    assert (scored.cited_in_label, len(scored.cited)) == (1, 2)


def test_any_agent_outcome_is_accepted_for_a_not_applicable_label():
    case = CASES["facet-01"]
    label = next(x for x in case.expected if x.outcome is Outcome.NOT_APPLICABLE)
    for outcome in ("met", "not_met", "insufficient"):
        answer = {label.criterion: _verdict(label, outcome, cite=[])}
        score = score_case(case, _judgment(case, answer))
        scored = next(c for c in score.criteria if c.criterion == label.criterion)
        assert (scored.scored, scored.correct, scored.false_approval) == (False, False, False)
