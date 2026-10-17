"""Build a draft Da Vinci PAS request Bundle, and its review envelope, from a judged case.

The draft is for human review only. It is never submitted, and nothing here talks to a
payer or to any network: this module builds dictionaries from the case, the recorded
judgment and the chart, and nothing else.

What goes in the Bundle follows the PAS 2.2.1 profiles (see evals/pas/README.md). What
does not go in it: the verdicts. PAS has no element for a provider-side determination, so
verdicts, readiness, placeholders and reviewer tasks go in the envelope, a separate
document that is not FHIR.

Every participant the synthetic data lacks (payer, coverage, requestor) is a labelled
placeholder under a namespace this project owns, so no value can be mistaken for, or
collide with, a real identifier. No NPI, TIN or NAIC is ever produced.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from precertly.evals.cases import EvalCase
from precertly.judgment.models import CaseJudgment, CriterionVerdict
from precertly.policies import Policy

Resource = dict[str, Any]

PAS = "http://hl7.org/fhir/us/davinci-pas"
PAS_PACKAGE = "hl7.fhir.us.davinci-pas"
PAS_VERSION = "2.2.1"
PROFILE = {
    "bundle": f"{PAS}/StructureDefinition/profile-pas-request-bundle",
    "claim": f"{PAS}/StructureDefinition/profile-claim",
    "patient": f"{PAS}/StructureDefinition/profile-beneficiary",
    "coverage": f"{PAS}/StructureDefinition/profile-coverage",
    "insurer": f"{PAS}/StructureDefinition/profile-insurer",
    "requestor": f"{PAS}/StructureDefinition/profile-requestor",
    "document": f"{PAS}/StructureDefinition/profile-documentreference",
}

# A namespace this project owns. Every placeholder identifier and fullUrl lives under it.
PROJECT = "https://github.com/tejavardhanreddy01/precertly"
SYNTHETIC = f"{PROJECT}/synthetic"
FULL_URL_BASE = f"{SYNTHETIC}/fhir"
REVIEW_TAG = {
    "system": f"{PROJECT}/CodeSystem/review-state",
    "code": "draft-pending-human-approval",
    "display": "Draft: requires human approval; not submitted",
}

CPT = "http://www.ama-assn.org/go/cpt"
PLACE_OF_SERVICE = (
    "https://www.cms.gov/Medicare/Coding/place-of-service-codes/Place_of_Service_Code_Set"
)

# X12 terminology is copyright of X12 and its code lists are for members (PAS 2.2.1,
# "X12 Terminology"). Only codes that appear in the IG's own published examples are used,
# as codes only, with no display, and each is listed for the reviewer to confirm.
X12 = {
    "service_type": ("https://codesystem.x12.org/005010/1365", "1"),
    "request_type": ("https://codesystem.x12.org/005010/1525", "IN"),
    "certification_type": ("https://codesystem.x12.org/005010/1322", "I"),
    "report_type": ("https://codesystem.x12.org/005010/755", "PY"),
    "transmission_method": ("https://codesystem.x12.org/005010/756", "EL"),
    "relationship": ("https://codesystem.x12.org/005010/1069", "18"),
}
X12_SOURCE = {
    "service_type": "Claim-MedicalServicesAuthorizationExample, item.category",
    "request_type": "Claim-MedicalServicesAuthorizationExample, extension-serviceItemRequestType",
    "certification_type": "Claim-MedicalServicesAuthorizationExample, extension-certificationType",
    "report_type": "Claim-ReferralAuthorizationExample, extension-documentInformation",
    "transmission_method": "Claim-ReferralAuthorizationExample, extension-documentInformation",
    # The coverage profile's self-beneficiary invariant looks for this code, not "self".
    "relationship": "Coverage-InsuranceExample, relationship",
}

LOINC = "http://loinc.org"
# Reviewed: note title -> LOINC document type, each checked to be in the US Core
# DocumentReference Type value set. A title not listed here stops the build.
NOTE_TYPES = {
    "Pain clinic initial evaluation": ("11488-4", "Consult note"),
    "Pain clinic follow-up": ("11506-3", "Progress note"),
    "Pain clinic progress note": ("11506-3", "Progress note"),
    "Primary care visit": ("11506-3", "Progress note"),
    "Physical therapy discharge summary": ("18842-5", "Discharge summary"),
}
PROCEDURE_NOTE = ("28570-0", "Procedure note")  # titles starting "Procedure note"
IMAGING_REPORT = ("18748-4", "Diagnostic imaging study")  # a report's presentedForm

DIAGNOSIS_MAP = Path(__file__).with_name("diagnosis_map.yaml")


@dataclass(frozen=True)
class Source:
    """The recorded results file the verdicts come from."""

    path: str
    sha256: str


@dataclass
class Draft:
    bundle: Resource
    envelope: dict[str, Any]


@lru_cache
def diagnosis_map() -> dict[str, Any]:
    with DIAGNOSIS_MAP.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _coding(system: str, code: str, display: str | None = None) -> dict[str, str]:
    coding = {"system": system, "code": code}
    if display:
        coding["display"] = display
    return coding


def _x12(name: str) -> dict[str, Any]:
    system, code = X12[name]
    return {"coding": [_coding(system, code)]}


def _ref(resource: Resource) -> str:
    return f"{resource['resourceType']}/{resource['id']}"


def _entry(resource: Resource) -> dict[str, Any]:
    return {"fullUrl": f"{FULL_URL_BASE}/{_ref(resource)}", "resource": resource}


def _meta(profile: str, tagged: bool = False) -> dict[str, Any]:
    meta: dict[str, Any] = {"profile": [f"{profile}|{PAS_VERSION}"]}
    if tagged:
        meta["tag"] = [dict(REVIEW_TAG)]
    return meta


def _final(verdict: CriterionVerdict) -> str:
    return verdict.outcome if verdict.applicable else "not_applicable"


# --- participants -------------------------------------------------------------------


def beneficiary(chart_patient: Resource) -> Resource:
    """The synthetic patient: demographics from the chart, a project-namespace identifier,
    and no name (the fixtures carry none), stated with a data-absent-reason."""
    patient: Resource = {
        "resourceType": "Patient",
        "id": chart_patient["id"],
        "meta": _meta(PROFILE["patient"]),
        "identifier": [{"system": f"{SYNTHETIC}/patient-id", "value": chart_patient["id"]}],
        "name": [
            {
                "extension": [
                    {
                        "url": "http://hl7.org/fhir/StructureDefinition/data-absent-reason",
                        "valueCode": "unknown",
                    }
                ]
            }
        ],
        "gender": chart_patient["gender"],
    }
    if chart_patient.get("birthDate"):
        patient["birthDate"] = chart_patient["birthDate"]
    return patient


def insurer() -> Resource:
    return {
        "resourceType": "Organization",
        "id": "insurer-placeholder",
        "meta": _meta(PROFILE["insurer"]),
        "active": True,
        "name": "Synthetic Medicare contractor (placeholder)",
    }


def requestor() -> Resource:
    return {
        "resourceType": "Organization",
        "id": "requestor-placeholder",
        "meta": _meta(PROFILE["requestor"]),
        "active": True,
        "name": "Synthetic pain clinic (placeholder)",
    }


def coverage(case: EvalCase, patient: Resource, payer: Resource) -> Resource:
    return {
        "resourceType": "Coverage",
        "id": "coverage-placeholder",
        "meta": _meta(PROFILE["coverage"]),
        "identifier": [
            {
                "type": {
                    "coding": [_coding("http://terminology.hl7.org/CodeSystem/v2-0203", "MB")]
                },
                "system": f"{SYNTHETIC}/member-id",
                "value": f"SYNTHETIC-{case.id}",
            }
        ],
        "status": "active",
        "beneficiary": {"reference": _ref(patient)},
        "relationship": {
            "coding": [
                _coding("http://terminology.hl7.org/CodeSystem/subscriber-relationship", "self"),
                _x12("relationship")["coding"][0],
            ]
        },
        "payor": [{"reference": _ref(payer)}],
    }


# --- attachments --------------------------------------------------------------------


@dataclass
class Attachment:
    source: str  # the chart resource the attached document comes from
    document: Resource
    title: str
    cited_by: list[dict[str, str]]
    dropped: list[str]  # links removed from the copy, recorded for the reviewer
    note: str | None = None


def _note_type(title: str) -> tuple[str, str]:
    if title.startswith("Procedure note"):
        return PROCEDURE_NOTE
    if title not in NOTE_TYPES:
        raise ValueError(f"no reviewed LOINC document type for note title {title!r}")
    return NOTE_TYPES[title]


def _document(source: Resource) -> tuple[Resource, str, list[str], str | None]:
    """A profile-documentreference copy of a cited free-text resource: content verbatim,
    with only the classification US Core requires added. Returns the copy, its title, the
    links dropped from it, and a note on how it was made."""
    category = {
        "coding": [
            _coding(
                "http://hl7.org/fhir/us/core/CodeSystem/us-core-documentreference-category",
                "clinical-note",
            )
        ]
    }
    dropped: list[str] = []
    if source["resourceType"] == "DocumentReference":
        document = copy.deepcopy(source)
        title = source["type"]["text"]
        code, display = _note_type(title)
        context = document.pop("context", None) or {}
        dropped += [ref["reference"] for ref in context.get("encounter", [])]
        how = None
    elif source["resourceType"] == "DiagnosticReport" and source.get("presentedForm"):
        title = source["code"]["text"]
        code, display = IMAGING_REPORT
        document = {
            "resourceType": "DocumentReference",
            "id": f"{source['id']}-report",
            "status": "current",
            "subject": source["subject"],
            "content": [{"attachment": a} for a in copy.deepcopy(source["presentedForm"])],
        }
        if source.get("effectiveDateTime"):
            document["date"] = _instant(source["effectiveDateTime"])
        if source.get("encounter"):
            dropped.append(source["encounter"]["reference"])
        how = f"the presentedForm of {_ref(source)}, copied verbatim into a DocumentReference"
    else:
        raise ValueError(f"{_ref(source)} has no free text to attach")
    document["meta"] = _meta(PROFILE["document"])
    document["type"] = {"coding": [_coding(LOINC, code, display)], "text": title}
    document["category"] = [category]
    return document, title, dropped, how


def _instant(value: str) -> str:
    """A date as an instant (DocumentReference.date), at midnight UTC if no time is given."""
    return value if "T" in value else f"{value}T00:00:00Z"


def attachments(judgment: CaseJudgment, chart: dict[str, Resource]) -> list[Attachment]:
    """Every free-text resource cited by any verdict, whatever its outcome: the reviewer
    decides what to send, and nothing unfavourable is filtered out."""
    cited: dict[str, list[dict[str, str]]] = {}
    for verdict in judgment.verdicts:
        for citation in verdict.citations:
            resource = citation.resource
            if not resource.startswith(("DocumentReference/", "DiagnosticReport/")):
                continue
            entry = {"criterion": verdict.criterion, "verdict": _final(verdict)}
            if entry not in cited.setdefault(resource, []):
                cited[resource].append(entry)
    found = []
    for resource in sorted(cited):
        document, title, dropped, how = _document(chart[resource])
        found.append(Attachment(resource, document, title, cited[resource], dropped, how))
    return found


def cited_not_attached(judgment: CaseJudgment) -> list[dict[str, Any]]:
    """Coded resources the verdicts cite or a rule read. The synthetic fixtures do not
    conform to their US Core profiles, so they are listed, not attached or altered."""
    by_resource: dict[str, list[dict[str, str]]] = {}
    for verdict in judgment.verdicts:
        refs = [c.resource for c in verdict.citations] + [
            ref.split("#")[0] for computed in verdict.computed for ref in computed.inputs
        ]
        for ref in refs:
            if ref.startswith(("DocumentReference/", "DiagnosticReport/", "request.")):
                continue
            entry = {"criterion": verdict.criterion, "verdict": _final(verdict)}
            if entry not in by_resource.setdefault(ref, []):
                by_resource[ref].append(entry)
    return [{"resource": ref, "cited_by": by_resource[ref]} for ref in sorted(by_resource)]


# --- diagnoses ----------------------------------------------------------------------


def candidate_conditions(chart: dict[str, Resource], policy: Policy) -> list[Resource]:
    """Conditions that are the reason for a chart Procedure with one of the policy's codes."""
    codes = {code for variant in policy.variants for code in variant.codes}
    found: dict[str, Resource] = {}
    for resource in chart.values():
        if resource["resourceType"] != "Procedure":
            continue
        if not any(c.get("code") in codes for c in resource.get("code", {}).get("coding", [])):
            continue
        for reason in resource.get("reasonReference", []):
            condition = chart.get(reason["reference"])
            if condition is not None:
                found[reason["reference"]] = condition
    return [found[ref] for ref in sorted(found)]


