"""Eval case files (evals/cases/**/*.yaml) and the rules that tie them to a policy.

Labeling rules are in evals/GUIDELINES.md. Two of them are code here: which criteria are
scored (chart and request stage), and how the case-level outcome follows from the labels.
Both live in precertly.judgment, because the pipeline applies the same rules.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from precertly.judgment.logic import JUDGED_STAGES as SCORED_STAGES
from precertly.judgment.logic import judged_criteria as scored_criteria
from precertly.judgment.logic import overall_outcome
from precertly.judgment.models import FACET_FIELDS, Outcome
from precertly.judgment.models import PriorAuthRequest as Request

EVALS_DIR = Path(__file__).resolve().parents[3] / "evals"
FACET_POLICY = "facet-joint-interventions"


class Label(BaseModel):
    criterion: str
    outcome: Outcome
    fact: str = Field(min_length=1, description="The fact in the record or request it rests on")
    evidence: list[str] = Field(
        default_factory=list,
        description=(
            "What establishes the label: resource references ('Observation/pain-post-mbb2') "
            "and request fields ('request.levels')"
        ),
    )

    def resources(self) -> list[str]:
        return [e for e in self.evidence if not e.startswith("request.")]

    def request_fields(self) -> list[str]:
        return [e.removeprefix("request.") for e in self.evidence if e.startswith("request.")]


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

    @model_validator(mode="after")
    def _facet_request_complete(self) -> EvalCase:
        """A facet case needs the facet request fields, whatever its codes say.

        The request checks this from its codes too; this check keys on the policy id, so a
        facet case with a mistyped code still fails to load.
        """
        if self.policy == FACET_POLICY:
            missing = [f for f in FACET_FIELDS if getattr(self.request, f) is None]
            if missing:
                raise ValueError(
                    f"{self.id}: a {FACET_POLICY} request must set {', '.join(missing)}"
                )
        return self

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


__all__ = [
    "EVALS_DIR",
    "FACET_POLICY",
    "SCORED_STAGES",
    "ChangedLabel",
    "EvalCase",
    "Label",
    "Outcome",
    "Request",
    "load_case",
    "load_cases",
    "overall_outcome",
    "scored_criteria",
]
