"""Draft PAS request bundles (src/precertly/pas): one structural test per mapped element,
the envelope, the committed drafts, the no-submission guarantees and the validator run."""

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from functools import cache
from pathlib import Path

import pytest

from precertly.evals import load_cases
from precertly.evals.cases import EVALS_DIR
from precertly.pas import __main__ as cli
from precertly.pas import builder

ROOT = EVALS_DIR.parent
CASES = load_cases()
PAS = "http://hl7.org/fhir/us/davinci-pas/StructureDefinition"
X12_SYSTEM = re.compile(r"^https://codesystem\.x12\.org/005010/\d+$")


@cache
def files() -> dict[str, str]:
    return cli.drafts()


def bundle(case_id: str) -> dict:
    return json.loads(files()[f"{case_id}.draft.json"])


def envelope(case_id: str) -> dict:
    return json.loads(files()[f"{case_id}.envelope.json"])


def resources(case_id: str) -> dict[str, dict]:
    return {
        f"{e['resource']['resourceType']}/{e['resource']['id']}": e["resource"]
        for e in bundle(case_id)["entry"]
    }


def claim(case_id: str) -> dict:
    return bundle(case_id)["entry"][0]["resource"]


IDS = sorted(CASES)


def codings(value) -> list[dict]:
    """Every Coding anywhere in value."""
    found = []
    if isinstance(value, dict):
        if "system" in value and "code" in value:
            found.append(value)
        for child in value.values():
            found += codings(child)
    elif isinstance(value, list):
        for child in value:
            found += codings(child)
    return found


# --- Bundle ---------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", IDS)
def test_bundle_is_a_pas_request_collection_with_the_claim_first(case_id):
    b = bundle(case_id)
    assert b["meta"]["profile"] == [f"{PAS}/profile-pas-request-bundle|2.2.1"]
    assert b["type"] == "collection"
    assert b["entry"][0]["resource"]["resourceType"] == "Claim"
    assert sum(e["resource"]["resourceType"] == "Claim" for e in b["entry"]) == 1


@pytest.mark.parametrize("case_id", IDS)
def test_bundle_identifier_and_timestamp_are_deterministic(case_id):
    b = bundle(case_id)
    assert b["identifier"]["system"] == f"{builder.SYNTHETIC}/pas-draft"
    assert b["identifier"]["value"].startswith(f"{case_id}-")
    assert b["timestamp"] == f"{CASES[case_id].request.date.isoformat()}T00:00:00Z"


@pytest.mark.parametrize("case_id", IDS)
def test_full_urls_are_unique_and_in_the_project_namespace(case_id):
    urls = [e["fullUrl"] for e in bundle(case_id)["entry"]]
    assert len(urls) == len(set(urls))
    for entry in bundle(case_id)["entry"]:
        r = entry["resource"]
        assert entry["fullUrl"] == f"{builder.FULL_URL_BASE}/{r['resourceType']}/{r['id']}"


@pytest.mark.parametrize("case_id", IDS)
def test_every_reference_resolves_inside_the_bundle(case_id):
    inside = set(resources(case_id))

    def refs(value):
        if isinstance(value, dict):
            if set(value) == {"reference"}:
                yield value["reference"]
            for child in value.values():
                yield from refs(child)
        elif isinstance(value, list):
            for child in value:
                yield from refs(child)

    assert set(refs(bundle(case_id))) <= inside


# --- Claim ----------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", IDS)
def test_claim_status_use_type_and_priority(case_id):
    c = claim(case_id)
    assert c["meta"]["profile"] == [f"{PAS}/profile-claim|2.2.1"]
    assert (c["status"], c["use"]) == ("active", "preauthorization")
    assert c["type"]["coding"][0]["code"] == "professional"
    assert c["priority"]["coding"][0]["code"] == "normal"
    assert c["created"] == CASES[case_id].request.date.isoformat()
    assert c["identifier"] == [{"system": f"{builder.SYNTHETIC}/claim-id", "value": case_id}]


@pytest.mark.parametrize("case_id", IDS)
def test_claim_points_at_the_patient_insurer_provider_and_one_focal_coverage(case_id):
    c, inside = claim(case_id), resources(case_id)
    assert inside[c["patient"]["reference"]]["meta"]["profile"][0].startswith(
        f"{PAS}/profile-beneficiary"
    )
    assert c["insurer"]["reference"] == "Organization/insurer-placeholder"
    assert c["provider"]["reference"] == "Organization/requestor-placeholder"
    (insurance,) = c["insurance"]
    assert insurance == {
        "sequence": 1,
        "focal": True,
        "coverage": {"reference": "Coverage/coverage-placeholder"},
    }


