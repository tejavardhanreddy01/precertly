"""Eval case files (evals/cases/**/*.yaml) and the rules that tie them to a policy.

Labeling rules are in evals/GUIDELINES.md. Two of them are code here: which criteria are
scored (chart and request stage), and how the case-level outcome follows from the labels.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from precertly.policies import EvidenceStage, LogicNode, Policy

EVALS_DIR = Path(__file__).resolve().parents[3] / "evals"

# The agent is scored only on criteria it can decide from the record or the request.
SCORED_STAGES = frozenset({EvidenceStage.CHART, EvidenceStage.REQUEST})


class Outcome(StrEnum):
    MET = "met"
    NOT_MET = "not_met"
    INSUFFICIENT = "insufficient"
    NOT_APPLICABLE = "not_applicable"  # scoring label only; the agent never outputs it


class Request(BaseModel):
    """The prior-authorization request. Request-stage criteria are judged from this."""

    date: date
    codes: list[str] = Field(min_length=1)
    region: str
    levels: list[str] = Field(min_length=1)
    same_day_blocks: list[str] = Field(default_factory=list)


class Label(BaseModel):
    criterion: str
    outcome: Outcome
    fact: str = Field(min_length=1, description="The fact in the record or request it rests on")
    evidence: list[str] = Field(
        default_factory=list, description="Resource references, e.g. 'Observation/pain-post-mbb2'"
    )


class ChangedLabel(BaseModel):
    """Why a label differs from the clean template, tied to the policy text."""

    criterion: str
    quote: str = Field(min_length=1)
    why: str = Field(min_length=1)


class EvalCase(BaseModel):
    id: str
    policy: str
    policy_version: dict[str, str] = Field(description="Governing document -> version")
    variant: str
    request: Request
    bundle: str = Field(description="Path to the FHIR bundle, relative to evals/")
    expected: list[Label] = Field(min_length=1)
    expected_overall: Outcome
    changed_labels: list[ChangedLabel] = Field(default_factory=list)
    notes: str
    tags: list[str] = Field(default_factory=list)

    def outcomes(self) -> dict[str, Outcome]:
        return {label.criterion: label.outcome for label in self.expected}

    def bundle_path(self) -> Path:
        return EVALS_DIR / self.bundle


def load_case(path: Path) -> EvalCase:
    with path.open(encoding="utf-8") as f:
        return EvalCase.model_validate(yaml.safe_load(f))


def load_cases(directory: Path = EVALS_DIR / "cases") -> dict[str, EvalCase]:
    cases = [load_case(p) for p in sorted(directory.rglob("*.yaml"))]
    by_id = {c.id: c for c in cases}
    if len(by_id) != len(cases):
        raise ValueError("duplicate case ids")
    return by_id


def scored_criteria(policy: Policy, variant_id: str) -> list[str]:
    """Criteria of a variant that must be labeled, in policy-file order."""
    in_variant = policy.variant(variant_id).logic.criterion_ids()
    return [
        c.id for c in policy.criteria if c.id in in_variant and c.evidence_stage in SCORED_STAGES
    ]


def overall_outcome(logic: LogicNode, outcomes: dict[str, Outcome]) -> Outcome:
    """Case-level outcome from the variant logic (GUIDELINES.md, "Overall outcome").

    not_applicable counts as satisfied. So does a criterion with no label: those are the
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
