"""Build the eval case bundles from a template patient. Nothing here is hand-edited JSON.

    uv run python evals/build_cases.py                 # write evals/bundles/<case>.json
    uv run python evals/build_cases.py --check         # fail if a committed bundle is stale
    uv run python evals/build_cases.py trim <fhir-dir> # rebuild evals/template/patient.json

Two steps:

1. trim (rare): take one patient from a pinned Synthea v4.0.0 run and keep a few hundred
   resources as realistic, unrelated background. The result is committed, so building
   cases needs neither Java nor Synthea. To regenerate the input:

       SYNTHEA_REFERENCE_DATE=20261001 SYNTHEA_OUTPUT=<dir> data/synthea/generate.sh 10

2. build: add the facet history to that background from a set of facts, one set per case.
   A case differs from the clean template only in the facts it overrides, so the notes,
   observations and procedures always agree with each other.

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

SYNTHEA_PATIENT_ID = "18f4617a"  # id prefix; seed 20261009, 10 patients, reference date below
SYNTHEA_REFERENCE_DATE = date(2026, 10, 1)
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
# The background must say nothing about the facts the facet cases turn on.
CONFLICTS = re.compile(
    r"back pain|radicul|sciatica|fibromyalg|fusion|fracture|chronic pain|claudication|"
    r"stenosis|spondyl|ablation|physical therapy|medial branch|facet|oswestry",
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
        if kind not in KEPT_TYPES:
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
        if resource["resourceType"] != "Patient" and CONFLICTS.search(_decoded_text(resource)):
            ref = f"{resource['resourceType']}/{resource['id']}"
            raise SystemExit(f"background conflicts with the facet template: {ref}")
    return kept


# --- Step 2: the facet history, generated from facts --------------------------------------

REQUEST_DATE = date(2026, 10, 5)


@dataclass(frozen=True)
class Facts:
    """What is true in a case's chart. The defaults are the clean template."""

    pain_onset: date = date(2026, 4, 3)  # six months before the request
    pcp_visit: date = date(2026, 4, 10)
    pt_start: date = date(2026, 5, 4)
    pt_end: date = date(2026, 8, 3)  # three months of physical therapy
    mri: date = date(2026, 7, 20)
    pain_clinic_eval: date = date(2026, 8, 10)
    baseline_nrs: int = 7
    baseline_odi: int = 48
    mbb1: date = date(2026, 8, 24)
    mbb1_post_nrs: int = 1
    mbb2_after_days: int = 21  # three weeks after the first block
    mbb2_post_nrs: int = 1
    rfa_planning: date = date(2026, 9, 21)

    @property
    def mbb2(self) -> date:
        return self.mbb1 + timedelta(days=self.mbb2_after_days)

    def relief(self, post_nrs: int) -> int:
        return round((self.baseline_nrs - post_nrs) / self.baseline_nrs * 100)


LEVELS = "L4-5 and L5-S1"
NRS = "Numerical Rating Scale (NRS) 0-10"
ODI = "Oswestry Disability Index (ODI)"

CASES: dict[str, Facts] = {
    # Clean: both blocks give 86% relief on the same scale, three weeks apart.
    "facet-01": Facts(),
    # Near miss: the second block gives 71% relief (NRS 7 to 2), below the 80% minimum.
    "facet-02": replace(Facts(), mbb2_post_nrs=2),
}


