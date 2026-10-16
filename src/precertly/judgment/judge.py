"""Judge criteria with an LLM, verify every quote, and combine the verdicts.

The model's answer is never taken on trust. It cites lines by handle and the cited text is
extracted here (lines.py), so a quote cannot be invented; a handle that was not in the
prompt, an over-long range, or an echo that does not match the cited line invalidates the
verdict, and so does a met or not_met answer with no citation. A verdict that a
criterion's condition does not hold must cite the evidence its policy names in
condition_evidence. An invalidated verdict becomes "insufficient", so an unverifiable
answer can never read as approval, and records the kind of invalidation.

With a partial rule result the computed fact is evidence item [C1]. It is cited like any
other item, and PARTIAL_BRANCHES fixes which outcomes may rest on it alone; any other
outcome still needs a verified chart citation.
"""

from __future__ import annotations

import asyncio
import re
from typing import Literal, Protocol

from pydantic import BaseModel

from precertly.judgment.lines import (
    MAX_CITATION_LINES,
    HandleError,
    echo_matches,
    lines_of,
    parse_handle,
)
from precertly.judgment.logic import judged_criteria, overall_outcome
from precertly.judgment.models import (
    CaseJudgment,
    ChecklistItem,
    Citation,
    ComputedCitation,
    CriterionVerdict,
    EvidenceChunk,
    InvalidationKind,
    ModelJudgment,
    Outcome,
    PriorAuthRequest,
    ShadowVerdict,
    VerifiedCitation,
)
from precertly.judgment.prompt import COMPUTED_HANDLE, SYSTEM, build_prompt, handles
from precertly.judgment.rules import (
    PARTIAL_BRANCHES,
    RULES_VERSION,
    FinalOutcome,
    RuleResult,
    evaluate,
)
from precertly.judgment.structured import StructuredChart
from precertly.llm import LLMUsage, StructuredOutputError, StructuredResponse
from precertly.policies import Criterion, Policy

# Settings that change what the model is asked or allowed to say. They are recorded in every
# results file, and a run may only be resumed under the same values.
MAX_TOKENS = 800
TEMPERATURE = 0.0
# Version of the checks that decide whether a model answer stands. 2: a not-applicable
# verdict must cite its criterion's condition_evidence. 3: line handles; the quote is
# extracted from the cited lines and the first-words echo must match.
VERIFIER_VERSION = "3"


class StructuredLLM(Protocol):
    async def structured[T: BaseModel](
        self,
        prompt: str,
        schema: type[T],
        *,
        system: str | None = None,
        max_tokens: int = ...,
        temperature: float = ...,
    ) -> StructuredResponse[T]: ...


class EvidenceProvider(Protocol):
    async def for_criterion(self, criterion: Criterion) -> list[EvidenceChunk]: ...


def find_quote(quote: str, text: str) -> tuple[int, int] | None:
    """Position of quote in text, ignoring differences in whitespace only."""
    words = quote.split()
    if not words:
        return None
    match = re.search(r"\s+".join(re.escape(word) for word in words), text)
    return (match.start(), match.end()) if match else None


Invalid = tuple[InvalidationKind, str]


def verify(
    judgment: ModelJudgment, chunks: list[EvidenceChunk], computed: str | None = None
) -> tuple[list[VerifiedCitation], str | None, Invalid | None]:
    """Verified chunk citations, the computed statement if [C1] was cited, and the kind and
    reason the verdict is invalid if it is.

    The model cites line handles (E3.2, or E3.2-E3.4); the cited lines are extracted here
    as the quote. [C1] is a handle only when computed (a partial rule's statement) was in
    the prompt. A handle that is not in the prompt, a range of more than MAX_CITATION_LINES
    lines, or an echo that does not match the first cited line is invalid.
    """
    by_handle = handles(chunks)
    verified, computed_quote = [], None
    for citation in judgment.citations:
        try:
            handle = parse_handle(citation.evidence)
        except HandleError as error:
            return [], None, ("unknown_handle", f"cited evidence is not a handle: {error}")
        if handle.item == COMPUTED_HANDLE and computed is not None:
            if handle.last not in (None, 1):
                return [], None, ("unknown_handle", f"[C1] has one line; cited {handle}")
            if not echo_matches(citation.first_words, computed):
                return [], None, ("handle_mismatch", _mismatch("[C1]", citation, computed))
            computed_quote = computed
            continue
        chunk = by_handle.get(handle.item)
        if chunk is None:
            return [], None, ("unknown_handle", _not_shown(citation.evidence))
        lines = lines_of(chunk)
        first, last = handle.first, handle.last
        if first is None:
            if len(lines) != 1:
                return (
                    [],
                    None,
                    (
                        "unknown_handle",
                        f"cited evidence {citation.evidence!r} names an item of {len(lines)} "
                        "lines, not a line",
                    ),
                )
            first = last = 1
        assert last is not None
        if last > len(lines):
            return [], None, ("unknown_handle", _not_shown(citation.evidence))
        if last - first + 1 > MAX_CITATION_LINES:
            return (
                [],
                None,
                (
                    "citation_too_long",
                    f"{citation.evidence!r} spans {last - first + 1} lines; at most "
                    f"{MAX_CITATION_LINES} may be cited together",
                ),
            )
        head = lines[first - 1]
        if not echo_matches(citation.first_words, head.text):
            return [], None, ("handle_mismatch", _mismatch(citation.evidence, citation, head.text))
        verified.append(
            VerifiedCitation(
                chunk_id=chunk.id,
                quote=chunk.text[head.start : lines[last - 1].end],
                char_start=head.start,
                char_end=lines[last - 1].end,
                first_line=first,
                last_line=last,
            )
        )
    return verified, computed_quote, None


