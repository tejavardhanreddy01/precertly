"""Bariatric case facts the generic case tests do not check: the stripped and rewritten
background, the weights and BMIs, the notes that carry the labels and the timelines."""

import base64
import importlib.util
import json
import re
import sys
from datetime import date

import pytest

from precertly.evals import EVALS_DIR, load_cases
from precertly.fhir import chunk_bundle, load_bundle

CASES = {k: v for k, v in load_cases().items() if v.policy == "bariatric-surgery"}
PLAN = (EVALS_DIR / "cases" / "bariatric" / "PLAN.md").read_text(encoding="utf-8")
REQUEST_DATE = date(2026, 10, 5)


def _build_module():
    spec = importlib.util.spec_from_file_location("build_cases", EVALS_DIR / "build_cases.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BUILD = _build_module()
TEMPLATE = json.loads((EVALS_DIR / "template" / "patient.json").read_text(encoding="utf-8"))
REF = re.compile(
    r"`((?:Condition|Encounter|Procedure|Observation|MedicationRequest|DocumentReference|DiagnosticReport)/[0-9a-f-]+)`"
)
CARDIAC = re.compile(
    r"ischemic|coronary|cardiolog|clopidogrel|metoprolol|nitroglycerin|simvastatin|angiograph"
    r"|percutaneous",
    re.IGNORECASE,
)
# The co-morbidity terms searched in the stripped background (see PLAN.md)
COMORBIDITY = re.compile(
    r"diabet|glucose|a1c|insulin|metformin|hypertens|apnea|sleep|snor|lipid|cholesterol"
    r"|triglycer|dyslipid|\bgerd\b|reflux|esophag|heartburn|fatty liver|steato|nafld|\bnash\b"
    r"|hepatitis|gallbladder|gallstone|arthritis|\bknee\b|\bhip\b|\bgout\b|asthma|pcos"
    r"|polycystic|infertil|stasis|incontinen|heart failure|kidney|\brenal\b|pseudotumor"
    r"|hypoventil",
    re.IGNORECASE,
)


def ref(resource: dict) -> str:
    return f"{resource['resourceType']}/{resource['id']}"


def text_of(resource: dict) -> str:
    """The resource as text, with base64 notes and reports decoded."""
    parts = [json.dumps(resource)]
    attachments = [c.get("attachment", {}) for c in resource.get("content") or []]
    attachments += resource.get("presentedForm") or []
    parts += [base64.b64decode(a["data"]).decode("utf-8") for a in attachments if a.get("data")]
    return "\n".join(parts)


def resources(case_id: str) -> list[dict]:
    return [e["resource"] for e in load_bundle(CASES[case_id].bundle_path())["entry"]]


TEMPLATE_IDS = {ref(r) for r in TEMPLATE}


def added(case_id: str) -> dict[str, dict]:
    """The resources a case adds to the background, by Type/id."""
    return {ref(r): r for r in resources(case_id) if ref(r) not in TEMPLATE_IDS}


def note(case_id: str, note_id: str) -> str:
    """The text of a note as written: the decoded attachment, without the resource around it."""
    resource = added(case_id)[f"DocumentReference/{note_id}"]
    return base64.b64decode(resource["content"][0]["attachment"]["data"]).decode("utf-8")


def plan_section(heading: str) -> str:
    start = PLAN.index(heading)
    return PLAN[start : PLAN.index("\n##", start + 1)]


def all_references(items: list[dict]) -> set[str]:
    return set(re.findall(r'"reference": "([A-Za-z]+/[^"]+)"', json.dumps(items)))


# --- 1. the six cases -----------------------------------------------------------------------


def test_the_bariatric_cases_are_the_six_planned():
    assert sorted(CASES) == [f"bariatric-0{n}" for n in range(1, 7)]
    assert {c.request.date for c in CASES.values()} == {REQUEST_DATE}
    assert {c.request.service_date for c in CASES.values()} == {date(2026, 10, 19)}
    for case in CASES.values():
        assert {c.id for c in case.request.chunks()} == {
            "request.date",
            "request.service_date",
            "request.procedure",
            "request.codes",
        }


# --- 2. what is removed and rewritten -------------------------------------------------------


def test_the_removed_set_is_the_one_in_the_plan_and_in_the_builder():
    listed = set(REF.findall(plan_section("### Removed from every bariatric bundle")))
    assert listed == set(BUILD.BARIATRIC_REMOVED) and len(listed) == 33
    assert listed <= TEMPLATE_IDS
    assert len(TEMPLATE) - len(listed) == 135


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_a_bariatric_bundle_has_none_of_the_removed_and_keeps_the_rest(case_id):
    present = {ref(r) for r in resources(case_id)}
    assert not present & set(BUILD.BARIATRIC_REMOVED)
    assert present >= TEMPLATE_IDS - set(BUILD.BARIATRIC_REMOVED)


def test_every_removed_resource_is_still_in_every_facet_and_pap_bundle():
    # the stimulator bundles remove the same cardiac cluster on their own, so they are not
    # part of this check
    other = {
        k: v
        for k, v in load_cases().items()
        if v.policy in ("facet-joint-interventions", "pap-sleep-apnea")
    }
    assert len(other) == 17
    for case in other.values():
        present = {ref(e["resource"]) for e in load_bundle(case.bundle_path())["entry"]}
        assert set(BUILD.BARIATRIC_REMOVED) <= present, case.id


def test_the_rewritten_observations_are_the_ones_in_the_plan():
    listed = set(REF.findall(plan_section("### Rewritten in every bariatric bundle")))
    codes = {BUILD.WEIGHT_LOINC, BUILD.BMI_LOINC, BUILD.BLOOD_PRESSURE_LOINC}
    expected = {
        ref(r)
        for r in TEMPLATE
        if r["resourceType"] == "Observation"
        and r["code"]["coding"][0]["code"] in codes
        and r["effectiveDateTime"][:10] in BUILD.BASELINE_EXAMS
    }
    assert listed == expected and len(listed) == 12


# --- 3. nothing refers to what was removed --------------------------------------------------


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_no_kept_resource_refers_to_or_mentions_a_removed_one(case_id):
    items = resources(case_id)
    assert not all_references(items) & set(BUILD.BARIATRIC_REMOVED)
    ids = {r.split("/")[1] for r in BUILD.BARIATRIC_REMOVED}
    for resource in items:
        body = text_of(resource)
        assert not CARDIAC.search(body), ref(resource)
        assert not {i for i in ids if i in body}, ref(resource)


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_a_bariatric_bundle_has_no_dangling_reference_the_template_lacks(case_id):
    def dangling(items: list[dict]) -> set[str]:
        return all_references(items) - {ref(r) for r in items}

    assert dangling(resources(case_id)) <= dangling(TEMPLATE)


# --- 4. the stripped background has no candidate co-morbidity -------------------------------


def test_the_stripped_background_has_no_candidate_comorbidity():
    background = BUILD.bariatric_background(
        json.loads(json.dumps(TEMPLATE)), BUILD.BARIATRIC_CASES["bariatric-01"]
    )
    for resource in background:
        assert not COMORBIDITY.search(text_of(resource)), ref(resource)
    pressures = sorted(
        (
            r["effectiveDateTime"][:10],
            {c["code"]["coding"][0]["code"]: c["valueQuantity"]["value"] for c in r["component"]},
        )
        for r in background
        if r["resourceType"] == "Observation"
        and r["code"]["coding"][0]["code"] == BUILD.BLOOD_PRESSURE_LOINC
    )
    assert [(d, p["8480-6"], p["8462-4"]) for d, p in pressures] == [
        ("2023-10-24", 118, 76),
        ("2024-07-09", 116, 74),
        ("2025-07-15", 114, 76),
        ("2026-07-21", 118, 76),
    ]


# --- 4b. no background note omits a medication that is active on its date -------------------

BACKGROUND_NOTE_TYPES = ("DocumentReference", "DiagnosticReport")
# The removed pairs of 2025-11-22 and 2026-05-02, which the check must flag where orlistat is active
REMOVED_PAIRS = {
    "2025-11-22": {
        "DocumentReference/f6ba0ccb-1752-effe-1bd8-851cd71b38a1",
        "DiagnosticReport/2d959dc6-9c73-5be5-37cd-96cfb0b7ffb4",
    },
    "2026-05-02": {
        "DocumentReference/ad8b6791-c5e2-872f-9f9d-901b0d809b9f",
        "DiagnosticReport/b5a9e535-f29f-fe02-c6d2-d8f8809e4047",
    },
}


def body_of(resource: dict) -> str | None:
    """The decoded note or report text of a resource, or None if it has none."""
    attachments = [c.get("attachment", {}) for c in resource.get("content") or []]
    attachments += resource.get("presentedForm") or []
    data = next((a["data"] for a in attachments if a.get("data")), None)
    return base64.b64decode(data).decode("utf-8") if data else None


def note_date(resource: dict, body: str) -> str:
    """The note's own date, which must be the first line of its text (after the blank line
    the template's notes start with)."""
    stamp = resource["date"] if resource["resourceType"] == "DocumentReference" else None
    stamp = stamp or resource["effectiveDateTime"]
    assert body.strip().split("\n", 1)[0] == stamp[:10], ref(resource)
    return stamp[:10]


def medication_section(body: str) -> list[str]:
    """The lines under "# Medications", up to the first blank line. "No Active Medications."
    is an empty list."""
    section = body.split("# Medications\n", 1)[1].split("\n\n", 1)[0].strip()
    return (
        [] if section.lower().startswith("no active medications") else section.lower().split("\n")
    )


def omitted_medications(notes: list[dict], items: list[dict]) -> dict[str, list[str]]:
    """For each note (by Type/id), the MedicationRequests active on its date that its list
    leaves out. A request is active from its authoredOn on, whatever its status."""
    requests = [r for r in items if r["resourceType"] == "MedicationRequest"]
    omitted = {}
    for resource in notes:
        body = body_of(resource)
        when = note_date(resource, body)
        listed = medication_section(body)
        missing = [
            r["medicationCodeableConcept"]["text"]
            for r in requests
            if r["authoredOn"][:10] <= when
            and not any(
                r["medicationCodeableConcept"]["text"].split()[0].lower() in x for x in listed
            )
        ]
        if missing:
            omitted[ref(resource)] = missing
    return omitted


def background_notes(items: list[dict]) -> list[dict]:
    """The template's notes and twin reports that have a Medications section."""
    return [
        r
        for r in items
        if r["resourceType"] in BACKGROUND_NOTE_TYPES
        and ref(r) in TEMPLATE_IDS
        and "# Medications\n" in (body_of(r) or "")
    ]


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_no_background_note_omits_a_medication_active_on_its_date(case_id):
    items = resources(case_id)
    notes = background_notes(items)
    # five kept visits (2023-10-12 to 2025-07-15), each a note and its twin report
    assert len(notes) == 10
    assert max(note_date(n, body_of(n)) for n in notes) == "2025-07-15"
    assert omitted_medications(notes, items) == {}


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_the_medication_check_flags_the_removed_note_pairs(case_id):
    # the same check, run on the four removed template notes against this case's requests
    removed = [r for r in TEMPLATE if ref(r) in set().union(*REMOVED_PAIRS.values())]
    assert len(removed) == 4
    flagged = omitted_medications(removed, resources(case_id))
    if case_id in ("bariatric-04", "bariatric-05"):  # no orlistat, and metformin starts later
        assert flagged == {}
    else:
        assert flagged == {
            r: ["Orlistat 120 MG oral capsule"] for p in REMOVED_PAIRS.values() for r in p
        }


# --- 5. bariatric-06 documents no co-morbidity, and no evaluation lists a history -----------

HISTORY = re.compile(
    r"\bhistory\b|co-?morbid|review of systems|\bpast medical\b|\bno prior\b", re.IGNORECASE
)


def test_bariatric_06_has_no_comorbidity_anywhere():
    items = resources("bariatric-06")
    for resource in items:
        assert not COMORBIDITY.search(text_of(resource)), ref(resource)
    assert not [r for r in added("bariatric-06").values() if r["resourceType"] == "Condition"]
    medications = [
        r for r in added("bariatric-06").values() if r["resourceType"] == "MedicationRequest"
    ]
    assert [m["id"] for m in medications] == ["orlistat"]


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_no_surgical_evaluation_lists_a_history_or_states_a_comorbidity_finding(case_id):
    body = note(case_id, "note-surgical-eval")
    assert not HISTORY.search(body)
    assert ("type 2 diabetes" in body) == (case_id != "bariatric-06")


# --- 6. weights and BMIs --------------------------------------------------------------------


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_every_coded_bmi_is_its_weight_over_the_height_squared(case_id):
    by_date: dict[str, dict[str, float]] = {}
    for r in resources(case_id):
        if r["resourceType"] != "Observation":
            continue
        code = r["code"]["coding"][0]["code"]
        if code in (BUILD.WEIGHT_LOINC, BUILD.BMI_LOINC):
            by_date.setdefault(r["effectiveDateTime"][:10], {})[code] = r["valueQuantity"]["value"]
    assert len(by_date) >= 7
    for when, values in by_date.items():
        assert set(values) == {BUILD.WEIGHT_LOINC, BUILD.BMI_LOINC}, when
        assert values[BUILD.BMI_LOINC] == round(values[BUILD.WEIGHT_LOINC] / 1.729**2, 2), when


def _bmis(case_id: str) -> list[float]:
    return [
        r["valueQuantity"]["value"]
        for r in resources(case_id)
        if r["resourceType"] == "Observation" and r["code"]["coding"][0]["code"] == BUILD.BMI_LOINC
    ]


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_every_bmi_is_on_the_designed_side_of_the_threshold(case_id):
    bmis = _bmis(case_id)
    assert min(bmis) >= 30  # the template's "BMI 30+ obesity" Condition stays true
    if case_id == "bariatric-02":
        assert max(bmis) < 33 and round(bmis[-1], 1) == 31.8
    elif case_id == "bariatric-03":
        # the near miss: below 35 unrounded, and nothing that rounds up to 35.0
        assert max(bmis) == 34.89 < 34.95
    else:
        assert min(bmis) >= 37


def test_the_near_miss_note_says_34_9_and_no_note_says_35():
    assert "BMI 34.9 kg/m2" in note("bariatric-03", "note-surgical-eval")
    for note_id in ("note-surgical-eval", "note-wm-12mo"):
        assert not re.search(r"BMI 35(\.0)?\b", note("bariatric-03", note_id))


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_weight_steps_are_small_except_around_the_month_6_dip(case_id):
    series = BUILD.bariatric_series(BUILD.BARIATRIC_CASES[case_id])
    dates = list(series)
    for before, after in zip(dates, dates[1:], strict=False):
        if BUILD.WM_SIX_MONTH not in (before, after):
            assert abs(series[after][0] - series[before][0]) <= 1.0, (before, after)


# --- 7. the notes that carry the labels keep their statements in the chunks -----------------


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_the_evaluation_states_the_labeled_bmi_and_the_chunks_keep_it(case_id):
    case = CASES[case_id]
    label = next(x for x in case.expected if x.criterion in ("bmi-35-plus", "lsg-bmi-35-plus"))
    stated = re.search(r"BMI ([\d.]+) kg/m2", label.fact).group(1)
    assert f"BMI {stated} kg/m2" in note(case_id, "note-surgical-eval")
    chunks = [
        c
        for c in chunk_bundle(load_bundle(case.bundle_path()))
        if c.resource_id == "note-surgical-eval"
    ]
    assert any(f"BMI {stated} kg/m2" in c.text and c.char_start is not None for c in chunks)


@pytest.mark.parametrize(
    "case_id", ["bariatric-01", "bariatric-02", "bariatric-03", "bariatric-06"]
)
def test_the_month_12_chunks_keep_the_outcome(case_id):
    case = CASES[case_id]
    text = "\n".join(
        c.text
        for c in chunk_bundle(load_bundle(case.bundle_path()))
        if c.resource_id == "note-wm-12mo"
    )
    assert "0.6 kg above the intake weight" in text
    assert "No lasting weight loss" in text
    assert "orlistat 120 mg three times daily taken for the 12 months" in text


# --- 8. bariatric-04 has no treatment record ------------------------------------------------

TREATMENT = re.compile(
    r"orlistat|diet|calorie|weight management|program|counsel|walking|exercise|lifestyle",
    re.IGNORECASE,
)


def test_bariatric_04_has_no_treatment_text_and_the_only_exercise_is_the_background_care_plan():
    for resource in added("bariatric-04").values():
        assert not TREATMENT.search(text_of(resource)), ref(resource)
    mentions = [ref(r) for r in resources("bariatric-04") if "exercise" in text_of(r).lower()]
    assert mentions == ["CarePlan/37b75dda-e187-ae40-030d-244596cf7923"]
    medications = [
        r["id"] for r in added("bariatric-04").values() if r["resourceType"] == "MedicationRequest"
    ]
    assert medications == ["metformin"]
    assert "Condition/type-2-diabetes" in added("bariatric-04")


# --- 9. bariatric-05: two notes of the same standing disagree -------------------------------


def test_bariatric_05_conflicts_only_on_whether_the_program_happened():
    items = added("bariatric-05")
    notes = {i.split("/")[1] for i, r in items.items() if r["resourceType"] == "DocumentReference"}
    assert notes == {
        "note-wm-referral",
        "note-wm-declined",
        "note-diabetes-dx",
        "note-surgical-eval",
    }
    assert not [
        r
        for r in items.values()
        if r["resourceType"] == "MedicationRequest" and r["id"] == "orlistat"
    ]
    declined, evaluation = (
        note("bariatric-05", "note-wm-declined"),
        note("bariatric-05", "note-surgical-eval"),
    )
    assert declined.startswith("2025-08-04\n")
    assert "declined enrollment" in declined and "Referral closed" in declined
    assert evaluation.startswith("2026-09-14\n")
    assert (
        "completed a 12-month physician-supervised weight management program"
        " (2025-08-04 to 2026-08-03)" in evaluation
    )
    # neither mentions the other, and nothing in the bundle reconciles them
    assert not re.search(r"completed|evaluation|surgery|2026|orlistat", declined)
    assert not re.search(r"declined|closed|clinic note", evaluation)
    for resource in items.values():
        body = text_of(resource)
        if "note-wm-declined" not in body and "note-surgical-eval" not in body:
            assert not re.search(r"reconcil|records|reviewed", body, re.IGNORECASE), ref(resource)
    # the program in the evaluation starts the day the clinic note records the refusal
    assert "2025-08-04" in declined and "2025-08-04 to" in evaluation
    # and these are the only program documents: no intake, month 6 or month 12 note
    assert not [i for i in items if re.search(r"note-wm-(intake|6mo|12mo)", i)]


# --- 10. timelines --------------------------------------------------------------------------


def _day(resource: dict) -> date:
    for key in ("effectiveDateTime", "authoredOn", "onsetDateTime", "date"):
        if key in resource:
            return date.fromisoformat(resource[key][:10])
    return date.fromisoformat(resource["period"]["start"])


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_every_case_event_precedes_the_request_and_matches_its_note_and_encounter(case_id):
    items = added(case_id)
    for item, resource in items.items():
        assert _day(resource) < REQUEST_DATE, item
        if resource["resourceType"] == "DocumentReference":
            body = text_of(resource)
            assert (
                base64.b64decode(resource["content"][0]["attachment"]["data"])
                .decode()
                .startswith(_day(resource).isoformat())
            ), item
            encounter = resource["context"]["encounter"][0]["reference"]
            assert _day(items[encounter]) == _day(resource), item
            assert body
    condition = items.get("Condition/type-2-diabetes")
    if condition:
        assert date.fromisoformat(condition["onsetDateTime"]) == BUILD.DIABETES_VISIT
        assert _day(items["DocumentReference/note-diabetes-dx"]) == BUILD.DIABETES_VISIT
        assert _day(items["MedicationRequest/metformin"]) == BUILD.DIABETES_VISIT


def test_the_program_timeline_is_in_order_and_orlistat_runs_through_month_12():
    order = [
        ("note-wm-referral", BUILD.WM_REFERRAL),
        ("note-wm-intake", BUILD.WM_INTAKE),
        ("note-wm-6mo", BUILD.WM_SIX_MONTH),
        ("note-wm-12mo", BUILD.WM_COMPLETION),
        ("note-surgical-eval", BUILD.SURGICAL_EVAL),
    ]
    for case_id in ("bariatric-01", "bariatric-02", "bariatric-03", "bariatric-06"):
        items = added(case_id)
        days = [_day(items[f"DocumentReference/{n}"]) for n, _ in order]
        assert days == [d for _, d in order] and days == sorted(days)
        orlistat = items["MedicationRequest/orlistat"]
        assert orlistat["status"] == "completed" and _day(orlistat) == BUILD.WM_INTAKE
        # the program ran 2025-08-04 to 2026-08-03: twelve months
        assert (BUILD.WM_COMPLETION - BUILD.WM_INTAKE).days == 364
        assert not re.search(r"\bstop|discontinu", note(case_id, "note-wm-6mo"), re.IGNORECASE)
        assert "orlistat" in note(case_id, "note-wm-6mo")


def test_the_diabetes_visit_is_in_the_chart_of_every_case_except_06():
    for case_id in CASES:
        assert ("Condition/type-2-diabetes" in added(case_id)) == (case_id != "bariatric-06")
