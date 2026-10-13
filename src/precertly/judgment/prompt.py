"""The prompt for judging one criterion. Kept apart so prompt changes are easy to review."""

from __future__ import annotations

from precertly.judgment.models import EvidenceChunk, PriorAuthRequest
from precertly.policies import Criterion

SYSTEM = """You check one Medicare coverage criterion against one patient's record for a prior-authorization request. A human reviewer makes the final decision; you report what the record shows.

Decide only from the evidence provided. Do not use outside knowledge about the patient and do not assume facts the evidence does not state.

Outcomes:
- met: the evidence satisfies the criterion as its policy text states it.
- not_met: the evidence shows the criterion is not satisfied.
- insufficient: the evidence does not let you decide, because it is absent, incomplete or contradictory.

Rules:
1. Read thresholds exactly as the policy text states them. "At least 80%" is met by 80%. "A minimum of 2 weeks" is met by 14 days and not by 13.
2. A criterion phrased as an absence (for example "absence of untreated radiculopathy") is met only when the record documents an assessment that addresses it and finds nothing. If the record documents the excluded condition, it is not_met. If the record never addresses it, or raises it without resolving it, it is insufficient. Silence is not evidence of absence.
3. If two pieces of evidence disagree on a fact the criterion depends on and nothing resolves the disagreement, the outcome is insufficient.
4. A documented fact that violates the requirement is not_met, not insufficient. If a value needed for a comparison cannot be compared (for example scores on two different scales), the criterion that needs the comparison is insufficient.
5. If the policy text allows an exception that must be documented, a clearly documented exception makes the criterion met.
6. Dates are relative to the request date.
7. If the criterion has a condition, first decide whether the condition holds and report it in condition_holds. If it does not hold, the criterion does not apply: set outcome to met and say so in the rationale.

Citations:
- Cite every piece of evidence your outcome rests on, by chunk id, with a quote copied character for character from that chunk. Do not paraphrase, correct or shorten words inside a quote.
- met and not_met need at least one citation. insufficient should cite the evidence that shows the gap or the contradiction, if there is any.
- Facts about the requested service come from the request chunks (ids starting with "request.")."""


def build_prompt(
    criterion: Criterion, request: PriorAuthRequest, chunks: list[EvidenceChunk]
) -> str:
    lines = [
        "CRITERION",
        f"Id: {criterion.id}",
        f"Statement: {criterion.text}",
        f"Policy text ({criterion.source_document}): {criterion.quote}",
    ]
    if criterion.thresholds:
        thresholds = ", ".join(f"{k}={v}" for k, v in criterion.thresholds.items())
        lines.append(f"Thresholds: {thresholds}")
    if criterion.condition:
        lines.append(f"Condition (the criterion applies only if this holds): {criterion.condition}")
    else:
        lines.append("Condition: none; the criterion always applies.")
    if criterion.note:
        lines.append(f"Note: {criterion.note}")
    lines += ["", f"REQUEST DATE: {request.date.isoformat()}", "", "EVIDENCE"]
    for chunk in chunks:
        when = f" (dated {chunk.effective_date.isoformat()})" if chunk.effective_date else ""
        lines += [f"[{chunk.id}]{when}", chunk.text, ""]
    lines.append("Judge the criterion from this evidence.")
    return "\n".join(lines)
