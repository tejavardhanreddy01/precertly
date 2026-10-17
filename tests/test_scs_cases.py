"""Spinal cord stimulator case facts the generic case tests do not check: the stripped and
rewritten background, the request fields and the notes that must agree with them, the
evidence each label cites, the notes that carry the labels, and the timelines."""

import base64
import importlib.util
import json
import re
import sys
from datetime import date
from fractions import Fraction

import pytest

from precertly.evals import EVALS_DIR, load_cases
from precertly.fhir import chunk_bundle, load_bundle
from precertly.judgment.models import SCS_SETTINGS

CASES = {k: v for k, v in load_cases().items() if v.policy == "spinal-cord-stimulator"}
PLAN = (EVALS_DIR / "cases" / "spinal-cord-stimulators" / "PLAN.md").read_text(encoding="utf-8")
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
    r"|percutaneous coronary",
    re.IGNORECASE,
)
# The pain, surgery, drug, device and anticoagulant terms searched in the stripped background
PAIN_AND_DEVICE = re.compile(
    r"pain|intractab|neck|\bleg\b|neuropath|radicul|crps|complex regional|reflex sympathetic"
    r"|failed back|laminect|fusion|spine|spinal|surg|physical therapy|physiotherap|conservative"
    r"|opioid|oxycod|hydrocod|morphin|tramadol|fentanyl|gabapentin|pregabalin|duloxetin"
    r"|amitriptyl|nsaid|ibuprofen|naproxen|acetaminophen|analgesic|psycholog|psychiatr|mental"
    r"|counsel|cannabis|marijuana|stimulat|electrode|implant|pacemaker|defibrillat|\bstent"
    r"|\bmri\b|magnetic|x-ray|radiograph|anticoag|warfarin|heparin|apixaban|rivaroxaban|aspirin"
    r"|antiplatelet|bleed|coagul|\binr\b|platelet|sepsis|fever|abscess|immunosupp|steroid"
    r"|consent|multidisciplin|\btrial\b",
    re.IGNORECASE,
)
DAST = re.compile(r"dast|82666-9|82667-7|drug abuse", re.IGNORECASE)
# What no note of scs-04 may say: a statement or an implication that the therapy was
# explained to or agreed with the patient. Specific phrases, so ordinary words such as
# "risk", "benefit", "discuss" or "inform" in a clinical note do not trip it.
DISCUSSION = re.compile(
    r"risks and benefits|disclos|consent|educat|counsel|informed|understands|agrees"
    r"|wishes to proceed|discussed with the patient",
    re.IGNORECASE,
)
OFFICE_FORBIDDEN = re.compile(r"\bASC\b|ambulatory|hospital|surgery center|surgical center")
PRIOR_DEVICE = re.compile(
    r"2023|implanted|gluteal|existing|replace|battery|reconcil", re.IGNORECASE
)
PLAN_SETTING = re.compile(r"Planned setting: ([^.\n]+)\.")
PLAN_LEADS = re.compile(r"(\d+) percutaneous leads? \((\d+) contacts\)")


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


def notes(case_id: str) -> dict[str, str]:
    """Every added note's text, by note id (without the resource type)."""
    return {
        r["id"]: base64.b64decode(r["content"][0]["attachment"]["data"]).decode("utf-8")
        for r in added(case_id).values()
        if r["resourceType"] == "DocumentReference"
    }


def plan_section(heading: str) -> str:
    start = PLAN.index(heading)
    return PLAN[start : PLAN.index("\n##", start + 1)]


def all_references(items: list[dict]) -> set[str]:
    return set(re.findall(r'"reference": "([A-Za-z]+/[^"]+)"', json.dumps(items)))


def _day(resource: dict) -> date:
    for key in ("effectiveDateTime", "authoredOn", "onsetDateTime", "date", "performedDateTime"):
        if key in resource:
            return date.fromisoformat(resource[key][:10])
    if "performedPeriod" in resource:
        return date.fromisoformat(resource["performedPeriod"]["end"])
    if "recordedDate" in resource:
        return date.fromisoformat(resource["recordedDate"])
    return date.fromisoformat(resource["period"]["end"])


# --- 1. the six cases and their requests ------------------------------------------------------


