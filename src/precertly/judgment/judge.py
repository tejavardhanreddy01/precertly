"""Judge criteria with an LLM, verify every quote, and combine the verdicts.

The model's answer is never taken on trust: a quote that is not found in the chunk it
cites invalidates the verdict, and so does a met or not_met answer with no citation. An
invalidated verdict becomes "insufficient", so an unverifiable answer can never read as
approval.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Protocol

from precertly.judgment.logic import judged_criteria, overall_outcome
from precertly.judgment.models import (
    CaseJudgment,
    ChecklistItem,
    CriterionVerdict,
    EvidenceChunk,
    ModelJudgment,
    Outcome,
    PriorAuthRequest,
    VerifiedCitation,
)
from precertly.judgment.prompt import SYSTEM, build_prompt, handles
from precertly.policies import Criterion, Policy


class StructuredLLM(Protocol):
    async def structured(self, prompt: str, schema: type, **options: Any) -> Any: ...


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
    judgment: ModelJudgment, chunks: list[EvidenceChunk]
) -> tuple[list[VerifiedCitation], str | None]:
    """Verified citations, and the reason the verdict is invalid if it is.

    The model cites handles (E1, E2, ...); they are mapped back to chunk ids here. A handle
    that was not in the prompt, or a quote that is not in the item it cites, is invalid.
    """
    by_handle = handles(chunks)
    verified = []
    for citation in judgment.citations:
        handle = citation.evidence.strip().strip("[]").upper()
        chunk = by_handle.get(handle)
        if chunk is None:
            return [], f"cited evidence {citation.evidence!r} was not in the prompt"
        span = find_quote(citation.quote, chunk.text)
        if span is None:
            return [], f"quote not found in {chunk.id}: {citation.quote[:80]!r}"
        verified.append(
            VerifiedCitation(
                chunk_id=chunk.id, quote=citation.quote, char_start=span[0], char_end=span[1]
            )
        )
    return verified, None


async def judge_criterion(
    llm: StructuredLLM,
    criterion: Criterion,
    request: PriorAuthRequest,
    chunks: list[EvidenceChunk],
    *,
    max_tokens: int = 800,
) -> CriterionVerdict:
    """One verdict for one criterion from the evidence given (request chunks included)."""
    response = await llm.structured(
        build_prompt(criterion, request, chunks),
        ModelJudgment,
        system=SYSTEM,
        max_tokens=max_tokens,
    )
    judgment: ModelJudgment = response.data
    citations, invalid = verify(judgment, chunks)
    applicable = not (criterion.condition and judgment.condition_holds is False)

    outcome = judgment.outcome
    if not applicable:
        outcome = "met"  # satisfied because the condition does not hold
    if invalid is None and applicable and outcome != "insufficient" and not citations:
        invalid = f"{outcome} with no citation"
    if invalid is not None:
        outcome, applicable = "insufficient", True

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
    )


async def judge_case(
    llm: StructuredLLM,
    policy: Policy,
    variant_id: str,
    request: PriorAuthRequest,
    evidence: EvidenceProvider,
    *,
    concurrency: int = 4,
) -> CaseJudgment:
    """Judge every chart- and request-stage criterion of a variant and combine them."""
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
        async with gate:
            return await judge_criterion(
                llm, policy.criterion(criterion_id), request, evidence_by_criterion[criterion_id]
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
