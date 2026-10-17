"""Build the eval case bundles from a template patient. Nothing here is hand-edited JSON.

    uv run python evals/build_cases.py                 # write bundles and evidence lists
    uv run python evals/build_cases.py --check         # fail if either is stale
    uv run python evals/build_cases.py trim <fhir-dir> # rebuild evals/template/patient.json

Two steps:

1. trim (rare): take one patient from a pinned Synthea v4.0.0 run and keep a few hundred
   resources as realistic, unrelated background. The result is committed, so building
   cases needs neither Java nor Synthea. To regenerate the input:

       SYNTHEA_REFERENCE_DATE=20261001 SYNTHEA_OUTPUT=<dir> data/synthea/generate.sh 100

2. build: add a case's history to that background from a set of facts, one set per case
   (facet joint interventions, then PAP devices, bariatric surgery, then spinal cord
   stimulators). A case differs
   from its variant's clean baseline only in the facts it overrides, so the notes,
   observations and procedures in a bundle always agree with each other, except where a
   case is about a disagreement. Bariatric and spinal cord stimulator bundles also change the
   background itself: see cases/bariatric/PLAN.md and cases/spinal-cord-stimulators/PLAN.md.

The request itself is not in the bundle. Each case file states it in its `request` block
(dates, procedure and codes; facet cases also region, levels, same-day blocks and
`technique`; stimulator cases also leads, contacts and place of service). `technique` is a structured field written by hand in the case file, one of
medial_branch_block, intraarticular_injection or radiofrequency_ablation; nothing parses
it from text.

Synthetic data only. Resources a label can cite get readable, stable ids.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

EVALS = Path(__file__).resolve().parent
TEMPLATE = EVALS / "template" / "patient.json"
BUNDLES = EVALS / "bundles"

Resource = dict[str, Any]

# --- Step 1: trim a Synthea patient -------------------------------------------------------

# Seed 20261009, 100 patients, reference date 2026-10-01. Aged 65 at the request date, with
# no neurological, spine, fracture, chronic pain or tumor history.
SYNTHEA_PATIENT_ID = "37b75dda"
BACKGROUND_SINCE = "2023-10-01"  # keep encounters from the last three years

KEPT_TYPES = {
    "Patient",
    "Encounter",
    "Condition",
    "Observation",
    "Procedure",
    "MedicationRequest",
    "Immunization",
    "DiagnosticReport",
    "DocumentReference",
    "CarePlan",
    "AllergyIntolerance",
}
# Synthea assigns providers differently on every run, and their resources are dropped
# anyway, so references to them are removed to keep the trimmed file reproducible.
PROVIDER_FIELDS = (
    "participant",
    "performer",
    "author",
    "requester",
    "serviceProvider",
    "custodian",
    "location",
    "recorder",
    "asserter",
    "careTeam",
    "managingOrganization",
    "generalPractitioner",
)
# Background that bears directly on a facet criterion is left out, so only the facet
# history decides it: routine pain scores (LOINC 72514-3) and analgesic prescriptions,
# which the LCD counts as conservative management.
PAIN_SCORE_LOINC = "72514-3"
ANALGESICS = re.compile(r"acetaminophen|ibuprofen|naproxen|codone|tramadol|morphine", re.I)
# The rest of the background must say nothing about the facts the cases turn on.
CONFLICTS = re.compile(
    r"neuropath|neuralg|seizure|epilep|stroke|dementia|parkinson|migraine|sclerosis|"
    r"back pain|neck pain|chronic pain|radicul|sciatica|fibromyalg|\bfusion|fracture|"
    r"claudication|stenosis|spondyl|ablation|physical therapy|medial branch|facet|oswestry|"
    # routine cancer screening is fine; a neoplasm diagnosis is not
    r"osteoporosis|arthritis|(?<!screening for malignant )neoplasm|carcinoma|tumor",
    re.IGNORECASE,
)


def _encounter_ref(resource: Resource) -> str | None:
    for holder in (resource.get("encounter"), (resource.get("context") or {}).get("encounter")):
        if isinstance(holder, dict):
            return holder.get("reference")
        if isinstance(holder, list) and holder:
            return holder[0].get("reference")
    return None


def _decoded_text(resource: Resource) -> str:
    """Resource as text for the conflict check, with base64 notes decoded."""
    parts = [json.dumps(resource)]
    attachments = [c.get("attachment", {}) for c in resource.get("content") or []]
    attachments += resource.get("presentedForm") or []
    for attachment in attachments:
        if attachment.get("data"):
            parts.append(base64.b64decode(attachment["data"]).decode("utf-8", "replace"))
    return "\n".join(parts)


def _bears_on_a_criterion(resource: Resource) -> bool:
    codings = (resource.get("code") or {}).get("coding") or []
    if any(c.get("code") == PAIN_SCORE_LOINC for c in codings):
        return True
    medication = (resource.get("medicationCodeableConcept") or {}).get("text", "")
    return resource["resourceType"] == "MedicationRequest" and bool(ANALGESICS.search(medication))


def trim(fhir_dir: Path) -> list[Resource]:
    (path,) = sorted(fhir_dir.glob(f"*{SYNTHEA_PATIENT_ID}*.json"))
    entries = json.loads(path.read_text(encoding="utf-8"))["entry"]
    recent = {
        entry["fullUrl"]
        for entry in entries
        if entry["resource"]["resourceType"] == "Encounter"
        and entry["resource"]["period"]["start"] >= BACKGROUND_SINCE
    }
    kept: list[Resource] = []
    for entry in entries:
        resource = entry["resource"]
        kind = resource["resourceType"]
        if kind not in KEPT_TYPES or _bears_on_a_criterion(resource):
            continue
        in_recent_encounter = (
            entry["fullUrl"] in recent
            if kind == "Encounter"
            else _encounter_ref(resource) in recent
        )
        # The patient and the whole problem list stay; everything else only if recent.
        if kind not in ("Patient", "Condition") and not in_recent_encounter:
            continue
        if kind == "Patient":
            # Demographics only: no synthetic name, address or identifiers.
            resource = {k: resource[k] for k in ("resourceType", "id", "gender", "birthDate")}
        resource = {k: v for k, v in resource.items() if k not in PROVIDER_FIELDS}
        resource.pop("meta", None)
        resource.pop("text", None)
        kept.append(resource)

    patient_id = next(r["id"] for r in kept if r["resourceType"] == "Patient")
    known = {f"urn:uuid:{r['id']}": f"{r['resourceType']}/{r['id']}" for r in kept}
    known[f"urn:uuid:{patient_id}"] = f"Patient/{patient_id}"
    text = json.dumps(kept)
    # urn:uuid references become Type/id; the ones whose target was dropped are harmless.
    text = re.sub(r"urn:uuid:[0-9a-f-]{36}", lambda m: known.get(m.group(0), m.group(0)), text)
    kept = json.loads(text)

    for resource in kept:
        found = CONFLICTS.search(_decoded_text(resource))
        if resource["resourceType"] != "Patient" and found:
            ref = f"{resource['resourceType']}/{resource['id']}"
            raise SystemExit(f"background conflicts with the facet cases: {ref} ({found.group(0)})")
    return kept


# --- Step 2: the facet history, generated from facts --------------------------------------

REQUEST_DATE = date(2026, 10, 5)
RECURRENCE_VISIT = date(2026, 6, 22)  # repeat-RFA cases: conservative care restarts here
RECURRENCE_ODI = 46  # disability score when the repeat ablation is requested
LEVELS = "L4-5 and L5-S1"
NRS = "Numerical Rating Scale (NRS) 0-10"
VAS = "Visual Analog Scale (VAS) 0-100 mm"
ODI = "Oswestry Disability Index (ODI)"


@dataclass(frozen=True)
class Facts:
    """What is true in a case's chart. Defaults are the clean initial-RFA history."""

    variant: str = "initial-rfa"
    pain_onset: date = date(2026, 4, 3)  # six months before the request
    pcp_visit: date = date(2026, 4, 10)
    pt_start: date = date(2026, 5, 4)
    pt_end: date = date(2026, 8, 3)  # twelve weeks of physical therapy
    mri: date = date(2026, 7, 20)
    evaluation: date = date(2026, 8, 10)
    baseline_nrs: int = 7
    baseline_odi: int = 48
    plan_levels: str = LEVELS

    # Diagnostic blocks. Relief is computed from the same session's pre-block score.
    mbb1: date | None = date(2026, 8, 24)
    mbb1_pre: int = 7
    mbb1_post: int = 1
    mbb2: date | None = date(2026, 9, 14)  # three weeks after the first
    mbb2_pre: int = 7
    mbb2_post: int = 1
    mbb2_post_vas: int | None = None  # set when the post-block score is on a different scale
    planning: date = date(2026, 9, 21)

    # Second diagnostic request: the block is planned, not yet done.
    planned_second_block: date | None = None
    interval_exception: bool = False

    # Prior radiofrequency ablation (repeat-rfa).
    rfa1: date | None = None
    rfa1_pre: int = 7
    rfa1_post: int = 3
    rfa1_relief_months: int = 8
    rfa1_follow_up_coded: bool = True  # False: the result exists only in a progress note

    # Facts a single case changes.
    leg_symptoms_unassessed: bool = False
    l2_fracture: bool = False
    pt_attendance_contradicted: bool = False


def relief(pre: int, post: int) -> int:
    return round((pre - post) / pre * 100)


def calculation(pre: int, post: int) -> str:
    return f"({pre} - {post}) / {pre} = {relief(pre, post)}%"


INITIAL_RFA = Facts()
SECOND_DIAGNOSTIC = replace(
    INITIAL_RFA,
    variant="second-diagnostic",
    mbb1=date(2026, 9, 28),
    mbb2=None,
    planning=date(2026, 9, 30),
    planned_second_block=date(2026, 10, 19),  # three weeks after the first
)
FIRST_DIAGNOSTIC = replace(
    INITIAL_RFA, variant="first-diagnostic", evaluation=date(2026, 9, 28), mbb1=None, mbb2=None
)
REPEAT_RFA = replace(
    INITIAL_RFA,
    variant="repeat-rfa",
    pain_onset=date(2024, 11, 4),
    pcp_visit=date(2024, 11, 11),
    pt_start=date(2024, 12, 2),
    pt_end=date(2025, 3, 3),
    mri=date(2025, 2, 17),
    evaluation=date(2025, 3, 17),
    mbb1=date(2025, 4, 7),
    mbb2=date(2025, 4, 28),
    rfa1=date(2025, 8, 4),  # fourteen months before the request
    planning=date(2026, 9, 21),
)

FACET_CASES: dict[str, Facts] = {
    "facet-01": INITIAL_RFA,
    "facet-02": replace(INITIAL_RFA, mbb2_post=2),
    "facet-03": replace(SECOND_DIAGNOSTIC, planned_second_block=date(2026, 10, 8)),
    "facet-04": replace(
        SECOND_DIAGNOSTIC, planned_second_block=date(2026, 10, 8), interval_exception=True
    ),
    "facet-05": replace(INITIAL_RFA, mbb2_post_vas=10),
    "facet-06": replace(FIRST_DIAGNOSTIC, leg_symptoms_unassessed=True),
    "facet-07": replace(FIRST_DIAGNOSTIC, l2_fracture=True),
    "facet-08": replace(FIRST_DIAGNOSTIC, plan_levels="L3-4, L4-5 and L5-S1"),
    "facet-09": REPEAT_RFA,
    "facet-10": replace(REPEAT_RFA, rfa1_relief_months=4, rfa1_follow_up_coded=False),
    "facet-11": replace(FIRST_DIAGNOSTIC, pt_attendance_contradicted=True),
}


def _attachment(text: str) -> dict[str, str]:
    return {
        "contentType": "text/plain; charset=utf-8",
        "data": base64.b64encode(text.encode("utf-8")).decode("ascii"),
    }


def _note(note_id: str, when: date, title: str, text: str, encounter: str) -> Resource:
    return {
        "resourceType": "DocumentReference",
        "id": note_id,
        "status": "current",
        "type": {"text": title},
        "date": f"{when.isoformat()}T10:00:00-04:00",
        "context": {"encounter": [{"reference": f"Encounter/{encounter}"}]},
        "content": [{"attachment": _attachment(f"{when.isoformat()}\n{title}\n\n{text}")}],
    }


def _encounter(
    encounter_id: str, when: date, kind: str, end: date | None = None, cls: str = "AMB"
) -> Resource:
    return {
        "resourceType": "Encounter",
        "id": encounter_id,
        "status": "finished",
        "class": {"code": cls},
        "type": [{"text": kind}],
        "period": {"start": when.isoformat(), "end": (end or when).isoformat()},
    }


def _at(when: date, clock: str | None) -> str:
    """A FHIR date, or a dateTime when the time of day matters."""
    return f"{when.isoformat()}T{clock}:00-04:00" if clock else when.isoformat()


# Times of day around a procedure, so structured data shows which score came before it.
BEFORE, DURING, AFTER = "09:00", "09:30", "10:00"
# LOINC 38214-3, "Pain severity [Score] Visual analog score" (https://loinc.org/38214-3).
VAS_LOINC = "38214-3"
# LOINC 97909-6, "Oswestry disability index score ODI" (https://loinc.org/97909-6/).
ODI_LOINC = "97909-6"
CPT = "http://www.ama-assn.org/go/cpt"
# Code numbers only, for routing; no descriptors. Lumbar, first level.
CPT_FACET_INJECTION = "64493"
CPT_FACET_RFA = "64635"


def _score(
    observation_id: str,
    when: date,
    value: int,
    encounter: str,
    scale: str = NRS,
    clock: str | None = None,
) -> Resource:
    loinc = PAIN_SCORE_LOINC if scale == NRS else VAS_LOINC
    # "text" first: the key order is part of the committed bundles
    code: dict[str, Any] = {
        "text": f"Pain severity, {scale}",
        "coding": [{"system": "http://loinc.org", "code": loinc}],
    }
    return {
        "resourceType": "Observation",
        "id": observation_id,
        "status": "final",
        "category": [{"coding": [{"code": "survey"}]}],
        "code": code,
        "valueQuantity": {"value": value, "unit": "{score}" if scale == NRS else "mm"},
        "effectiveDateTime": _at(when, clock),
        "encounter": {"reference": f"Encounter/{encounter}"},
    }


def _odi(observation_id: str, when: date, value: int, encounter: str) -> Resource:
    return {
        "resourceType": "Observation",
        "id": observation_id,
        "status": "final",
        "category": [{"coding": [{"code": "survey"}]}],
        "code": {
            "coding": [{"system": "http://loinc.org", "code": ODI_LOINC}],
            "text": f"{ODI} score",
        },
        "valueQuantity": {"value": value, "unit": "%"},
        "effectiveDateTime": when.isoformat(),
        "encounter": {"reference": f"Encounter/{encounter}"},
    }


def _procedure(procedure_id: str, when: date, text: str, encounter: str, cpt: str) -> Resource:
    return {
        "resourceType": "Procedure",
        "id": procedure_id,
        "status": "completed",
        "code": {"coding": [{"system": CPT, "code": cpt}], "text": text},
        "performedDateTime": _at(when, DURING),
        "encounter": {"reference": f"Encounter/{encounter}"},
        "reasonReference": [{"reference": "Condition/chronic-low-back-pain"}],
        "bodySite": [{"text": f"Lumbar facet joints {LEVELS}, bilateral"}],
    }


def _block(number: int, when: date, pre: int, post: int, post_vas: int | None) -> list[Resource]:
    encounter = f"enc-mbb{number}"
    if post_vas is None:
        after = f"""- 30 minutes after the block: {post}/10 on the same scale, with the same provocative movements.
- Relief of index pain, from today's pre-block score: {calculation(pre, post)}."""
        post_score = _score(f"pain-post-mbb{number}", when, post, encounter, clock=AFTER)
    else:
        after = f"- 30 minutes after the block: {post_vas} mm on the {VAS}, with the same provocative movements."
        post_score = _score(
            f"pain-post-mbb{number}", when, post_vas, encounter, scale=VAS, clock=AFTER
        )
    text = f"""Indication: chronic axial low back pain with suspected lumbar facet syndrome.
Levels: bilateral {LEVELS} (medial branches of L3 and L4 and the L5 dorsal ramus).
Technique: fluoroscopic guidance, 0.5 mL of 0.5% bupivacaine at each target nerve. No corticosteroid. No sedation. No other injections were performed today.

Pain assessment:
- Before the block: {pre}/10 on the {NRS}, index low back pain with extension and rotation.
{after}
The patient kept a pain diary; relief lasted about 6 hours, consistent with the duration of bupivacaine, and pain then returned to its earlier level.

No complications. Discharged in stable condition.
"""
    title = f"Procedure note, diagnostic medial branch block {number}"
    return [
        _encounter(encounter, when, "Pain clinic procedure visit"),
        _procedure(
            f"mbb{number}",
            when,
            f"Diagnostic lumbar medial branch block, bilateral {LEVELS}",
            encounter,
            CPT_FACET_INJECTION,
        ),
        _score(f"pain-pre-mbb{number}", when, pre, encounter, clock=BEFORE),
        post_score,
        _note(f"note-mbb{number}", when, title, text, encounter),
    ]


