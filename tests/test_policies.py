import pytest
from pydantic import ValidationError

from precertly.policies import LogicNode, Policy, load_policies

EXPECTED = {
    "facet-joint-interventions",
    "spinal-cord-stimulator",
    "pap-sleep-apnea",
    "bariatric-surgery",
}


def test_all_four_policies_load():
    assert set(load_policies()) == EXPECTED


def test_every_policy_cites_a_source():
    for policy in load_policies().values():
        assert policy.sources, policy.id


def test_codes_route_to_expected_variants():
    policies = load_policies()
    facet = policies["facet-joint-interventions"]
    assert {v.id for v in facet.variant_for_code("64635")} == {"initial-rfa", "repeat-rfa"}
    assert [v.id for v in policies["pap-sleep-apnea"].variant_for_code("E0601")] == [
        "initial",
        "continued",
    ]


def test_pap_logic_has_alternative_paths():
    pap = load_policies()["pap-sleep-apnea"]
    initial = pap.variant("initial").logic
    assert {"ahi-15-plus", "ahi-5-to-14", "osa-symptom-or-condition"} <= initial.criterion_ids()


def test_logic_node_requires_exactly_one_operator():
    with pytest.raises(ValidationError):
        LogicNode(all_of=["a"], any_of=["b"])
    with pytest.raises(ValidationError):
        LogicNode()


def _minimal(**overrides):
    data = {
        "id": "demo",
        "title": "Demo",
        "document": "NCD 0",
        "verified_on": "2026-10-09",
        "sources": [{"title": "x", "url": "https://example.com"}],
        "criteria": [{"id": "a", "text": "A", "kind": "clinical"}],
        "variants": [{"id": "v", "title": "V", "codes": ["00000"], "logic": {"all_of": ["a"]}}],
    }
    data.update(overrides)
    return data


def test_unknown_criterion_in_logic_is_rejected():
    bad = _minimal(
        variants=[{"id": "v", "title": "V", "codes": ["0"], "logic": {"all_of": ["a", "missing"]}}]
    )
    with pytest.raises(ValidationError, match="unknown criteria"):
        Policy.model_validate(bad)


def test_unused_criterion_is_rejected():
    bad = _minimal(
        criteria=[
            {"id": "a", "text": "A", "kind": "clinical"},
            {"id": "b", "text": "B", "kind": "clinical"},
        ]
    )
    with pytest.raises(ValidationError, match="never used"):
        Policy.model_validate(bad)