def _not_shown(evidence: str) -> str:
    return f"cited evidence {evidence!r} was not in the prompt"


def _mismatch(where: str, citation: Citation, line: str) -> str:
    return (
        f"first words {citation.first_words[:40]!r} do not match {where}, which starts "
        f"{line[:40]!r}"
    )


def citation_problem(
    criterion_id: str,
    outcome: Literal["met", "not_met", "insufficient"],
    applicable: bool,
    citations: list[VerifiedCitation],
    computed_quote: str | None,
    partial: RuleResult | None,
    condition_holds: bool | None = None,
    condition_evidence: str | None = None,
) -> Invalid | None:
    """Why the citations cannot support this outcome, with its kind, or None if they can.

    Without a partial rule result, met and not_met need at least one citation. With one,
    the PARTIAL_BRANCHES entry decides: an outcome it lists may rest on [C1] alone, and any
    other needs a verified chart citation (a request field does not count). On either
    path, not applicable must also cite the criterion's condition_evidence.
    """
    if applicable and outcome == "insufficient":
        return None
    if partial is None:
        if not applicable:
            return _condition(condition_evidence, citations)
        return None if citations else ("missing_citation", f"{outcome} with no citation")
    assert partial.branch is not None
    branch = PARTIAL_BRANCHES[partial.branch]
    if branch.criterion != criterion_id:
        raise ValueError(f"{criterion_id}: partial branch {partial.branch!r} belongs elsewhere")
    if applicable:
        assert outcome != "insufficient"  # returned above
        final: FinalOutcome = outcome
    else:
        final = "not_applicable"
    if branch.condition_fixed and condition_holds is False:
        return (
            "condition_contradicts_computed",
            "condition_holds is false, but [C1] establishes that the condition holds",
        )
    if not branch.needs_chart(final):
        if citations or computed_quote is not None:
            return None
        return ("missing_citation", f"{final} with no citation")
    if not any(citation.is_chart for citation in citations):
        cited = "only [C1] or request items" if citations or computed_quote else "nothing"
        return ("chart_citation_required", f"{final} needs a chart citation; cited {cited}")
    if not applicable:
        return _condition(condition_evidence, citations)
    return None


def _condition(condition_evidence: str | None, citations: list[VerifiedCitation]) -> Invalid | None:
    problem = condition_citation_problem(condition_evidence, citations)
    return None if problem is None else ("condition_evidence", problem)


def condition_citation_problem(
    condition_evidence: str | None, citations: list[VerifiedCitation]
) -> str | None:
    """Why the citations cannot show that a criterion's condition does not hold, or None.

    Strict both ways: condition_evidence "chart" needs a verified chart citation, and a
    request field alone does not count; "request.<field>" needs a verified citation of that
    field, and a chart citation alone does not count. The citation shows the context the
    model relied on; it cannot prove an absence.
    """
    if condition_evidence is None:
        raise ValueError("a criterion with a condition must name its condition_evidence")
    if condition_evidence == "chart":
        if any(citation.is_chart for citation in citations):
            return None
        needed = "a chart citation"
    else:
        if any(citation.chunk_id == condition_evidence for citation in citations):
            return None
        needed = f"a citation of {condition_evidence}"
    cited = ", ".join(sorted({citation.chunk_id for citation in citations})) or "nothing"
    return f"not_applicable needs {needed}; cited {cited}"


