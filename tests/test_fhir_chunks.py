import base64
from datetime import date

from precertly.fhir import SUPPORTED_TYPES, chunk_bundle, split_spans

AS_OF = date(2026, 10, 9)

SHORT_NOTE = "Sleep study reviewed.\n\nAHI 22 events per hour. CPAP ordered."
LONG_NOTE = "\n\n".join(
    f"Visit {i}: low back pain persists despite physical therapy and NSAIDs. " * 6
    for i in range(1, 9)
)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _note(resource_id: str, text: str, when: str = "2026-03-02T09:30:00-05:00") -> dict:
    return {
        "resourceType": "DocumentReference",
        "id": resource_id,
        "status": "current",
        "date": when,
        "type": {"coding": [{"system": "http://loinc.org", "code": "34117-2", "display": "H&P"}]},
        "content": [{"attachment": {"contentType": "text/plain; charset=utf-8", "data": text}}],
    }


def _bundle(*resources: dict) -> dict:
    return {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [{"fullUrl": f"urn:uuid:{r['id']}", "resource": r} for r in resources],
    }


BUNDLE = _bundle(
    {
        "resourceType": "Patient",
        "id": "pat-1",
        "name": [{"family": "Testerson", "given": ["Sam"]}],
        "identifier": [{"system": "http://hl7.org/fhir/sid/us-ssn", "value": "999-00-1234"}],
        "address": [{"line": ["1 Example St"], "city": "Springfield"}],
        "gender": "female",
        "birthDate": "1970-12-25",
    },
    {
        "resourceType": "Condition",
        "id": "cond-1",
        "clinicalStatus": {"coding": [{"code": "active"}]},
        "code": {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": "78275009",
                    "display": "Obstructive sleep apnea syndrome",
                }
            ]
        },
        "onsetDateTime": "2025-11",
    },
    {
        "resourceType": "Observation",
        "id": "obs-bmi",
        "status": "final",
        "category": [{"coding": [{"code": "vital-signs"}]}],
        "code": {"coding": [{"system": "http://loinc.org", "code": "39156-5"}], "text": "BMI"},
        "valueQuantity": {"value": 36.2, "unit": "kg/m2"},
        "effectiveDateTime": "2026-02-14T10:00:00-05:00",
    },
    {
        "resourceType": "Observation",
        "id": "obs-bp",
        "status": "final",
        "code": {"text": "Blood pressure"},
        "component": [
            {"code": {"text": "Systolic"}, "valueQuantity": {"value": 142, "unit": "mm[Hg]"}},
            {"code": {"text": "Diastolic"}, "valueQuantity": {"value": 91, "unit": "mm[Hg]"}},
        ],
    },
    {"resourceType": "Medication", "id": "med-1", "code": {"text": "Naproxen 500 MG"}},
    {
        "resourceType": "MedicationRequest",
        "id": "medreq-1",
        "status": "active",
        "medicationReference": {"reference": "urn:uuid:med-1"},
        "authoredOn": "2026-01-05",
        "dosageInstruction": [{"text": "1 tablet twice daily"}],
    },
    {"resourceType": "Device", "id": "dev-1", "type": {"text": "CPAP device"}},
    {
        "resourceType": "DeviceUseStatement",
        "id": "use-1",
        "status": "active",
        "device": {"reference": "Device/dev-1"},
        "timingPeriod": {"start": "2026-04-01", "end": "2026-04-30"},
        "note": [{"text": "Used 4 or more hours on 24 of 30 nights."}],
    },
    {
        "resourceType": "Communication",
        "id": "comm-1",
        "status": "completed",
        "sent": "2026-05-02T08:00:00Z",
        "category": [{"text": "Adherence report"}],
        "payload": [{"contentString": "Patient reports improved daytime sleepiness."}],
    },
    {
        "resourceType": "DiagnosticReport",
        "id": "report-dup",
        "status": "final",
        "code": {"text": "History and physical note"},
        "effectiveDateTime": "2026-03-02",
        "presentedForm": [{"contentType": "text/plain", "data": _b64(SHORT_NOTE)}],
    },
    {
        "resourceType": "DiagnosticReport",
        "id": "report-psg",
        "status": "final",
        "code": {"text": "Polysomnography"},
        "presentedForm": [{"contentType": "text/plain", "data": _b64("AHI 22. Lowest SpO2 81%.")}],
    },
    _note("note-short", _b64(SHORT_NOTE)),
    _note("note-long", _b64(LONG_NOTE)),
    _note("note-bad", "!!! not base64 !!!"),
    {"resourceType": "Claim", "id": "claim-1", "total": {"value": 129.16}},
    {"resourceType": "ExplanationOfBenefit", "id": "eob-1"},
    {"resourceType": "Organization", "id": "org-1", "name": "Example Clinic"},
)