def test_the_scs_cases_are_the_six_planned():
    assert sorted(CASES) == [f"scs-0{n}" for n in range(1, 7)]
    assert {c.request.date for c in CASES.values()} == {REQUEST_DATE}
    assert {c.request.service_date for c in CASES.values()} == {date(2026, 10, 19)}
    assert {c.variant for c in CASES.values()} == {"trial", "permanent"}
    assert sorted(c.id for c in CASES.values() if c.variant == "trial") == ["scs-02", "scs-04"]
    assert {c.expected_overall.value for c in CASES.values()} == {"met", "not_met", "insufficient"}


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_the_request_chunks_state_leads_contacts_and_setting_in_plain_words(case_id):
    request = CASES[case_id].request
    chunks = {c.id: c.text for c in request.chunks()}
    assert list(chunks) == [
        "request.date",
        "request.service_date",
        "request.procedure",
        "request.codes",
        "request.leads",
        "request.contacts",
        "request.place_of_service",
    ]
    assert chunks["request.leads"] == f"Number of leads: {request.leads}"
    assert chunks["request.contacts"] == f"Total number of contacts: {request.contacts}"
    setting = SCS_SETTINGS[request.place_of_service]
    assert chunks["request.place_of_service"] == (
        f"Place of service: {setting} (code {request.place_of_service})"
    )
    # every lead has 8 contacts, and the trial uses one lead
    assert request.contacts == 8 * request.leads
    assert (request.leads == 1) == (CASES[case_id].variant == "trial")


def test_the_builders_setting_words_are_the_models():
    assert BUILD.SCS_SETTING_WORDS == SCS_SETTINGS


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_the_leads_contacts_and_setting_in_the_notes_equal_the_request(case_id):
    """A note and the request must never disagree about the planned device or setting."""
    case = CASES[case_id]
    note_id = "note-trial-request" if case.variant == "trial" else "note-permanent-plan"
    body = note(case_id, note_id)
    leads, contacts = PLAN_LEADS.search(body).groups()
    assert (int(leads), int(contacts)) == (case.request.leads, case.request.contacts)
    assert PLAN_SETTING.search(body).group(1) == SCS_SETTINGS[case.request.place_of_service]
    # no other note names a different setting or device count
    for other_id, other in notes(case_id).items():
        if other_id != note_id:
            assert not PLAN_SETTING.search(other), other_id
            assert not PLAN_LEADS.search(other), other_id


def test_the_plan_names_the_labeled_leads_and_setting_words_of_the_request():
    for case in CASES.values():
        label = {x.criterion: x for x in case.expected}["leads-max-2-or-16-contacts"]
        assert f"{case.request.leads} lead" in label.fact
        assert f"{case.request.contacts} contacts" in label.fact


# --- 2. what is removed and rewritten --------------------------------------------------------


def test_the_removed_set_is_the_one_in_the_plan_and_in_the_builder():
    listed = set(REF.findall(plan_section("### Removed from every SCS bundle")))
    assert listed == set(BUILD.SCS_REMOVED) and len(listed) == 38 == len(BUILD.SCS_REMOVED)
    assert listed <= TEMPLATE_IDS
    assert len(TEMPLATE) - len(listed) == 130
    kinds = {k.split("/")[0] for k in listed}
    assert kinds == {
        "DocumentReference",
        "DiagnosticReport",
        "Condition",
        "Encounter",
        "Procedure",
        "Observation",
        "MedicationRequest",
    }


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_an_scs_bundle_has_none_of_the_removed_and_keeps_the_rest(case_id):
    present = {ref(r) for r in resources(case_id)}
    assert not present & set(BUILD.SCS_REMOVED)
    assert present >= TEMPLATE_IDS - set(BUILD.SCS_REMOVED)


def test_every_removed_resource_is_still_in_every_facet_and_pap_bundle():
    other = {
        k: v
        for k, v in load_cases().items()
        if v.policy in ("facet-joint-interventions", "pap-sleep-apnea")
    }
    assert len(other) == 17
    for case in other.values():
        present = {ref(e["resource"]) for e in load_bundle(case.bundle_path())["entry"]}
        assert set(BUILD.SCS_REMOVED) <= present, case.id


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_no_kept_resource_refers_to_or_mentions_a_removed_one(case_id):
    items = resources(case_id)
    assert not all_references(items) & set(BUILD.SCS_REMOVED)
    ids = {r.split("/")[1] for r in BUILD.SCS_REMOVED}
    for resource in items:
        body = text_of(resource)
        assert not CARDIAC.search(body), ref(resource)
        assert not {i for i in ids if i in body}, ref(resource)


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_an_scs_bundle_has_no_dangling_reference_the_template_lacks(case_id):
    def dangling(items: list[dict]) -> set[str]:
        return all_references(items) - {ref(r) for r in items}

    assert dangling(resources(case_id)) <= dangling(TEMPLATE)