# Reason text as earlier verifier versions wrote it, for files that record no kind.
_REASON_KINDS: list[tuple[str, InvalidationKind]] = [
    ("no structured answer", "no_usable_answer"),
    ("quote not found", "quote_not_found"),
    ("not_applicable needs", "condition_evidence"),
    ("condition_holds is false", "condition_contradicts_computed"),
]


def invalidation_kind(reason: str | None) -> InvalidationKind | None:
    """The kind of an invalidation reason, for results files written before verifier
    version 3 recorded it."""
    if reason is None:
        return None
    for prefix, kind in _REASON_KINDS:
        if reason.startswith(prefix):
            return kind
    if any(
        text in reason
        for text in (
            "was not in the prompt",
            "is not a handle",
            "not in the evidence",
            "names an item of",
            "has one line",
        )
    ):
        return "unknown_handle"
    if " do not match " in reason:
        return "handle_mismatch"
    if "may be cited together" in reason:
        return "citation_too_long"
    if "needs a chart citation" in reason:
        return "chart_citation_required"
    if reason.endswith("with no citation"):
        return "missing_citation"
    raise ValueError(f"unrecognized invalidation reason: {reason!r}")


def computed_citation(
    criterion_id: str, rule: RuleResult, quote: str | None = None
) -> ComputedCitation:
    return ComputedCitation(
        statement=rule.statement,
        inputs=rule.inputs,
        rule=criterion_id,
        rules_version=RULES_VERSION,
        branch=rule.branch,
        cited_by_model=quote is not None,
        quote=quote,
    )


async def judge_criterion(
    llm: StructuredLLM,
    criterion: Criterion,
    request: PriorAuthRequest,
    chunks: list[EvidenceChunk],
    *,
    max_tokens: int = MAX_TOKENS,
    partial: RuleResult | None = None,
) -> CriterionVerdict:
    """One verdict for one criterion from the evidence given (request chunks included).

    partial is a rule's partial result: the model is given it as [C1] and judges the rest.
    """
    computed = [computed_citation(criterion.id, partial)] if partial else []
    try:
        response = await llm.structured(
            build_prompt(criterion, request, chunks, partial),
            ModelJudgment,
            system=SYSTEM,
            max_tokens=max_tokens,
            temperature=TEMPERATURE,
        )
    except StructuredOutputError as error:
        # No usable answer is not a reason to stop the case, and never a reason to
        # approve: the criterion is left for the reviewer as insufficient.
        return CriterionVerdict(
            criterion=criterion.id,
            outcome="insufficient",
            confidence=0.0,
            rationale="The model returned no usable answer for this criterion.",
            invalidated=f"no structured answer: {error}",
            invalidation="no_usable_answer",
            model_outcome="none",
            chunk_ids=[chunk.id for chunk in chunks],
            usage=error.usage or LLMUsage(model_id="unknown", calls=0),
            exchanges=error.exchanges,
            computed=computed,
        )
    judgment: ModelJudgment = response.data
    citations, computed_quote, invalid = verify(
        judgment, chunks, partial.statement if partial else None
    )
    applicable = not (criterion.condition and judgment.condition_holds is False)

    outcome = judgment.outcome
    if not applicable:
        outcome = "met"  # satisfied because the condition does not hold
    # A decision needs evidence; with a partial rule result, PARTIAL_BRANCHES says which.
    if invalid is None:
        invalid = citation_problem(
            criterion.id,
            outcome,
            applicable,
            citations,
            computed_quote,
            partial,
            judgment.condition_holds,
            criterion.condition_evidence,
        )
    if invalid is not None:
        outcome, applicable, computed_quote = "insufficient", True, None
    kind, reason = invalid if invalid is not None else (None, None)
    if partial is not None:
        computed = [computed_citation(criterion.id, partial, computed_quote)]

    return CriterionVerdict(
        criterion=criterion.id,
        outcome=outcome,
        applicable=applicable,
        confidence=judgment.confidence,
        rationale=judgment.rationale,
        citations=citations,
        invalidated=reason,
        invalidation=kind,
        model_outcome=judgment.outcome,
        chunk_ids=[chunk.id for chunk in chunks],
        usage=response.usage,
        exchanges=response.exchanges,
        computed=computed,
    )


