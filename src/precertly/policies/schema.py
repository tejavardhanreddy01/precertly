"""Typed model of a coverage policy: atomic criteria plus AND/OR logic per request variant.

A policy file encodes one payer coverage document (an NCD or LCD). Each criterion is one
checkable statement. Each variant (for example "initial RFA" vs "repeat RFA") combines
criteria with a logic tree of all_of / any_of nodes whose leaves are criterion ids.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

# Separator between non-adjacent passages inside one quote.
QUOTE_GAP = "[...]"


class CriterionKind(StrEnum):
    CLINICAL = "clinical"  # a fact about the patient (duration, score, lab value)
    DOCUMENTATION = "documentation"  # something that must be on file
    FREQUENCY = "frequency"  # counts and intervals over time
    EXCLUSION = "exclusion"  # must NOT be present
    COVERAGE = "coverage"  # the requested service itself must be a covered type


class Severity(StrEnum):
    REQUIRED = "required"  # failing it means criteria are not met
    WARNING = "warning"  # policy says it may lead to denial; flag, don't fail


class Criterion(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    text: str
    kind: CriterionKind
    severity: Severity = Severity.REQUIRED
    thresholds: dict[str, Any] = Field(default_factory=dict)
    evidence: list[str] = Field(
        default_factory=list, description="FHIR resource types likely to hold the evidence"
    )
    quote: str = Field(
        min_length=1,
        description=(
            "Exact sentence(s) of the governing document that this criterion encodes. "
            f"Non-adjacent passages are joined with '{QUOTE_GAP}'."
        ),
    )
    source_document: str = Field(description="Governing document id, e.g. 'LCD L38773'")
    source_section: str = Field(min_length=1, description="Section of that document")
    condition: str | None = Field(
        default=None,
        description=(
            "When the criterion applies. Absent means always. If the condition does not "
            "hold for a case, the criterion is not applicable."
        ),
    )
    note: str | None = None

    def quote_parts(self) -> list[str]:
        return [part.strip() for part in self.quote.split(QUOTE_GAP)]


class LogicNode(BaseModel):
    """all_of / any_of node. Children are criterion ids or nested nodes."""

    all_of: list[str | LogicNode] | None = None
    any_of: list[str | LogicNode] | None = None

    @model_validator(mode="after")
    def exactly_one_operator(self) -> LogicNode:
        if (self.all_of is None) == (self.any_of is None):
            raise ValueError("a logic node needs exactly one of all_of or any_of")
        if not (self.all_of or self.any_of):
            raise ValueError("a logic node cannot be empty")
        return self

    def children(self) -> list[str | LogicNode]:
        return self.all_of if self.all_of is not None else self.any_of  # type: ignore[return-value]

    def criterion_ids(self) -> set[str]:
        ids: set[str] = set()
        for child in self.children():
            ids |= {child} if isinstance(child, str) else child.criterion_ids()
        return ids


class Variant(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    title: str
    codes: list[str] = Field(min_length=1, description="CPT or HCPCS codes for this request")
    logic: LogicNode


class Source(BaseModel):
    """A governing coverage document: an NCD, an LCD or an LCD-related article."""

    document: str = Field(pattern=r"^(NCD \d+(\.\d+)*|LCD L\d+|Article A\d+)$")
    title: str
    url: HttpUrl
    version: str = Field(description="Document version in the Medicare Coverage Database")
    effective_date: date
    contractor: str | None = None  # the MAC, for LCDs and articles


class Policy(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    title: str
    document: str = Field(description="Governing document, e.g. 'LCD L38773' or 'NCD 100.1'")
    verified_on: date = Field(description="Date the governing documents were retrieved")
    sources: list[Source] = Field(min_length=1)
    criteria: list[Criterion] = Field(min_length=1)
    variants: list[Variant] = Field(min_length=1)
    not_covered: list[str] = Field(
        default_factory=list, description="Non-covered items, verbatim from a source document"
    )
    notes: list[str] = Field(default_factory=list)

    @field_validator("criteria")
    @classmethod
    def unique_criterion_ids(cls, criteria: list[Criterion]) -> list[Criterion]:
        ids = [c.id for c in criteria]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate criterion ids: {sorted(dupes)}")
        return criteria

    @model_validator(mode="after")
    def logic_references_known_criteria(self) -> Policy:
        known = {c.id for c in self.criteria}
        used: set[str] = set()
        for variant in self.variants:
            refs = variant.logic.criterion_ids()
            missing = refs - known
            if missing:
                raise ValueError(
                    f"variant {variant.id} references unknown criteria {sorted(missing)}"
                )
            used |= refs
        unused = known - used
        if unused:
            raise ValueError(f"criteria never used by any variant: {sorted(unused)}")
        variant_ids = [v.id for v in self.variants]
        if len(variant_ids) != len(set(variant_ids)):
            raise ValueError("duplicate variant ids")
        return self

    @model_validator(mode="after")
    def criteria_cite_listed_sources(self) -> Policy:
        documents = {s.document for s in self.sources}
        for criterion in self.criteria:
            if criterion.source_document not in documents:
                raise ValueError(
                    f"criterion {criterion.id} cites {criterion.source_document!r}, "
                    f"which is not one of the policy's sources {sorted(documents)}"
                )
        return self

    def source(self, document: str) -> Source:
        return next(s for s in self.sources if s.document == document)

    def criterion(self, criterion_id: str) -> Criterion:
        return next(c for c in self.criteria if c.id == criterion_id)

    def variant(self, variant_id: str) -> Variant:
        return next(v for v in self.variants if v.id == variant_id)

    def variant_for_code(self, code: str) -> list[Variant]:
        return [v for v in self.variants if code in v.codes]
