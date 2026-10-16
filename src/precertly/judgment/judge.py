"""Judge criteria with an LLM, verify every quote, and combine the verdicts.

The model's answer is never taken on trust: a quote that is not found in the chunk it
cites invalidates the verdict, and so does a met or not_met answer with no citation. A
verdict that a criterion's condition does not hold must cite the evidence its policy names
in condition_evidence. An invalidated verdict becomes "insufficient", so an unverifiable
answer can never read as approval.

With a partial rule result the computed fact is evidence item [C1]. It is verified like
any other item (the quote must be in its statement), and PARTIAL_BRANCHES fixes which
outcomes may rest on it alone; any other outcome still needs a verified chart citation.
"""

from __future__ import annotations

import asyncio
import re
from typing import Literal, Protocol

from pydantic import BaseModel

from precertly.judgment.logic import judged_criteria, overall_outcome
from precertly.judgment.models import (
    CaseJudgment,
    ChecklistItem,
    ComputedCitation,
    CriterionVerdict,
    EvidenceChunk,
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
# verdict must cite its criterion's condition_evidence.
VERIFIER_VERSION = "2"


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


def verify(
    judgment: ModelJudgment, chunks: list[EvidenceChunk], computed: str | None = None
) -> tuple[list[VerifiedCitation], str | None, str | None]:
    """Verified chunk citations, the verified quote from [C1] if it was cited, and the
    reason the verdict is invalid if it is.

    The model cites handles (E1, E2, ...); they are mapped back to chunk ids here. [C1] is
    a handle only when computed (a partial rule's statement) was in the prompt. A handle
    that was not in the prompt, or a quote that is not in the item it cites, is invalid.
    """
    by_handle = handles(chunks)
    verified, computed_quote = [], None
    for citation in judgment.citations:
        handle = citation.evidence.strip().strip("[]").upper()
        if handle == COMPUTED_HANDLE and computed is not None:
            if find_quote(citation.quote, computed) is None:
                return [], None, f"quote not found in [C1]: {citation.quote[:80]!r}"
            computed_quote = citation.quote
            continue
        chunk = by_handle.get(handle)
        if chunk is None:
            return [], None, f"cited evidence {citation.evidence!r} was not in the prompt"
        span = find_quote(citation.quote, chunk.text)
        if span is None:
            return [], None, f"quote not found in {chunk.id}: {citation.quote[:80]!r}"
        verified.append(
            VerifiedCitation(
                chunk_id=chunk.id, quote=citation.quote, char_start=span[0], char_end=span[1]
            )
        )
    return verified, computed_quote, None


def citation_problem(
    criterion_id: str,
    outcome: Literal["met", "not_met", "insufficient"],
    applicable: bool,
    citations: list[VerifiedCitation],
    computed_quote: str | None,
    partial: RuleResult | None,
    condition_holds: bool | None = None,
    condition_evidence: str | None = None,
) -> str | None:
    """Why the citations cannot support this outcome, or None if they can.

    Without a partial rule result, met and not_met need at least one citation. With one,
    the PARTIAL_BRANCHES entry decides: an outcome it lists may rest on [C1] alone, and any
    other needs a verified chart citation (a request field does not count). On either
    path, not applicable must also cite the criterion's condition_evidence.
    """
    if applicable and outcome == "insufficient":
        return None
    if partial is None:
        if not applicable:
            return condition_citation_problem(condition_evidence, citations)
        return None if citations else f"{outcome} with no citation"
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
        return "condition_holds is false, but [C1] establishes that the condition holds"
    if not branch.needs_chart(final):
        if citations or computed_quote is not None:
            return None
        return f"{final} with no citation"
    if not any(citation.is_chart for citation in citations):
        cited = "only [C1] or request items" if citations or computed_quote else "nothing"
        return f"{final} needs a chart citation; cited {cited}"
    if not applicable:
        return condition_citation_problem(condition_evidence, citations)
    return None


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
    if partial is not None:
        computed = [computed_citation(criterion.id, partial, computed_quote)]

    return CriterionVerdict(
        criterion=criterion.id,
        outcome=outcome,
        applicable=applicable,
        confidence=judgment.confidence,
        rationale=judgment.rationale,
        citations=citations,
        invalidated=invalid,
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