def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + end.month - start.month - (end.day < start.day)


def _early_history(facts: Facts) -> list[Resource]:
    """Primary care, conservative management, imaging and the pain clinic evaluation."""
    months = _months_between(facts.pain_onset, facts.evaluation)
    contradicted = facts.pt_attendance_contradicted

    pcp_plan = (
        "referral to physical therapy. The patient prefers not to take medication for now."
        if contradicted
        else "naproxen 500 mg twice daily with food. Referral to physical therapy."
    )
    pcp_note = f"""Chief complaint: low back pain.
History: low back pain that began on {facts.pain_onset.isoformat()} without injury. The pain is across the lower back, worse with standing and bending backward. No leg pain, numbness or weakness. No bowel or bladder changes.
Exam: tenderness over the lower lumbar paraspinal muscles. Strength and sensation intact in both legs.
Plan: {pcp_plan} Return if not improving.
"""
    pt_note = f"""Course: {facts.pt_start.isoformat()} to {facts.pt_end.isoformat()} (12 weeks), 22 of 24 scheduled sessions attended.
Treatment: core stabilization, lumbar flexion-based exercise, manual therapy and a home exercise program, performed as instructed.
Outcome: no meaningful improvement in pain or function. Pain {facts.baseline_nrs}/10 at discharge, unchanged from the start of therapy. Still unable to stand longer than 15 minutes.
Recommendation: return to the referring physician; consider referral to a pain specialist.
"""
    pt_records_note = f"""Physical therapy records were requested after the evaluation of {facts.evaluation.isoformat()} and received today.
The referral was opened on {facts.pt_start.isoformat()} with 12 sessions scheduled. The patient attended 2 of the 12 sessions, on {facts.pt_start.isoformat()} and {(facts.pt_start + timedelta(days=7)).isoformat()}, and did not return. The therapist recorded no outcome measures and no discharge assessment.
"""
    if facts.l2_fracture:
        mri_findings = f"Acute compression fracture of the L2 vertebral body with about 25% loss of anterior height and marrow edema. No retropulsion. No marrow-replacing lesion or mass. No evidence of discitis or osteomyelitis. Moderate bilateral facet arthropathy at {LEVELS}. No significant central canal or foraminal narrowing at any level."
        mri_impression = f"Acute L2 vertebral compression fracture. Facet arthropathy at {LEVELS}. No tumor or infection. No nerve root compression."
        imaging = f"lumbar MRI of {facts.mri.isoformat()} shows an acute compression fracture of the L2 vertebral body, and facet arthropathy at {LEVELS}."
        assessment = f"chronic axial low back pain. Lumbar facet syndrome at {LEVELS} is suspected. The L2 compression fracture is noted."
    else:
        mri_findings = f"Vertebral body heights and alignment are maintained. No fracture. No marrow-replacing lesion or mass. No evidence of discitis or osteomyelitis. No scoliosis or spondylolisthesis. Mild disc desiccation at L4-5 without herniation. Moderate bilateral facet arthropathy at {LEVELS} with small joint effusions. No significant central canal or foraminal narrowing at any level."
        mri_impression = f"Facet arthropathy at {LEVELS}. No fracture, tumor, infection or significant deformity. No nerve root compression."
        imaging = f"lumbar MRI of {facts.mri.isoformat()} shows facet arthropathy at {LEVELS} with no fracture, tumor, infection or significant deformity."
        assessment = (
            f"chronic axial low back pain, most consistent with lumbar facet syndrome at {LEVELS}."
        )
    mri_report = f"""{facts.mri.isoformat()}
MRI lumbar spine without contrast

Indication: low back pain for more than three months, not improved with conservative care.
Findings: {mri_findings}
Impression: {mri_impression}
"""
    if facts.leg_symptoms_unassessed:
        radiation = "Over the past month the patient has also noticed pain radiating down the left leg with numbness in the left foot."
        neuro_exam = ""
    else:
        radiation = "It does not radiate below the buttocks."
        neuro_exam = "Neurological exam: strength 5/5 in both legs. Sensation intact to light touch. Reflexes 2+ and symmetric at the knees and ankles. Straight-leg raise negative on both sides. No radicular symptoms and no neurogenic claudication.\n"
    if contradicted:
        conservative = f"the patient reports having completed 12 weeks of physical therapy ({facts.pt_start.isoformat()} to {facts.pt_end.isoformat()}) with no improvement."
    else:
        conservative = f"naproxen 500 mg twice daily since {facts.pcp_visit.isoformat()} with minimal benefit; a 12-week course of physical therapy ({facts.pt_start.isoformat()} to {facts.pt_end.isoformat()}) with no meaningful improvement in pain or function."
    eval_note = f"""History: low back pain since {facts.pain_onset.isoformat()}, now present for {months} months. The pain is predominantly axial, across the lower lumbar spine, moderate to severe, worse with extension, rotation and prolonged standing, and eased by sitting. {radiation} The patient can no longer stand longer than 15 minutes, has stopped gardening and needs help with housework.

Conservative management: {conservative}

Pain and function at baseline:
- Pain: {facts.baseline_nrs}/10 on the {NRS}.
- Disability: {facts.baseline_odi}% on the {ODI}, severe disability.

Past history: no prior spine surgery, including no lumbar fusion. No history of fibromyalgia or any widespread or centralized pain syndrome. No prior radiofrequency ablation or spinal injections.

{neuro_exam}Spine exam: pain reproduced by lumbar extension and rotation; tenderness over the {LEVELS} facet joints on both sides.

Imaging: {imaging}

Assessment: {assessment}
Plan: diagnostic medial branch blocks at {facts.plan_levels}; if two blocks confirm the diagnosis, radiofrequency ablation at those levels. Pain will be recorded on the same {NRS} before and after each block. No prior diagnostic facet procedures. No other injections are planned on the day of a block.
"""
    condition = {
        "resourceType": "Condition",
        "id": "chronic-low-back-pain",
        "clinicalStatus": {"coding": [{"code": "active"}]},
        "verificationStatus": {"coding": [{"code": "confirmed"}]},
        "code": {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": "278860009",
                    "display": "Chronic low back pain (finding)",
                }
            ],
            "text": "Chronic low back pain",
        },
        "onsetDateTime": facts.pain_onset.isoformat(),
        "recordedDate": facts.pcp_visit.isoformat(),
    }
    naproxen = {
        "resourceType": "MedicationRequest",
        "id": "naproxen",
        "status": "active",
        "intent": "order",
        "medicationCodeableConcept": {"text": "Naproxen 500 mg oral tablet"},
        "authoredOn": facts.pcp_visit.isoformat(),
        "encounter": {"reference": "Encounter/enc-pcp"},
        "dosageInstruction": [{"text": "1 tablet twice daily with food"}],
        "reasonReference": [{"reference": "Condition/chronic-low-back-pain"}],
    }
    physical_therapy = {
        "resourceType": "Procedure",
        "id": "physical-therapy-course",
        "status": "completed",
        "code": {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": "91251008",
                    "display": "Physical therapy procedure (regime/therapy)",
                }
            ],
            "text": "Physical therapy, 12-week course",
        },
        "performedPeriod": {"start": facts.pt_start.isoformat(), "end": facts.pt_end.isoformat()},
        "encounter": {"reference": "Encounter/enc-pt"},
        "reasonReference": [{"reference": "Condition/chronic-low-back-pain"}],
    }
    mri = {
        "resourceType": "DiagnosticReport",
        "id": "mri-lumbar",
        "status": "final",
        "code": {"text": "MRI lumbar spine without contrast"},
        "effectiveDateTime": facts.mri.isoformat(),
        "encounter": {"reference": "Encounter/enc-mri"},
        "conclusion": mri_impression,
        "presentedForm": [_attachment(mri_report)],
    }
    if contradicted:
        # No medication trial, and the only therapy records are the two notes that disagree.
        records_date = facts.evaluation + timedelta(days=4)
        conservative_care = [
            _encounter("enc-pt-records", records_date, "Records review"),
            _note(
                "note-pt-records",
                records_date,
                "Records review, physical therapy",
                pt_records_note,
                "enc-pt-records",
            ),
        ]
    else:
        conservative_care = [
            naproxen,
            _encounter("enc-pt", facts.pt_start, "Physical therapy", facts.pt_end),
            physical_therapy,
            _note(
                "note-pt-discharge",
                facts.pt_end,
                "Physical therapy discharge summary",
                pt_note,
                "enc-pt",
            ),
        ]
    return [
        condition,
        _encounter("enc-pcp", facts.pcp_visit, "Primary care visit"),
        _note("note-pcp", facts.pcp_visit, "Primary care visit", pcp_note, "enc-pcp"),
        *conservative_care,
        _encounter("enc-mri", facts.mri, "Imaging visit"),
        mri,
        _encounter("enc-pain-clinic-eval", facts.evaluation, "Pain clinic evaluation"),
        _score("pain-nrs-baseline", facts.evaluation, facts.baseline_nrs, "enc-pain-clinic-eval"),
        _odi("odi-baseline", facts.evaluation, facts.baseline_odi, "enc-pain-clinic-eval"),
        _note(
            "note-pain-clinic-eval",
            facts.evaluation,
            "Pain clinic initial evaluation",
            eval_note,
            "enc-pain-clinic-eval",
        ),
    ]


def _second_block_planning(facts: Facts) -> list[Resource]:
    assert facts.mbb1 and facts.planned_second_block
    days = (facts.planned_second_block - facts.mbb1).days
    exception = ""
    if facts.interval_exception:
        exception = f"""
Exception to the two-week interval: the patient starts therapeutic anticoagulation on {(facts.planned_second_block + timedelta(days=4)).isoformat()} ahead of a planned cardiac procedure, and spinal injections will have to be deferred for at least three months after that. To complete the diagnostic work-up before anticoagulation begins, the second block is scheduled {days} days after the first instead of the usual minimum of two weeks. The patient understands and agrees.
"""
    text = f"""Result of the first diagnostic medial branch block at {LEVELS} on {facts.mbb1.isoformat()}, scored on the {NRS}: pain {facts.mbb1_pre}/10 before and {facts.mbb1_post}/10 after, {calculation(facts.mbb1_pre, facts.mbb1_post)} relief, lasting about 6 hours, consistent with bupivacaine.

This was the patient's first diagnostic facet procedure.
Plan: a second, confirmatory medial branch block at the same levels ({LEVELS}, lumbar region) on {facts.planned_second_block.isoformat()}, {days} days after the first block. No other injections are planned for that day.
{exception}"""
    return [
        _encounter("enc-second-block-planning", facts.planning, "Pain clinic follow-up"),
        _note(
            "note-second-block-planning",
            facts.planning,
            "Pain clinic follow-up",
            text,
            "enc-second-block-planning",
        ),
    ]


def _block_results(facts: Facts) -> str:
    assert facts.mbb1 and facts.mbb2
    weeks = (facts.mbb2 - facts.mbb1).days // 7
    first = f"- Block 1 on {facts.mbb1.isoformat()}: pain {facts.mbb1_pre}/10 before and {facts.mbb1_post}/10 after on the {NRS}, {calculation(facts.mbb1_pre, facts.mbb1_post)} relief."
    if facts.mbb2_post_vas is None:
        second = f"- Block 2 on {facts.mbb2.isoformat()}, {weeks} weeks after the first: pain {facts.mbb2_pre}/10 before and {facts.mbb2_post}/10 after on the {NRS}, {calculation(facts.mbb2_pre, facts.mbb2_post)} relief."
    else:
        second = f"- Block 2 on {facts.mbb2.isoformat()}, {weeks} weeks after the first: pain {facts.mbb2_pre}/10 before on the {NRS} and {facts.mbb2_post_vas} mm after on the {VAS}."
    return (
        f"{first}\n{second}\nIn each case relief lasted about 6 hours, consistent with bupivacaine."
    )


def _rfa_planning(facts: Facts) -> list[Resource]:
    text = f"""Results of the diagnostic medial branch blocks at {LEVELS}:
{_block_results(facts)}

The patient has not had radiofrequency ablation before.
Plan: request prior authorization for thermal radiofrequency ablation of the medial branch nerves at {LEVELS} (lumbar region, two levels). No other injections are planned for that day.
"""
    return [
        _encounter("enc-rfa-planning", facts.planning, "Pain clinic follow-up"),
        _note(
            "note-rfa-planning", facts.planning, "Pain clinic follow-up", text, "enc-rfa-planning"
        ),
    ]


def _add_months(start: date, months: int) -> date:
    """A follow-up visit about `months` months after `start`, on the same weekday."""
    return start + timedelta(weeks=round(months * 52 / 12))