def _note(note_id: str, when: date, title: str, text: str, encounter: str) -> Resource:
    return {
        "resourceType": "DocumentReference",
        "id": note_id,
        "status": "current",
        "type": {"text": title},
        "date": f"{when.isoformat()}T10:00:00-04:00",
        "context": {"encounter": [{"reference": f"Encounter/{encounter}"}]},
        "content": [
            {
                "attachment": {
                    "contentType": "text/plain; charset=utf-8",
                    "data": base64.b64encode(text.encode("utf-8")).decode("ascii"),
                }
            }
        ],
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


def _pain_score(observation_id: str, when: date, value: int, encounter: str) -> Resource:
    return {
        "resourceType": "Observation",
        "id": observation_id,
        "status": "final",
        "category": [{"coding": [{"code": "survey"}]}],
        "code": {
            "coding": [{"system": "http://loinc.org", "code": "72514-3"}],
            "text": f"Pain severity, {NRS}",
        },
        "valueQuantity": {"value": value, "unit": "{score}"},
        "effectiveDateTime": when.isoformat(),
        "encounter": {"reference": f"Encounter/{encounter}"},
    }


def _block(number: int, when: date, post_nrs: int, facts: Facts) -> list[Resource]:
    encounter = f"enc-mbb{number}"
    relief = facts.relief(post_nrs)
    note = f"""{when.isoformat()}
Procedure note: diagnostic lumbar medial branch block #{number}

Indication: chronic axial low back pain with suspected lumbar facet syndrome.
Levels: bilateral {LEVELS} (medial branches of L3 and L4 and the L5 dorsal ramus).
Technique: fluoroscopic guidance, 0.5 mL of 0.5% bupivacaine at each target nerve. No corticosteroid. No sedation. No other injections were performed today.

Pain assessment ({NRS}):
- Before the block: {facts.baseline_nrs}/10, index low back pain with extension and rotation.
- 30 minutes after the block: {post_nrs}/10 with the same provocative movements.
- Relief of index pain: {relief}%.
The patient kept a pain diary; relief lasted about 6 hours, consistent with the duration of bupivacaine, and pain then returned to baseline.

No complications. Discharged in stable condition.
"""
    return [
        _encounter(encounter, when, "Pain clinic procedure visit"),
        {
            "resourceType": "Procedure",
            "id": f"mbb{number}",
            "status": "completed",
            "code": {"text": f"Diagnostic lumbar medial branch block, bilateral {LEVELS}"},
            "performedDateTime": when.isoformat(),
            "encounter": {"reference": f"Encounter/{encounter}"},
            "reasonReference": [{"reference": "Condition/chronic-low-back-pain"}],
            "bodySite": [{"text": f"Lumbar facet joints {LEVELS}, bilateral"}],
        },
        _pain_score(f"pain-pre-mbb{number}", when, facts.baseline_nrs, encounter),
        _pain_score(f"pain-post-mbb{number}", when, post_nrs, encounter),
        _note(
            f"note-mbb{number}",
            when,
            f"Procedure note, medial branch block {number}",
            note,
            encounter,
        ),
    ]


def facet_history(facts: Facts) -> list[Resource]:
    relief1, relief2 = facts.relief(facts.mbb1_post_nrs), facts.relief(facts.mbb2_post_nrs)
    weeks_between = facts.mbb2_after_days // 7

    pcp_note = f"""{facts.pcp_visit.isoformat()}
Primary care visit

Chief complaint: low back pain.
History: low back pain that began on {facts.pain_onset.isoformat()} without injury. The pain is across the lower back, worse with standing and bending backward. No leg pain, numbness or weakness. No bowel or bladder changes.
Exam: tenderness over the lower lumbar paraspinal muscles. Strength and sensation intact in both legs.
Plan: naproxen 500 mg twice daily with food. Referral to physical therapy. Return if not improving.
"""
    pt_note = f"""{facts.pt_end.isoformat()}
Physical therapy discharge summary

Course: {facts.pt_start.isoformat()} to {facts.pt_end.isoformat()} (12 weeks), 22 of 24 scheduled sessions attended.
Treatment: core stabilization, lumbar flexion-based exercise, manual therapy and a home exercise program, performed as instructed.
Outcome: no meaningful improvement in pain or function. Pain {facts.baseline_nrs}/10 at discharge, unchanged from the start of therapy. Still unable to stand longer than 15 minutes.
Recommendation: return to the referring physician; consider referral to a pain specialist.
"""
    mri_report = f"""{facts.mri.isoformat()}
MRI lumbar spine without contrast

Indication: low back pain for more than three months, not improved with conservative care.
Findings: vertebral body heights and alignment are maintained. No fracture. No marrow-replacing lesion or mass. No evidence of discitis or osteomyelitis. No scoliosis or spondylolisthesis. Mild disc desiccation at L4-5 without herniation. Moderate bilateral facet arthropathy at {LEVELS} with small joint effusions. No significant central canal or foraminal narrowing at any level.
Impression: facet arthropathy at {LEVELS}. No fracture, tumor, infection or significant deformity. No nerve root compression.
"""
    eval_note = f"""{facts.pain_clinic_eval.isoformat()}
Pain clinic initial evaluation

History: low back pain since {facts.pain_onset.isoformat()}, now present for more than four months. The pain is predominantly axial, across the lower lumbar spine, moderate to severe, worse with extension, rotation and prolonged standing, and eased by sitting. It does not radiate below the buttocks. The patient can no longer stand longer than 15 minutes, has stopped gardening and needs help with housework.

Conservative management: naproxen 500 mg twice daily since {facts.pcp_visit.isoformat()} with minimal benefit; a 12-week course of physical therapy ({facts.pt_start.isoformat()} to {facts.pt_end.isoformat()}) with no meaningful improvement in pain or function.

Pain and function at baseline:
- Pain: {facts.baseline_nrs}/10 on the {NRS}.
- Disability: {facts.baseline_odi}% on the {ODI}, severe disability.

Past history: no prior spine surgery, including no lumbar fusion. No history of fibromyalgia or any widespread or centralized pain syndrome. No prior radiofrequency ablation or spinal injections.

Neurological exam: strength 5/5 in both legs. Sensation intact to light touch. Reflexes 2+ and symmetric at the knees and ankles. Straight-leg raise negative on both sides. No radicular symptoms and no neurogenic claudication.
Spine exam: pain reproduced by lumbar extension and rotation; tenderness over the {LEVELS} facet joints on both sides.

Imaging: lumbar MRI of {facts.mri.isoformat()} shows facet arthropathy at {LEVELS} with no fracture, tumor, infection or significant deformity.

Assessment: chronic axial low back pain, most consistent with lumbar facet syndrome at {LEVELS}.
Plan: diagnostic medial branch blocks at {LEVELS}; if two blocks confirm the diagnosis, radiofrequency ablation at those levels. Pain will be recorded on the same {NRS} before and after each block.
"""
    planning_note = f"""{facts.rfa_planning.isoformat()}
Pain clinic follow-up

Results of the diagnostic medial branch blocks at {LEVELS}, both scored on the {NRS}:
- Block 1 on {facts.mbb1.isoformat()}: pain {facts.baseline_nrs}/10 before and {facts.mbb1_post_nrs}/10 after, {relief1}% relief.
- Block 2 on {facts.mbb2.isoformat()}, {weeks_between} weeks after the first: pain {facts.baseline_nrs}/10 before and {facts.mbb2_post_nrs}/10 after, {relief2}% relief.
In each case relief lasted about 6 hours, consistent with bupivacaine.

The patient has not had radiofrequency ablation before.
Plan: request prior authorization for thermal radiofrequency ablation of the medial branch nerves at {LEVELS} (lumbar region, two levels). No other injections are planned for that day.
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
        "conclusion": (
            f"Facet arthropathy at {LEVELS}. No fracture, tumor, infection or significant "
            "deformity. No nerve root compression."
        ),
        "presentedForm": [
            {
                "contentType": "text/plain; charset=utf-8",
                "data": base64.b64encode(mri_report.encode("utf-8")).decode("ascii"),
            }
        ],
    }
    odi = {
        "resourceType": "Observation",
        "id": "odi-baseline",
        "status": "final",
        "category": [{"coding": [{"code": "survey"}]}],
        "code": {"text": f"{ODI} score"},
        "valueQuantity": {"value": facts.baseline_odi, "unit": "%"},
        "effectiveDateTime": facts.pain_clinic_eval.isoformat(),
        "encounter": {"reference": "Encounter/enc-pain-clinic-eval"},
    }
    return [
        condition,
        _encounter("enc-pcp", facts.pcp_visit, "Primary care visit"),
        _note("note-pcp", facts.pcp_visit, "Primary care note", pcp_note, "enc-pcp"),
        naproxen,
        _encounter("enc-pt", facts.pt_start, "Physical therapy", facts.pt_end),
        physical_therapy,
        _note("note-pt-discharge", facts.pt_end, "Physical therapy discharge", pt_note, "enc-pt"),
        _encounter("enc-mri", facts.mri, "Imaging visit"),
        mri,
        _encounter("enc-pain-clinic-eval", facts.pain_clinic_eval, "Pain clinic evaluation"),
        _pain_score(
            "pain-nrs-baseline",
            facts.pain_clinic_eval,
            facts.baseline_nrs,
            "enc-pain-clinic-eval",
        ),
        odi,
        _note(
            "note-pain-clinic-eval",
            facts.pain_clinic_eval,
            "Pain clinic evaluation",
            eval_note,
            "enc-pain-clinic-eval",
        ),
        *_block(1, facts.mbb1, facts.mbb1_post_nrs, facts),
        *_block(2, facts.mbb2, facts.mbb2_post_nrs, facts),
        _encounter("enc-rfa-planning", facts.rfa_planning, "Pain clinic follow-up"),
        _note(
            "note-rfa-planning",
            facts.rfa_planning,
            "Pain clinic follow-up",
            planning_note,
            "enc-rfa-planning",
        ),
    ]


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