def test_the_stripped_background_has_no_pain_surgery_drug_device_or_cardiac_term():
    background = BUILD.scs_background(json.loads(json.dumps(TEMPLATE)))
    assert len(background) == 130
    for resource in background:
        assert not PAIN_AND_DEVICE.search(text_of(resource)), ref(resource)
        assert not CARDIAC.search(text_of(resource)), ref(resource)


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_the_dast_total_is_rewritten_to_zero_and_nothing_else_states_a_dast_value(case_id):
    section = plan_section("### Rewritten in every SCS bundle")
    assert BUILD.DAST_TOTAL.split("/")[1] in section
    # the template has no item-level answers: the total stands alone
    template_dast = {ref(r) for r in TEMPLATE if DAST.search(text_of(r))}
    assert BUILD.DAST_TOTAL in template_dast
    original = next(r for r in TEMPLATE if ref(r) == BUILD.DAST_TOTAL)
    assert original["valueQuantity"]["value"] == 2 and "component" not in original
    mentions = {ref(r): r for r in resources(case_id) if DAST.search(text_of(r))}
    assert set(mentions) == {
        BUILD.DAST_TOTAL,
        "DiagnosticReport/37b75dda-e187-ae40-1258-a981d5b37caf",
        "Procedure/37b75dda-e187-ae40-91ce-e54a632ea650",
    }
    total = mentions[BUILD.DAST_TOTAL]
    assert total["valueQuantity"]["value"] == 0 and "component" not in total
    report = mentions["DiagnosticReport/37b75dda-e187-ae40-1258-a981d5b37caf"]
    assert not {"presentedForm", "conclusion", "component"} & set(report)
    assert not any(
        k.startswith("value") for k in mentions["Procedure/37b75dda-e187-ae40-91ce-e54a632ea650"]
    )
    # the AUDIT-C scores of 0 stay as they were
    audit = [
        r["valueQuantity"]["value"]
        for r in resources(case_id)
        if r["resourceType"] == "Observation" and r["code"]["coding"][0]["code"] == "75626-2"
    ]
    assert audit == [0, 0]


# --- 3. the evidence each label cites ------------------------------------------------------


def _evidence_text(case_id: str, reference: str) -> tuple[str, bool]:
    """What the model can quote from a cited resource, and whether it is a note."""
    kind, _, resource_id = reference.partition("/")
    if kind == "DocumentReference":
        return note(case_id, resource_id), True
    chunks = [
        c.text
        for c in chunk_bundle(load_bundle(CASES[case_id].bundle_path()))
        if c.ref == reference
    ]
    return "\n".join(chunks), False


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_every_cited_resource_states_the_fact_its_label_rests_on(case_id):
    facts = BUILD.SCS_CASES[case_id]
    phrases = BUILD.scs_label_phrases(facts)
    case = CASES[case_id]
    labels = {x.criterion: x for x in case.expected}
    for criterion, by_reference in phrases.items():
        assert criterion in labels
        assert labels[criterion].resources() == list(by_reference), criterion
        for reference, statements in by_reference.items():
            body, _ = _evidence_text(case_id, reference)
            assert body, f"{criterion}: {reference} has no text"
            for statement in statements:
                assert statement in body, f"{criterion}: {reference} lacks {statement!r}"


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_a_note_that_carries_a_criterion_marker_is_cited_by_that_criterion(case_id):
    phrases = BUILD.scs_label_phrases(BUILD.SCS_CASES[case_id])
    for criterion, marker in BUILD.SCS_MARKERS.items():
        if criterion not in phrases:
            continue
        carriers = {
            f"DocumentReference/{note_id}"
            for note_id, body in notes(case_id).items()
            if re.search(marker, body)
        }
        assert carriers <= set(phrases[criterion]), (
            criterion,
            sorted(carriers - set(phrases[criterion])),
        )


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_the_chunks_keep_every_statement_whole_so_the_model_can_quote_it(case_id):
    phrases = BUILD.scs_label_phrases(BUILD.SCS_CASES[case_id])
    chunks = chunk_bundle(load_bundle(CASES[case_id].bundle_path()))
    for criterion, by_reference in phrases.items():
        for reference, statements in by_reference.items():
            kind, _, resource_id = reference.partition("/")
            own = [c for c in chunks if (c.resource_type, c.resource_id) == (kind, resource_id)]
            for statement in statements:
                holders = [c for c in own if statement in c.text]
                assert holders, f"{criterion}: no chunk of {reference} holds {statement!r}"
                if kind == "DocumentReference":
                    assert any(c.char_start is not None for c in holders)


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_numbers_in_a_labels_fact_appear_in_the_notes_it_cites(case_id):
    labels = {x.criterion: x for x in CASES[case_id].expected}
    if CASES[case_id].variant == "permanent":
        label = labels["trial-success-50pct-and-function"]
        percent = re.search(r"(\(8 - \d\) / 8 = \d+%)", label.fact).group(1)
        assert percent in note(case_id, "note-trial-result")
        assert f"{percent} reduction in target pain" in note(case_id, "note-trial-result")
    for label in labels.values():
        cited = "\n".join(_evidence_text(case_id, r)[0] for r in label.resources())
        request = json.dumps(CASES[case_id].request.model_dump(mode="json"))
        for day in re.findall(r"\b20\d\d-\d\d-\d\d\b", label.fact):
            if label.resources():
                assert day in cited or day in request, (label.criterion, day)