def _prior_rfa(facts: Facts) -> list[Resource]:
    assert facts.rfa1
    pre, post = facts.rfa1_pre, facts.rfa1_post
    months = facts.rfa1_relief_months
    improvement = calculation(pre, post)
    returned = _add_months(facts.rfa1, months + 1)

    rfa_note = f"""Indication: lumbar facet syndrome at {LEVELS}, confirmed by two diagnostic medial branch blocks.
{_block_results(facts)}
Procedure: thermal radiofrequency ablation of the medial branch nerves at {LEVELS}, bilateral, under fluoroscopic guidance. Lesions at 80 degrees Celsius for 90 seconds at each site. Local anesthetic only, no sedation.
Pain before the procedure: {pre}/10 on the {NRS}.
No complications.
"""
    resources = [
        _encounter("enc-rfa1", facts.rfa1, "Pain clinic procedure visit"),
        _procedure(
            "rfa1",
            facts.rfa1,
            f"Thermal radiofrequency ablation of lumbar medial branch nerves, bilateral {LEVELS}",
            "enc-rfa1",
            CPT_FACET_RFA,
        ),
        _score("pain-pre-rfa1", facts.rfa1, pre, "enc-rfa1", clock=BEFORE),
        _note(
            "note-rfa1", facts.rfa1, "Procedure note, radiofrequency ablation", rfa_note, "enc-rfa1"
        ),
    ]

    if facts.rfa1_follow_up_coded:
        visits = [m for m in (1, 3, 6, 8) if m <= months]
        for month in visits:
            when, encounter = _add_months(facts.rfa1, month), f"enc-rfa1-followup-{month}mo"
            text = f"""Follow-up {month} months after radiofrequency ablation at {LEVELS} on {facts.rfa1.isoformat()}.
Pain today: {post}/10 on the {NRS}, against {pre}/10 before the ablation: {improvement} improvement, unchanged since the first follow-up visit.
Function: standing tolerance about one hour; the patient has returned to gardening and housework.
"""
            resources += [
                _encounter(encounter, when, "Pain clinic follow-up"),
                _score(f"pain-post-rfa1-{month}mo", when, post, encounter),
                _note(
                    f"note-rfa1-followup-{month}mo", when, "Pain clinic follow-up", text, encounter
                ),
            ]
        six_months = _add_months(facts.rfa1, 6)
        resources.append(_odi("odi-post-rfa1-6mo", six_months, 22, "enc-rfa1-followup-6mo"))
        course = f"gave {improvement} improvement in pain ({pre}/10 to {post}/10 on the {NRS}) that lasted {months} months, with better function ({ODI} 22% at six months against {facts.baseline_odi}% at baseline)"
    else:
        when = _add_months(facts.rfa1, months + 1)
        text = f"""Progress note, {months + 1} months after radiofrequency ablation at {LEVELS} on {facts.rfa1.isoformat()}.
After the ablation the patient's pain fell from {pre}/10 to {post}/10 on the {NRS}, {improvement} improvement, and stayed at that level for about {months} months. In the past month it has returned to {pre}/10.
Function did not improve after the ablation: the {ODI} was 46% at the six-week visit and is 46% today, against {facts.baseline_odi}% at baseline. Standing tolerance has stayed at about 15 minutes throughout and the patient did not resume gardening or housework.
"""
        resources += [
            _encounter("enc-rfa1-progress", when, "Pain clinic follow-up"),
            _note(
                "note-rfa1-progress", when, "Pain clinic progress note", text, "enc-rfa1-progress"
            ),
        ]
        course = f"gave {improvement} improvement in pain ({pre}/10 to {post}/10 on the {NRS}) that lasted about {months} months, with no improvement in function"

    recurrence_note = f"""History: low back pain at {LEVELS}, treated with radiofrequency ablation on {facts.rfa1.isoformat()}. The pain returned around {returned.strftime("%B %Y")} and is back to {facts.baseline_nrs}/10 on the {NRS}.
Plan: restart naproxen 500 mg twice daily with food. Resume the home exercise program from the earlier physical therapy course, daily. Review in three months.
"""
    naproxen = {
        "resourceType": "MedicationRequest",
        "id": "naproxen-recurrence",
        "status": "active",
        "intent": "order",
        "medicationCodeableConcept": {"text": "Naproxen 500 mg oral tablet"},
        "authoredOn": RECURRENCE_VISIT.isoformat(),
        "encounter": {"reference": "Encounter/enc-recurrence"},
        "dosageInstruction": [{"text": "1 tablet twice daily with food"}],
        "reasonReference": [{"reference": "Condition/chronic-low-back-pain"}],
    }
    resources += [
        _encounter("enc-recurrence", RECURRENCE_VISIT, "Pain clinic follow-up"),
        naproxen,
        _note(
            "note-recurrence",
            RECURRENCE_VISIT,
            "Pain clinic follow-up",
            recurrence_note,
            "enc-recurrence",
        ),
    ]

    planning = f"""History: low back pain at {LEVELS}, treated with radiofrequency ablation on {facts.rfa1.isoformat()}, which {course}. The pain returned around {returned.strftime("%B %Y")} and is now back to its earlier level. This has been the patient's only radiofrequency ablation.

Conservative management since the pain returned: naproxen 500 mg twice daily and a daily home exercise program since {RECURRENCE_VISIT.isoformat()}, three months, with no meaningful improvement in pain or function.

Today: pain {facts.baseline_nrs}/10 on the {NRS}, moderate to severe, predominantly axial, across the lower lumbar spine, worse with extension and rotation, not radiating below the buttocks. Disability: {RECURRENCE_ODI}% on the {ODI}. The patient again cannot stand longer than 15 minutes.
Neurological exam: strength 5/5 in both legs, sensation intact, reflexes symmetric, straight-leg raise negative on both sides. No radicular symptoms and no neurogenic claudication.
No new trauma, fever or weight loss, and no change on exam to suggest a cause of pain other than the facet joints; the lumbar MRI of {facts.mri.isoformat()} showed no fracture, tumor, infection or significant deformity. No prior spine surgery, including no lumbar fusion. No fibromyalgia or any widespread or centralized pain syndrome.

Plan: request prior authorization for repeat thermal radiofrequency ablation at the same levels ({LEVELS}, lumbar region, two levels). No other injections are planned for that day.
"""
    resources += [
        _encounter("enc-repeat-rfa-planning", facts.planning, "Pain clinic follow-up"),
        _score(
            "pain-nrs-recurrence", facts.planning, facts.baseline_nrs, "enc-repeat-rfa-planning"
        ),
        _odi("odi-recurrence", facts.planning, RECURRENCE_ODI, "enc-repeat-rfa-planning"),
        _note(
            "note-repeat-rfa-planning",
            facts.planning,
            "Pain clinic follow-up",
            planning,
            "enc-repeat-rfa-planning",
        ),
    ]
    return resources


def facet_history(facts: Facts) -> list[Resource]:
    resources = _early_history(facts)
    if facts.mbb1:
        resources += _block(1, facts.mbb1, facts.mbb1_pre, facts.mbb1_post, None)
    if facts.mbb2:
        resources += _block(2, facts.mbb2, facts.mbb2_pre, facts.mbb2_post, facts.mbb2_post_vas)
    if facts.variant == "second-diagnostic":
        resources += _second_block_planning(facts)
    elif facts.variant == "initial-rfa":
        resources += _rfa_planning(facts)
    elif facts.variant == "repeat-rfa":
        resources += _prior_rfa(facts)
    return resources


def facet_label_evidence(facts: Facts) -> dict[str, list[str]]:
    """For each criterion, every resource or request field that states the fact.

    The guideline asks labels to list every resource that establishes them. Deriving the
    lists here, from the same facts that write the chart, keeps them complete and in step
    with the bundles: a resource is listed if its text or values state the fact, whether
    it is a coded resource or a note. Criteria a case labels not_applicable list nothing,
    and the case files ignore entries for criteria outside their variant.
    """
    note = "DocumentReference/{}".format
    evaluation, pcp = note("note-pain-clinic-eval"), note("note-pcp")
    repeat = facts.variant == "repeat-rfa"
    planning = {
        "second-diagnostic": [note("note-second-block-planning")],
        "initial-rfa": [note("note-rfa-planning")],
    }.get(facts.variant, [])
    repeat_planning = [note("note-repeat-rfa-planning")] if repeat else []
    recurrence = [note("note-recurrence")] if repeat else []

    def block(number: int) -> dict[str, str]:
        return {
            "procedure": f"Procedure/mbb{number}",
            "pre": f"Observation/pain-pre-mbb{number}",
            "post": f"Observation/pain-post-mbb{number}",
            "note": note(f"note-mbb{number}"),
        }

    blocks = [block(n) for n, done in ((1, facts.mbb1), (2, facts.mbb2)) if done]
    block_all = [ref for b in blocks for ref in b.values()]
    first_block = list(block(1).values()) if facts.mbb1 else []

    # Follow-up after a prior ablation: coded visits, or a single progress note.
    if not repeat:
        rfa, rfa_result = [], []
    else:
        rfa = ["Procedure/rfa1", "Observation/pain-pre-rfa1", note("note-rfa1")]
        if facts.rfa1_follow_up_coded:
            visits = [m for m in (1, 3, 6, 8) if m <= facts.rfa1_relief_months]
            rfa_result = [
                ref
                for m in visits
                for ref in (f"Observation/pain-post-rfa1-{m}mo", note(f"note-rfa1-followup-{m}mo"))
            ] + ["Observation/odi-post-rfa1-6mo"]
        else:
            rfa_result = [note("note-rfa1-progress")]

    if facts.pt_attendance_contradicted:
        conservative = [pcp, evaluation, note("note-pt-records")]
    elif repeat:
        # only care given after the pain returned
        conservative = ["MedicationRequest/naproxen-recurrence", *recurrence, *repeat_planning]
    else:
        conservative = [
            pcp,
            "MedicationRequest/naproxen",
            "Procedure/physical-therapy-course",
            note("note-pt-discharge"),
            "DiagnosticReport/mri-lumbar",
            evaluation,
        ]
    pt_discharge = [] if facts.pt_attendance_contradicted else [note("note-pt-discharge")]
    progress = [note("note-rfa1-progress")] if repeat and not facts.rfa1_follow_up_coded else []

    if repeat:
        # A repeat request is judged on the current episode: the visit after the pain
        # returned and the visit that requests the repeat ablation.
        current_pain = "Observation/pain-nrs-recurrence"
        current_odi = "Observation/odi-recurrence"
        general = {
            "chronic-axial-pain-functional-deficit": [
                *repeat_planning,
                *recurrence,
                current_pain,
                current_odi,
            ],
            # the progress note, where there is one, is what dates the recurrence
            "pain-minimum-3-months": [*progress, *recurrence, *repeat_planning],
            "no-untreated-radiculopathy": repeat_planning,
            "no-non-facet-pathology": repeat_planning,
            "pain-scale-at-baseline": [current_pain, *repeat_planning, *recurrence],
            "disability-scale-at-baseline": [current_odi, *repeat_planning],
            "scales-documented": [*repeat_planning, *recurrence, current_pain, current_odi],
            "no-generalized-pain-indication": repeat_planning,
            "not-after-alif": repeat_planning,
        }
    else:
        general = {
            "chronic-axial-pain-functional-deficit": [
                "Condition/chronic-low-back-pain",
                pcp,
                *pt_discharge,
                evaluation,
                "Observation/pain-nrs-baseline",
                "Observation/odi-baseline",
                *[b["note"] for b in blocks],
            ],
            "pain-minimum-3-months": [
                "Condition/chronic-low-back-pain",
                pcp,
                "DiagnosticReport/mri-lumbar",
                evaluation,
            ],
            "no-untreated-radiculopathy": (
                [evaluation] if facts.leg_symptoms_unassessed else [pcp, evaluation]
            ),
            "no-non-facet-pathology": ["DiagnosticReport/mri-lumbar", evaluation],
            "pain-scale-at-baseline": ["Observation/pain-nrs-baseline", evaluation],
            "disability-scale-at-baseline": ["Observation/odi-baseline", evaluation],
            "scales-documented": [
                evaluation,
                "Observation/pain-nrs-baseline",
                "Observation/odi-baseline",
                *[ref for b in blocks for ref in (b["pre"], b["post"], b["note"])],
                *planning,
            ],
            "no-generalized-pain-indication": [evaluation],
            "not-after-alif": [evaluation],
        }

    after_each_diagnostic = [*[ref for b in blocks for ref in (b["post"], b["note"])], *planning]
    if facts.mbb2_post_vas is not None:
        # a mismatch between scales needs both sides: the scale used before, and after
        after_each_diagnostic = [
            "Observation/pain-nrs-baseline",
            "Observation/pain-pre-mbb2",
            *after_each_diagnostic,
        ]

    return {
        **general,
        "conservative-care-failed": conservative,
        "pain-scale-after-each-diagnostic": after_each_diagnostic,
        "one-spinal-region-per-session": ["request.region", "request.levels"],
        "levels-1-to-2": ["request.levels"],
        "first-diagnostic-80pct-relief": [*first_block, *planning],
        "second-diagnostic-same-level": [
            "request.levels",
            *[ref for ref in first_block if "Observation" not in ref],
            *planning,
        ],
        "second-diagnostic-2-weeks-after-first": [
            "request.service_date",
            *[ref for ref in first_block if "Observation" not in ref],
            *planning,
        ],
        "diagnostic-sessions-max-4": [
            evaluation,
            *[ref for ref in first_block if "Observation" not in ref],
            *planning,
        ],
        "two-mbbs-80pct-sustained-relief": [*block_all, *planning],
        "rfa-sessions-max-2": (
            ["Procedure/rfa1", note("note-rfa1"), *recurrence, *repeat_planning]
            if repeat
            else [evaluation, *planning]
        ),
        "prior-rfa-50pct-6-months": [*rfa, *rfa_result, *recurrence, *repeat_planning],
    }


# --- PAP devices (LCD L33718, article A52467): the history, generated from facts ----------

# The shared background patient already has this coded Condition. It is one of the three
# comorbidities that osa-symptom-or-condition names, so that criterion is met in every PAP
# case and its not-met path is untested (cases/pap/PLAN.md, known limitations).
IHD_CONDITION = "Condition/37b75dda-e187-ae40-4f8f-f53efc54cfc9"
# LOINC 28633-6, "Polysomnography (sleep) study", confirmed on the LOINC terminology
# (tx.fhir.org, LOINC 2.82). The only total apnea-hypopnea index code there, 69990-0, is
# defined over 24 hours, so the index is recorded as text only.
PSG_LOINC = "28633-6"
HISTORY_AFTER_IHD = "ischemic heart disease, followed by cardiology, and obesity."
HISTORY_BEFORE_IHD = "obesity. No other chronic condition that bears on sleep."

# Bi-level case (pap-04): the one intended conflict is how long the patient used an E0601.
CPAP_SETUP = date(2026, 1, 12)
CPAP_FOLLOW_UP = date(2026, 3, 10)  # note: in use nightly since the setup
CPAP_FIRST_USE = date(2026, 8, 4)  # note: first PAP use, no previous PAP use
CPAP_REFIT_VISIT = date(2026, 8, 18)
CPAP_TELEPHONE = date(2026, 9, 25)

# Continued-coverage cases: night 1 is the night therapy starts, and the first three months
# end on the night of 2026-09-21 (92 nights).
FIRST_THREE_MONTHS = 92
BEST_WINDOW = 30
NIGHTS_REQUIRED = 20  # the near miss: 21 of 30 nights is exactly 70%


@dataclass(frozen=True)
class PapFacts:
    """What is true in a PAP case's chart. Defaults are the clean first-device history."""

    variant: str = "initial"
    evaluation: date = date(2026, 8, 24)  # in person, before the sleep test
    test_date: date = date(2026, 9, 2)
    results_visit: date = date(2026, 9, 10)
    facility_test: bool = False  # True: Type I polysomnogram; False: Type III home test
    study_minutes: int = 430  # recording time (home test) or total sleep time (polysomnogram)
    events: int = 230
    ahi_coded: bool = True  # False: the index is only in the report and the notes
    history: str = HISTORY_AFTER_IHD
    # Continued coverage
    therapy_start: date | None = None
    reeval: date | None = None
    download: bool = True  # False: no download or usage report anywhere in the record

    @property
    def ahi(self) -> float:
        return round(self.events * 60 / self.study_minutes, 1)


PAP_INITIAL = PapFacts()
PAP_CONTINUED = PapFacts(
    variant="continued",
    evaluation=date(2026, 5, 4),
    test_date=date(2026, 5, 19),
    results_visit=date(2026, 6, 2),
    facility_test=True,
    study_minutes=414,
    events=196,
    history=HISTORY_BEFORE_IHD,
    therapy_start=date(2026, 6, 22),
    reeval=date(2026, 8, 22),  # day 61 after the start of therapy
)
PAP_BILEVEL = PapFacts(
    variant="initial-bilevel",
    evaluation=date(2025, 11, 20),
    test_date=date(2025, 12, 4),
    results_visit=date(2025, 12, 16),
    study_minutes=400,
    events=178,
    history=HISTORY_BEFORE_IHD,
)

PAP_CASES: dict[str, PapFacts] = {
    "pap-01": PAP_INITIAL,
    "pap-02": replace(PAP_INITIAL, events=23, study_minutes=410),
    "pap-03": PAP_CONTINUED,
    "pap-04": PAP_BILEVEL,
    "pap-05": replace(PAP_INITIAL, events=77, study_minutes=390, ahi_coded=False),
    "pap-06": replace(PAP_CONTINUED, download=False),
}


def _hm(minutes: int) -> str:
    return f"{minutes // 60} h {minutes % 60} min"


def usage_hours() -> list[float]:
    """Hours of PAP use on each of the first 92 nights.

    Nights that count (4 hours or more) are 4.6 hours or more and the others 3.4 or less, so
    no night sits near the 4-hour line. Use is low in the first weeks, at two nights in three
    for the next six, and lower again after that.
    """
    hours = []
    for night in range(1, FIRST_THREE_MONTHS + 1):
        if night <= 25:
            counts = night % 3 == 0
        elif night <= 70:
            counts = night % 3 != 0
        else:
            counts = night % 2 == 0
        hours.append(
            round(4.6 + (night * 7 % 33) / 10, 1) if counts else round((night * 5 % 35) / 10, 1)
        )
    return hours


def best_window(hours: list[float]) -> tuple[int, int]:
    """(first night of the earliest best 30-night window, nights of 4+ hours in it)."""
    counts = [
        sum(h >= 4.0 for h in hours[start : start + BEST_WINDOW])
        for start in range(len(hours) - BEST_WINDOW + 1)
    ]
    best = max(counts)
    return counts.index(best) + 1, best


def _study_report(f: PapFacts) -> str:
    if f.facility_test:
        study = (
            "Type I study: attended polysomnogram performed in a facility-based sleep laboratory."
        )
        basis = f"Total sleep time: {_hm(f.study_minutes)} ({f.study_minutes} minutes)."
        index = f"Apnea-hypopnea index (AHI): {f.ahi} events per hour of sleep."
        title = "Polysomnogram report (Type I)"
    else:
        study = (
            f"Type III home sleep apnea test, recorded at home on {f.test_date.isoformat()}. "
            "Channels: airflow, respiratory effort and pulse oximetry."
        )
        basis = f"Recording time: {_hm(f.study_minutes)} ({f.study_minutes} minutes)."
        index = f"Apnea-hypopnea index (AHI): {f.ahi} events per hour of recording."
        title = "Home sleep apnea test report (Type III)"
    return f"""{f.test_date.isoformat()}
{title}

Study: {study}
Ordered by: the patient's treating sleep physician, at the in-person evaluation of {f.evaluation.isoformat()}.
{basis}
Respiratory events (apneas and hypopneas): {f.events}.
{index}
"""


