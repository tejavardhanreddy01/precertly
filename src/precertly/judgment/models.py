"""Types for the judgment step: the request, the evidence, and the verdicts."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from precertly.judgment.structured import FACET_CODES, SCS_CODES
from precertly.llm import Exchange, LLMUsage

# The facet-only request fields. A request for any other procedure leaves them unset.
FACET_FIELDS = ("technique", "region", "levels", "same_day_blocks")
# The spinal cord stimulator request fields. A request for any other procedure leaves them
# unset; a stimulator request also needs a place of service (SCS_SETTINGS).
SCS_FIELDS = ("leads", "contacts")
# CMS place-of-service codes a stimulator request may carry, in the words the model sees.
# The LCD and the article name the setting of a permanent implant (an ASC, an outpatient
# hospital or a hospital), and these codes are how the request states it. There is no second
# field for the setting, so a request cannot hold two values that disagree.
SCS_SETTINGS = {
    "11": "office",
    "19": "off-campus outpatient hospital",
    "21": "inpatient hospital",
    "22": "on-campus outpatient hospital",
    "24": "ambulatory surgical center",
}


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
    # True for a verbatim slice of a free-text attachment (a note or a report's
    # presentedForm: the chunker set char_start), False for text rendered from coded
    # fields or a request field. Free text is split into lines; the rest is one line.
    free_text: bool = False


class PriorAuthRequest(BaseModel):
    """The prior-authorization request. Request-stage criteria are judged from this."""

    date: date
    service_date: date = Field(description="Planned date of the requested service")
    procedure: str = Field(description="The requested procedure, in plain words")
    # Facet fields (FACET_FIELDS): required on a facet request, unset on any other.
    # Structured, because the procedure codes are the same for a medial branch block and
    # an intraarticular injection, and one criterion depends on which it is.
    technique: (
        Literal["medial_branch_block", "intraarticular_injection", "radiofrequency_ablation"] | None
    ) = None
    codes: list[str] = Field(min_length=1)
    region: str | None = None
    levels: list[str] | None = Field(default=None, min_length=1)
    # An empty list means the request lists no other blocks; None means it does not say.
    same_day_blocks: list[str] | None = None
    # Spinal cord stimulator fields (SCS_FIELDS): required on a stimulator request, unset on
    # any other. contacts is the total across leads.
    leads: int | None = Field(default=None, ge=1)
    contacts: int | None = Field(default=None, ge=1)
    # CMS place-of-service code for the requested service. It drafts the PAS request, and a
    # stimulator request must set it (SCS_SETTINGS), because the setting of a permanent
    # implant is a coverage criterion. chunks() renders it for a stimulator request only,
    # so the prompts of every other request never show it.
    place_of_service: str | None = Field(default=None, pattern=r"^\d{2}$")

    @model_validator(mode="after")
    def _facet_fields_complete(self) -> PriorAuthRequest:
        """A request with a facet code, or with any facet field set, needs all of them.

        Fails closed: the facet rules and criteria read these fields, and an unset one must
        never pass for "no blocks" or "no levels".
        """
        facet_codes = sorted(set(self.codes) & FACET_CODES)
        given = [f for f in FACET_FIELDS if getattr(self, f) is not None]
        if not facet_codes and not given:
            return self
        missing = [f for f in FACET_FIELDS if getattr(self, f) is None]
        if missing:
            why = f"facet codes {facet_codes}" if facet_codes else f"facet fields {given}"
            raise ValueError(f"a facet request ({why}) must set {', '.join(missing)}")
        return self

    @model_validator(mode="after")
    def _scs_fields_complete(self) -> PriorAuthRequest:
        """A request with a stimulator code needs leads, contacts and a place of service, and
        leads or contacts on any other request is an error (they would be silently ignored)."""
        scs_codes = sorted(set(self.codes) & SCS_CODES)
        given = [f for f in SCS_FIELDS if getattr(self, f) is not None]
        if given and not scs_codes:
            raise ValueError(
                f"{', '.join(given)} belong to a spinal cord stimulator request, "
                f"which needs one of the codes {sorted(SCS_CODES)}"
            )
        if not scs_codes:
            return self
        if set(self.codes) & FACET_CODES:
            raise ValueError("a request cannot carry both facet and spinal cord stimulator codes")
        missing = [f for f in (*SCS_FIELDS, "place_of_service") if getattr(self, f) is None]
        if missing:
            why = f"a spinal cord stimulator request (codes {scs_codes})"
            raise ValueError(f"{why} must set {', '.join(missing)}")
        if self.place_of_service not in SCS_SETTINGS:
            raise ValueError(
                f"place_of_service {self.place_of_service!r} is not one of {sorted(SCS_SETTINGS)}"
            )
        assert self.leads is not None and self.contacts is not None
        if self.contacts < self.leads:
            raise ValueError(f"{self.contacts} contacts cannot be fewer than {self.leads} leads")
        return self

    @property
    def is_scs(self) -> bool:
        return bool(set(self.codes) & SCS_CODES)

    def chunks(self) -> list[EvidenceChunk]:
        """One citable chunk per field that is set, with id 'request.<field>'."""
        labels = {
            "date": "Request date",
            "service_date": "Planned date of service",
            "procedure": "Requested procedure",
            "technique": "Technique",
            "codes": "Procedure codes",
            "region": "Spinal region",
            "levels": "Levels",
            "same_day_blocks": "Other blocks planned for the same day",
            "leads": "Number of leads",
            "contacts": "Total number of contacts",
        }
        chunks = []
        for field, label in labels.items():
            value = getattr(self, field)
            if value is None:
                continue
            if isinstance(value, list):
                value = ", ".join(value) if value else "none"
            chunks.append(EvidenceChunk(id=f"request.{field}", text=f"{label}: {value}"))
        if self.is_scs and self.place_of_service is not None:
            setting = SCS_SETTINGS[self.place_of_service]
            chunks.append(
                EvidenceChunk(
                    id="request.place_of_service",
                    text=f"Place of service: {setting} (code {self.place_of_service})",
                )
            )
        return chunks


class Citation(BaseModel):
    """What the model returns: a handle from the prompt (E1, E2, ...) and a quote."""

    evidence: str = Field(description="Handle of the evidence item, for example E3")
    quote: str = Field(description="Words copied exactly from that evidence item")


# Why a verdict was invalidated. Older results files record only the reason text; see
# judge.invalidation_kind for how it maps to these.
InvalidationKind = Literal[
    "no_usable_answer",
    "quote_not_found",
    "unknown_handle",
    "citation_too_long",
    "handle_mismatch",
    "missing_citation",
    "chart_citation_required",
    "condition_evidence",
    "condition_contradicts_computed",
]


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
    """A citation checked against its chunk: the quoted text and its position there.

    From verifier version 3 the quote is the cited lines, extracted by the verifier, and
    first_line / last_line give them; earlier files hold the model's own verified quote.
    """

    chunk_id: str
    quote: str
    char_start: int
    char_end: int
    first_line: int | None = None
    last_line: int | None = None

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
    invalidation: InvalidationKind | None = None
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
    # Set when the model's answer was overridden: a citation did not check out, or the
    # citations do not meet what the outcome needs. The outcome is then "insufficient".
    invalidated: str | None = None
    invalidation: InvalidationKind | None = None  # the kind of invalidated, from version 3
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
            invalidation=self.shadow.invalidation,
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