CHUNKS = chunk_bundle(BUNDLE, as_of=AS_OF)


def _for(resource_id: str):
    return [c for c in CHUNKS if c.resource_id == resource_id]


def test_billing_and_admin_resources_are_skipped():
    types = {c.resource_type for c in CHUNKS}
    assert types <= SUPPORTED_TYPES
    assert not types & {"Claim", "ExplanationOfBenefit", "Organization", "Medication"}


def test_chunk_ids_are_stable_and_unique():
    ids = [c.id for c in CHUNKS]
    assert len(ids) == len(set(ids))
    assert _for("cond-1")[0].id == "Condition/cond-1#0"
    assert [c.id for c in chunk_bundle(BUNDLE, as_of=AS_OF)] == ids
    assert _for("cond-1")[0].model_dump()["id"] == "Condition/cond-1#0"


def test_patient_renders_only_age_and_sex():
    (patient,) = _for("pat-1")
    assert patient.text == "Patient\nAge: 55\nSex: female"
    assert patient.effective_date is None


def test_condition_keeps_codes_and_partial_date():
    (condition,) = _for("cond-1")
    assert "Obstructive sleep apnea syndrome (SNOMED 78275009)" in condition.text
    assert "Clinical status: active" in condition.text
    assert condition.effective_date == date(2025, 11, 1)


def test_observation_values_and_components():
    (bmi,) = _for("obs-bmi")
    assert "BMI (LOINC 39156-5)" in bmi.text
    assert "Value: 36.2 kg/m2" in bmi.text
    assert bmi.effective_date == date(2026, 2, 14)
    (bp,) = _for("obs-bp")
    assert "Systolic: 142 mm[Hg]; Diastolic: 91 mm[Hg]" in bp.text
    assert bp.effective_date is None


def test_references_are_resolved_within_the_bundle():
    assert "Medication request: Naproxen 500 MG" in _for("medreq-1")[0].text
    assert "Dosage: 1 tablet twice daily" in _for("medreq-1")[0].text
    use = _for("use-1")[0]
    assert use.text.startswith("Device use: CPAP device")
    assert "Timing: 2026-04-01 to 2026-04-30" in use.text
    assert "Note: Used 4 or more hours on 24 of 30 nights." in use.text
    assert use.effective_date == date(2026, 4, 1)


def test_communication_payload_is_rendered():
    (communication,) = _for("comm-1")
    assert "Adherence report" in communication.text
    assert "Patient reports improved daytime sleepiness." in communication.text
    assert communication.effective_date == date(2026, 5, 2)


def test_note_is_decoded_with_offsets():
    (note,) = _for("note-short")
    assert note.text == SHORT_NOTE
    assert (note.char_start, note.char_end) == (0, len(SHORT_NOTE))
    assert note.effective_date == date(2026, 3, 2)


def test_long_note_splits_into_verbatim_slices():
    parts = _for("note-long")
    assert len(parts) > 1
    assert [c.chunk_index for c in parts] == list(range(len(parts)))
    for chunk in parts:
        assert len(chunk.text) <= 1500
        assert LONG_NOTE[chunk.char_start : chunk.char_end] == chunk.text
    # nothing but whitespace is lost between slices
    assert "".join("".join(c.text.split()) for c in parts) == "".join(LONG_NOTE.split())


def test_malformed_base64_falls_back_to_document_summary():
    (bad,) = _for("note-bad")
    assert bad.text.startswith("Document: H&P (LOINC 34117-2)")
    assert bad.char_start is None


def test_presented_form_is_kept_unless_it_duplicates_a_note():
    assert _for("report-dup") == []
    summary, form = _for("report-psg")
    assert summary.char_start is None
    assert form.text == "AHI 22. Lowest SpO2 81%."
    assert (form.char_start, form.char_end) == (0, len(form.text))


def test_split_spans_handles_text_without_breaks():
    text = "x" * 4000
    spans = split_spans(text, max_chars=1500)
    assert [end - start for start, end in spans] == [1500, 1500, 1000]


def test_empty_bundle():
    assert chunk_bundle({"resourceType": "Bundle"}) == []