def _pap_workup(f: PapFacts) -> list[Resource]:
    """Evaluation, order, sleep test and the results visit with the CPAP prescription."""
    test_name = (
        "attended polysomnogram (Type I)" if f.facility_test else "home sleep apnea test (Type III)"
    )
    eval_note = f"""Reason for visit: loud snoring, witnessed pauses in breathing and daytime sleepiness. First visit with me.
History: the patient snores loudly most nights and a household member has seen pauses in breathing during sleep. Wakes unrefreshed. Excessive daytime sleepiness: dozes off when seated in the afternoon and has nearly fallen asleep while driving. No complaint of insomnia.
Past history: {f.history}
Exam, in person today: crowded oropharynx (Mallampati class 3). Lungs clear. Heart rhythm regular.
Assessment: suspected obstructive sleep apnea.
Plan: {test_name} to assess for obstructive sleep apnea. I am this patient's treating physician for sleep-disordered breathing and I ordered the study at today's visit. Results will be reviewed at a follow-up visit.
"""
    if f.ahi >= 15:
        assessment = "obstructive sleep apnea."
    elif f.ahi >= 5:
        assessment = "obstructive sleep apnea with excessive daytime sleepiness."
    else:
        assessment = (
            "AHI within the normal range. The patient remains very sleepy and I am requesting "
            "a trial of CPAP."
        )
    basis = "of sleep" if f.facility_test else "of recording"
    if f.variant == "continued":
        plan = "CPAP device (E0601) ordered. Therapy starts when the supplier delivers it."
    elif f.variant == "initial-bilevel":
        plan = "single-level CPAP device (E0601), auto-titrating 5 to 15 cm H2O. Supplier to set it up."
    else:
        plan = "single-level CPAP device (E0601), auto-titrating 5 to 15 cm H2O, mask fitted by the supplier. Request prior authorization."
    results_note = f"""Results of the {test_name} of {f.test_date.isoformat()}: AHI {f.ahi} events per hour ({f.events} events over {_hm(f.study_minutes)} {basis}).
Assessment: {assessment}
Plan: {plan}
"""
    report = {
        "resourceType": "DiagnosticReport",
        "id": "report-sleep-test",
        "status": "final",
        "code": (
            {"coding": [{"system": "http://loinc.org", "code": PSG_LOINC}], "text": "Polysomnogram"}
            if f.facility_test
            else {"text": "Home sleep apnea test"}
        ),
        "effectiveDateTime": f.test_date.isoformat(),
        "presentedForm": [_attachment(_study_report(f))],
    }
    resources = [
        _encounter("enc-sleep-eval", f.evaluation, "Sleep medicine evaluation, in person"),
        _note(
            "note-sleep-eval",
            f.evaluation,
            "Sleep medicine initial evaluation",
            eval_note,
            "enc-sleep-eval",
        ),
        {
            "resourceType": "ServiceRequest",
            "id": "sr-sleep-test",
            "status": "completed",
            "intent": "order",
            "code": {"text": test_name.capitalize()},
            "authoredOn": f.evaluation.isoformat(),
            "encounter": {"reference": "Encounter/enc-sleep-eval"},
            "reasonCode": [{"text": "Suspected obstructive sleep apnea"}],
        },
    ]
    if f.facility_test:
        resources.append(_encounter("enc-sleep-test", f.test_date, "Facility-based polysomnogram"))
        report["encounter"] = {"reference": "Encounter/enc-sleep-test"}
    if f.ahi_coded:
        report["result"] = [{"reference": "Observation/obs-ahi"}]
        report["conclusion"] = f"AHI {f.ahi} events per hour."
        resources.append(
            {
                "resourceType": "Observation",
                "id": "obs-ahi",
                "status": "final",
                "category": [{"coding": [{"code": "procedure"}]}],
                "code": {"text": "Apnea-hypopnea index (AHI)"},
                "valueQuantity": {"value": f.ahi, "unit": "events/hour"},
                "effectiveDateTime": f.test_date.isoformat(),
            }
        )
    resources += [
        report,
        _encounter("enc-sleep-results", f.results_visit, "Sleep medicine follow-up, in person"),
        _note(
            "note-sleep-results",
            f.results_visit,
            "Sleep medicine follow-up, test results",
            results_note,
            "enc-sleep-results",
        ),
    ]
    return resources


def _first_three_months_end(f: PapFacts) -> date:
    assert f.therapy_start
    return f.therapy_start + timedelta(days=FIRST_THREE_MONTHS - 1)


def _pap_continued(f: PapFacts) -> list[Resource]:
    assert f.therapy_start and f.reeval
    start, reeval = f.therapy_start, f.reeval
    day = (reeval - start).days
    setup_note = f"""Supplier setup visit. CPAP device (E0601) delivered and set up. Full-face mask fitted and checked for leaks. The patient and a household member were shown how to use and clean the equipment and how to read the device display.
Therapy begins tonight, {start.isoformat()}.
"""
    ongoing = (
        "Device usage data will be downloaded at the end of the third month and reviewed."
        if f.download
        else "Return visit in six months."
    )
    reeval_note = f"""Re-evaluation {day} days after the start of CPAP therapy (started {start.isoformat()}), in person.
Symptoms: daytime sleepiness is much improved. The patient no longer dozes off when seated, wakes feeling more refreshed, and a household member reports that the snoring and the pauses in breathing have stopped.
Therapy: the patient reports wearing the mask most nights and has no problem with the mask or the pressure.
Past history update: ischemic heart disease diagnosed in July, followed by cardiology.
Assessment: obstructive sleep apnea. The patient is benefiting from PAP therapy.
Plan: continue CPAP at current settings. {ongoing}
"""
    resources = [
        _encounter("enc-cpap-setup", start, "Supplier setup visit"),
        _note(
            "note-cpap-setup",
            start,
            "Supplier setup note, CPAP",
            setup_note,
            "enc-cpap-setup",
        ),
        {
            "resourceType": "DeviceUseStatement",
            "id": "cpap-use",
            "status": "active",
            "device": {"display": "CPAP device (E0601)"},
            "timingPeriod": {"start": start.isoformat()},
            "recordedOn": start.isoformat(),
            "reasonCode": [{"text": "Obstructive sleep apnea"}],
        },
        _encounter("enc-reeval", reeval, "Sleep medicine re-evaluation, in person"),
        _note(
            "note-reeval",
            reeval,
            "Sleep medicine re-evaluation on CPAP",
            reeval_note,
            "enc-reeval",
        ),
    ]
    if f.download:
        resources += _pap_download(f)
    return resources


def _pap_download(f: PapFacts) -> list[Resource]:
    """The usage report for the first three months, dated after its last night, and the
    practitioner's review, dated after the report."""
    assert f.therapy_start
    start, last = f.therapy_start, _first_three_months_end(f)
    report_date, review_date = last + timedelta(days=2), last + timedelta(days=3)
    hours = usage_hours()
    first, qualifying = best_window(hours)
    assert qualifying == NIGHTS_REQUIRED, "the best 30-night window must be exactly 20"
    window_start = start + timedelta(days=first - 1)
    window_end = window_start + timedelta(days=BEST_WINDOW - 1)
    any_use = sum(h > 0 for h in hours)
    long_nights = sum(h >= 4.0 for h in hours)
    average = sum(hours) / len(hours)
    rows = []
    for begin in range(1, len(hours) + 1, 10):
        end = min(begin + 9, len(hours))
        span = f"{(start + timedelta(days=begin - 1)).isoformat()} to {(start + timedelta(days=end - 1)).isoformat()}"
        values = " ".join(f"{h:.1f}" for h in hours[begin - 1 : end])
        rows.append(f"Nights {begin}-{end} ({span}): {values}")
    table = "\n".join(rows)
    percent = round(qualifying / BEST_WINDOW * 100)
    report = f"""Device: CPAP (E0601), written report of the device download.
Data period: {start.isoformat()} to {last.isoformat()}, {len(hours)} nights, the first three months of therapy. Night 1 is {start.isoformat()}.
Summary of usage:
- Nights with any use: {any_use} of {len(hours)}.
- Average use: {average:.1f} hours per night over all {len(hours)} nights.
- Nights with use of 4 hours or more: {long_nights} of {len(hours)}.
Best consecutive 30-night period (earliest if several are equal): nights {first} to {first + BEST_WINDOW - 1} ({window_start.isoformat()} to {window_end.isoformat()}), use of 4 hours or more on {qualifying} of {BEST_WINDOW} nights ({percent}%).
Nightly use in hours:
{table}
"""
    review = f"""Reviewed the PAP device download report dated {report_date.isoformat()}, covering {start.isoformat()} to {last.isoformat()}. The report is filed in the medical record.
Plan: the patient will be contacted about mask comfort and encouraged to use the device for more hours each night.
"""
    return [
        _encounter("enc-pap-download", report_date, "Device data download", cls="VR"),
        _note(
            "note-pap-download",
            report_date,
            "PAP device download report",
            report,
            "enc-pap-download",
        ),
        _encounter(
            "enc-adherence-review", review_date, "Practitioner review of device data", cls="VR"
        ),
        _note(
            "note-adherence-review",
            review_date,
            "Practitioner review of PAP download",
            review,
            "enc-adherence-review",
        ),
    ]


def _pap_bilevel(f: PapFacts) -> list[Resource]:
    """Two sleep-clinic notes that disagree on how long the patient used an E0601, then a
    telephone order for an E0470 with no visit after it."""
    march = f"""Follow-up on CPAP therapy. The CPAP device (E0601) was set up by the supplier on {CPAP_SETUP.isoformat()} and the patient has used it nightly since then.
Mask: full-face. The patient reports some mask leak. No other complaints today.
Plan: continue current settings. Return in six months.
"""
    august = f"""Visit to start PAP therapy. First use of PAP therapy was the night of {CPAP_FIRST_USE.isoformat()}, on CPAP at 8 cm H2O. No previous PAP use.
Mask: the full-face mask was refit today. It is properly fit and the patient is comfortable in it.
Daytime sleepiness persists. CPAP pressure raised to 10 cm H2O.
Plan: return in four weeks.
"""
    telephone = f"""Telephone encounter. Reviewed the patient's CPAP device download and spoke with the patient.
CPAP has failed to meet therapeutic goals despite optimal therapy: the device-reported residual AHI remains elevated and the daytime sleepiness persists.
Interface fit and comfort: the full-face mask was refit at the visit of {CPAP_REFIT_VISIT.isoformat()}. An appropriate interface has been properly fit and the patient is using it without difficulty. This mask will be used with the bi-level device.
Pressure settings: the current CPAP pressure of 14 cm H2O prevents the patient from tolerating the therapy; the patient wakes and removes the mask. Lower pressure settings of 8, 10 and 12 cm H2O were tried but failed to adequately control the symptoms of obstructive sleep apnea, to improve sleep quality and to reduce the AHI to acceptable levels.
Plan: change to a bi-level device without back-up rate (E0470). The order was placed by telephone today and no visit is scheduled before the device is supplied. Request prior authorization.
"""
    return [
        _encounter(
            "enc-cpap-followup-march", CPAP_FOLLOW_UP, "Sleep medicine follow-up, in person"
        ),
        _note(
            "note-cpap-followup-march",
            CPAP_FOLLOW_UP,
            "Sleep medicine follow-up on CPAP",
            march,
            "enc-cpap-followup-march",
        ),
        _encounter("enc-cpap-followup-august", CPAP_REFIT_VISIT, "Sleep medicine visit, in person"),
        _note(
            "note-cpap-followup-august",
            CPAP_REFIT_VISIT,
            "Sleep medicine visit, start of PAP therapy",
            august,
            "enc-cpap-followup-august",
        ),
        _encounter("enc-telephone", CPAP_TELEPHONE, "Telephone encounter", cls="VR"),
        _note(
            "note-telephone",
            CPAP_TELEPHONE,
            "Telephone encounter, change to bi-level device",
            telephone,
            "enc-telephone",
        ),
    ]


def pap_history(f: PapFacts) -> list[Resource]:
    resources = _pap_workup(f)
    if f.variant == "continued":
        resources += _pap_continued(f)
    elif f.variant == "initial-bilevel":
        resources += _pap_bilevel(f)
    return resources


def pap_label_evidence(f: PapFacts) -> dict[str, list[str]]:
    """For each criterion, every resource that states the fact (see facet_label_evidence)."""
    eval_note, eval_enc = "DocumentReference/note-sleep-eval", "Encounter/enc-sleep-eval"
    report, results = "DiagnosticReport/report-sleep-test", "DocumentReference/note-sleep-results"
    index = ["Observation/obs-ahi"] if f.ahi_coded else []
    evidence = {
        "in-person-evaluation-before-sleep-test": [eval_enc, eval_note, report],
        "ahi-15-plus": [report, *index, results],
        "ahi-5-to-14": [report, *index, results],
        "osa-symptom-or-condition": [eval_note, IHD_CONDITION],
        "short-study-event-minimum": [report],
        "sleep-test-type": [report],
        "sleep-test-ordered-by-treating-practitioner": [
            "ServiceRequest/sr-sleep-test",
            eval_note,
            report,
        ],
    }
    telephone = "DocumentReference/note-telephone"
    evidence |= {
        "cpap-tried-and-ineffective": [telephone],
        "interface-fit-addressed": [telephone, "DocumentReference/note-cpap-followup-august"],
        "cpap-pressure-settings-failed": [telephone],
        "new-evaluation-after-3-months-on-cpap": [
            "DocumentReference/note-cpap-followup-march",
            "DocumentReference/note-cpap-followup-august",
            telephone,
        ],
    }
    reeval = "DocumentReference/note-reeval"
    adherence = (
        ["DocumentReference/note-pap-download", "DocumentReference/note-adherence-review"]
        if f.download
        else [reeval]
    )
    evidence |= {
        "reevaluation-day-31-to-91": [
            "Encounter/enc-reeval",
            reeval,
            "DeviceUseStatement/cpap-use",
            "DocumentReference/note-cpap-setup",
        ],
        "symptoms-improved-in-person": [reeval],
        "adherence-4h-70pct": adherence,
        "adherence-report-in-record": adherence,
    }
    return evidence


# --- Bariatric surgery (NCD 100.1): the history, generated from facts ---------------------

# The shared background patient cannot serve the bariatric cases as it is. Its coded weight
# and BMI (27.97) contradict every BMI of 35 or more, and its ischemic heart disease is a
# possible co-morbidity that the NCD does not name. A bariatric bundle therefore starts from
# the template with the cardiac cluster removed and the weights, BMIs and blood pressures
# rewritten from the case facts. The template file, and the facet and PAP bundles, are not
# touched (cases/bariatric/PLAN.md lists every change).
HEIGHT_CM = 172.9  # the template's height, unchanged
HEIGHT_M2 = (HEIGHT_CM / 100) ** 2
# LOINC 29463-7 "Body weight" and 39156-5 "Body mass index (BMI) [Ratio]": both ACTIVE on the
# LOINC terminology server (tx.fhir.org, LOINC 2.82), and already on the template's own
# observations. The HbA1c in the diagnosis note is text only.
WEIGHT_LOINC = "29463-7"
BMI_LOINC = "39156-5"
BLOOD_PRESSURE_LOINC = "85354-9"
SYSTOLIC_LOINC, DIASTOLIC_LOINC = "8480-6", "8462-4"
BASELINE_EXAMS = ("2023-10-24", "2024-07-09", "2025-07-15", "2026-07-21")  # template annual exams
# Replaces the template's 135/90, 136/84, 139/88 and 137/86, which could be read as
# hypertension, a possible co-morbidity the NCD does not name.
BASELINE_BLOOD_PRESSURE = dict(
    zip(BASELINE_EXAMS, [(118, 76), (116, 74), (114, 76), (118, 76)], strict=True)
)
OBESITY_CONDITION = "Condition/37b75dda-e187-ae40-3667-262f391a4e9a"  # BMI 30+ obesity, 1995