@pytest.mark.parametrize("case_id", IDS)
def test_one_item_per_requested_code_with_cpt_codes_only(case_id):
    request = CASES[case_id].request
    items = claim(case_id)["item"]
    assert [i["sequence"] for i in items] == list(range(1, len(request.codes) + 1))
    for item, code in zip(items, request.codes, strict=True):
        assert item["productOrService"] == {"coding": [{"system": builder.CPT, "code": code}]}


@pytest.mark.parametrize("case_id", IDS)
def test_items_carry_the_service_date_and_place_of_service(case_id):
    request = CASES[case_id].request
    for item in claim(case_id)["item"]:
        assert item["servicedDate"] == request.service_date.isoformat()
        assert item["locationCodeableConcept"]["coding"] == [
            {"system": builder.PLACE_OF_SERVICE, "code": request.place_of_service}
        ]


@pytest.mark.parametrize("case_id", IDS)
def test_items_carry_the_required_x12_request_and_certification_types(case_id):
    for item in claim(case_id)["item"]:
        urls = [e["url"] for e in item["extension"]]
        assert urls == [
            f"{PAS}/extension-serviceItemRequestType",
            f"{PAS}/extension-certificationType",
        ]
        assert item["category"] == builder._x12("service_type")


@pytest.mark.parametrize("case_id", IDS)
def test_x12_codes_are_codes_only_and_from_the_ig_examples(case_id):
    x12 = [c for c in codings(bundle(case_id)) if X12_SYSTEM.match(c["system"])]
    assert x12
    allowed = set(builder.X12.values())
    for coding in x12:
        assert "display" not in coding
        assert (coding["system"], coding["code"]) in allowed
    assert set(builder.X12) == set(builder.X12_SOURCE)


def test_no_coding_anywhere_carries_a_cpt_descriptor():
    for case_id in IDS:
        for coding in codings(bundle(case_id)):
            if coding["system"] == builder.CPT:
                assert set(coding) == {"system", "code"}


@pytest.mark.parametrize("case_id", IDS)
def test_supporting_info_attaches_every_cited_note_with_document_information(case_id):
    c, inside = claim(case_id), resources(case_id)
    info = c.get("supportingInfo", [])
    assert [s["sequence"] for s in info] == list(range(1, len(info) + 1))
    for entry in info:
        assert entry["category"]["coding"][0]["code"] == "additionalInformation"
        (doc_info,) = entry["extension"]
        assert doc_info["url"] == f"{PAS}/extension-documentInformation"
        assert [e["url"] for e in doc_info["extension"]] == [
            "reportTypeCode",
            "transmissionMethod",
            "description",
        ]
        target = inside[entry["valueReference"]["reference"]]
        assert target["resourceType"] == "DocumentReference"
    attached = {a["document"] for a in envelope(case_id)["attachments"]}
    assert {s["valueReference"]["reference"] for s in info} == attached


@pytest.mark.parametrize("case_id", IDS)
def test_items_point_at_all_supporting_info(case_id):
    c = claim(case_id)
    sequences = [s["sequence"] for s in c.get("supportingInfo", [])]
    for item in c["item"]:
        assert item.get("informationSequence", []) == sequences


def test_attachments_include_notes_cited_by_unfavourable_verdicts():
    seen = {
        cite["verdict"]
        for case_id in IDS
        for attachment in envelope(case_id)["attachments"]
        for cite in attachment["cited_by"]
    }
    assert {"not_met", "insufficient"} & seen


@pytest.mark.parametrize("case_id", IDS)
def test_every_free_text_resource_any_verdict_cites_is_attached(case_id):
    cited = {
        citation.split("#")[0]
        for verdict in envelope(case_id)["verdicts"]
        for citation in verdict["citations"]
        if citation.startswith(("DocumentReference/", "DiagnosticReport/"))
    }
    assert {a["source"] for a in envelope(case_id)["attachments"]} == cited


