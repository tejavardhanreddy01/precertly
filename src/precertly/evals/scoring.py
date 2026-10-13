"""Score a case judgment against its labels (evals/README.md, "Metrics")."""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel

from precertly.evals.cases import EvalCase
from precertly.judgment.models import CaseJudgment, Outcome

NEGATIVE = (Outcome.NOT_MET, Outcome.INSUFFICIENT)


class CriterionScore(BaseModel):
    criterion: str
    expected: Outcome
    # What the verdict amounts to: a criterion the agent found not applicable counts as
    # met, because that is its effect on the case outcome.
    predicted: Outcome
    agent_not_applicable: bool
    # For a not_applicable label: correct only if the agent also found that the condition
    # does not hold. The case outcome depends on these criteria, so they are scored.
    correct: bool
    false_approval: bool  # agent says met, label says not_met or insufficient
    invalidated: bool
    # The model gave no usable answer at all and the verdict was set to insufficient. A
    # subset of invalidated; the rest are citations or quotes that did not verify.
    unusable_answer: bool = False
    cited: list[str]
    cited_in_label: int


class CaseScore(BaseModel):
    case_id: str
    variant: str
    expected_overall: Outcome
    predicted_overall: Outcome
    criteria: list[CriterionScore]
    input_tokens: int
    output_tokens: int
    llm_latency_ms: float

    @property
    def overall_correct(self) -> bool:
        return self.expected_overall == self.predicted_overall

    @property
    def overall_false_approval(self) -> bool:
        return self.predicted_overall is Outcome.MET and self.expected_overall in NEGATIVE


class CitationPrecision(BaseModel):
    cited: int
    in_label: int
    precision: float


class Summary(BaseModel):
    cases: int
    cases_correct: int
    case_false_approvals: int
    scored_criteria: int
    criteria_correct: int
    criterion_accuracy: float | None
    negative_labels: int  # labels that are not_met or insufficient
    false_approvals: int
    false_approval_rate: float | None  # false approvals / negative labels
    citations: int
    citations_in_label: int
    citation_precision: float | None  # micro: all cited resources pooled
    # macro: the mean of the per-criterion precisions, so a criterion with many
    # citations does not dominate
    citation_precision_macro: float | None
    citation_precision_by_criterion: dict[str, CitationPrecision]
    invalidated_verdicts: int  # quote_invalidations + unusable_answers
    quote_invalidations: int
    unusable_answers: int
    unusable_answers_by_case: dict[str, int]
    unusable_answers_by_criterion: dict[str, int]
    input_tokens: int
    output_tokens: int
    llm_calls: int


def score_case(case: EvalCase, judgment: CaseJudgment) -> CaseScore:
    verdicts = {v.criterion: v for v in judgment.verdicts}
    criteria = []
    for label in case.expected:
        verdict = verdicts[label.criterion]
        predicted = Outcome(verdict.outcome)
        if label.outcome is Outcome.NOT_APPLICABLE:
            correct = not verdict.applicable
        else:
            correct = predicted == label.outcome
        cited = sorted({c.resource for c in verdict.citations})
        criteria.append(
            CriterionScore(
                criterion=label.criterion,
                expected=label.outcome,
                predicted=predicted,
                agent_not_applicable=not verdict.applicable,
                correct=correct,
                false_approval=predicted is Outcome.MET and label.outcome in NEGATIVE,
                invalidated=verdict.invalidated is not None,
                unusable_answer=verdict.model_outcome == "none",
                cited=cited,
                cited_in_label=sum(resource in label.evidence for resource in cited),
            )
        )
    return CaseScore(
        case_id=case.id,
        variant=case.variant,
        expected_overall=case.expected_overall,
        predicted_overall=judgment.overall,
        criteria=criteria,
        input_tokens=sum(v.usage.input_tokens for v in judgment.verdicts),
        output_tokens=sum(v.usage.output_tokens for v in judgment.verdicts),
        llm_latency_ms=sum(v.usage.latency_ms for v in judgment.verdicts),
    )


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def summarize(scores: list[CaseScore]) -> Summary:
    criteria = [c for score in scores for c in score.criteria]
    negatives = [c for c in criteria if c.expected in NEGATIVE]
    # Citation precision is measured where the label lists evidence to compare against;
    # not_applicable labels list none.
    citing = [c for c in criteria if c.cited and c.expected is not Outcome.NOT_APPLICABLE]
    citations = sum(len(c.cited) for c in citing)
    in_label = sum(c.cited_in_label for c in citing)
    by_criterion: dict[str, CitationPrecision] = {}
    for criterion in dict.fromkeys(c.criterion for c in citing):
        cited = sum(len(c.cited) for c in citing if c.criterion == criterion)
        found = sum(c.cited_in_label for c in citing if c.criterion == criterion)
        by_criterion[criterion] = CitationPrecision(
            cited=cited, in_label=found, precision=round(found / cited, 4)
        )
    macro = (
        round(sum(p.precision for p in by_criterion.values()) / len(by_criterion), 4)
        if by_criterion
        else None
    )
    return Summary(
        cases=len(scores),
        cases_correct=sum(s.overall_correct for s in scores),
        case_false_approvals=sum(s.overall_false_approval for s in scores),
        scored_criteria=len(criteria),
        criteria_correct=sum(c.correct for c in criteria),
        criterion_accuracy=_rate(sum(c.correct for c in criteria), len(criteria)),
        negative_labels=len(negatives),
        false_approvals=sum(c.false_approval for c in negatives),
        false_approval_rate=_rate(sum(c.false_approval for c in negatives), len(negatives)),
        citations=citations,
        citations_in_label=in_label,
        citation_precision=_rate(in_label, citations),
        citation_precision_macro=macro,
        citation_precision_by_criterion=by_criterion,
        invalidated_verdicts=sum(c.invalidated for c in criteria),
        quote_invalidations=sum(c.invalidated and not c.unusable_answer for c in criteria),
        unusable_answers=sum(c.unusable_answer for c in criteria),
        unusable_answers_by_case={
            score.case_id: count
            for score in scores
            if (count := sum(c.unusable_answer for c in score.criteria))
        },
        unusable_answers_by_criterion=dict(
            Counter(c.criterion for c in criteria if c.unusable_answer)
        ),
        input_tokens=sum(s.input_tokens for s in scores),
        output_tokens=sum(s.output_tokens for s in scores),
        llm_calls=len(criteria),
    )