_P = "37b75dda-e187-ae40"
# Removed from every bariatric bundle: the cardiac cluster, the note pair that mentions it,
# and the lipid panel. Listed in cases/bariatric/PLAN.md; a test ties the two together.
BARIATRIC_REMOVED: tuple[str, ...] = (
    # ischemic heart disease and the abnormal cardiac imaging finding
    f"Condition/{_P}-4f8f-f53efc54cfc9",
    f"Condition/{_P}-149d-10660d15eefd",
    # 2026-07-28 encounter: cardiology referral, operative status, note and its twin report
    f"Encounter/{_P}-919a-852251f5aff3",
    f"Procedure/{_P}-c852-d61e8e01c819",
    f"Observation/{_P}-3757-ee628716a210",
    "DocumentReference/8a804714-d88f-1efa-b374-09a2ded1640b",
    "DiagnosticReport/842f4763-17a4-7525-d5ef-0fabb9c9939d",
    # 2026-08-02 encounter: angiography, consultation, anesthesia care, referral, note and report
    f"Encounter/{_P}-065e-55ee51cb2573",
    f"Procedure/{_P}-f6e7-8385846bf707",
    f"Procedure/{_P}-1674-33cf97dc9b5c",
    f"Procedure/{_P}-5b70-175be4e23f55",
    f"Procedure/{_P}-63b7-95497c5dd0b4",
    "DocumentReference/8ce34cbb-7dcb-26e3-5bed-84b486df6b87",
    "DiagnosticReport/ea559e77-5506-0eef-e976-c0ffe97ff036",
    # 2026-08-09 encounter: percutaneous coronary intervention, note and report
    f"Encounter/{_P}-4c99-3aab45f0cd0d",
    f"Procedure/{_P}-c5ed-5ffe4c89afec",
    "DocumentReference/771daebe-064d-3403-07ae-f308433dd783",
    "DiagnosticReport/cf4a10ce-e5a8-9a4e-5891-aee3559aac77",
    # clopidogrel, simvastatin, metoprolol, nitroglycerin
    f"MedicationRequest/{_P}-7172-b7842d7a2b47",
    f"MedicationRequest/{_P}-c0f4-15273b5391f3",
    f"MedicationRequest/{_P}-acd3-2a7639746de8",
    f"MedicationRequest/{_P}-d1b7-2b381bed9ed0",
    # the 2026-07-21 annual-exam note and report, which describe the removed cluster
    "DocumentReference/98a61531-2b8f-f251-ab35-515a0bf416a2",
    "DiagnosticReport/703eb6d6-25bc-c714-f55a-b35aafdf18fb",
    # the lipid panel of 2026-07-21 and its four results
    f"DiagnosticReport/{_P}-f188-4c73123a16a4",
    f"Observation/{_P}-1295-7a60d2d1e589",
    f"Observation/{_P}-8772-5ec7ebe1237e",
    f"Observation/{_P}-6a38-cd02fbcb8a50",
    f"Observation/{_P}-42cc-ce6e2cf7a872",
)

WM_REFERRAL = date(2025, 7, 28)  # primary care refers to the weight management clinic
WM_INTAKE = date(2025, 8, 4)  # intake: month 0 of the program, or the declined call
WM_SIX_MONTH = date(2026, 2, 2)
DIABETES_VISIT = date(2026, 7, 28)
WM_COMPLETION = date(2026, 8, 3)  # month 12
SURGERY_REFERRAL = date(2026, 8, 17)  # only when there was no program
SURGICAL_EVAL = date(2026, 9, 14)

# Offsets from the weight at the surgical evaluation: a slow rise from 2023, a dip of 3.2 kg
# by month 6 of a program and the regain by month 12 (+0.6 kg over the intake weight).
WEIGHT_OFFSETS: dict[date, float] = {
    date(2023, 10, 24): -2.6,
    date(2024, 7, 9): -1.8,
    date(2025, 7, 15): -0.9,
    WM_REFERRAL: -0.9,
    WM_INTAKE: -1.0,
    WM_SIX_MONTH: -4.2,
    date(2026, 7, 21): -0.6,
    DIABETES_VISIT: -0.5,
    WM_COMPLETION: -0.4,
    SURGERY_REFERRAL: -0.3,
    SURGICAL_EVAL: 0.0,
}


@dataclass(frozen=True)
class BariatricFacts:
    """What is true in a bariatric case's chart. Defaults are the clean covered-procedure case."""

    variant: str = "bariatric"
    procedure: str = "laparoscopic Roux-en-Y gastric bypass"
    weight: float = 122.9  # kg at the surgical evaluation, 2026-09-14
    # high: every BMI is 37 or more; low: every BMI is below 33; near_miss: the evaluation BMI
    # is below 35 unrounded and no BMI reaches 34.95
    bmi_design: str = "high"
    # completed: 12 months of supervised treatment; declined: a clinic note says the patient
    # declined enrollment (the evaluation says it was completed); none: no treatment record
    program: str = "completed"
    diabetes: bool = True


def bmi_of(kg: float) -> float:
    return round(kg / HEIGHT_M2, 2)


def bariatric_weight_dates(f: BariatricFacts) -> list[date]:
    """The visits at which this case's own chart records a weight, in date order."""
    dates = []
    if f.program != "none":
        dates.append(WM_REFERRAL)
    if f.program == "completed":
        dates += [WM_INTAKE, WM_SIX_MONTH]
    if f.diabetes:
        dates.append(DIABETES_VISIT)
    if f.program == "completed":
        dates.append(WM_COMPLETION)
    if f.program == "none":
        dates.append(SURGERY_REFERRAL)
    return [*dates, SURGICAL_EVAL]


def bariatric_series(f: BariatricFacts) -> dict[date, tuple[float, float]]:
    """(kg, BMI) at every date with a weight: the four baseline exams, then the case's visits.

    Asserts the design of the series, so a change to a number cannot quietly move a label.
    """
    dates = sorted({date.fromisoformat(d) for d in BASELINE_EXAMS} | set(bariatric_weight_dates(f)))
    series = {d: (w := round(f.weight + WEIGHT_OFFSETS[d], 1), bmi_of(w)) for d in dates}
    bmis = [b for _, b in series.values()]
    assert min(bmis) >= 30, "the template's BMI 30+ obesity Condition would be contradicted"
    evaluation = series[SURGICAL_EVAL][1]
    if f.bmi_design == "high":
        assert min(bmis) >= 37, bmis
    elif f.bmi_design == "low":
        assert max(bmis) < 33, bmis
    else:
        assert f.bmi_design == "near_miss"
        assert 34.8 < evaluation < 35 and max(bmis) < 34.95, bmis
    ordered = list(series.items())
    for (before, (kg_before, _)), (after, (kg_after, _)) in zip(ordered, ordered[1:], strict=False):
        # weights move by at most 1.0 kg between visits, except into and out of the month-6 dip
        if WM_SIX_MONTH not in (before, after):
            assert abs(kg_after - kg_before) <= 1.0 + 1e-9, (before, after, kg_before, kg_after)
    if f.program == "completed":
        intake, month6, month12 = (series[d][0] for d in (WM_INTAKE, WM_SIX_MONTH, WM_COMPLETION))
        assert round(intake - month6, 1) == 3.2 and round(month12 - intake, 1) == 0.6
    return series


def _note_bmi(kg: float) -> str:
    """The BMI as a note states it: one decimal, rounded from the unrounded value."""
    return f"{kg / HEIGHT_M2:.1f}"


def bariatric_background(background: list[Resource], f: BariatricFacts) -> list[Resource]:
    """The template for a bariatric case: the cardiac cluster and lipid panel removed, and the
    weights, BMIs and blood pressures of the four annual exams rewritten from the case."""
    removed = set(BARIATRIC_REMOVED)
    refs = {f"{r['resourceType']}/{r['id']}" for r in background}
    assert removed <= refs, sorted(removed - refs)
    kept = [r for r in background if f"{r['resourceType']}/{r['id']}" not in removed]
    assert len(kept) == len(background) - len(BARIATRIC_REMOVED) == 139
    series = bariatric_series(f)
    rewritten = 0
    for resource in kept:
        if resource["resourceType"] != "Observation":
            continue
        code = resource["code"]["coding"][0]["code"]
        when = resource["effectiveDateTime"][:10]
        if (
            code not in (WEIGHT_LOINC, BMI_LOINC, BLOOD_PRESSURE_LOINC)
            or when not in BASELINE_EXAMS
        ):
            continue
        kg, bmi = series[date.fromisoformat(when)]
        if code == WEIGHT_LOINC:
            resource["valueQuantity"]["value"] = kg
        elif code == BMI_LOINC:
            resource["valueQuantity"]["value"] = bmi
        else:
            systolic, diastolic = BASELINE_BLOOD_PRESSURE[when]
            for part in resource["component"]:
                part_code = part["code"]["coding"][0]["code"]
                part["valueQuantity"]["value"] = (
                    systolic if part_code == SYSTOLIC_LOINC else diastolic
                )
        rewritten += 1
    assert rewritten == 12  # weight, BMI and blood pressure at each of the four exams
    return kept


def _vital(obs_id: str, when: date, loinc: str, name: str, value: float, unit: str, enc: str):
    return {
        "resourceType": "Observation",
        "id": obs_id,
        "status": "final",
        "category": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                        "code": "vital-signs",
                        "display": "Vital signs",
                    }
                ]
            }
        ],
        "code": {
            "coding": [{"system": "http://loinc.org", "code": loinc, "display": name}],
            "text": name,
        },
        "effectiveDateTime": when.isoformat(),
        "valueQuantity": {
            "value": value,
            "unit": unit,
            "system": "http://unitsofmeasure.org",
            "code": unit,
        },
        "encounter": {"reference": f"Encounter/{enc}"},
    }


def _surgical_evaluation(f: BariatricFacts, kg: float) -> str:
    """The evaluation by the surgeon. It carries no history sentence in any case: no past
    medical or surgical history and no statement about co-morbidities. What it says about
    co-morbidity is the Assessment line, and only where the chart has type 2 diabetes."""
    bmi = _note_bmi(kg)
    if f.program == "none":
        reason = "evaluation for bariatric surgery. Referred by primary care."
        treatment = ""
    elif f.program == "declined":
        reason = "evaluation for bariatric surgery after a 12-month weight management program."
        treatment = "Medical weight management: completed a 12-month physician-supervised weight management program (2025-08-04 to 2026-08-03) with a dietitian-led diet, exercise and orlistat, without lasting weight loss.\n"
    else:
        reason = "evaluation for bariatric surgery after a 12-month weight management program."
        treatment = "Medical weight management: completed a 12-month physician-supervised program (2025-08-04 to 2026-08-03: dietitian-led 1,500 kcal/day diet, 150 minutes of walking a week, monthly counseling, orlistat 120 mg three times daily). Weight at completion was 0.6 kg above the weight at intake; no lasting weight loss.\n"
    diabetes = ", with type 2 diabetes on metformin" if f.diabetes else ""
    return f"""Reason for visit: {reason}
Measurements today: weight {kg} kg, height {HEIGHT_CM} cm, BMI {bmi} kg/m2.
{treatment}Assessment: obesity, BMI {bmi}{diabetes}.
Plan: {f.procedure}. Request prior authorization.
"""


def bariatric_history(f: BariatricFacts) -> list[Resource]:
    series = bariatric_series(f)
    encounters: dict[date, str] = {
        WM_REFERRAL: "enc-pcp-wm-referral",
        WM_INTAKE: "enc-wm-intake",
        WM_SIX_MONTH: "enc-wm-6mo",
        DIABETES_VISIT: "enc-pcp-diabetes",
        WM_COMPLETION: "enc-wm-12mo",
        SURGERY_REFERRAL: "enc-bariatric-referral",
        SURGICAL_EVAL: "enc-surgical-eval",
    }
    resources: list[Resource] = []

    def visit(when: date, kind: str, note_id: str, title: str, text: str, cls: str = "AMB"):
        resources.extend(
            [
                _encounter(encounters[when], when, kind, cls=cls),
                _note(note_id, when, title, text, encounters[when]),
            ]
        )

    def kg(when: date) -> float:
        return series[when][0]

    if f.program != "none":
        visit(
            WM_REFERRAL,
            "Primary care visit",
            "note-wm-referral",
            "Primary care visit, weight",
            "Chief complaint: weight.\nObesity. Treatment options discussed. Plan: referral to the medical weight management clinic. No medication started at this visit.\n",
        )
    if f.program == "completed":
        visit(
            WM_INTAKE,
            "Weight management clinic visit",
            "note-wm-intake",
            "Weight management clinic intake",
            f"Reason for visit: obesity, referred by primary care. First visit with me.\nMeasurements: weight {kg(WM_INTAKE)} kg, height {HEIGHT_CM} cm.\nPlan: 12-month supervised program. Dietitian-led diet of 1,500 kcal/day, 150 minutes of walking a week, monthly behavioral counseling visits. Start orlistat 120 mg three times daily with meals.\n",
        )
        resources.append(
            {
                "resourceType": "MedicationRequest",
                "id": "orlistat",
                "status": "completed",
                "intent": "order",
                "medicationCodeableConcept": {"text": "Orlistat 120 MG oral capsule"},
                "authoredOn": WM_INTAKE.isoformat(),
                "encounter": {"reference": f"Encounter/{encounters[WM_INTAKE]}"},
                "dosageInstruction": [{"text": "1 capsule three times daily with meals"}],
                "reasonReference": [{"reference": OBESITY_CONDITION}],
            }
        )
        visit(
            WM_SIX_MONTH,
            "Weight management clinic visit",
            "note-wm-6mo",
            "Weight management clinic, month 6",
            f"Month 6 of the supervised weight management program. 6 of 6 monthly sessions attended.\nWeight {kg(WM_SIX_MONTH)} kg, {kg(WM_INTAKE)} kg at intake: down 3.2 kg.\nPlan: continue the diet, the walking program, monthly counseling and orlistat.\n",
        )
    if f.program == "declined":
        visit(
            WM_INTAKE,
            "Weight management clinic telephone intake",
            "note-wm-declined",
            "Weight management clinic, telephone intake",
            f"Referral from primary care, sent {WM_REFERRAL.isoformat()}, received. Intake call with the patient today. The patient declined enrollment in the program and does not wish to schedule a first visit. Referral closed.\n",
            cls="VR",
        )
    if f.diabetes:
        visit(
            DIABETES_VISIT,
            "Primary care visit",
            "note-diabetes-dx",
            "Primary care visit",
            "Chief complaint: increased thirst and fatigue.\nType 2 diabetes mellitus diagnosed on HbA1c 7.4%. Started metformin 500 mg twice daily with meals.\n",
        )
        resources += [
            {
                "resourceType": "Condition",
                "id": "type-2-diabetes",
                "clinicalStatus": {"coding": [{"code": "active"}]},
                "verificationStatus": {"coding": [{"code": "confirmed"}]},
                "code": {
                    "coding": [
                        {
                            "system": "http://snomed.info/sct",
                            "code": "44054006",
                            "display": "Type 2 diabetes mellitus",
                        }
                    ],
                    "text": "Type 2 diabetes mellitus",
                },
                "onsetDateTime": DIABETES_VISIT.isoformat(),
                "recordedDate": DIABETES_VISIT.isoformat(),
            },
            {
                "resourceType": "MedicationRequest",
                "id": "metformin",
                "status": "active",
                "intent": "order",
                "medicationCodeableConcept": {"text": "Metformin hydrochloride 500 MG oral tablet"},
                "authoredOn": DIABETES_VISIT.isoformat(),
                "encounter": {"reference": f"Encounter/{encounters[DIABETES_VISIT]}"},
                "dosageInstruction": [{"text": "1 tablet twice daily with meals"}],
                "reasonReference": [{"reference": "Condition/type-2-diabetes"}],
            },
        ]
    if f.program == "completed":
        visit(
            WM_COMPLETION,
            "Weight management clinic visit",
            "note-wm-12mo",
            "Weight management clinic, month 12",
            f"Month 12, end of the supervised weight management program (2025-08-04 to 2026-08-03). 12 of 12 monthly sessions attended. Diet, walking program and counseling followed throughout; orlistat 120 mg three times daily taken for the 12 months.\nWeight {kg(WM_COMPLETION)} kg, {kg(WM_INTAKE)} kg at intake: 0.6 kg above the intake weight. The early loss of 3.2 kg was regained. No lasting weight loss.\nPlan: program complete. Referral to bariatric surgery.\n",
        )
    if f.program == "none":
        visit(
            SURGERY_REFERRAL,
            "Primary care visit",
            "note-bariatric-referral",
            "Primary care visit, referral to bariatric surgery",
            f"Referral to bariatric surgery for obesity. Weight {kg(SURGERY_REFERRAL)} kg, height {HEIGHT_CM} cm, BMI {_note_bmi(kg(SURGERY_REFERRAL))} kg/m2. Type 2 diabetes mellitus on metformin.\n",
        )
    visit(
        SURGICAL_EVAL,
        "Bariatric surgery evaluation, in person",
        "note-surgical-eval",
        "Bariatric surgery evaluation",
        _surgical_evaluation(f, kg(SURGICAL_EVAL)),
    )

    for when in bariatric_weight_dates(f):
        weight, bmi = series[when]
        enc = encounters[when]
        resources += [
            _vital(f"weight-{when}", when, WEIGHT_LOINC, "Body Weight", weight, "kg", enc),
            _vital(
                f"bmi-{when}", when, BMI_LOINC, "Body mass index (BMI) [Ratio]", bmi, "kg/m2", enc
            ),
        ]
    return sorted(resources, key=_history_order)


