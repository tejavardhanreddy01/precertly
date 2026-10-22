"""Models for every file the recorded-mode export writes. Extra fields are forbidden, and
schema.json is generated from Index, PolicyFile and CaseFile so the UI can derive its types."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_serializer

SCHEMA_VERSION = 1

# Shown on every page of the site; fixed text, never computed.
BANNERS = [
    "All patient data is synthetic (Synthea); no real patient information.",
    "Recorded run; its date and prompt hash are shown below. Nothing runs when you open this site.",
    "Single annotator; not clinically validated.",
    "Scored on the 11 facet cases only; the other 18 cases show labels, not yet scored.",
    "The copilot never denies a request; a human reviewer decides.",
]

Outcome = Literal["met", "not_met", "insufficient"]
LabelOutcome = Literal["met", "not_met", "insufficient", "not_applicable"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OmitNone(Strict):
    """Writes only the fields that are set, as the YAML the model came from does."""

    @model_serializer(mode="wrap")
    def _omit_none(self, handler: Any) -> dict[str, Any]:
        return {key: value for key, value in handler(self).items() if value is not None}


# --- index.json ---------------------------------------------------------------------------


class JudgmentInfo(Strict):
    verifier_version: str


class RulesInfo(Strict):
    version: str


class RetrievalInfo(Strict):
    top_k: int
    method: str


class RescoredInfo(Strict):
    source: str
    source_sha256: str


class RunInfo(Strict):
    run_date: str
    git_sha: str
    git_dirty: bool
    complete: bool
    model_id: str
    prompt_version: str
    system_prompt_sha256: str
    judgment: JudgmentInfo
    rules: RulesInfo
    retrieval: RetrievalInfo
    annotation: str
    rescored: RescoredInfo
    source_file: str
    source_sha256: str


class RunSummary(Strict):
    cases: int
    cases_correct: int
    case_false_approvals: int
    scored_criteria: int
    criteria_correct: int
    criterion_accuracy: float
    negative_labels: int
    false_approvals: int
    false_approval_rate: float
    citations: int
    citations_in_label: int
    citation_precision: float
    citation_precision_macro: float


class PolicyRef(Strict):
    id: str
    title: str
    document: str


class CaseRow(Strict):
    id: str
    set: str
    policy: str
    variant: str
    procedure: str
    expected_overall: Outcome
    scored: bool
    predicted_overall: Outcome | None
    overall_correct: bool | None
    criteria: int  # every criterion of the variant, judged or not
    labeled_criteria: int  # the criteria that carry an expected label
    tags: list[str]


class Index(Strict):
    schema_version: Literal[1]
    run: RunInfo
    summary: RunSummary
    banners: list[str]
    policies: list[PolicyRef]
    cases: list[CaseRow]


# --- policies/<id>.json -------------------------------------------------------------------


class PolicySource(Strict):
    document: str
    title: str
    url: str
    version: str
    effective_date: date
    contractor: str | None


class PolicyCriterion(Strict):
    id: str
    text: str
    kind: str
    evidence_stage: str
    severity: str
    quote: str
    source_document: str
    source_section: str
    condition: str | None
    condition_evidence: str | None
    note: str | None


class Logic(OmitNone):
    """The variant logic tree exactly as in the policy YAML: one of all_of / any_of."""

    all_of: list[str | Logic] | None = None
    any_of: list[str | Logic] | None = None


class PolicyVariant(Strict):
    id: str
    title: str
    codes: list[str]
    logic: Logic


class PolicyFile(Strict):
    id: str
    title: str
    document: str
    verified_on: date
    sources: list[PolicySource]
    criteria: list[PolicyCriterion]
    variants: list[PolicyVariant]
    not_covered: list[str]
    notes: list[str]


# --- cases/<id>.json ----------------------------------------------------------------------


class Request(OmitNone):
    """The request block as in the case YAML; fields the case does not set are absent."""

    date: date
    service_date: date
    procedure: str
    technique: str | None = None
    codes: list[str]
    region: str | None = None
    levels: list[str] | None = None
    same_day_blocks: list[str] | None = None
    leads: int | None = None
    contacts: int | None = None
    place_of_service: str | None = None


class ExpectedLabel(Strict):
    criterion: str
    outcome: LabelOutcome
    fact: str
    evidence: list[str]


class ChangedLabel(Strict):
    criterion: str
    quote: str
    why: str


class ChartChunk(Strict):
    """A piece of text the model could cite: a chart chunk, or a request field."""

    chunk_id: str
    source: Literal["chart", "request"]
    resource_type: str
    resource_id: str
    chunk_index: int
    effective_date: date | None
    text: str
    # For a note chunk: the chunk's own span in the note it was sliced from.
    char_start: int | None
    char_end: int | None


class CaseCriterion(Strict):
    """A criterion of the case's variant. The copilot judges only chart and request stage
    criteria; for the rest judged is false and a human reviewer confirms them."""

    criterion: str
    text: str
    evidence_stage: str
    judged: bool


class Citation(Strict):
    chunk_id: str
    quote: str
    char_start: int
    char_end: int
    # True when the quote is at these offsets of the chunk, up to whitespace (as the
    # verifier matches). False only on an invalidated verdict.
    located: bool


class ComputedFact(Strict):
    computed: Literal[True]
    statement: str
    inputs: list[str]
    rule: str | None
    rules_version: str | None
    branch: str | None
    cited_by_model: bool
    quote: str | None


class ChecklistItem(Strict):
    criterion: str
    evidence_stage: str
    text: str


class Verdict(Strict):
    criterion: str
    outcome: Outcome
    model_outcome: str
    decided_by: Literal["model", "rule", "rule+model"]
    rule_kind: Literal["decided", "partial", "absent"] | None
    applicable: bool
    confidence: float
    rationale: str
    citations: list[Citation]
    invalidated: str | None
    invalidation: str | None
    computed: list[ComputedFact]
    shown_chunk_ids: list[str]
    expected: LabelOutcome
    correct: bool
    false_approval: bool
    prompt_sha256: str | None  # the first exchange; None when a rule decided without a call


class RecordedResult(Strict):
    predicted_overall: Outcome
    overall_correct: bool
    reviewer_checklist: list[ChecklistItem]
    verdicts: list[Verdict]


class CaseFile(Strict):
    id: str
    set: str
    policy: str
    policy_version: dict[str, str]
    variant: str
    request: Request
    description: str
    tags: list[str]
    expected_overall: Outcome
    expected: list[ExpectedLabel]
    changed_labels: list[ChangedLabel]
    criteria: list[CaseCriterion]
    chart: list[ChartChunk]
    scored: bool
    result: RecordedResult | None


MODELS: dict[str, type[BaseModel]] = {
    "CaseFile": CaseFile,
    "Index": Index,
    "PolicyFile": PolicyFile,
}
