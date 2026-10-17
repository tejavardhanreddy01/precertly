"""The request model for facet and non-facet procedures.

A facet request must carry technique, region, levels and same_day_blocks; any other request
(PAP, for one) leaves them unset and renders no chunk for them. The facet prompts must stay
byte-identical: tests/golden/facet_request_chunks.json was captured before the facet fields
became optional.
"""

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from precertly.evals.cases import EVALS_DIR, FACET_POLICY, EvalCase, load_cases
from precertly.judgment import rules
from precertly.judgment.models import FACET_FIELDS, PriorAuthRequest
from precertly.judgment.structured import FACET_CODES, StructuredChart
from precertly.policies import load_policies

GOLDEN = Path(__file__).parent / "golden" / "facet_request_chunks.json"
FACET_CASE = yaml.safe_load((EVALS_DIR / "cases" / "facet" / "facet-02.yaml").read_text())
FACET_REQUEST = FACET_CASE["request"]
PAP_REQUEST = {
    "date": "2026-10-05",
    "service_date": "2026-10-19",
    "procedure": "continuous positive airway pressure device",
    "codes": ["E0601"],
}


def facet_cases():
    return {k: c for k, c in load_cases().items() if c.policy == FACET_POLICY}


def without(fields, *names):
    return {k: v for k, v in fields.items() if k not in names}


# --- (i) the request model -------------------------------------------------------------


@pytest.mark.parametrize("field", FACET_FIELDS)
def test_a_facet_request_missing_a_facet_field_is_rejected(field):
    with pytest.raises(ValidationError, match=field):
        PriorAuthRequest.model_validate(without(FACET_REQUEST, field))


def test_same_day_blocks_is_a_required_facet_field():
    """Named outright, so dropping it from FACET_FIELDS cannot silently remove this check."""
    assert "same_day_blocks" in FACET_FIELDS
    with pytest.raises(ValidationError, match="same_day_blocks"):
        PriorAuthRequest.model_validate(without(FACET_REQUEST, "same_day_blocks"))
    with pytest.raises(ValidationError, match="same_day_blocks"):
        PriorAuthRequest.model_validate(FACET_REQUEST | {"same_day_blocks": None})
    # an empty list is an answer ("none listed") and stays valid
    assert PriorAuthRequest.model_validate(FACET_REQUEST | {"same_day_blocks": []})


def test_a_facet_case_missing_same_day_blocks_fails_to_load():
    request = without(FACET_REQUEST, "same_day_blocks")
    with pytest.raises(ValidationError, match="same_day_blocks"):
        EvalCase.model_validate(FACET_CASE | {"request": request})


def test_a_facet_request_with_empty_levels_is_rejected():
    with pytest.raises(ValidationError):
        PriorAuthRequest.model_validate(FACET_REQUEST | {"levels": []})


@pytest.mark.parametrize("code", sorted(FACET_CODES))
def test_any_facet_code_makes_it_a_facet_request(code):
    with pytest.raises(ValidationError, match="levels"):
        PriorAuthRequest.model_validate(PAP_REQUEST | {"codes": ["E0601", code]})


def test_a_partly_facet_shaped_request_is_rejected():
    """No facet code, but a facet field set: fail closed rather than guess."""
    with pytest.raises(ValidationError, match="region"):
        PriorAuthRequest.model_validate(PAP_REQUEST | {"technique": "medial_branch_block"})


def test_facet_codes_cover_every_facet_variant_and_case_and_no_other_policy():
    policies = load_policies()
    for variant in policies[FACET_POLICY].variants:
        assert set(variant.codes) <= FACET_CODES, variant.id
    for case in facet_cases().values():
        assert set(case.request.codes) <= FACET_CODES, case.id
    assert "64495" in FACET_CODES  # facet-08's third level
    for policy_id, policy in policies.items():
        if policy_id != FACET_POLICY:
            for variant in policy.variants:
                assert not set(variant.codes) & FACET_CODES, (policy_id, variant.id)


# --- (ii) the eval case ----------------------------------------------------------------


@pytest.mark.parametrize("field", FACET_FIELDS)
def test_a_facet_case_missing_a_facet_field_fails_to_load(field):
    with pytest.raises(ValidationError, match=field):
        EvalCase.model_validate(FACET_CASE | {"request": without(FACET_REQUEST, field)})