def _history_order(resource: Resource) -> tuple[str, str]:
    """Date, then type, so a bundle reads in order and stays stable."""
    for key in ("effectiveDateTime", "authoredOn", "onsetDateTime", "date"):
        if key in resource:
            return resource[key][:10], resource["resourceType"]
    return resource["period"]["start"], resource["resourceType"]


def bariatric_label_evidence(f: BariatricFacts) -> dict[str, list[str]]:
    """For each criterion, every resource or request field that states the fact."""
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    baseline_bmi = [
        f"Observation/{r['id']}"
        for r in sorted(template, key=lambda r: r.get("effectiveDateTime", ""))
        if r["resourceType"] == "Observation"
        and r["code"]["coding"][0]["code"] == BMI_LOINC
        and r["effectiveDateTime"][:10] in BASELINE_EXAMS
    ]
    own = [d for d in bariatric_weight_dates(f)]
    note = "DocumentReference/{}".format
    bmi_notes = [note("note-surgical-eval")]
    if f.program == "none":
        bmi_notes.insert(0, note("note-bariatric-referral"))
    bmi = [*baseline_bmi, *[f"Observation/bmi-{d}" for d in own], *bmi_notes]

    comorbidity = [note("note-surgical-eval")]
    if f.diabetes:
        comorbidity = [
            "Condition/type-2-diabetes",
            "MedicationRequest/metformin",
            note("note-diabetes-dx"),
            *([note("note-bariatric-referral")] if f.program == "none" else []),
            note("note-surgical-eval"),
        ]
    treatment = {
        "completed": [
            note("note-wm-intake"),
            "MedicationRequest/orlistat",
            note("note-wm-6mo"),
            note("note-wm-12mo"),
            note("note-surgical-eval"),
        ],
        "declined": [note("note-wm-declined"), note("note-surgical-eval")],
        "none": [note("note-surgical-eval"), note("note-bariatric-referral")],
    }[f.program]
    return {
        "nationally-covered-procedure": ["request.procedure", "request.codes"],
        "bmi-35-plus": bmi,
        "obesity-comorbidity": comorbidity,
        "failed-medical-treatment": treatment,
        "lsg-bmi-35-plus": bmi,
        "lsg-obesity-comorbidity": comorbidity,
        "lsg-failed-medical-treatment": treatment,
    }


BARIATRIC_CASES: dict[str, BariatricFacts] = {
    "bariatric-01": BariatricFacts(),
    "bariatric-02": BariatricFacts(
        procedure="laparoscopic adjustable gastric banding", weight=95.0, bmi_design="low"
    ),
    "bariatric-03": BariatricFacts(
        variant="sleeve-gastrectomy",
        procedure="stand-alone laparoscopic sleeve gastrectomy",
        weight=104.3,
        bmi_design="near_miss",
    ),
    "bariatric-04": BariatricFacts(
        variant="sleeve-gastrectomy",
        procedure="stand-alone laparoscopic sleeve gastrectomy",
        weight=121.6,
        program="none",
    ),
    "bariatric-05": BariatricFacts(
        procedure="open Roux-en-Y gastric bypass", weight=126.3, program="declined"
    ),
    "bariatric-06": BariatricFacts(weight=117.5, diabetes=False),
}

# --- Spinal cord stimulators (NCD 160.7, LCD L36204, article A57792): the history ---------

# The shared background cannot serve these cases as it is. Its 11 clinic notes read "No
# complaints" with acetaminophen as the only medication in every period an SCS history needs
# to cover, and its ischemic heart disease and coronary intervention would defer any trial
# lead. An SCS bundle therefore starts from the template with those removed and one DAST-10
# total rewritten (cases/spinal-cord-stimulators/PLAN.md lists every change). The template
# file, and the facet, PAP and bariatric bundles, are not touched.
DAST_TOTAL = f"Observation/{_P}-7379-2f446a673ba9"  # total score 2 on 2025-07-15, rewritten to 0
SCS_REMOVED: tuple[str, ...] = (
    # the 11 background notes and their twin reports (identical text)
    "DocumentReference/c51717c9-a06a-4067-edf7-7bedf17ccfa3",
    "DiagnosticReport/97583fea-5ec2-bc35-1be2-623c4647d832",
    "DocumentReference/7fc433c4-3564-21a6-91ec-bd0bd3789198",
    "DiagnosticReport/29af9190-d89b-c5a0-52a3-541edede0ab1",
    "DocumentReference/f4723689-faca-1ed4-6e53-7d891beca769",
    "DiagnosticReport/9b135c75-6e31-1c97-7b77-80c680c058c0",
    "DocumentReference/c42a95c7-ee92-41a6-d653-1f0f8ca6b198",
    "DiagnosticReport/0a26b754-d88f-a285-331a-79e2ded1e796",
    "DocumentReference/ab44a957-6c29-abeb-f2e2-bdc75e3bd474",
    "DiagnosticReport/3790268a-1756-917a-c596-68cf287f853c",
    "DocumentReference/f6ba0ccb-1752-effe-1bd8-851cd71b38a1",
    "DiagnosticReport/2d959dc6-9c73-5be5-37cd-96cfb0b7ffb4",
    "DocumentReference/ad8b6791-c5e2-872f-9f9d-901b0d809b9f",
    "DiagnosticReport/b5a9e535-f29f-fe02-c6d2-d8f8809e4047",
    "DocumentReference/98a61531-2b8f-f251-ab35-515a0bf416a2",
    "DiagnosticReport/703eb6d6-25bc-c714-f55a-b35aafdf18fb",
    "DocumentReference/8a804714-d88f-1efa-b374-09a2ded1640b",
    "DiagnosticReport/842f4763-17a4-7525-d5ef-0fabb9c9939d",
    "DocumentReference/8ce34cbb-7dcb-26e3-5bed-84b486df6b87",
    "DiagnosticReport/ea559e77-5506-0eef-e976-c0ffe97ff036",
    "DocumentReference/771daebe-064d-3403-07ae-f308433dd783",
    "DiagnosticReport/cf4a10ce-e5a8-9a4e-5891-aee3559aac77",
    # the cardiac coded resources
    f"Condition/{_P}-4f8f-f53efc54cfc9",
    f"Condition/{_P}-149d-10660d15eefd",
    f"Encounter/{_P}-919a-852251f5aff3",
    f"Encounter/{_P}-065e-55ee51cb2573",
    f"Encounter/{_P}-4c99-3aab45f0cd0d",
    f"Procedure/{_P}-c852-d61e8e01c819",
    f"Procedure/{_P}-63b7-95497c5dd0b4",
    f"Procedure/{_P}-f6e7-8385846bf707",
    f"Procedure/{_P}-1674-33cf97dc9b5c",
    f"Procedure/{_P}-5b70-175be4e23f55",
    f"Procedure/{_P}-c5ed-5ffe4c89afec",
    f"Observation/{_P}-3757-ee628716a210",
    f"MedicationRequest/{_P}-7172-b7842d7a2b47",
    f"MedicationRequest/{_P}-c0f4-15273b5391f3",
    f"MedicationRequest/{_P}-acd3-2a7639746de8",
    f"MedicationRequest/{_P}-d1b7-2b381bed9ed0",
)

# The settings a stimulator request may state, as the request chunk words them
# (precertly.judgment.models.SCS_SETTINGS; a test keeps the two equal).
SCS_SETTING_WORDS = {
    "11": "office",
    "19": "off-campus outpatient hospital",
    "21": "inpatient hospital",
    "22": "on-campus outpatient hospital",
    "24": "ambulatory surgical center",
}

# One chronic pain history for all six cases: onset, then four modalities that fail.
SCS_ONSET = date(2020, 6, 8)
SCS_PCP = date(2020, 9, 14)  # naproxen, referral to physical therapy
SCS_PT_START, SCS_PT_END = date(2020, 10, 5), date(2020, 12, 28)  # 12 weeks
SCS_MRI_2021 = date(2021, 1, 19)  # reported in the surgical follow-up note
SCS_SURGERY = date(2021, 4, 12)  # left L4-5 microdiscectomy
SCS_POSTOP_MRI = date(2021, 7, 6)  # reported in the surgical follow-up note
SCS_POSTOP = date(2021, 7, 13)
SCS_PAIN_CLINIC = date(2021, 9, 21)  # gabapentin, duloxetine
SCS_CBT_START, SCS_CBT_END = date(2022, 2, 7), date(2022, 4, 4)  # 8 weekly sessions
SCS_PAIN_CLINIC_2022 = date(2022, 3, 15)  # oxycodone with acetaminophen
SCS_MRI = date(2026, 4, 20)
SCS_EVAL = date(2026, 5, 18)  # pain clinic re-evaluation
SCS_BASELINE_NRS = 8
# Permanent variant: psychological evaluation and team conference, education, a 7-day trial,
# the surgical visit and the pre-anesthesia evaluation.
SCS_PERMANENT_DATES = dict(
    psych=date(2026, 6, 9),
    conference=date(2026, 6, 23),
    education=date(2026, 7, 7),
    trial=date(2026, 8, 4),
    trial_end=date(2026, 8, 11),
    plan=date(2026, 9, 1),
    preanesthesia=date(2026, 9, 28),
)
# Trial variant: the same steps, later, ending with the visit that requests the trial.
SCS_TRIAL_DATES = dict(
    psych=date(2026, 9, 8),
    conference=date(2026, 9, 15),
    education=date(2026, 9, 22),
    request=date(2026, 9, 29),
)

PAIN_TEXT = "Chronic low back and left leg pain after lumbar surgery"
CPT_SCS_TRIAL_LEADS = "63650"
CPT_SCS_GENERATOR = "63685"
SNOMED_CHRONIC_LOW_BACK_PAIN = "278860009"  # already used by the facet history

# Statements the notes make. The notes are written from these, and the evidence lists and
# the tests check the same strings, so a note and the fact a label cites cannot drift.
S_ONSET = "Low back and left leg pain since {onset}, now present for {months} months."
S_INTRACTABLE = (
    f"Pain remains intractable: {SCS_BASELINE_NRS}/10 on the {NRS} despite the treatments above."
)
S_LATE_EVAL = "spinal cord stimulation is being considered as a late resort"
S_NO_PREVIOUS = "No previous spinal cord stimulator trial or implant."
S_PHYSICAL = "Physical evaluation, in person today:"
S_PSYCH = "Psychological evaluation by a clinical psychologist"
S_TEAM = "Attendees: pain physician, spine surgeon, clinical psychologist, physical therapist."
S_CHRONIC_TEAM = "chronic intractable low back and left leg pain"
S_LATE_TEAM = "spinal cord stimulation is a late resort for this patient"
S_NO_SUBSTANCE = "no current or past substance use disorder"
S_COCAINE_SHORT = "active cocaine use disorder"
S_COCAINE = f"{S_COCAINE_SHORT}, moderate"
SCS_LAST_USE = date(2026, 9, 4)  # scs-02: the patient's report of last cocaine use
S_COCAINE_SCREEN = "Urine drug screen positive for cocaine metabolite on {day}."
S_ADDICTION = "The patient declined the referral to addiction medicine."
S_RISKS = "Extensive discussion of the risks and benefits of the therapy."
S_FIRST_TRIAL = "This would be the patient's first trial."
S_FIRST_PERMANENT = "This would be the patient's first permanent spinal cord stimulator."
S_EARLIER_IMPLANT = "Spinal cord stimulator generator in the right gluteal pocket (implanted 2023)."
S_ANALGESICS = "Analgesic medications unchanged during the trial."
S_FUNCTION = "walking tolerance rose from 10 minutes to 40 minutes"
S_POSITIVE = "Assessment: positive response to the trial."
S_POSITIVE_PLAN = "positive response to the trial"
S_TRIAL_PLACED = "temporary percutaneous epidural leads placed at T8-T10"
S_PT = "no improvement in pain or function"
S_NAPROXEN = "naproxen 500 mg twice daily from 2020-09-14 with minimal benefit"
S_POSTOP_RETURN = "then the back and leg pain returned to its earlier level"
S_CBT_RESULT = f"pain unchanged at {SCS_BASELINE_NRS}/10"
S_MEDS_2022 = (
    f"Pain remains {SCS_BASELINE_NRS}/10 on gabapentin 600 mg three times daily and duloxetine "
    "60 mg daily, both at full doses."
)

# Text of the coded resources, as the chunker shows it.
T_NAPROXEN = "Naproxen 500 mg oral tablet"
T_GABAPENTIN = "Gabapentin 300 mg oral capsule"
T_DULOXETINE = "Duloxetine 30 mg delayed-release oral capsule"
T_OXYCODONE = "Oxycodone hydrochloride 5 mg and acetaminophen 325 mg oral tablet"
T_PT = "Physical therapy, 12-week course"
T_SURGERY = "Left L4-5 microdiscectomy"
T_CBT = "Cognitive behavioral therapy for chronic pain, 8 weekly sessions"
T_PSYCH_PROCEDURE = "Psychological evaluation by a clinical psychologist"
T_TRIAL = "Spinal cord stimulator trial, temporary percutaneous epidural leads"
T_CONFERENCE = "Multidisciplinary pain conference"
T_COCAINE = "Cocaine use disorder"
T_MRI_CONCLUSION = (
    "Post-surgical changes at L4-5 with epidural scarring. No recurrent disc herniation. "
    "No evidence of infection."
)


@dataclass(frozen=True)
class ScsFacts:
    """What is true in a stimulator case's chart. Defaults are the clean permanent case."""

    variant: str = "permanent"
    leads: int = 2
    contacts: int = 16  # total across leads; every lead here has 8
    place_of_service: str = "24"
    trial_end_nrs: int = 2  # permanent: pain at the end of the 7-day trial, from 8/10
    substance: bool = False  # trial: an active cocaine use disorder is documented
    education: bool = True  # a visit with the risks-and-benefits discussion is documented
    earlier_implant: bool = False  # permanent: the pre-anesthesia note records a 2023 generator

    @property
    def setting(self) -> str:
        return SCS_SETTING_WORDS[self.place_of_service]

    @property
    def dates(self) -> dict[str, date]:
        return SCS_TRIAL_DATES if self.variant == "trial" else SCS_PERMANENT_DATES


def scs_relief(pre: int, post: int) -> int:
    """Percent reduction, asserted to be a whole number so the stated figure is exact."""
    percent, remainder = divmod((pre - post) * 100, pre)
    assert remainder == 0, (pre, post)
    return percent


def scs_design_checks(f: ScsFacts) -> None:
    """The design of a case, asserted, so a changed number cannot quietly move a label."""
    assert f.variant in ("trial", "permanent")
    assert f.place_of_service in SCS_SETTING_WORDS
    assert f.contacts == 8 * f.leads, "every lead has 8 contacts"
    assert _months_between(SCS_ONSET, SCS_EVAL) == 71
    assert (SCS_PT_END - SCS_PT_START).days == 84 and (SCS_CBT_END - SCS_CBT_START).days == 56
    assert f.variant == "trial" or f.leads in (2, 3)
    if f.variant == "permanent":
        assert (f.dates["trial_end"] - f.dates["trial"]).days == 7
        percent = scs_relief(SCS_BASELINE_NRS, f.trial_end_nrs)
        # the near miss sits exactly on the stated 50%; every other case clears it clearly
        assert percent == 50 if f.trial_end_nrs == 4 else percent >= 60, percent
    else:
        assert f.leads == 1 and f.contacts == 8
        assert not f.earlier_implant
    for day in [*f.dates.values(), SCS_ONSET, SCS_PCP, SCS_PT_START, SCS_PT_END, SCS_SURGERY]:
        assert day < REQUEST_DATE and day.weekday() < 5, day