async def judge_with_rule(
    llm: StructuredLLM,
    criterion: Criterion,
    request: PriorAuthRequest,
    chunks: list[EvidenceChunk],
    rule: RuleResult | None,
    *,
    shadow: bool = False,
) -> CriterionVerdict:
    """A verdict that uses the rule's result where there is one.

    decided: code's result stands. partial: the model judges with the computed fact fixed.
    absent, or no rule: the model judges alone, exactly as without rules.

    With shadow, a decided or partial criterion also gets the plain model-only call, so
    one run holds both verdicts for comparison. The shadow never affects the outcome.
    """
    if rule is None or rule.kind == "absent":
        verdict = await judge_criterion(llm, criterion, request, chunks)
        verdict.rule_kind = rule.kind if rule else None
        return verdict

    if rule.kind == "decided":
        assert rule.outcome is not None
        verdict = CriterionVerdict(
            criterion=criterion.id,
            outcome=rule.outcome,
            applicable=rule.applicable,
            confidence=1.0,
            rationale=rule.statement,
            model_outcome="rule",
            chunk_ids=[chunk.id for chunk in chunks],
            usage=LLMUsage(model_id="rule", calls=0),
            decided_by="rule",
            computed=[computed_citation(criterion.id, rule)],
        )
    else:
        verdict = await judge_criterion(llm, criterion, request, chunks, partial=rule)
        verdict.decided_by = "rule+model"
    verdict.rule_kind = rule.kind

    if shadow:
        alone = await judge_criterion(llm, criterion, request, chunks)
        verdict.shadow = ShadowVerdict(
            outcome=alone.outcome,
            applicable=alone.applicable,
            invalidated=alone.invalidated,
            invalidation=alone.invalidation,
            model_outcome=alone.model_outcome,
            citations=alone.citations,
            usage=alone.usage,
            exchanges=alone.exchanges,
        )
    return verdict


async def judge_case(
    llm: StructuredLLM,
    policy: Policy,
    variant_id: str,
    request: PriorAuthRequest,
    evidence: EvidenceProvider,
    *,
    concurrency: int = 4,
    structured: StructuredChart | None = None,
    shadow: bool = False,
) -> CaseJudgment:
    """Judge every chart- and request-stage criterion of a variant and combine them.

    With a structured chart, criteria that have a rule are decided or informed by code
    (see rules.py). Without one, every criterion is judged by the model alone.
    """
    variant = policy.variant(variant_id)
    judged = judged_criteria(policy, variant_id)

    # Evidence is gathered one criterion at a time (a database session is not safe to
    # share between tasks); the model calls then run concurrently.
    request_chunks = request.chunks()
    evidence_by_criterion = {
        criterion_id: request_chunks + await evidence.for_criterion(policy.criterion(criterion_id))
        for criterion_id in judged
    }

    gate = asyncio.Semaphore(concurrency)

    async def one(criterion_id: str) -> CriterionVerdict:
        criterion = policy.criterion(criterion_id)
        rule = evaluate(criterion, structured, request) if structured is not None else None
        async with gate:
            return await judge_with_rule(
                llm, criterion, request, evidence_by_criterion[criterion_id], rule, shadow=shadow
            )

    verdicts = list(await asyncio.gather(*(one(criterion_id) for criterion_id in judged)))
    outcomes = {
        v.criterion: Outcome(v.outcome) if v.applicable else Outcome.NOT_APPLICABLE
        for v in verdicts
    }
    return CaseJudgment(
        policy=policy.id,
        variant=variant_id,
        verdicts=verdicts,
        overall=overall_outcome(variant.logic, outcomes),
        reviewer_checklist=[
            ChecklistItem(criterion=c.id, evidence_stage=c.evidence_stage.value, text=c.text)
            for c in policy.criteria
            if c.id in variant.logic.criterion_ids() and c.id not in judged
        ],
    )


def model_only(judgment: CaseJudgment, policy: Policy) -> CaseJudgment:
    """The case as the model alone judged it: shadow verdicts in place of rule verdicts,
    and the overall outcome recombined from them."""
    verdicts = [verdict.model_only() for verdict in judgment.verdicts]
    outcomes = {
        v.criterion: Outcome(v.outcome) if v.applicable else Outcome.NOT_APPLICABLE
        for v in verdicts
    }
    return CaseJudgment(
        policy=judgment.policy,
        variant=judgment.variant,
        verdicts=verdicts,
        overall=overall_outcome(policy.variant(judgment.variant).logic, outcomes),
        reviewer_checklist=judgment.reviewer_checklist,
    )