def test_a_facet_case_fails_on_the_policy_even_without_a_facet_code():
    """A mistyped code must not turn a facet case into a non-facet one."""
    request = without(FACET_REQUEST, *FACET_FIELDS) | {"codes": ["64999"]}
    PriorAuthRequest.model_validate(request)  # the request alone cannot tell
    with pytest.raises(ValidationError, match="technique, region, levels, same_day_blocks"):
        EvalCase.model_validate(FACET_CASE | {"request": request})


# --- (iii) a non-facet request ---------------------------------------------------------


def test_a_pap_request_loads_and_renders_only_its_own_fields():
    request = PriorAuthRequest.model_validate(PAP_REQUEST)
    assert all(getattr(request, field) is None for field in FACET_FIELDS)
    assert [c.id for c in request.chunks()] == [
        "request.date",
        "request.service_date",
        "request.procedure",
        "request.codes",
    ]
    assert request.chunks()[-1].text == "Procedure codes: E0601"


def test_an_empty_same_day_list_still_renders_none():
    chunks = {c.id: c.text for c in PriorAuthRequest.model_validate(FACET_REQUEST).chunks()}
    assert chunks["request.same_day_blocks"] == "Other blocks planned for the same day: none"


# --- (iv) the rules fail loudly on a malformed facet request ----------------------------


def malformed(*names):
    """A facet request with these fields unset, built without validation."""
    fields = PriorAuthRequest.model_validate(FACET_REQUEST).model_dump()
    return PriorAuthRequest.model_construct(**(fields | dict.fromkeys(names)))


EMPTY_CHART = StructuredChart.from_bundle({"entry": []})
FACET = load_policies()[FACET_POLICY]


@pytest.mark.parametrize("field", FACET_FIELDS)
@pytest.mark.parametrize("criterion", sorted(rules.RULES))
def test_every_rule_raises_on_a_missing_facet_field(criterion, field):
    with pytest.raises(ValueError, match=field):
        rules.evaluate(FACET.criterion(criterion), EMPTY_CHART, malformed(field))


@pytest.mark.parametrize("criterion", sorted(rules.RULES))
def test_every_rule_raises_on_empty_levels(criterion):
    request = malformed().model_copy(update={"levels": []})
    with pytest.raises(ValueError, match="levels"):
        rules.evaluate(FACET.criterion(criterion), EMPTY_CHART, request)


@pytest.mark.parametrize(
    ("rule", "field"),
    [
        (rules.same_day_blocks, "same_day_blocks"),
        (rules.intraarticular_only_if_needed, "technique"),
        (rules.diagnostics_repeated_after_two_years, "region"),
        (rules.rfa_sessions_max, "region"),
        (rules.diagnostic_sessions_max, "region"),
        (rules.second_diagnostic_interval, "region"),
        (rules.first_diagnostic_relief, "region"),
        (rules.two_blocks_relief, "region"),
        (rules.pain_scale_after_each_diagnostic, "region"),
        (rules.prior_rfa_improvement, "region"),
        (rules.disability_scale_at_baseline, "region"),
    ],
)
def test_rules_called_directly_raise_rather_than_decide(rule, field):
    criterion = next(c for c, r in rules.RULES.items() if r is rule)
    with pytest.raises(ValueError, match=field):
        rule(EMPTY_CHART, malformed(field), FACET.criterion(criterion))


@pytest.mark.parametrize("field", ["levels", "region"])
def test_same_site_raises_rather_than_answering_unclear(field):
    procedure = {
        "resourceType": "Procedure",
        "id": "mbb1",
        "status": "completed",
        "code": {"coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": "64493"}]},
        "performedDateTime": "2026-09-01T09:30:00-04:00",
        "bodySite": [{"text": "Lumbar facet joints L4-5 and L5-S1, bilateral"}],
    }
    chart = StructuredChart.from_bundle({"entry": [{"resource": procedure}]})
    with pytest.raises(ValueError, match=field):
        rules.same_site(chart.procedures[0], malformed(field))


# --- (v) the facet prompts are unchanged -----------------------------------------------


def test_facet_request_chunks_match_the_golden():
    golden = json.loads(GOLDEN.read_text())
    cases = facet_cases()
    assert sorted(cases) == sorted(golden)
    for case_id, case in cases.items():
        assert [[c.id, c.text] for c in case.request.chunks()] == golden[case_id], case_id