def scs_background(background: list[Resource]) -> list[Resource]:
    """The template for a stimulator case: the 11 notes with their twins and the cardiac
    cluster removed, and the DAST-10 total of 2025-07-15 rewritten to 0."""
    removed = set(SCS_REMOVED)
    refs = {f"{r['resourceType']}/{r['id']}" for r in background}
    assert removed <= refs, sorted(removed - refs)
    kept = [r for r in background if f"{r['resourceType']}/{r['id']}" not in removed]
    assert len(SCS_REMOVED) == 38 and len(kept) == len(background) - 38 == 130
    rewritten = 0
    for resource in kept:
        if f"{resource['resourceType']}/{resource['id']}" == DAST_TOTAL:
            # the template has no item-level DAST-10 answers: the total stands alone
            assert resource["valueQuantity"]["value"] == 2 and "component" not in resource
            resource["valueQuantity"]["value"] = 0
            rewritten += 1
    assert rewritten == 1
    return kept


def _scs_leads(f: ScsFacts) -> str:
    return f"{f.leads} percutaneous lead{'s' if f.leads > 1 else ''} ({f.contacts} contacts)"


def _scs_medication(
    med_id: str, text: str, when: date, status: str, dosage: str, encounter: str
) -> Resource:
    return {
        "resourceType": "MedicationRequest",
        "id": med_id,
        "status": status,
        "intent": "order",
        "medicationCodeableConcept": {"text": text},
        "authoredOn": when.isoformat(),
        "encounter": {"reference": f"Encounter/{encounter}"},
        "dosageInstruction": [{"text": dosage}],
        "reasonReference": [{"reference": "Condition/chronic-pain"}],
    }


def _scs_procedure(
    procedure_id: str,
    text: str,
    encounter: str,
    *,
    when: date | None = None,
    period: tuple[date, date] | None = None,
    coding: list[dict[str, str]] | None = None,
    clock: str | None = None,
) -> Resource:
    resource: Resource = {
        "resourceType": "Procedure",
        "id": procedure_id,
        "status": "completed",
        "code": {"coding": coding, "text": text} if coding else {"text": text},
    }
    if period:
        resource["performedPeriod"] = {"start": period[0].isoformat(), "end": period[1].isoformat()}
    else:
        assert when is not None
        resource["performedDateTime"] = _at(when, clock)
    resource["encounter"] = {"reference": f"Encounter/{encounter}"}
    resource["reasonReference"] = [{"reference": "Condition/chronic-pain"}]
    return resource


def _scs_event_date(resource: Resource) -> str:
    keys = ("effectiveDateTime", "authoredOn", "onsetDateTime", "date", "performedDateTime")
    for key in (*keys, "recordedDate"):
        if key in resource:
            return resource[key][:10]
    if "performedPeriod" in resource:
        return resource["performedPeriod"]["start"]
    return resource["period"]["start"]


def _scs_order(resource: Resource) -> tuple[str, str]:
    return _scs_event_date(resource), resource["resourceType"]


def scs_history(f: ScsFacts) -> list[Resource]:
    scs_design_checks(f)
    d = f.dates
    trial = f.variant == "trial"
    months = _months_between(SCS_ONSET, SCS_EVAL)
    onset = S_ONSET.format(onset=SCS_ONSET.isoformat(), months=months)
    setting = f.setting
    resources: list[Resource] = []

    def visit(
        when: date,
        enc_id: str,
        kind: str,
        note_id: str,
        title: str,
        text: str,
        start: date | None = None,
    ) -> None:
        """An encounter and the note written at its end; a course starts before its note."""
        resources.extend(
            [
                _encounter(enc_id, start or when, kind, when if start else None),
                _note(note_id, when, title, text, enc_id),
            ]
        )

    resources.append(
        {
            "resourceType": "Condition",
            "id": "chronic-pain",
            "clinicalStatus": {"coding": [{"code": "active"}]},
            "verificationStatus": {"coding": [{"code": "confirmed"}]},
            "code": {
                "coding": [
                    {
                        "system": "http://snomed.info/sct",
                        "code": SNOMED_CHRONIC_LOW_BACK_PAIN,
                        "display": "Chronic low back pain (finding)",
                    }
                ],
                "text": PAIN_TEXT,
            },
            "onsetDateTime": SCS_ONSET.isoformat(),
            "recordedDate": SCS_PCP.isoformat(),
        }
    )

    # --- the four modalities that fail ---------------------------------------------------
    visit(
        SCS_PCP,
        "enc-pcp",
        "Primary care visit",
        "note-pcp",
        "Primary care visit",
        f"""Chief complaint: low back pain with pain down the left leg.
History: low back pain that began on {SCS_ONSET.isoformat()} after lifting, radiating down the back of the left leg to the foot. No weakness. No bowel or bladder changes.
Exam: tenderness over the lower lumbar paraspinal muscles. Straight-leg raise positive on the left. Strength 5/5 in both legs.
Plan: naproxen 500 mg twice daily with food. Referral to physical therapy.
""",
    )
    resources.append(
        _scs_medication(
            "naproxen",
            T_NAPROXEN,
            SCS_PCP,
            "completed",
            "1 tablet twice daily with food",
            "enc-pcp",
        )
    )
    visit(
        SCS_PT_END,
        "enc-pt",
        "Physical therapy",
        "note-pt-discharge",
        "Physical therapy discharge summary",
        f"""Course: {SCS_PT_START.isoformat()} to {SCS_PT_END.isoformat()} (12 weeks), 22 of 24 scheduled sessions attended.
Treatment: core stabilization, flexion-based exercise, manual therapy and a home exercise program, performed as instructed.
Outcome: {S_PT}. Pain {SCS_BASELINE_NRS}/10 on the {NRS} at discharge, unchanged from the start of therapy.
Recommendation: return to the referring physician.
""",
        start=SCS_PT_START,
    )
    resources.append(
        _scs_procedure(
            "physical-therapy-course",
            T_PT,
            "enc-pt",
            period=(SCS_PT_START, SCS_PT_END),
            coding=[
                {
                    "system": "http://snomed.info/sct",
                    "code": "91251008",
                    "display": "Physical therapy procedure (regime/therapy)",
                }
            ],
        )
    )
    visit(
        SCS_POSTOP,
        "enc-postop",
        "Spine surgery follow-up",
        "note-postop",
        "Spine surgery follow-up",
        f"""History: low back and left leg pain since {SCS_ONSET.isoformat()}. Lumbar MRI of {SCS_MRI_2021.isoformat()} showed a left paracentral disc herniation at L4-5 with compression of the left L5 root. Left L4-5 microdiscectomy was performed on {SCS_SURGERY.isoformat()}.
Course: relief for about 6 weeks, {S_POSTOP_RETURN}. Lumbar MRI of {SCS_POSTOP_MRI.isoformat()}: no recurrent herniation or compressive lesion; post-surgical scarring at L4-5.
Assessment: persistent pain after lumbar surgery, with no further surgical target.
Plan: referral to the pain clinic.
""",
    )
    resources.append(
        _scs_procedure(
            "lumbar-microdiscectomy",
            T_SURGERY,
            "enc-postop",
            when=SCS_SURGERY,
        )
    )
    visit(
        SCS_PAIN_CLINIC,
        "enc-pain-clinic-2021",
        "Pain clinic visit",
        "note-pain-clinic-2021",
        "Pain clinic initial visit",
        f"""History: low back and left leg pain since {SCS_ONSET.isoformat()}, {SCS_BASELINE_NRS}/10 on the {NRS}. Treatments so far: {S_NAPROXEN} (stopped); physical therapy for 12 weeks with {S_PT}; left L4-5 microdiscectomy on {SCS_SURGERY.isoformat()} with 6 weeks of relief.
Exam: tenderness over the lower lumbar paraspinal muscles; reduced sensation over the left lateral calf; strength 5/5.
Plan: gabapentin, increased to 600 mg three times daily, and duloxetine, increased to 60 mg daily. Review in 6 months.
""",
    )
    resources += [
        _scs_medication(
            "gabapentin",
            T_GABAPENTIN,
            SCS_PAIN_CLINIC,
            "active",
            "increased to 600 mg three times daily",
            "enc-pain-clinic-2021",
        ),
        _scs_medication(
            "duloxetine",
            T_DULOXETINE,
            SCS_PAIN_CLINIC,
            "active",
            "increased to 60 mg once daily",
            "enc-pain-clinic-2021",
        ),
    ]
    visit(
        SCS_PAIN_CLINIC_2022,
        "enc-pain-clinic-2022",
        "Pain clinic visit",
        "note-pain-clinic-2022",
        "Pain clinic follow-up",
        f"""{S_MEDS_2022} Cognitive behavioral therapy for chronic pain was started on {SCS_CBT_START.isoformat()} (8 weekly sessions).
Plan: add oxycodone 5 mg with acetaminophen 325 mg, up to 4 tablets a day. Continue gabapentin and duloxetine.
""",
    )
    resources.append(
        _scs_medication(
            "oxycodone-apap",
            T_OXYCODONE,
            SCS_PAIN_CLINIC_2022,
            "active",
            "1 tablet up to 4 times daily as needed",
            "enc-pain-clinic-2022",
        )
    )
    visit(
        SCS_CBT_END,
        "enc-cbt",
        "Cognitive behavioral therapy",
        "note-cbt-discharge",
        "Cognitive behavioral therapy discharge summary",
        f"""Course: {SCS_CBT_START.isoformat()} to {SCS_CBT_END.isoformat()}, 8 weekly sessions, all attended.
Treatment: pain coping skills, pacing and relaxation training, practiced between sessions.
Outcome: skills learned and used; {S_CBT_RESULT} on the {NRS}.
""",
        start=SCS_CBT_START,
    )
    resources.append(
        _scs_procedure("cbt-course", T_CBT, "enc-cbt", period=(SCS_CBT_START, SCS_CBT_END))
    )

    # --- the pain clinic re-evaluation ---------------------------------------------------
    mri_report = f"""{SCS_MRI.isoformat()}
MRI lumbar spine without contrast

Indication: chronic low back and left leg pain after lumbar surgery.
Findings: post-surgical changes at L4-5 with epidural scarring. No recurrent disc herniation. No significant central canal or foraminal narrowing at any level. No evidence of discitis, osteomyelitis or other infection. No mass.
Impression: {T_MRI_CONCLUSION}
"""
    resources += [
        _encounter("enc-mri", SCS_MRI, "Imaging visit"),
        {
            "resourceType": "DiagnosticReport",
            "id": "mri-lumbar",
            "status": "final",
            "code": {"text": "MRI lumbar spine without contrast"},
            "effectiveDateTime": SCS_MRI.isoformat(),
            "encounter": {"reference": "Encounter/enc-mri"},
            "conclusion": T_MRI_CONCLUSION,
            "presentedForm": [_attachment(mri_report)],
        },
    ]
    visit(
        SCS_EVAL,
        "enc-pain-clinic-eval",
        "Pain clinic evaluation",
        "note-pain-clinic-eval",
        "Pain clinic re-evaluation",
        f"""History: {onset} The pain is constant, worse with standing and walking, and the patient can walk for only 10 minutes. It followed a lifting injury and persisted after a left L4-5 microdiscectomy on {SCS_SURGERY.isoformat()}.

Treatments tried: {S_NAPROXEN}; physical therapy for 12 weeks with {S_PT}; left L4-5 microdiscectomy with 6 weeks of relief; gabapentin 600 mg three times daily and duloxetine 60 mg daily since {SCS_PAIN_CLINIC.isoformat()} and oxycodone 5 mg with acetaminophen 325 mg, up to 4 tablets a day, since {SCS_PAIN_CLINIC_2022.isoformat()}, with the pain still {SCS_BASELINE_NRS}/10; cognitive behavioral therapy for chronic pain (8 sessions, 2022) with {S_CBT_RESULT}.

{S_INTRACTABLE}

Past history: left L4-5 microdiscectomy on {SCS_SURGERY.isoformat()}. {S_NO_PREVIOUS}

{S_PHYSICAL} lumbar paraspinal tenderness, reduced sensation over the left lateral calf, strength 5/5 in both legs, straight-leg raise positive on the left, no signs of infection.

Imaging: lumbar MRI of {SCS_MRI.isoformat()}: post-surgical changes at L4-5, no compressive lesion, no infection.

Assessment: {PAIN_TEXT.lower()}, intractable to medication, physical therapy, surgery and psychological therapy.
Plan: {S_LATE_EVAL}. Referral for psychological evaluation and review by the multidisciplinary pain team.
""",
    )
    resources.append(
        _score("pain-nrs-baseline", SCS_EVAL, SCS_BASELINE_NRS, "enc-pain-clinic-eval")
    )

    # --- psychological evaluation and the team conference --------------------------------
    if f.substance:
        substance = (
            f"Substance use assessment: {S_COCAINE}. {S_COCAINE_SCREEN.format(day=d['psych'].isoformat())} "
            "Oxycodone also detected, consistent with the prescription. "
            f"The patient reports last use on {SCS_LAST_USE.isoformat()}. No alcohol misuse."
        )
        recommendation = (
            "Psychological recommendation: defer until the substance use disorder is treated."
        )
    else:
        substance = (
            f"Substance use assessment: {S_NO_SUBSTANCE}. Urine drug screen of "
            f"{d['psych'].isoformat()} consistent with the prescribed oxycodone, no other "
            "substance. No alcohol misuse."
        )
        recommendation = (
            "Psychological recommendation: no psychological contraindication to spinal cord "
            "stimulation."
        )
    visit(
        d["psych"],
        "enc-psych-eval",
        "Psychological evaluation",
        "note-psych-eval",
        "Psychological evaluation",
        f"""{S_PSYCH}: structured clinical interview and review of the records.
Mood and anxiety: no major depressive disorder, anxiety disorder or somatic symptom disorder. Mood is appropriate to the chronic pain.
Pain-related coping: good; uses the pacing and relaxation skills from the 2022 therapy course. No cognitive impairment.
{substance}
{recommendation}
""",
    )
    resources.append(
        _scs_procedure(
            "psychological-evaluation", T_PSYCH_PROCEDURE, "enc-psych-eval", when=d["psych"]
        )
    )
    if f.substance:
        resources.append(
            {
                "resourceType": "Condition",
                "id": "cocaine-use-disorder",
                "clinicalStatus": {"coding": [{"code": "active"}]},
                "verificationStatus": {"coding": [{"code": "confirmed"}]},
                "code": {"text": T_COCAINE},
                "recordedDate": d["psych"].isoformat(),
            }
        )
        team = (
            f"Psychology reports an {S_COCAINE_SHORT}. Refer to addiction medicine first; "
            "reconsider the trial after treatment."
        )
    else:
        team = "Proceed to a spinal cord stimulator trial."
    visit(
        d["conference"],
        "enc-team-conference",
        T_CONFERENCE,
        "note-team-conference",
        T_CONFERENCE,
        f"""{S_TEAM}
Reviewed: the pain clinic evaluation of {SCS_EVAL.isoformat()} (physical evaluation), the psychological evaluation of {d["psych"].isoformat()}, the lumbar MRI of {SCS_MRI.isoformat()} and the treatments tried.
Diagnosis: {S_CHRONIC_TEAM} after lumbar surgery.
Team assessment: {S_LATE_TEAM}, after medication, physical therapy, surgery and psychological therapy.
Recommendation: {team}
""",
    )
    if f.education:
        visit(
            d["education"],
            "enc-education",
            "Spinal cord stimulation education visit",
            "note-education",
            "Spinal cord stimulation education visit",
            f"""Education visit with the pain physician and the nurse. How a trial and a permanent implant work, the expected benefit and its limits (no guarantee of relief), and the risks: infection, bleeding, lead migration, nerve injury, device malfunction and the need for further surgery. Alternatives reviewed. Written material given. All questions answered.
{S_RISKS} Signed consent for the trial.
""",
        )

    if trial:
        extra = (
            f" {S_COCAINE_SCREEN.format(day=d['psych'].isoformat())} {S_ADDICTION}"
            if f.substance
            else ""
        )
        visit(
            d["request"],
            "enc-trial-request",
            "Pain clinic visit",
            "note-trial-request",
            "Pain clinic visit, request for a spinal cord stimulator trial",
            f"""Follow-up after the multidisciplinary pain conference of {d["conference"].isoformat()}. Pain {SCS_BASELINE_NRS}/10 on the {NRS}.{extra}
{S_FIRST_TRIAL}
Plan: spinal cord stimulator trial with {_scs_leads(f)}. Planned setting: {setting}. Request prior authorization.
""",
        )
        return sorted(resources, key=_scs_order)

    # --- permanent: the trial, the result, the surgical visit ----------------------------
    percent = scs_relief(SCS_BASELINE_NRS, f.trial_end_nrs)
    visit(
        d["trial"],
        "enc-trial",
        "Spinal cord stimulator trial",
        "note-trial-procedure",
        "Procedure note, spinal cord stimulator trial",
        f"""Indication: {S_CHRONIC_TEAM} after lumbar surgery.
Procedure: {S_TRIAL_PLACED} under fluoroscopy and connected to an external pulse generator. Local anesthetic. No complications.
Pain before the procedure: {SCS_BASELINE_NRS}/10 on the {NRS}.
Plan: trial for 7 days, then removal of the leads.
""",
    )
    resources += [
        _scs_procedure(
            "scs-trial",
            T_TRIAL,
            "enc-trial",
            when=d["trial"],
            coding=[{"system": CPT, "code": CPT_SCS_TRIAL_LEADS}],
            clock=DURING,
        ),
        _score("pain-pre-trial", d["trial"], SCS_BASELINE_NRS, "enc-trial", clock=BEFORE),
    ]
    visit(
        d["trial_end"],
        "enc-trial-result",
        "Spinal cord stimulator trial, end of trial",
        "note-trial-result",
        "Spinal cord stimulator trial, end of trial visit",
        f"""The temporary leads were removed today after the 7-day trial.
Pain: {SCS_BASELINE_NRS}/10 before the trial and {f.trial_end_nrs}/10 at the end of the 7-day trial on the {NRS}: ({SCS_BASELINE_NRS} - {f.trial_end_nrs}) / {SCS_BASELINE_NRS} = {percent}% reduction in target pain (low back and left leg pain).
{S_ANALGESICS}
Function: {S_FUNCTION}; the patient sat through a 60-minute meal.
{S_POSITIVE}
Plan: permanent implantation.
""",
    )
    resources.append(_score("pain-end-trial", d["trial_end"], f.trial_end_nrs, "enc-trial-result"))
    first = f"{S_FIRST_PERMANENT}\n" if not f.earlier_implant else ""
    visit(
        d["plan"],
        "enc-permanent-plan",
        "Surgical visit",
        "note-permanent-plan",
        "Surgical visit, permanent implantation",
        f"""Trial of {d["trial"].isoformat()} to {d["trial_end"].isoformat()}: {S_POSITIVE_PLAN}.
{first}Plan: permanent implantation of {_scs_leads(f)} with an implantable pulse generator. Planned setting: {setting}. Request prior authorization.
""",
    )
    history = f"Past surgical history: left L4-5 microdiscectomy ({SCS_SURGERY.isoformat()})."
    if f.earlier_implant:
        history += f" {S_EARLIER_IMPLANT}"
    visit(
        d["preanesthesia"],
        "enc-preanesthesia",
        "Pre-anesthesia evaluation",
        "note-preanesthesia",
        "Pre-anesthesia evaluation",
        f"""Planned procedure: permanent spinal cord stimulator implantation.
{history}
Medications: gabapentin, duloxetine, oxycodone with acetaminophen. No anticoagulants. No known allergies.
Exam: airway Mallampati class 2; heart and lungs normal. ASA class 2.
""",
    )
    return sorted(resources, key=_scs_order)