# --- 4. the cases the labels turn on ---------------------------------------------------------


def test_scs_02_documents_an_active_cocaine_use_disorder_and_no_other_case_mentions_cocaine():
    body = note("scs-02", "note-psych-eval")
    assert BUILD.S_COCAINE in body
    assert BUILD.S_COCAINE_SCREEN.format(day="2026-09-08") in body
    assert "last use on 2026-09-04" in body
    assert BUILD.S_ADDICTION in note("scs-02", "note-trial-request")
    assert "Condition/cocaine-use-disorder" in added("scs-02")
    for case_id in CASES:
        if case_id != "scs-02":
            assert not re.search(r"cocaine", "".join(text_of(r) for r in added(case_id).values()))
            assert BUILD.S_NO_SUBSTANCE in note(case_id, "note-psych-eval")


def test_scs_04_has_no_note_that_states_or_implies_a_discussion_with_the_patient():
    for note_id, body in notes("scs-04").items():
        assert not DISCUSSION.search(body), note_id
    assert not [i for i in added("scs-04") if "education" in i]
    # the pattern does catch the education visit where there is one
    assert DISCUSSION.search(note("scs-02", "note-education"))
    assert DISCUSSION.search(note("scs-01", "note-education"))
    # and the team conference only says the team reviewed the evaluations
    assert "Reviewed:" in note("scs-04", "note-team-conference")


def test_scs_05_conflicts_only_on_whether_there_was_an_earlier_implant():
    items = notes("scs-05")
    evaluation, anesthesia = items["note-pain-clinic-eval"], items["note-preanesthesia"]
    assert BUILD.S_NO_PREVIOUS in evaluation and evaluation.startswith("2026-05-18\n")
    assert BUILD.S_EARLIER_IMPLANT in anesthesia and anesthesia.startswith("2026-09-28\n")
    # neither mentions the other, and no other note says anything about an earlier device
    assert not PRIOR_DEVICE.search(evaluation)
    assert not re.search(r"No previous|evaluation of|reconcil", anesthesia)
    for note_id, body in items.items():
        if note_id not in ("note-preanesthesia",):
            assert not PRIOR_DEVICE.search(body), note_id
    statements = [
        note_id
        for note_id, body in items.items()
        if re.search(r"No previous spinal cord stimulator|first permanent|generator in the", body)
    ]
    assert sorted(statements) == ["note-pain-clinic-eval", "note-preanesthesia"]
    assert "first permanent" not in note("scs-05", "note-permanent-plan")
    for resource in added("scs-05").values():
        if resource["resourceType"] in ("Device", "DeviceUseStatement", "Procedure"):
            assert "stimulator" not in json.dumps(resource).lower() or resource["id"] == "scs-trial"
    # the other permanent cases state it consistently
    for case_id in ("scs-01", "scs-03", "scs-06"):
        assert BUILD.S_FIRST_PERMANENT in note(case_id, "note-permanent-plan")
        assert "generator in the" not in note(case_id, "note-preanesthesia")


def test_scs_06_is_scs_01_with_an_office_setting_and_no_note_mentions_an_asc_or_hospital():
    one, six = CASES["scs-01"], CASES["scs-06"]
    assert six.request.place_of_service == "11" and one.request.place_of_service == "24"
    assert six.request.model_dump() | {"place_of_service": "24"} == one.request.model_dump()
    first, last = notes("scs-01"), notes("scs-06")
    assert set(first) == set(last)
    differing = [i for i in first if first[i] != last[i]]
    assert differing == ["note-permanent-plan"]
    assert "Planned setting: office." in last["note-permanent-plan"]
    for resource in added("scs-06").values():
        assert not OFFICE_FORBIDDEN.search(text_of(resource)), ref(resource)
    # apart from the plan note, the two bundles are the same resources
    assert {k: v for k, v in added("scs-01").items() if "note-permanent-plan" not in k} == {
        k: v for k, v in added("scs-06").items() if "note-permanent-plan" not in k
    }


