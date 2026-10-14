"""Every eval case must agree with its policy, its bundle and the case plan."""

import importlib.util
import json
import re
import sys

import pytest

from precertly.evals import EVALS_DIR, Outcome, load_cases, overall_outcome, scored_criteria
from precertly.fhir import chunk_bundle, load_bundle
from precertly.policies import load_policies

CASES = load_cases()
POLICIES = load_policies()


def _plan() -> dict[str, tuple[str, str]]:
    """case id -> (variant, overall outcome) from the PLAN.md tables."""
    rows = {}
    for plan in (EVALS_DIR / "cases").rglob("PLAN.md"):
        for line in plan.read_text(encoding="utf-8").splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) >= 5 and re.fullmatch(r"`[a-z]+-\d+`", cells[0]):
                rows[cells[0].strip("`")] = (cells[1], cells[4])
    return rows


def _build_module():
    spec = importlib.util.spec_from_file_location("build_cases", EVALS_DIR / "build_cases.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up while it loads
    spec.loader.exec_module(module)
    return module


def test_every_planned_case_exists():
    assert set(_plan()) == set(CASES)
    assert len(CASES) == 11


@pytest.mark.parametrize("case", CASES.values(), ids=list(CASES))
def test_case_matches_its_policy_version(case):
    policy = POLICIES[case.policy]
    assert case.policy_version == {s.document: s.version for s in policy.sources}
    # at least one requested code routes to the variant (a third-level code does not)
    assert set(case.request.codes) & set(policy.variant(case.variant).codes)


@pytest.mark.parametrize("case", CASES.values(), ids=list(CASES))
def test_every_scored_criterion_is_labeled_once(case):
    labeled = [label.criterion for label in case.expected]
    assert labeled == scored_criteria(POLICIES[case.policy], case.variant)


@pytest.mark.parametrize("case", CASES.values(), ids=list(CASES))
def test_not_applicable_only_for_conditional_criteria(case):
    policy = POLICIES[case.policy]
    for label in case.expected:
        if label.outcome is Outcome.NOT_APPLICABLE:
            assert policy.criterion(label.criterion).condition, label.criterion
            assert label.evidence == [], label.criterion


@pytest.mark.parametrize("case", CASES.values(), ids=list(CASES))
def test_cited_evidence_exists(case):
    bundle = load_bundle(case.bundle_path())
    resources = {f"{e['resource']['resourceType']}/{e['resource']['id']}" for e in bundle["entry"]}
    chunked = {chunk.ref for chunk in chunk_bundle(bundle)}
    for label in case.expected:
        for reference in label.resources():
            assert reference in resources, f"{label.criterion}: {reference} not in bundle"
            # the agent only sees chunks, so cited evidence has to survive chunking
            assert reference in chunked, f"{label.criterion}: {reference} produces no chunk"
        for field in label.request_fields():
            assert field in type(case.request).model_fields, f"{label.criterion}: request.{field}"


@pytest.mark.parametrize("case", CASES.values(), ids=list(CASES))
def test_labels_cite_the_right_kind_of_evidence(case):
    for label in case.expected:
        stage = POLICIES[case.policy].criterion(label.criterion).evidence_stage
        if label.outcome is Outcome.NOT_APPLICABLE:
            continue
        if stage == "request":
            assert label.request_fields(), f"{label.criterion}: cite the request field"
        elif label.outcome in (Outcome.MET, Outcome.NOT_MET):
            assert label.resources(), f"{label.criterion}: a chart label needs a resource"


@pytest.mark.parametrize("case", CASES.values(), ids=list(CASES))
def test_overall_outcome_is_computed_and_matches_the_plan(case):
    logic = POLICIES[case.policy].variant(case.variant).logic
    computed = overall_outcome(logic, case.outcomes())
    assert computed is case.expected_overall
    variant, planned = _plan()[case.id]
    assert (case.variant, computed.value) == (variant, planned)


@pytest.mark.parametrize("case", CASES.values(), ids=list(CASES))
def test_changed_labels_quote_the_policy(case):
    policy = POLICIES[case.policy]
    explained = {change.criterion for change in case.changed_labels}
    failing = {
        label.criterion
        for label in case.expected
        if label.outcome in (Outcome.NOT_MET, Outcome.INSUFFICIENT)
    }
    assert failing <= explained
    for change in case.changed_labels:
        assert change.quote in policy.criterion(change.criterion).quote, change.criterion


def test_planned_second_block_date_is_the_requested_service_date():
    facts = _build_module().CASES
    for case in CASES.values():
        if case.variant == "second-diagnostic":
            assert facts[case.id].planned_second_block == case.request.service_date, case.id
        assert facts[case.id].variant == case.variant, case.id


def test_template_patient_is_demographics_only_and_65_or_older():
    template = json.loads((EVALS_DIR / "template" / "patient.json").read_text(encoding="utf-8"))
    (patient,) = [r for r in template if r["resourceType"] == "Patient"]
    assert set(patient) == {"resourceType", "id", "gender", "birthDate"}
    request_date = next(iter(CASES.values())).request.date
    assert patient["birthDate"] <= request_date.replace(year=request_date.year - 65).isoformat()


def test_committed_bundles_match_the_build_script():
    build_cases = _build_module()
    assert set(build_cases.CASES) == {c.id for c in CASES.values()}
    for case in CASES.values():
        built = build_cases.dumps(build_cases.build(case.id))
        assert case.bundle_path().read_text(encoding="utf-8") == built, case.id


def test_label_evidence_lists_are_generated_by_the_build_script():
    build_cases = _build_module()
    for case in CASES.values():
        path = EVALS_DIR / "cases" / "facet" / f"{case.id}.yaml"
        assert path.read_text(encoding="utf-8") == build_cases.with_generated_evidence(case.id)
        generated = build_cases.label_evidence(build_cases.CASES[case.id])
        for label in case.expected:
            expected = [] if label.outcome is Outcome.NOT_APPLICABLE else generated[label.criterion]
            assert label.evidence == expected, f"{case.id}: {label.criterion}"


def test_overall_outcome_logic():
    from precertly.policies import LogicNode

    logic = LogicNode(all_of=["a", LogicNode(any_of=["b", "c"]), "d"])
    met, not_met, insufficient = Outcome.MET, Outcome.NOT_MET, Outcome.INSUFFICIENT
    assert overall_outcome(logic, {"a": met, "b": not_met, "c": met}) is met  # d unlabeled
    assert overall_outcome(logic, {"a": met, "b": not_met, "c": insufficient}) is insufficient
    assert overall_outcome(logic, {"a": met, "b": not_met, "c": not_met}) is not_met
    assert overall_outcome(logic, {"a": not_met, "b": insufficient, "c": met}) is not_met
    assert overall_outcome(logic, {"a": Outcome.NOT_APPLICABLE, "b": met}) is met