S_RELIEF = "({pre} - {post}) / {pre} = {percent}% reduction in target pain"


def _scs_relief_sentence(f: ScsFacts) -> str:
    post = f.trial_end_nrs
    return S_RELIEF.format(
        pre=SCS_BASELINE_NRS, post=post, percent=scs_relief(SCS_BASELINE_NRS, post)
    )


def scs_label_phrases(f: ScsFacts) -> dict[str, dict[str, list[str]]]:
    """For each note-backed criterion of the case's variant: every resource that states the
    fact the label rests on, and the statements it must contain.

    A statement is a substring of the decoded note, or of the chunk text of a coded resource.
    The lists are complete and exact: a test checks that every cited resource contains its
    statements and that a note carrying a criterion's marker (SCS_MARKERS) is cited. For a
    criterion labeled insufficient because nothing addresses it, the list holds the notes that
    would carry the fact and no statements: a test checks that they do not.
    """
    n = "DocumentReference/{}".format
    d = f.dates
    onset = S_ONSET.format(onset=SCS_ONSET.isoformat(), months=_months_between(SCS_ONSET, SCS_EVAL))
    phrases: dict[str, dict[str, list[str]]] = {
        "late-resort-chronic-intractable-pain": {
            "Condition/chronic-pain": [PAIN_TEXT],
            n("note-pain-clinic-eval"): [onset, S_INTRACTABLE, S_LATE_EVAL],
            n("note-team-conference"): [S_CHRONIC_TEAM, S_LATE_TEAM],
        },
        "other-modalities-tried-or-unsuitable": {
            "MedicationRequest/naproxen": [T_NAPROXEN],
            "MedicationRequest/gabapentin": [T_GABAPENTIN],
            "MedicationRequest/duloxetine": [T_DULOXETINE],
            "MedicationRequest/oxycodone-apap": [T_OXYCODONE],
            "Procedure/physical-therapy-course": [T_PT],
            "Procedure/lumbar-microdiscectomy": [T_SURGERY],
            "Procedure/cbt-course": [T_CBT],
            n("note-pt-discharge"): [S_PT],
            n("note-postop"): [S_POSTOP_RETURN],
            n("note-pain-clinic-2021"): [S_NAPROXEN],
            n("note-cbt-discharge"): [S_CBT_RESULT],
            n("note-pain-clinic-2022"): [S_MEDS_2022],
            n("note-pain-clinic-eval"): [S_INTRACTABLE],
        },
        "multidisciplinary-screening": {
            "Encounter/enc-team-conference": [T_CONFERENCE],
            n("note-team-conference"): [S_TEAM],
            n("note-psych-eval"): [S_PSYCH],
            n("note-pain-clinic-eval"): [S_PHYSICAL],
        },
        "screening-documented-in-chart": {
            n("note-pain-clinic-eval"): [onset, S_PHYSICAL],
            n("note-psych-eval"): [S_PSYCH],
            n("note-team-conference"): [S_TEAM],
        },
        "diagnosis-and-prior-therapies-in-record": {
            "Condition/chronic-pain": [PAIN_TEXT],
            "DiagnosticReport/mri-lumbar": [T_MRI_CONCLUSION],
            n("note-pain-clinic-eval"): [onset, S_INTRACTABLE],
            n("note-pt-discharge"): [S_PT],
            n("note-postop"): [S_POSTOP_RETURN],
            n("note-pain-clinic-2021"): [S_NAPROXEN],
            n("note-cbt-discharge"): [S_CBT_RESULT],
            n("note-pain-clinic-2022"): [S_MEDS_2022],
        },
    }
    if f.variant == "trial":
        if f.substance:
            substance = {
                "Condition/cocaine-use-disorder": [T_COCAINE],
                n("note-psych-eval"): [S_COCAINE],
                n("note-team-conference"): [S_COCAINE_SHORT],
                n("note-trial-request"): [
                    S_COCAINE_SCREEN.format(day=d["psych"].isoformat()),
                    S_ADDICTION,
                ],
            }
        else:
            substance = {n("note-psych-eval"): [S_NO_SUBSTANCE]}
        education = (
            {n("note-education"): [S_RISKS]}
            if f.education
            else {n("note-team-conference"): [], n("note-trial-request"): []}
        )
        phrases |= {
            "no-active-substance-abuse": substance,
            "patient-education-and-disclosure": education,
            "psychological-screening": {
                "Procedure/psychological-evaluation": [T_PSYCH_PROCEDURE],
                n("note-psych-eval"): [S_PSYCH],
            },
            "trials-max-2-per-region-lifetime": {
                n("note-pain-clinic-eval"): [S_NO_PREVIOUS],
                n("note-trial-request"): [S_FIRST_TRIAL],
            },
        }
        return phrases
    score = "Value: {} {{score}}".format
    pre, post = SCS_BASELINE_NRS, f.trial_end_nrs
    relief = _scs_relief_sentence(f)
    phrases |= {
        "trial-pain-relief-before-permanent": {
            "Procedure/scs-trial": [T_TRIAL],
            "Observation/pain-pre-trial": [score(pre), f"Date: {d['trial'].isoformat()}"],
            "Observation/pain-end-trial": [score(post), f"Date: {d['trial_end'].isoformat()}"],
            n("note-trial-procedure"): [S_TRIAL_PLACED],
            n("note-trial-result"): [relief],
        },
        "trial-success-50pct-and-function": {
            "Observation/pain-pre-trial": [score(pre)],
            "Observation/pain-end-trial": [score(post)],
            n("note-trial-result"): [relief, S_ANALGESICS, S_FUNCTION],
        },
        "positive-trial-documented": {
            n("note-trial-result"): [S_POSITIVE],
            n("note-permanent-plan"): [S_POSITIVE_PLAN],
        },
        "one-permanent-stimulator-per-lifetime": {
            n("note-pain-clinic-eval"): [S_NO_PREVIOUS],
            **(
                {n("note-preanesthesia"): [S_EARLIER_IMPLANT]}
                if f.earlier_implant
                else {n("note-permanent-plan"): [S_FIRST_PERMANENT]}
            ),
        },
    }
    return phrases


# A statement that only one criterion's resources should make: a note that carries it must be
# cited by that criterion (tested). Only the criteria of the case's variant are checked.
SCS_MARKERS: dict[str, str] = {
    "late-resort-chronic-intractable-pain": r"late resort",
    "no-active-substance-abuse": r"substance use|cocaine",
    "patient-education-and-disclosure": r"risks and benefits",
    "psychological-screening": r"Psychological evaluation by a clinical psychologist",
    "trials-max-2-per-region-lifetime": r"first trial|No previous spinal cord stimulator trial",
    "trial-success-50pct-and-function": r"reduction in target pain",
    "positive-trial-documented": r"positive response",
    "one-permanent-stimulator-per-lifetime": (
        r"No previous spinal cord stimulator|first permanent spinal cord stimulator"
        r"|Spinal cord stimulator generator"
    ),
}


def scs_label_evidence(f: ScsFacts) -> dict[str, list[str]]:
    """For each scored criterion of the case's variant, every resource or request field that
    states the fact. The request-stage criteria cite the request, as GUIDELINES rule 7 says."""
    evidence = {criterion: list(refs) for criterion, refs in scs_label_phrases(f).items()}
    evidence["leads-max-2-or-16-contacts"] = ["request.leads", "request.contacts"]
    if f.variant == "trial":
        evidence["repeat-trial-extenuating-circumstances"] = []  # not_applicable: nothing cited
    else:
        evidence["permanent-placed-in-asc-or-hospital"] = ["request.place_of_service"]
    return evidence


SCS_CASES: dict[str, ScsFacts] = {
    "scs-01": ScsFacts(),
    "scs-02": ScsFacts(variant="trial", leads=1, contacts=8, place_of_service="22", substance=True),
    "scs-03": ScsFacts(leads=3, contacts=24, place_of_service="22", trial_end_nrs=4),
    "scs-04": ScsFacts(variant="trial", leads=1, contacts=8, education=False),
    "scs-05": ScsFacts(earlier_implant=True),
    "scs-06": ScsFacts(place_of_service="11"),
}

CASES: dict[str, Facts | PapFacts | BariatricFacts | ScsFacts] = {
    **FACET_CASES,
    **PAP_CASES,
    **BARIATRIC_CASES,
    **SCS_CASES,
}


def label_evidence(facts: Facts | PapFacts | BariatricFacts | ScsFacts) -> dict[str, list[str]]:
    if isinstance(facts, ScsFacts):
        return scs_label_evidence(facts)
    if isinstance(facts, BariatricFacts):
        return bariatric_label_evidence(facts)
    if isinstance(facts, PapFacts):
        return pap_label_evidence(facts)
    return facet_label_evidence(facts)


def history(facts: Facts | PapFacts | BariatricFacts | ScsFacts) -> list[Resource]:
    if isinstance(facts, ScsFacts):
        return scs_history(facts)
    if isinstance(facts, BariatricFacts):
        return bariatric_history(facts)
    return pap_history(facts) if isinstance(facts, PapFacts) else facet_history(facts)


def case_path(case_id: str) -> Path:
    """The case file: evals/cases/<facet|pap|bariatric|spinal-cord-stimulators>/<case id>.yaml."""
    facts = CASES[case_id]
    folder = (
        "spinal-cord-stimulators"
        if isinstance(facts, ScsFacts)
        else "bariatric"
        if isinstance(facts, BariatricFacts)
        else "pap"
        if isinstance(facts, PapFacts)
        else "facet"
    )
    return EVALS / "cases" / folder / f"{case_id}.yaml"


_CRITERION = re.compile(r"^  - criterion: (\S+)$")
_OUTCOME = re.compile(r"^    outcome: (\S+)$")
_EVIDENCE = re.compile(r"^    evidence: \[.*\]$")


def with_generated_evidence(case_id: str) -> str:
    """The case file's text with each label's evidence list set from label_evidence."""
    generated = label_evidence(CASES[case_id])
    lines = case_path(case_id).read_text(encoding="utf-8").splitlines()
    criterion = outcome = None
    for i, line in enumerate(lines):
        if match := _CRITERION.match(line):
            criterion, outcome = match.group(1), None
        elif match := _OUTCOME.match(line):
            outcome = match.group(1)
        elif criterion and _EVIDENCE.match(line):
            evidence = [] if outcome == "not_applicable" else generated[criterion]
            lines[i] = f"    evidence: [{', '.join(evidence)}]"
    return "\n".join(lines) + "\n"


def build(case_id: str) -> dict[str, Any]:
    background = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    facts = CASES[case_id]
    if isinstance(facts, BariatricFacts):
        background = bariatric_background(background, facts)
    elif isinstance(facts, ScsFacts):
        background = scs_background(background)
    patient_id = next(r["id"] for r in background if r["resourceType"] == "Patient")
    added = history(facts)
    for resource in added:
        resource["subject"] = {"reference": f"Patient/{patient_id}"}
    resources = background + added
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [{"fullUrl": f"{r['resourceType']}/{r['id']}", "resource": r} for r in resources],
    }


def dumps(value: Any) -> str:
    """One resource or entry per line: stable output and readable diffs."""
    if isinstance(value, dict):
        entries = ",\n".join(json.dumps(e, ensure_ascii=False) for e in value["entry"])
        head = json.dumps({k: v for k, v in value.items() if k != "entry"})[:-1]
        return f'{head}, "entry": [\n{entries}\n]}}\n'
    return "[\n" + ",\n".join(json.dumps(r, ensure_ascii=False) for r in value) + "\n]\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if a bundle is stale")
    commands = parser.add_subparsers(dest="command")
    trim_parser = commands.add_parser("trim", help="rebuild the template from Synthea output")
    trim_parser.add_argument("fhir_dir", type=Path)
    args = parser.parse_args()

    if args.command == "trim":
        resources = trim(args.fhir_dir)
        TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
        TEMPLATE.write_text(dumps(resources), encoding="utf-8")
        print(f"wrote {TEMPLATE.relative_to(EVALS.parent)}: {len(resources)} resources")
        return 0

    stale = []
    BUNDLES.mkdir(exist_ok=True)
    for case_id in CASES:
        # the bundle, and the evidence lists in the case file, both come from the facts
        outputs = {
            BUNDLES / f"{case_id}.json": dumps(build(case_id)),
            case_path(case_id): with_generated_evidence(case_id),
        }
        for path, text in outputs.items():
            if args.check:
                if not path.exists() or path.read_text(encoding="utf-8") != text:
                    stale.append(str(path.relative_to(EVALS.parent)))
            elif not path.exists() or path.read_text(encoding="utf-8") != text:
                path.write_text(text, encoding="utf-8")
                print(f"wrote {path.relative_to(EVALS.parent)}")
    if stale:
        print(f"stale, rerun build_cases.py: {', '.join(stale)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
