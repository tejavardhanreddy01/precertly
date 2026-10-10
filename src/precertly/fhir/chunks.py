"""Turn a FHIR R4 bundle into text chunks that keep their resource id.

Structured resources (Condition, Observation, ...) become one chunk of readable text.
Free-text attachments (DocumentReference notes, DiagnosticReport presentedForm) are
base64-decoded and split into chunks that are verbatim slices of the decoded text, with
character offsets, so a quote cited by the agent can be checked against the source.
Billing and administrative resources are skipped.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, computed_field

Resource = dict[str, Any]

MAX_NOTE_CHARS = 1500

_SYSTEMS = {
    "http://loinc.org": "LOINC",
    "http://snomed.info/sct": "SNOMED",
    "http://www.nlm.nih.gov/research/umls/rxnorm": "RxNorm",
    "http://www.ama-assn.org/go/cpt": "CPT",
    "http://hl7.org/fhir/sid/icd-10-cm": "ICD-10-CM",
    "https://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets": "HCPCS",
    "http://hl7.org/fhir/sid/cvx": "CVX",
}


class Chunk(BaseModel):
    resource_type: str
    resource_id: str
    chunk_index: int
    effective_date: date | None = None
    text: str
    # Offsets into the decoded attachment this chunk was sliced from; None for chunks
    # rendered from structured fields.
    char_start: int | None = None
    char_end: int | None = None
    # Structured fields, set on chunks rendered from coded resources: "system|code" tokens
    # (component codes included) and the numeric value of an Observation.
    codes: list[str] = []
    value: float | None = None
    unit: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def id(self) -> str:
        return f"{self.ref}#{self.chunk_index}"

    @property
    def ref(self) -> str:
        return f"{self.resource_type}/{self.resource_id}"


class _Index:
    """Resolves references inside one bundle, by fullUrl or by Type/id."""

    def __init__(self, resources: list[tuple[str | None, Resource]]) -> None:
        self._by_ref: dict[str, Resource] = {}
        for full_url, resource in resources:
            if full_url:
                self._by_ref[full_url] = resource
            self._by_ref[f"{resource.get('resourceType')}/{resource.get('id')}"] = resource

    def label(self, reference: dict[str, Any] | None) -> str:
        if not reference:
            return ""
        target = self._by_ref.get(reference.get("reference", ""))
        if target is not None:
            for key in ("code", "type", "medicationCodeableConcept", "vaccineCode"):
                value = target.get(key)
                concept = value[0] if isinstance(value, list) and value else value
                if isinstance(concept, dict) and (label := _concept(concept)):
                    return label
        return reference.get("display", "")


def _get(resource: Resource, path: str) -> Any:
    value: Any = resource
    for key in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _parse_date(value: Any) -> date | None:
    """FHIR date/dateTime/instant to a date. Partial dates fall on the 1st."""
    if not isinstance(value, str):
        return None
    match = re.match(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", value)
    if match is None:
        return None
    year, month, day = match.groups()
    try:
        return date(int(year), int(month or 1), int(day or 1))
    except ValueError:
        return None


def _code(concept: dict[str, Any] | None) -> str:
    """Bare code or text, for status-like concepts."""
    if not concept:
        return ""
    codings = concept.get("coding") or []
    return concept.get("text") or (codings[0].get("code", "") if codings else "")


def _concept(concept: dict[str, Any] | None) -> str:
    """Display text plus codes from well-known systems: 'Body mass index (LOINC 39156-5)'."""
    if not concept:
        return ""
    codings = concept.get("coding") or []
    label = concept.get("text") or next((c["display"] for c in codings if c.get("display")), "")
    if not label and codings:
        label = codings[0].get("code", "")
    codes = [
        f"{_SYSTEMS[c['system']]} {c['code']}"
        for c in codings
        if c.get("system") in _SYSTEMS and c.get("code")
    ]
    return f"{label} ({', '.join(codes)})" if label and codes else label


def _concepts(concepts: list[dict[str, Any]] | None) -> str:
    return "; ".join(filter(None, (_concept(c) for c in concepts or [])))


def _day(value: Any) -> str:
    return value[:10] if isinstance(value, str) else ""


def _period(period: dict[str, Any] | None) -> str:
    if not period:
        return ""
    start, end = _day(period.get("start")), _day(period.get("end"))
    if start and end and start != end:
        return f"{start} to {end}"
    return start or end


def _quantity(quantity: dict[str, Any]) -> str:
    unit = quantity.get("unit") or quantity.get("code") or ""
    return f"{quantity.get('value', '')} {unit}".strip()


def _value(holder: dict[str, Any]) -> str:
    if "valueQuantity" in holder:
        return _quantity(holder["valueQuantity"])
    if "valueCodeableConcept" in holder:
        return _concept(holder["valueCodeableConcept"])
    for key in ("valueString", "valueBoolean", "valueInteger", "valueDateTime"):
        if key in holder:
            return str(holder[key])
    return ""


def _codes(resource: Resource) -> list[str]:
    concepts = [resource.get(k) for k in ("code", "medicationCodeableConcept", "vaccineCode")]
    concepts += [c.get("code") for c in resource.get("component") or []]
    tokens = [
        f"{coding['system']}|{coding['code']}"
        for concept in concepts
        if isinstance(concept, dict)
        for coding in concept.get("coding") or []
        if coding.get("system") and coding.get("code")
    ]
    return list(dict.fromkeys(tokens))


def _numeric_value(resource: Resource) -> tuple[float | None, str | None]:
    quantity = resource.get("valueQuantity") or {}
    value = quantity.get("value")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None, None
    return float(value), quantity.get("unit") or quantity.get("code")


def _reasons(resource: Resource, index: _Index) -> str:
    reasons = [_concept(c) for c in resource.get("reasonCode") or []]
    reasons += [index.label(r) for r in resource.get("reasonReference") or []]
    return "; ".join(filter(None, reasons))


def _text(head: str, *fields: tuple[str, Any], resource: Resource | None = None) -> str:
    lines = [head] + [f"{label}: {value}" for label, value in fields if value not in (None, "")]
    for note in (resource or {}).get("note") or []:
        if note.get("text"):
            lines.append(f"Note: {note['text']}")
    return "\n".join(lines)


def _patient(r: Resource, index: _Index, as_of: date) -> str:
    # Age and sex only: names, addresses and identifiers never reach a prompt.
    born = _parse_date(r.get("birthDate"))
    age = None
    if born is not None:
        age = as_of.year - born.year - ((as_of.month, as_of.day) < (born.month, born.day))
    return _text("Patient", ("Age", age), ("Sex", r.get("gender")))


def _condition(r: Resource, index: _Index, as_of: date) -> str:
    return _text(
        f"Condition: {_concept(r.get('code'))}",
        ("Clinical status", _code(r.get("clinicalStatus"))),
        ("Verification", _code(r.get("verificationStatus"))),
        ("Onset", _day(r.get("onsetDateTime")) or _period(r.get("onsetPeriod"))),
        ("Resolved", _day(r.get("abatementDateTime"))),
        ("Recorded", _day(r.get("recordedDate"))),
        resource=r,
    )


def _observation(r: Resource, index: _Index, as_of: date) -> str:
    category = ", ".join(filter(None, (_code(c) for c in r.get("category") or [])))
    head = f"Observation ({category})" if category else "Observation"
    components = [
        f"{_concept(c.get('code'))}: {_value(c)}" for c in r.get("component") or [] if _value(c)
    ]
    return _text(
        f"{head}: {_concept(r.get('code'))}",
        ("Value", _value(r)),
        ("Components", "; ".join(components)),
        ("Interpretation", _concepts(r.get("interpretation"))),
        ("Date", _day(r.get("effectiveDateTime")) or _period(r.get("effectivePeriod"))),
        ("Status", r.get("status")),
        resource=r,
    )


def _procedure(r: Resource, index: _Index, as_of: date) -> str:
    return _text(
        f"Procedure: {_concept(r.get('code'))}",
        ("Status", r.get("status")),
        ("Performed", _day(r.get("performedDateTime")) or _period(r.get("performedPeriod"))),
        ("Body site", _concepts(r.get("bodySite"))),
        ("Reason", _reasons(r, index)),
        ("Outcome", _concept(r.get("outcome"))),
        resource=r,
    )


def _medication(r: Resource, index: _Index, as_of: date) -> str:
    kind = {
        "MedicationRequest": "Medication request",
        "MedicationStatement": "Medication statement",
        "MedicationAdministration": "Medication administration",
    }[r["resourceType"]]
    name = _concept(r.get("medicationCodeableConcept")) or index.label(r.get("medicationReference"))
    dosage = r.get("dosageInstruction") or r.get("dosage") or []
    if isinstance(dosage, dict):  # MedicationAdministration.dosage is a single object
        dosage = [dosage]
    return _text(
        f"{kind}: {name}",
        ("Status", r.get("status")),
        ("Authored", _day(r.get("authoredOn"))),
        ("Effective", _day(r.get("effectiveDateTime")) or _period(r.get("effectivePeriod"))),
        ("Dosage", "; ".join(d["text"] for d in dosage if d.get("text"))),
        ("Reason", _reasons(r, index)),
        resource=r,
    )


def _encounter(r: Resource, index: _Index, as_of: date) -> str:
    encounter_class = (r.get("class") or {}).get("code")
    head = f"Encounter ({encounter_class})" if encounter_class else "Encounter"
    return _text(
        f"{head}: {_concepts(r.get('type'))}",
        ("Period", _period(r.get("period"))),
        ("Reason", _reasons(r, index)),
        ("Status", r.get("status")),
    )


def _diagnostic_report(r: Resource, index: _Index, as_of: date) -> str:
    results = [index.label(ref) for ref in r.get("result") or []]
    return _text(
        f"Diagnostic report: {_concept(r.get('code'))}",
        ("Status", r.get("status")),
        ("Date", _day(r.get("effectiveDateTime")) or _period(r.get("effectivePeriod"))),
        ("Results", "; ".join(filter(None, results))),
        ("Conclusion", r.get("conclusion")),
    )


def _document_reference(r: Resource, index: _Index, as_of: date) -> str:
    return _text(
        f"Document: {_concept(r.get('type'))}",
        ("Category", _concepts(r.get("category"))),
        ("Date", _day(r.get("date"))),
        ("Description", r.get("description")),
        ("Status", r.get("status")),
    )


def _immunization(r: Resource, index: _Index, as_of: date) -> str:
    return _text(
        f"Immunization: {_concept(r.get('vaccineCode'))}",
        ("Status", r.get("status")),
        ("Date", _day(r.get("occurrenceDateTime"))),
        resource=r,
    )


def _allergy(r: Resource, index: _Index, as_of: date) -> str:
    reactions = [_concepts(x.get("manifestation")) for x in r.get("reaction") or []]
    return _text(
        f"Allergy or intolerance: {_concept(r.get('code'))}",
        ("Clinical status", _code(r.get("clinicalStatus"))),
        ("Criticality", r.get("criticality")),
        ("Reactions", "; ".join(filter(None, reactions))),
        ("Recorded", _day(r.get("recordedDate"))),
        resource=r,
    )


def _care_plan(r: Resource, index: _Index, as_of: date) -> str:
    activities = [_concept(_get(a, "detail.code")) for a in r.get("activity") or []]
    addresses = [index.label(ref) for ref in r.get("addresses") or []]
    return _text(
        f"Care plan: {r.get('title') or _concepts(r.get('category'))}",
        ("Status", r.get("status")),
        ("Period", _period(r.get("period"))),
        ("Activities", "; ".join(filter(None, activities))),
        ("Addresses", "; ".join(filter(None, addresses))),
        resource=r,
    )


def _device(r: Resource, index: _Index, as_of: date) -> str:
    kind = r.get("type") or {}
    shown = {kind.get("text")} | {c.get("display") for c in kind.get("coding") or []}
    names = [n["name"] for n in r.get("deviceName") or [] if n.get("name") not in shown | {None}]
    return _text(
        f"Device: {_concept(kind)}",
        ("Name", "; ".join(names)),
        ("Status", r.get("status")),
        resource=r,
    )


def _device_use(r: Resource, index: _Index, as_of: date) -> str:
    timing = _day(r.get("timingDateTime")) or _period(r.get("timingPeriod"))
    return _text(
        f"Device use: {index.label(r.get('device'))}",
        ("Status", r.get("status")),
        ("Timing", timing or _concept(_get(r, "timingTiming.code"))),
        ("Recorded", _day(r.get("recordedOn"))),
        ("Reason", _reasons(r, index)),
        ("Body site", _concept(r.get("bodySite"))),
        resource=r,
    )


def _communication(r: Resource, index: _Index, as_of: date) -> str:
    content = [p["contentString"] for p in r.get("payload") or [] if p.get("contentString")]
    return _text(
        f"Communication: {_concepts(r.get('category')) or _concept(r.get('topic'))}",
        ("Status", r.get("status")),
        ("Sent", _day(r.get("sent"))),
        ("Received", _day(r.get("received"))),
        ("Reason", _reasons(r, index)),
        ("Content", "\n".join(content)),
        resource=r,
    )


def _imaging_study(r: Resource, index: _Index, as_of: date) -> str:
    series = [
        " ".join(
            filter(
                None,
                (_get(s, "modality.display"), _get(s, "bodySite.display"), s.get("description")),
            )
        )
        for s in r.get("series") or []
    ]
    return _text(
        f"Imaging study: {_concepts(r.get('procedureCode')) or r.get('description') or ''}",
        ("Status", r.get("status")),
        ("Started", _day(r.get("started"))),
        ("Series", "; ".join(filter(None, series))),
        ("Reason", _reasons(r, index)),
        resource=r,
    )


def _service_request(r: Resource, index: _Index, as_of: date) -> str:
    return _text(
        f"Service request: {_concept(r.get('code'))}",
        ("Status", r.get("status")),
        ("Intent", r.get("intent")),
        ("Authored", _day(r.get("authoredOn"))),
        ("Reason", _reasons(r, index)),
        resource=r,
    )


Renderer = Callable[[Resource, _Index, date], str]

# resource type -> (renderer, paths tried in order for the effective date).
# Anything not listed (Claim, ExplanationOfBenefit, Coverage, Organization, Practitioner,
# Location, Provenance, ...) is skipped.
_RENDERERS: dict[str, tuple[Renderer, tuple[str, ...]]] = {
    "Patient": (_patient, ()),
    "Condition": (_condition, ("onsetDateTime", "onsetPeriod.start", "recordedDate")),
    "Observation": (
        _observation,
        ("effectiveDateTime", "effectivePeriod.start", "effectiveInstant", "issued"),
    ),
    "Procedure": (_procedure, ("performedDateTime", "performedPeriod.start")),
    "MedicationRequest": (_medication, ("authoredOn",)),
    "MedicationStatement": (
        _medication,
        ("effectiveDateTime", "effectivePeriod.start", "dateAsserted"),
    ),
    "MedicationAdministration": (_medication, ("effectiveDateTime", "effectivePeriod.start")),
    "Encounter": (_encounter, ("period.start",)),
    "DiagnosticReport": (
        _diagnostic_report,
        ("effectiveDateTime", "effectivePeriod.start", "issued"),
    ),
    "DocumentReference": (_document_reference, ("date", "context.period.start")),
    "Immunization": (_immunization, ("occurrenceDateTime", "recorded")),
    "AllergyIntolerance": (_allergy, ("onsetDateTime", "recordedDate")),
    "CarePlan": (_care_plan, ("period.start", "created")),
    "Device": (_device, ()),
    "DeviceUseStatement": (_device_use, ("timingDateTime", "timingPeriod.start", "recordedOn")),
    "Communication": (_communication, ("sent", "received")),
    "ImagingStudy": (_imaging_study, ("started",)),
    "ServiceRequest": (
        _service_request,
        ("authoredOn", "occurrenceDateTime", "occurrencePeriod.start"),
    ),
}

SUPPORTED_TYPES = frozenset(_RENDERERS)


def _decode(attachment: dict[str, Any] | None) -> str | None:
    """Decoded text of a base64 text attachment, or None if absent, binary or malformed."""
    if not attachment or not attachment.get("data"):
        return None
    if not attachment.get("contentType", "text/plain").startswith("text/"):
        return None
    try:
        text = base64.b64decode(attachment["data"], validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return text if text.strip() else None


def _attachments(resource: Resource) -> Iterator[str]:
    kind = resource.get("resourceType")
    if kind == "DocumentReference":
        found = [c.get("attachment") for c in resource.get("content") or []]
    elif kind == "DiagnosticReport":
        found = resource.get("presentedForm") or []
    elif kind == "Communication":
        found = [p.get("contentAttachment") for p in resource.get("payload") or []]
    else:
        found = []
    for attachment in found:
        if (text := _decode(attachment)) is not None:
            yield text


def split_spans(text: str, max_chars: int = MAX_NOTE_CHARS) -> list[tuple[int, int]]:
    """(start, end) spans covering text, cut at paragraph, line, sentence or word breaks.

    Spans are trimmed of surrounding whitespace, so text[start:end] is always a verbatim,
    non-empty slice.
    """
    spans: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            window = text[start:end]
            for separator in ("\n\n", "\n", ". ", " "):
                cut = window.rfind(separator)
                if cut > 0:
                    end = start + cut + len(separator)
                    break
        piece = text[start:end]
        if stripped := piece.strip():
            lead = len(piece) - len(piece.lstrip())
            spans.append((start + lead, start + lead + len(stripped)))
        start = end
    return spans


def chunk_bundle(bundle: dict[str, Any], *, as_of: date | None = None) -> list[Chunk]:
    """Chunks for every clinical resource in a bundle. as_of fixes the patient's age."""
    as_of = as_of or date.today()
    entries = [
        (entry.get("fullUrl"), entry["resource"])
        for entry in bundle.get("entry") or []
        if isinstance(entry.get("resource"), dict)
    ]
    index = _Index(entries)
    resources = [r for _, r in entries if r.get("resourceType") in _RENDERERS and r.get("id")]

    # Synthea repeats each encounter note as a DiagnosticReport presentedForm; keep the
    # DocumentReference copy only.
    notes = {
        text
        for r in resources
        if r["resourceType"] == "DocumentReference"
        for text in _attachments(r)
    }

    chunks: list[Chunk] = []
    for resource in resources:
        kind = resource["resourceType"]
        renderer, date_paths = _RENDERERS[kind]
        decoded = list(_attachments(resource))
        attachments = [t for t in decoded if kind == "DocumentReference" or t not in notes]

        # (text, char_start, char_end) for each chunk of this resource, in order.
        pieces: list[tuple[str, int | None, int | None]] = []
        if kind == "DocumentReference":
            # A decoded note stands for its DocumentReference.
            if not attachments:
                pieces.append((renderer(resource, index, as_of), None, None))
        elif kind == "DiagnosticReport" and decoded and not attachments:
            # Only mirrors a note; keep it if it adds results or a conclusion.
            if resource.get("result") or resource.get("conclusion"):
                pieces.append((renderer(resource, index, as_of), None, None))
        else:
            pieces.append((renderer(resource, index, as_of), None, None))
        for text in attachments:
            pieces += [(text[start:end], start, end) for start, end in split_spans(text)]

        effective = next(filter(None, (_parse_date(_get(resource, p)) for p in date_paths)), None)
        value, unit = _numeric_value(resource)
        for i, (text, start, end) in enumerate(pieces):
            chunk = Chunk(
                resource_type=kind,
                resource_id=resource["id"],
                chunk_index=i,
                effective_date=effective,
                text=text,
                char_start=start,
                char_end=end,
            )
            if start is None:  # rendered from structured fields, not a note slice
                chunk.codes, chunk.value, chunk.unit = _codes(resource), value, unit
            chunks.append(chunk)
    return chunks


def load_bundle(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)
