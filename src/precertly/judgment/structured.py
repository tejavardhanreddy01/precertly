"""Typed facts read straight from a FHIR bundle, for the rules in rules.py.

Rules never go through retrieval: they read every coded Procedure and Observation in the
chart, so a retrieval miss cannot hide a coded score.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

CPT = "http://www.ama-assn.org/go/cpt"
LOINC = "http://loinc.org"

# Facet procedure code numbers by spinal region, as article A58364 groups them. Numbers
# only, for classification. The injection codes cover diagnostic and therapeutic
# injections alike, and medial branch blocks and intraarticular injections alike.
FACET_INJECTION = {
    "cervical_thoracic": frozenset({"64490", "64491", "64492"}),
    "lumbar_sacral": frozenset({"64493", "64494", "64495"}),
}
FACET_RFA = {
    "cervical_thoracic": frozenset({"64633", "64634"}),
    "lumbar_sacral": frozenset({"64635", "64636"}),
}
# Every facet procedure code. PriorAuthRequest uses it to tell a facet request, which must
# carry technique, region, levels and same_day_blocks, from any other request.
FACET_CODES = frozenset().union(*FACET_INJECTION.values(), *FACET_RFA.values())
REGIONS = {
    "cervical": "cervical_thoracic",
    "thoracic": "cervical_thoracic",
    "lumbar": "lumbar_sacral",
    "sacral": "lumbar_sacral",
}

# Pain severity scales code can recognize: 72514-3 (0-10 numeric rating) and 38214-3 (visual
# analog). A score is a pain score only if it carries one of these codes, and two pain
# scores are on the same scale only if they carry the same one.
PAIN_SCORE_CODES = frozenset({f"{LOINC}|72514-3", f"{LOINC}|38214-3"})
DISABILITY_SCORE_CODES = frozenset({f"{LOINC}|97909-6"})  # Oswestry Disability Index score


@dataclass(frozen=True)
class Moment:
    """A FHIR date or dateTime. `time` is None when the source gave a date only."""

    day: date
    time: datetime | None

    @classmethod
    def parse(cls, value: Any) -> Moment | None:
        if not isinstance(value, str) or len(value) < 10:
            return None
        try:
            if len(value) == 10:
                return cls(date.fromisoformat(value), None)
            moment = datetime.fromisoformat(value)
        except ValueError:
            return None
        return cls(moment.date(), moment)


@dataclass(frozen=True)
class ProcedureFact:
    ref: str
    cpt: frozenset[str]
    start: Moment
    end: Moment  # performedPeriod.end if present, else the start
    encounter: str | None
    body_site_text: str | None
    body_site_codes: frozenset[str]


@dataclass(frozen=True)
class ScoreFact:
    """An Observation with a numeric value."""

    ref: str
    codes: frozenset[str]  # "system|code"
    unit: str | None
    value: float
    when: Moment
    encounter: str | None

    @property
    def is_pain(self) -> bool:
        return bool(self.codes & PAIN_SCORE_CODES)

    @property
    def pain_scale(self) -> frozenset[str]:
        """The pain scale this score is on. Empty for anything that is not a coded pain
        score, which code then treats as missing, never as a different scale."""
        return self.codes & PAIN_SCORE_CODES

    @property
    def is_disability(self) -> bool:
        return bool(self.codes & DISABILITY_SCORE_CODES)


def _encounter(resource: dict[str, Any]) -> str | None:
    return (resource.get("encounter") or {}).get("reference")


def _codes(concept: dict[str, Any] | None) -> frozenset[str]:
    return frozenset(
        f"{c['system']}|{c['code']}"
        for c in (concept or {}).get("coding") or []
        if c.get("system") and c.get("code")
    )


@dataclass(frozen=True)
class StructuredChart:
    procedures: tuple[ProcedureFact, ...]
    scores: tuple[ScoreFact, ...]

    @classmethod
    def from_bundle(cls, bundle: dict[str, Any]) -> StructuredChart:
        procedures, scores = [], []
        for entry in bundle.get("entry") or []:
            r = entry.get("resource") or {}
            ref = f"{r.get('resourceType')}/{r.get('id')}"
            if r.get("resourceType") == "Procedure" and r.get("status") == "completed":
                period = r.get("performedPeriod") or {}
                start = Moment.parse(r.get("performedDateTime") or period.get("start"))
                if start is None:
                    continue
                sites = r.get("bodySite") or []
                cpt = {c.split("|", 1)[1] for c in _codes(r.get("code")) if c.startswith(CPT)}
                procedures.append(
                    ProcedureFact(
                        ref=ref,
                        cpt=frozenset(cpt),
                        start=start,
                        end=Moment.parse(period.get("end")) or start,
                        encounter=_encounter(r),
                        body_site_text=sites[0].get("text") if len(sites) == 1 else None,
                        body_site_codes=frozenset().union(*(_codes(s) for s in sites)),
                    )
                )
            elif r.get("resourceType") == "Observation":
                quantity = r.get("valueQuantity") or {}
                when = Moment.parse(r.get("effectiveDateTime"))
                value = quantity.get("value")
                if when is None or isinstance(value, bool) or not isinstance(value, int | float):
                    continue
                scores.append(
                    ScoreFact(
                        ref=ref,
                        codes=_codes(r.get("code")),
                        unit=quantity.get("unit") or quantity.get("code"),
                        value=float(value),
                        when=when,
                        encounter=_encounter(r),
                    )
                )
        return cls(tuple(procedures), tuple(scores))

    def with_codes(self, codes: frozenset[str]) -> list[ProcedureFact]:
        """Completed procedures carrying any of these CPT codes, oldest first."""
        found = [p for p in self.procedures if p.cpt & codes]
        return sorted(found, key=lambda p: (p.start.day, p.ref))