@pytest.mark.parametrize("case_id", IDS)
def test_attached_documents_keep_their_content_verbatim(case_id):
    chart = {
        f"{e['resource']['resourceType']}/{e['resource']['id']}": e["resource"]
        for e in json.loads(CASES[case_id].bundle_path().read_text())["entry"]
    }
    inside = resources(case_id)
    for attachment in envelope(case_id)["attachments"]:
        document, source = inside[attachment["document"]], chart[attachment["source"]]
        original = source.get("content") or [{"attachment": a} for a in source["presentedForm"]]
        assert document["content"] == original
        assert document["subject"] == source["subject"]
        assert document["meta"]["profile"] == [f"{PAS}/profile-documentreference|2.2.1"]
        assert document["type"]["coding"][0]["system"] == builder.LOINC
        assert document["type"]["text"] == attachment["title"]
        assert "context" not in document  # encounter links are dropped and recorded
        if source["resourceType"] == "DocumentReference":
            dropped = [r["reference"] for r in source.get("context", {}).get("encounter", [])]
        else:
            dropped = [source["encounter"]["reference"]] if source.get("encounter") else []
        assert attachment["links_dropped"] == dropped


def test_a_note_title_without_a_reviewed_document_type_stops_the_build():
    with pytest.raises(ValueError, match="no reviewed LOINC document type"):
        builder._note_type("Telephone encounter")


# Cases whose chart has no earlier facet procedure, so no Condition is recorded as the
# reason for one: there is no candidate to map.
NO_CANDIDATE = {"facet-06", "facet-07", "facet-08", "facet-11"}


@pytest.mark.parametrize("case_id", IDS)
def test_claim_carries_no_diagnosis(case_id):
    assert "diagnosis" not in claim(case_id)
    records = envelope(case_id)["diagnoses"]
    if case_id in NO_CANDIDATE:
        assert records == []
        return
    (record,) = records
    assert record["condition"] == "Condition/chronic-low-back-pain"
    assert (record["icd10cm"], record["status"]) == (None, "not mapped; left out of the draft")


def test_the_diagnosis_map_is_reviewed_and_only_names_covered_codes():
    table = builder.diagnosis_map()
    assert table["source"]["document"] == "Article A58364"
    assert table["source"]["version"] == "42"
    for entry in table["mappings"]:
        assert entry["reason"] if entry["icd10cm"] is None else entry["icd10cm"]


# --- participants -------------------------------------------------------------------


@pytest.mark.parametrize("case_id", IDS)
def test_patient_is_the_synthetic_beneficiary(case_id):
    patient = next(r for r in resources(case_id).values() if r["resourceType"] == "Patient")
    chart = next(
        e["resource"]
        for e in json.loads(CASES[case_id].bundle_path().read_text())["entry"]
        if e["resource"]["resourceType"] == "Patient"
    )
    assert patient["identifier"] == [
        {"system": f"{builder.SYNTHETIC}/patient-id", "value": chart["id"]}
    ]
    assert patient["name"][0]["extension"][0]["valueCode"] == "unknown"
    assert (patient["gender"], patient.get("birthDate")) == (chart["gender"], chart["birthDate"])


@pytest.mark.parametrize("case_id", IDS)
def test_coverage_is_a_labelled_placeholder_for_the_patient(case_id):
    coverage = resources(case_id)["Coverage/coverage-placeholder"]
    (member,) = coverage["identifier"]
    assert member["system"] == f"{builder.SYNTHETIC}/member-id"
    assert member["value"] == f"SYNTHETIC-{case_id}"
    assert member["type"]["coding"][0]["code"] == "MB"
    assert coverage["status"] == "active"
    assert coverage["beneficiary"] == claim(case_id)["patient"]
    assert {c["code"] for c in coverage["relationship"]["coding"]} == {"self", "18"}
    assert coverage["payor"] == [{"reference": "Organization/insurer-placeholder"}]


@pytest.mark.parametrize("case_id", IDS)
def test_organizations_are_labelled_placeholders_without_identifiers(case_id):
    inside = resources(case_id)
    for ref, profile in (
        ("Organization/insurer-placeholder", "profile-insurer"),
        ("Organization/requestor-placeholder", "profile-requestor"),
    ):
        org = inside[ref]
        assert org["meta"]["profile"] == [f"{PAS}/{profile}|2.2.1"]
        assert org["active"] is True and "placeholder" in org["name"]
        assert "identifier" not in org


def test_no_npi_tin_or_naic_and_every_identifier_is_in_the_project_namespace():
    for case_id in IDS:
        text = files()[f"{case_id}.draft.json"]
        for forbidden in ("us-npi", "USEIN", "NAIC", "naic"):
            assert forbidden not in text
        for resource in resources(case_id).values():
            identifiers = resource.get("identifier", [])
            if isinstance(identifiers, dict):
                identifiers = [identifiers]
            for identifier in identifiers:
                assert identifier["system"].startswith(f"{builder.SYNTHETIC}/"), case_id