# --- 5. exact numbers ----------------------------------------------------------------------


@pytest.mark.parametrize("case_id", [c for c in sorted(CASES) if CASES[c].variant == "permanent"])
def test_trial_relief_is_exact_and_matches_the_coded_scores(case_id):
    items = added(case_id)
    pre = items["Observation/pain-pre-trial"]["valueQuantity"]["value"]
    end = items["Observation/pain-end-trial"]["valueQuantity"]["value"]
    percent = Fraction(pre - end, pre) * 100
    assert pre == 8 == items["Observation/pain-nrs-baseline"]["valueQuantity"]["value"]
    assert percent.denominator == 1
    assert percent == 50 if case_id == "scs-03" else percent >= 60
    assert BUILD.scs_relief(pre, end) == percent
    # the pre-trial score is before the lead placement and the end score is seven days later
    assert items["Observation/pain-pre-trial"]["effectiveDateTime"].endswith("09:00:00-04:00")
    assert items["Procedure/scs-trial"]["performedDateTime"].startswith("2026-08-04T09:30")
    assert (
        _day(items["Observation/pain-end-trial"]) - _day(items["Observation/pain-pre-trial"])
    ).days == 7
    assert "analgesic" in note(case_id, "note-trial-result").lower()


def test_durations_and_the_pain_history_are_the_designed_ones():
    items = added("scs-01")
    assert (BUILD.SCS_PT_END - BUILD.SCS_PT_START).days == 84
    assert (BUILD.SCS_CBT_END - BUILD.SCS_CBT_START).days == 56
    assert BUILD._months_between(BUILD.SCS_ONSET, BUILD.SCS_EVAL) == 71
    assert "now present for 71 months" in note("scs-01", "note-pain-clinic-eval")
    pt = items["Procedure/physical-therapy-course"]["performedPeriod"]
    assert (pt["start"], pt["end"]) == ("2020-10-05", "2020-12-28")
    cbt = items["Procedure/cbt-course"]["performedPeriod"]
    assert (cbt["start"], cbt["end"]) == ("2022-02-07", "2022-04-04")
    assert "12 weeks" in note("scs-01", "note-pt-discharge")
    assert "8 weekly sessions" in note("scs-01", "note-cbt-discharge")


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_the_medications_active_on_the_request_date_are_the_same_in_every_case(case_id):
    medications = {
        r["id"]: r["status"]
        for r in added(case_id).values()
        if r["resourceType"] == "MedicationRequest"
    }
    assert medications == {
        "naproxen": "completed",
        "gabapentin": "active",
        "duloxetine": "active",
        "oxycodone-apap": "active",
    }
    for resource in added(case_id).values():
        if resource["resourceType"] == "MedicationRequest":
            assert date.fromisoformat(resource["authoredOn"]) < date(2026, 1, 1)


# --- 6. timelines ----------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_every_event_precedes_the_request_and_every_note_matches_its_encounter(case_id):
    items = added(case_id)
    for item, resource in items.items():
        assert _day(resource) < REQUEST_DATE, item
        assert _day(resource).weekday() < 5, item
        if resource["resourceType"] == "DocumentReference":
            body = note(case_id, resource["id"])
            assert body.startswith(_day(resource).isoformat() + "\n"), item
            encounter = items[resource["context"]["encounter"][0]["reference"]]
            # a course ends on the day of its note; any other encounter is that day
            assert date.fromisoformat(encounter["period"]["end"]) == _day(resource), item


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_the_visits_are_in_the_planned_order(case_id):
    items = added(case_id)
    if CASES[case_id].variant == "permanent":
        order = [
            "note-pcp",
            "note-pt-discharge",
            "note-postop",
            "note-pain-clinic-2021",
            "note-pain-clinic-2022",
            "note-cbt-discharge",
            "note-pain-clinic-eval",
            "note-psych-eval",
            "note-team-conference",
            "note-education",
            "note-trial-procedure",
            "note-trial-result",
            "note-permanent-plan",
            "note-preanesthesia",
        ]
    else:
        order = [
            "note-pcp",
            "note-pt-discharge",
            "note-postop",
            "note-pain-clinic-2021",
            "note-pain-clinic-2022",
            "note-cbt-discharge",
            "note-pain-clinic-eval",
            "note-psych-eval",
            "note-team-conference",
            *(["note-education"] if case_id == "scs-02" else []),
            "note-trial-request",
        ]
    days = [_day(items[f"DocumentReference/{n}"]) for n in order]
    assert days == sorted(days) and len(set(days)) == len(days)
    assert set(notes(case_id)) == set(order)
