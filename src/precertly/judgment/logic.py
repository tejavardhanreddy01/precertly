"""Which criteria the agent judges, and how verdicts combine into a case outcome."""

from __future__ import annotations

from precertly.judgment.models import Outcome
from precertly.policies import EvidenceStage, LogicNode, Policy

# The agent judges only criteria it can decide from the record or the request. The other
# stages (provider attestation, post-service, payer) go to the human reviewer.
JUDGED_STAGES = frozenset({EvidenceStage.CHART, EvidenceStage.REQUEST})


def judged_criteria(policy: Policy, variant_id: str) -> list[str]:
    """Criteria of a variant that the agent judges, in policy-file order."""
    in_variant = policy.variant(variant_id).logic.criterion_ids()
    return [
        c.id for c in policy.criteria if c.id in in_variant and c.evidence_stage in JUDGED_STAGES
    ]


def overall_outcome(logic: LogicNode, outcomes: dict[str, Outcome]) -> Outcome:
    """Case-level outcome from the variant logic (evals/GUIDELINES.md, "Overall outcome").

    not_applicable counts as satisfied. So does a criterion with no outcome: those are the
    attestation, post-service and payer criteria left to the human reviewer.
    """
    results = []
    for child in logic.children():
        if isinstance(child, LogicNode):
            results.append(overall_outcome(child, outcomes))
        else:
            outcome = outcomes.get(child, Outcome.MET)
            results.append(Outcome.MET if outcome is Outcome.NOT_APPLICABLE else outcome)

    if logic.all_of is not None:
        for failing in (Outcome.NOT_MET, Outcome.INSUFFICIENT):
            if failing in results:
                return failing
        return Outcome.MET
    for best in (Outcome.MET, Outcome.INSUFFICIENT):
        if best in results:
            return best
    return Outcome.NOT_MET
