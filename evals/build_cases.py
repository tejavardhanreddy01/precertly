"""Build the eval case bundles from a template patient. Nothing here is hand-edited JSON.

    uv run python evals/build_cases.py                 # write evals/bundles/<case>.json
    uv run python evals/build_cases.py --check         # fail if a committed bundle is stale
    uv run python evals/build_cases.py trim <fhir-dir> # rebuild evals/template/patient.json

Two steps:

1. trim (rare): take one patient from a pinned Synthea v4.0.0 run and keep a few hundred
   resources as realistic, unrelated background. The result is committed, so building
   cases needs neither Java nor Synthea. To regenerate the input:

       SYNTHEA_REFERENCE_DATE=20261001 SYNTHEA_OUTPUT=<dir> data/synthea/generate.sh 100

2. build: add the facet history to that background from a set of facts, one set per case.
   A case differs from its variant's clean baseline only in the facts it overrides, so the
   notes, observations and procedures in a bundle always agree with each other, except
   where a case is about a disagreement.

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

CASES: dict[str, Facts] = {
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


def _encounter(encounter_id: str, when: date, kind: str, end: date | None = None) -> Resource:
    return {
        "resourceType": "Encounter",
        "id": encounter_id,
        "status": "finished",
        "class": {"code": "AMB"},
        "type": [{"text": kind}],
        "period": {"start": when.isoformat(), "end": (end or when).isoformat()},
    }


def _score(
    observation_id: str, when: date, value: int, encounter: str, scale: str = NRS
) -> Resource:
    code: dict[str, Any] = {"text": f"Pain severity, {scale}"}
    if scale == NRS:
        code["coding"] = [{"system": "http://loinc.org", "code": PAIN_SCORE_LOINC}]
    return {
        "resourceType": "Observation",
        "id": observation_id,
        "status": "final",
        "category": [{"coding": [{"code": "survey"}]}],
        "code": code,
        "valueQuantity": {"value": value, "unit": "{score}" if scale == NRS else "mm"},
        "effectiveDateTime": when.isoformat(),
        "encounter": {"reference": f"Encounter/{encounter}"},
    }


def _odi(observation_id: str, when: date, value: int, encounter: str) -> Resource:
    return {
        "resourceType": "Observation",
        "id": observation_id,
        "status": "final",
        "category": [{"coding": [{"code": "survey"}]}],
        "code": {"text": f"{ODI} score"},
        "valueQuantity": {"value": value, "unit": "%"},
        "effectiveDateTime": when.isoformat(),
        "encounter": {"reference": f"Encounter/{encounter}"},
    }


def _procedure(procedure_id: str, when: date, text: str, encounter: str) -> Resource:
    return {
        "resourceType": "Procedure",
        "id": procedure_id,
        "status": "completed",
        "code": {"text": text},
        "performedDateTime": when.isoformat(),
        "encounter": {"reference": f"Encounter/{encounter}"},
        "reasonReference": [{"reference": "Condition/chronic-low-back-pain"}],
        "bodySite": [{"text": f"Lumbar facet joints {LEVELS}, bilateral"}],
    }


def _block(number: int, when: date, pre: int, post: int, post_vas: int | None) -> list[Resource]:
    encounter = f"enc-mbb{number}"
    if post_vas is None:
        after = f"""- 30 minutes after the block: {post}/10 on the same scale, with the same provocative movements.
- Relief of index pain, from today's pre-block score: {calculation(pre, post)}."""
        post_score = _score(f"pain-post-mbb{number}", when, post, encounter)
    else:
        after = f"- 30 minutes after the block: {post_vas} mm on the {VAS}, with the same provocative movements."
        post_score = _score(f"pain-post-mbb{number}", when, post_vas, encounter, scale=VAS)
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
        ),
        _score(f"pain-pre-mbb{number}", when, pre, encounter),
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
        ),
        _score("pain-pre-rfa1", facts.rfa1, pre, "enc-rfa1"),
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

Today: pain {facts.baseline_nrs}/10 on the {NRS}, predominantly axial, across the lower lumbar spine, worse with extension and rotation, not radiating below the buttocks. The patient again cannot stand longer than 15 minutes.
Neurological exam: strength 5/5 in both legs, sensation intact, reflexes symmetric, straight-leg raise negative on both sides. No radicular symptoms and no neurogenic claudication.
No new trauma, fever or weight loss, and no change on exam to suggest a cause of pain other than the facet joints; the lumbar MRI of {facts.mri.isoformat()} showed no fracture, tumor, infection or significant deformity. No spine surgery since the last visit. No fibromyalgia or widespread pain.

Plan: request prior authorization for repeat thermal radiofrequency ablation at the same levels ({LEVELS}, lumbar region, two levels). No other injections are planned for that day.
"""
    resources += [
        _encounter("enc-repeat-rfa-planning", facts.planning, "Pain clinic follow-up"),
        _score(
            "pain-nrs-recurrence", facts.planning, facts.baseline_nrs, "enc-repeat-rfa-planning"
        ),
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


def build(case_id: str) -> dict[str, Any]:
    background = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    patient_id = next(r["id"] for r in background if r["resourceType"] == "Patient")
    history = facet_history(CASES[case_id])
    for resource in history:
        resource["subject"] = {"reference": f"Patient/{patient_id}"}
    resources = background + history
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
        path = BUNDLES / f"{case_id}.json"
        text = dumps(build(case_id))
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(case_id)
        else:
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path.relative_to(EVALS.parent)}")
    if stale:
        print(f"stale bundles, rerun build_cases.py: {', '.join(stale)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
