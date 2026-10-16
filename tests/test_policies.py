import pytest
from pydantic import ValidationError

from precertly.policies import EvidenceStage, LogicNode, Policy, load_policies

EXPECTED = {
    "facet-joint-interventions",
    "spinal-cord-stimulator",
    "pap-sleep-apnea",
    "bariatric-surgery",
}


def test_all_four_policies_load():
    assert set(load_policies()) == EXPECTED


def test_sources_are_governing_documents_with_version_and_dates():
    for policy in load_policies().values():
        assert policy.verified_on
        for source in policy.sources:
            assert source.document.split()[0] in {"NCD", "LCD", "Article"}, policy.id
            assert source.version and source.effective_date
            assert source.url.host == "www.cms.gov"
            assert "checklist" not in source.title.lower()


def test_every_criterion_quotes_a_listed_governing_document():
    for policy in load_policies().values():
        documents = {s.document for s in policy.sources}
        for criterion in policy.criteria:
            assert criterion.source_document in documents, criterion.id
            assert criterion.source_section, criterion.id
            assert all(len(part) > 20 for part in criterion.quote_parts()), criterion.id


def test_conditional_criteria_say_when_they_apply():
    facet = load_policies()["facet-joint-interventions"]
    assert facet.criterion("diagnostics-repeated-after-2-years").condition.startswith("Two years")
    assert facet.criterion("pain-minimum-3-months").condition is None


def test_every_conditional_criterion_names_where_its_condition_evidence_is():
    from precertly.judgment import PriorAuthRequest

    fields = set(PriorAuthRequest.model_fields)
    for policy in load_policies().values():
        for criterion in policy.criteria:
            if criterion.condition is None:
                assert criterion.condition_evidence is None, criterion.id
                continue
            evidence = criterion.condition_evidence
            assert evidence == "chart" or evidence.removeprefix("request.") in fields, criterion.id


def test_condition_and_condition_evidence_go_together():
    criterion = load_policies()["facet-joint-interventions"].criterion("same-day-blocks-justified")
    data = criterion.model_dump()
    with pytest.raises(ValidationError, match="go together"):
        type(criterion).model_validate(data | {"condition_evidence": None})
    with pytest.raises(ValidationError, match="go together"):
        type(criterion).model_validate(data | {"condition": None})
    with pytest.raises(ValidationError):
        type(criterion).model_validate(data | {"condition_evidence": "notes"})


def test_facet_thresholds_match_the_lcd():
    facet = load_policies()["facet-joint-interventions"]
    assert facet.criterion("pain-minimum-3-months").thresholds == {"min_duration_months": 3}
    # the LCD gives no duration for conservative management
    assert facet.criterion("conservative-care-failed").thresholds == {}
    interval = facet.criterion("second-diagnostic-2-weeks-after-first").thresholds
    assert interval == {"min_interval_weeks": 2, "documented_exception_allowed": True}
    second = facet.variant("second-diagnostic").logic.criterion_ids()
    assert {
        "pain-scale-at-baseline",
        "pain-scale-after-each-diagnostic",
        "disability-scale-at-baseline",
    } <= second


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


def _criterion(criterion_id, **overrides):
    data = {
        "id": criterion_id,
        "text": criterion_id.upper(),
        "kind": "clinical",
        "evidence_stage": "chart",
        "quote": "The beneficiary has X.",
        "source_document": "NCD 0",
        "source_section": "B",
    }
    data.update(overrides)
    return data


def _minimal(**overrides):
    data = {
        "id": "demo",
        "title": "Demo",
        "document": "NCD 0",
        "verified_on": "2026-10-09",
        "sources": [
            {
                "document": "NCD 0",
                "title": "x",
                "url": "https://example.com",
                "version": "1",
                "effective_date": "2020-01-01",
            }
        ],
        "criteria": [_criterion("a")],
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
    bad = _minimal(criteria=[_criterion("a"), _criterion("b")])
    with pytest.raises(ValidationError, match="never used"):
        Policy.model_validate(bad)


def test_criterion_must_cite_one_of_the_policy_sources():
    bad = _minimal(criteria=[_criterion("a", source_document="LCD L1")])
    with pytest.raises(ValidationError, match="not one of the policy's sources"):
        Policy.model_validate(bad)


def test_criterion_needs_a_quote():
    bad = _minimal(criteria=[_criterion("a", quote="")])
    with pytest.raises(ValidationError):
        Policy.model_validate(bad)


def test_evidence_stage_is_required():
    criterion = _criterion("a")
    del criterion["evidence_stage"]
    with pytest.raises(ValidationError, match="evidence_stage"):
        Policy.model_validate(_minimal(criteria=[criterion]))
    with pytest.raises(ValidationError):
        Policy.model_validate(_minimal(criteria=[_criterion("a", evidence_stage="pre_service")]))


def test_evidence_stages_of_known_criteria():
    policies = load_policies()
    facet = policies["facet-joint-interventions"]
    assert facet.criterion("pain-minimum-3-months").evidence_stage is EvidenceStage.CHART
    assert facet.criterion("levels-1-to-2").evidence_stage is EvidenceStage.REQUEST
    pap = policies["pap-sleep-apnea"]
    # relative to the continued-coverage request, adherence data is already in the record
    assert pap.criterion("adherence-4h-70pct").evidence_stage is EvidenceStage.CHART
    assert facet.criterion("thermal-rf-only").evidence_stage is EvidenceStage.POST_SERVICE
    assert facet.criterion("same-day-blocks-justified").evidence_stage is EvidenceStage.CHART
    lsg = policies["bariatric-surgery"].criterion("lsg-covered-by-local-mac")
    assert lsg.evidence_stage is EvidenceStage.PAYER
    scs = policies["spinal-cord-stimulator"]
    stage = scs.criterion("facilities-and-personnel-available").evidence_stage
    assert stage is EvidenceStage.PROVIDER_ATTESTATION


def test_pain_scale_after_diagnostics_applies_only_where_a_diagnostic_came_first():
    facet = load_policies()["facet-joint-interventions"]
    baseline = facet.criterion("pain-scale-at-baseline")
    after = facet.criterion("pain-scale-after-each-diagnostic")
    assert baseline.quote == after.quote
    with_after = {
        v.id
        for v in facet.variants
        if "pain-scale-after-each-diagnostic" in v.logic.criterion_ids()
    }
    # not first-diagnostic (nothing came before) and not repeat-rfa (justified by the prior
    # RFA; repeated diagnostics are the conditional diagnostics-repeated-after-2-years)
    assert with_after == {"second-diagnostic", "therapeutic-injection", "initial-rfa"}
    assert all("pain-scale-at-baseline" in v.logic.criterion_ids() for v in facet.variants)
