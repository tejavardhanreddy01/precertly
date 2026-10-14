"""Types for the judgment step: the request, the evidence, and the verdicts."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from precertly.llm import Exchange, LLMUsage


class Outcome(StrEnum):
    MET = "met"
    NOT_MET = "not_met"
    INSUFFICIENT = "insufficient"
    # Not a verdict outcome: a verdict whose condition does not hold has outcome "met" and
    # applicable=False. The judge maps such verdicts to NOT_APPLICABLE before combining
    # (as does CaseJudgment.outcomes()), the variant logic counts it as satisfied, and eval
    # labels use it too.
    NOT_APPLICABLE = "not_applicable"


class EvidenceChunk(BaseModel):
    """A piece of text the model may cite. Chart chunks use the chunk id 'Type/id#n'."""

    id: str
    text: str
    effective_date: date | None = None


class PriorAuthRequest(BaseModel):
    """The prior-authorization request. Request-stage criteria are judged from this."""

    date: date
    service_date: date = Field(description="Planned date of the requested service")
    procedure: str = Field(description="The requested procedure, in plain words")
    # Structured, because the procedure codes are the same for a medial branch block and
    # an intraarticular injection, and one criterion depends on which it is.
    technique: Literal["medial_branch_block", "intraarticular_injection", "radiofrequency_ablation"]
    codes: list[str] = Field(min_length=1)
    region: str
    levels: list[str] = Field(min_length=1)
    same_day_blocks: list[str] = Field(default_factory=list)

    def chunks(self) -> list[EvidenceChunk]:
        """One citable chunk per field, with id 'request.<field>'."""
        labels = {
            "date": "Request date",
            "service_date": "Planned date of service",
            "procedure": "Requested procedure",
            "technique": "Technique",
            "codes": "Procedure codes",
            "region": "Spinal region",
            "levels": "Levels",
            "same_day_blocks": "Other blocks planned for the same day",
        }
        chunks = []
        for field, label in labels.items():
            value = getattr(self, field)
            if isinstance(value, list):
                value = ", ".join(value) if value else "none"
            chunks.append(EvidenceChunk(id=f"request.{field}", text=f"{label}: {value}"))
        return chunks


class Citation(BaseModel):
    """What the model returns: a handle from the prompt (E1, E2, ...) and a quote."""

    evidence: str = Field(description="Handle of the evidence item, for example E3")
    quote: str = Field(description="Words copied exactly from that evidence item")


class ModelJudgment(BaseModel):
    """Judgment of one coverage criterion against the evidence provided."""

    condition_holds: bool | None = Field(
        default=None,
        description=(
            "Only for a criterion that has a condition: whether the condition holds in "
            "this case. Null when the criterion has no condition."
        ),
    )
    outcome: Literal["met", "not_met", "insufficient"]
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(description="One to three sentences")
    citations: list[Citation] = Field(
        default_factory=list, description="The evidence the outcome rests on"
    )


class VerifiedCitation(BaseModel):
    """A citation whose quote was found in the chunk, with its position there."""

    chunk_id: str
    quote: str
    char_start: int
    char_end: int

    @property
    def resource(self) -> str:
        """'Type/id' for a chart chunk, 'request.<field>' for a request field."""
        return self.chunk_id.split("#")[0]

    @property
    def is_chart(self) -> bool:
        return not self.chunk_id.startswith("request.")


class ComputedCitation(BaseModel):
    """What a rule read and what it computed. Not a verbatim quote: it is checked by
    recomputing it from the listed inputs, not by finding its words in a chunk."""

    computed: Literal[True] = True
    statement: str
    inputs: list[str]
    rule: str | None = None  # the criterion id whose rule computed it
    rules_version: str | None = None
    branch: str | None = None  # partial only: the PARTIAL_BRANCHES entry
    # Partial only: the model cited it as [C1], with this quote, verified against the
    # statement.
    cited_by_model: bool = False
    quote: str | None = None


class ShadowVerdict(BaseModel):
    """The model-only verdict for a criterion a rule decided or informed: the same call
    the baseline makes, kept so rule and model can be compared within one run."""

    outcome: Literal["met", "not_met", "insufficient"]
    applicable: bool
    invalidated: str | None
    model_outcome: str
    citations: list[VerifiedCitation]
    usage: LLMUsage
    exchanges: list[Exchange] = Field(default_factory=list)


class CriterionVerdict(BaseModel):
    criterion: str
    outcome: Literal["met", "not_met", "insufficient"]
    # False when the criterion's condition does not hold. It then counts as satisfied
    # and the outcome is "met" by construction, not by evidence.
    applicable: bool = True
    confidence: float
    rationale: str
    citations: list[VerifiedCitation] = Field(default_factory=list)
    # Set when the model's answer was overridden: a quote was not found in its chunk, or
    # a met / not_met answer cited nothing. The outcome is then "insufficient".
    invalidated: str | None = None
    model_outcome: str
    chunk_ids: list[str] = Field(default_factory=list, description="Evidence shown to the model")
    usage: LLMUsage
    exchanges: list[Exchange] = Field(default_factory=list, description="One per model call")
    # "model": no rule, or the rule found no structured data. "rule": code decided.
    # "rule+model": code fixed a fact and the model judged the rest.
    decided_by: Literal["model", "rule", "rule+model"] = "model"
    rule_kind: Literal["decided", "partial", "absent"] | None = None
    computed: list[ComputedCitation] = Field(default_factory=list)
    shadow: ShadowVerdict | None = None

    def model_only(self) -> CriterionVerdict:
        """This verdict as the model alone would have given it."""
        if self.shadow is None:
            return self
        return CriterionVerdict(
            criterion=self.criterion,
            outcome=self.shadow.outcome,
            applicable=self.shadow.applicable,
            confidence=self.confidence,
            rationale=self.rationale,
            citations=self.shadow.citations,
            invalidated=self.shadow.invalidated,
            model_outcome=self.shadow.model_outcome,
            chunk_ids=self.chunk_ids,
            usage=self.shadow.usage,
            exchanges=self.shadow.exchanges,
        )


class ChecklistItem(BaseModel):
    """A criterion the agent does not judge; a human reviewer confirms it."""

    criterion: str
    evidence_stage: str
    text: str


class CaseJudgment(BaseModel):
    policy: str
    variant: str
    verdicts: list[CriterionVerdict]
    overall: Outcome  # met here means "criteria met"; a human still decides
    reviewer_checklist: list[ChecklistItem]

    def outcomes(self) -> dict[str, Outcome]:
        return {
            v.criterion: Outcome(v.outcome) if v.applicable else Outcome.NOT_APPLICABLE
            for v in self.verdicts
        }