# --- draft status and the envelope -------------------------------------------------------


@pytest.mark.parametrize("case_id", IDS)
def test_bundle_and_claim_are_tagged_as_drafts_pending_human_approval(case_id):
    for resource in (bundle(case_id), claim(case_id)):
        assert resource["meta"]["tag"] == [builder.REVIEW_TAG]


@pytest.mark.parametrize("case_id", IDS)
def test_the_bundle_carries_no_verdicts(case_id):
    text = files()[f"{case_id}.draft.json"]
    for word in ("not_met", "insufficient", "not_applicable", "rationale", "verdict"):
        assert word not in text


@pytest.mark.parametrize("case_id", IDS)
def test_envelope_is_not_ready_without_a_covered_diagnosis(case_id):
    readiness = envelope(case_id)["readiness"]
    assert readiness["ready"] is False
    assert readiness["reasons"][0] == "no covered diagnosis code (A58364 v42)"
    assert readiness["summary"].startswith("not ready: no covered diagnosis code (A58364 v42)")
    assert not re.search(r"\bden(y|ial|ied)\b", readiness["summary"], re.I)  # no denial wording


@pytest.mark.parametrize("case_id", IDS)
def test_envelope_records_the_source_bundle_hash_and_pending_approval(case_id):
    e = envelope(case_id)
    assert e["draft"]["approval"] == "pending"
    assert e["source"]["results"] == cli.SOURCE.relative_to(ROOT).as_posix()
    import hashlib

    assert e["draft"]["sha256"] == hashlib.sha256(files()[e["draft"]["file"]].encode()).hexdigest()
    assert e["source"]["sha256"] == hashlib.sha256(cli.SOURCE.read_bytes()).hexdigest()


@pytest.mark.parametrize("case_id", IDS)
def test_envelope_lists_every_verdict_and_the_diagnosis_task(case_id):
    e = envelope(case_id)
    labelled = {label.criterion for label in CASES[case_id].expected}
    assert {v["criterion"] for v in e["verdicts"]} == labelled
    task = e["reviewer_tasks"][0]
    if case_id in NO_CANDIDATE:
        assert task.startswith(
            "No coded Condition in the chart is recorded as the reason for a procedure with "
            "one of the policy's codes. Assign an ICD-10-CM code from A58364 v42's covered "
            "list if the documentation supports one. See the attached notes and the MRI report"
        )
    else:
        assert task.startswith(
            "The chart's coded Condition, chronic low back pain (SNOMED 278860009), has no "
            "equivalent on A58364 v42's covered list. Assign an ICD-10-CM code from that list "
            "if the documentation supports one. See the attached notes and the MRI report"
        )
    assert "M47" not in task and "M54" not in task  # no code is suggested


def test_envelope_lists_cited_coded_resources_as_not_attached():
    listed = {r["resource"] for case_id in IDS for r in envelope(case_id)["cited_not_attached"]}
    assert any(ref.startswith("Procedure/") for ref in listed)
    assert all(not ref.startswith(("DocumentReference/", "request.")) for ref in listed)


# --- committed drafts, determinism and no submission path -------------------------------


def test_committed_drafts_match_the_builder():
    assert cli.main(["--check"]) == 0


def test_building_twice_gives_identical_files():
    assert cli.drafts() == files()


def test_the_pas_package_imports_no_network_library():
    banned = {"urllib", "http", "socket", "requests", "httpx", "aiohttp", "boto3", "botocore"}
    for path in Path(builder.__file__).parent.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in banned, f"{path.name} imports {name}"


def test_the_pas_package_has_no_submission_function():
    for path in Path(builder.__file__).parent.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                assert not re.search(r"submit|send|post|transmit", node.name), node.name


# --- the validator ------------------------------------------------------------------


# Slow (several minutes, mostly loading packages), so opt-in locally. CI's pas-validate job
# runs scripts/validate_pas.py directly.
@pytest.mark.skipif(
    os.environ.get("PRECERTLY_RUN_VALIDATOR") != "1" or shutil.which("java") is None,
    reason="set PRECERTLY_RUN_VALIDATOR=1 to run the HL7 FHIR Validator (needs Java)",
)
def test_the_hl7_validator_finds_no_errors_and_no_unlisted_warnings():
    result = subprocess.run(
        [sys.executable, "scripts/validate_pas.py"], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout[-3000:]
    assert "errors: 0" in result.stdout