def diagnoses(
    candidates: list[Resource],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Claim.diagnosis entries, the envelope's diagnosis records, and reviewer tasks."""
    table = diagnosis_map()
    article = table["source"]
    mapped = {entry["snomed"]: entry for entry in table["mappings"]}
    claim_entries: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    tasks: list[str] = []
    for condition in candidates:
        snomed = next(
            (c for c in condition["code"]["coding"] if c["system"] == "http://snomed.info/sct"),
            None,
        )
        entry = mapped.get(snomed["code"]) if snomed else None
        label = condition["code"].get("text") or (snomed or {}).get("display", "")
        if entry is None or entry.get("icd10cm") is None:
            records.append(
                {
                    "condition": _ref(condition),
                    "snomed": snomed["code"] if snomed else None,
                    "icd10cm": None,
                    "status": "not mapped; left out of the draft",
                    "reason": entry["reason"] if entry else "no reviewed mapping",
                }
            )
            tasks.append(
                f"The chart's coded Condition, {label.lower()} (SNOMED "
                f"{snomed['code'] if snomed else 'none'}), has no equivalent on "
                f"{article['document'].removeprefix('Article ')} v{article['version']}'s "
                "covered list. Assign an ICD-10-CM code from that list if the documentation "
                "supports one."
            )
            continue
        claim_entries.append(
            {
                "sequence": len(claim_entries) + 1,
                "diagnosisCodeableConcept": {
                    "coding": [_coding("http://hl7.org/fhir/sid/icd-10-cm", entry["icd10cm"])]
                },
            }
        )
        records.append(
            {
                "condition": _ref(condition),
                "snomed": snomed["code"] if snomed else None,
                "icd10cm": entry["icd10cm"],
                "status": "mapped; reviewer must confirm",
            }
        )
    return claim_entries, records, tasks


# --- the claim and the bundle -------------------------------------------------------


def claim(
    case: EvalCase,
    patient: Resource,
    payer: Resource,
    provider: Resource,
    plan: Resource,
    attached: list[Attachment],
    diagnosis: list[dict[str, Any]],
) -> Resource:
    request = case.request
    if request.place_of_service is None:
        raise ValueError(f"{case.id}: the request has no place_of_service")
    information = [
        {
            "extension": [
                {
                    "url": f"{PAS}/StructureDefinition/extension-documentInformation",
                    "extension": [
                        {"url": "reportTypeCode", "valueCodeableConcept": _x12("report_type")},
                        {
                            "url": "transmissionMethod",
                            "valueCodeableConcept": _x12("transmission_method"),
                        },
                        {"url": "description", "valueString": attachment.title},
                    ],
                }
            ],
            "sequence": number,
            "category": {
                "coding": [_coding(f"{PAS}/CodeSystem/PASTempCodes", "additionalInformation")]
            },
            "valueReference": {"reference": _ref(attachment.document)},
        }
        for number, attachment in enumerate(attached, start=1)
    ]
    items = []
    for number, code in enumerate(request.codes, start=1):
        item: dict[str, Any] = {
            "extension": [
                {
                    "url": f"{PAS}/StructureDefinition/extension-serviceItemRequestType",
                    "valueCodeableConcept": _x12("request_type"),
                },
                {
                    "url": f"{PAS}/StructureDefinition/extension-certificationType",
                    "valueCodeableConcept": _x12("certification_type"),
                },
            ],
            "sequence": number,
        }
        if information:
            item["informationSequence"] = [entry["sequence"] for entry in information]
        item |= {
            "category": _x12("service_type"),
            "productOrService": {"coding": [_coding(CPT, code)]},
            "servicedDate": request.service_date.isoformat(),
            "locationCodeableConcept": {
                "coding": [_coding(PLACE_OF_SERVICE, request.place_of_service)]
            },
        }
        items.append(item)
    resource: Resource = {
        "resourceType": "Claim",
        "id": case.id,
        "meta": _meta(PROFILE["claim"], tagged=True),
        "identifier": [{"system": f"{SYNTHETIC}/claim-id", "value": case.id}],
        "status": "active",
        "type": {
            "coding": [_coding("http://terminology.hl7.org/CodeSystem/claim-type", "professional")]
        },
        "use": "preauthorization",
        "patient": {"reference": _ref(patient)},
        "created": request.date.isoformat(),
        "insurer": {"reference": _ref(payer)},
        "provider": {"reference": _ref(provider)},
        "priority": {
            "coding": [_coding("http://terminology.hl7.org/CodeSystem/processpriority", "normal")]
        },
    }
    if information:
        resource["supportingInfo"] = information
    if diagnosis:
        resource["diagnosis"] = diagnosis
    resource["insurance"] = [{"sequence": 1, "focal": True, "coverage": {"reference": _ref(plan)}}]
    resource["item"] = items
    return resource


def build(
    case: EvalCase, policy: Policy, judgment: CaseJudgment, chart: list[Resource], source: Source
) -> Draft:
    """The draft request Bundle and its review envelope. Deterministic: the same inputs
    give byte-identical output."""
    by_ref = {_ref(resource): resource for resource in chart}
    chart_patient = next(r for r in chart if r["resourceType"] == "Patient")
    patient, payer, provider = beneficiary(chart_patient), insurer(), requestor()
    plan = coverage(case, patient, payer)
    attached = attachments(judgment, by_ref)
    claim_diagnoses, diagnosis_records, diagnosis_tasks = diagnoses(
        candidate_conditions(by_ref, policy)
    )
    request_claim = claim(case, patient, payer, provider, plan, attached, claim_diagnoses)
    bundle: Resource = {
        "resourceType": "Bundle",
        "id": f"{case.id}-pas-draft",
        "meta": _meta(PROFILE["bundle"], tagged=True),
        "identifier": {
            "system": f"{SYNTHETIC}/pas-draft",
            "value": f"{case.id}-{source.sha256[:8]}",
        },
        "type": "collection",
        "timestamp": f"{case.request.date.isoformat()}T00:00:00Z",
        "entry": [
            _entry(resource)
            for resource in [request_claim, patient, plan, payer, provider]
            + [a.document for a in attached]
        ],
    }
    envelope = _envelope(
        case, judgment, source, attached, diagnosis_records, diagnosis_tasks, by_ref
    )
    return Draft(bundle=bundle, envelope=envelope)


def _envelope(
    case: EvalCase,
    judgment: CaseJudgment,
    source: Source,
    attached: list[Attachment],
    diagnosis_records: list[dict[str, Any]],
    diagnosis_tasks: list[str],
    chart: dict[str, Resource],
) -> dict[str, Any]:
    finals = {v.criterion: _final(v) for v in judgment.verdicts}
    not_met = sorted(c for c, f in finals.items() if f == "not_met")
    insufficient = sorted(c for c, f in finals.items() if f == "insufficient")
    article = diagnosis_map()["source"]
    reasons = []
    if not any(r["icd10cm"] for r in diagnosis_records):
        reasons.append(
            f"no covered diagnosis code ({article['document'].removeprefix('Article ')} "
            f"v{article['version']})"
        )
    if not_met:
        reasons.append(f"criteria not met: {', '.join(not_met)}")
    if insufficient:
        reasons.append(f"criteria with insufficient evidence: {', '.join(insufficient)}")
    reasons.append("payer, coverage and requestor are synthetic placeholders")
    mri = "DiagnosticReport/mri-lumbar"
    mri_where = (
        f"attached as DocumentReference/{mri.split('/')[1]}-report"
        if any(a.source == mri for a in attached)
        else f"{mri} in the chart, not cited by any verdict and so not attached"
        if mri in chart
        else "none in the chart"
    )
    tasks = [
        f"{task} See the attached notes and the MRI report ({mri_where})."
        for task in diagnosis_tasks
    ]
    tasks += [
        "Replace the placeholder payer, coverage and requestor with the real participants.",
        "Confirm each X12 code against the X12 278 guide (codes taken from the IG examples).",
        "Confirm the LOINC document type given to each attached note.",
        "Decide which attachments to send; every cited note is attached, whatever its verdict.",
    ]
    return {
        "kind": "precertly PAS draft review envelope",
        "case": case.id,
        "policy": judgment.policy,
        "variant": judgment.variant,
        "draft": {
            "file": f"{case.id}.draft.json",
            "status": "draft: requires human approval; never submitted",
            "approval": "pending",
            "ig": {"package": PAS_PACKAGE, "version": PAS_VERSION},
        },
        "source": {"results": source.path, "sha256": source.sha256},
        "readiness": {
            "ready": False,
            "summary": "not ready: " + "; ".join(reasons),
            "reasons": reasons,
        },
        "overall": judgment.overall.value,
        "verdicts": [
            {
                "criterion": v.criterion,
                "verdict": _final(v),
                "decided_by": v.decided_by,
                "invalidated": v.invalidated,
                "citations": [c.chunk_id for c in v.citations],
                "computed_inputs": [ref for c in v.computed for ref in c.inputs],
            }
            for v in judgment.verdicts
        ],
        "reviewer_checklist": [item.model_dump() for item in judgment.reviewer_checklist],
        "attachments": [
            {
                "document": _ref(a.document),
                "source": a.source,
                "title": a.title,
                "cited_by": a.cited_by,
                "links_dropped": a.dropped,
                **({"made_from": a.note} if a.note else {}),
            }
            for a in attached
        ],
        "cited_not_attached": cited_not_attached(judgment),
        "diagnoses": diagnosis_records,
        "placeholders": [
            "Organization/insurer-placeholder",
            "Organization/requestor-placeholder",
            "Coverage/coverage-placeholder",
            "Patient identifier (project namespace) and name (data-absent-reason: unknown)",
        ],
        "x12_codes": [
            {
                "element": name,
                "system": system,
                "code": code,
                "from": X12_SOURCE[name],
            }
            for name, (system, code) in X12.items()
        ],
        "reviewer_tasks": tasks,
    }
