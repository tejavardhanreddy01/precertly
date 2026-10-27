"""A rule's computed facts reach the live result, and nothing else is added to it."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from precertly.evals import load_cases
from precertly.judgment.models import (
    CaseJudgment,
    ChecklistItem,
    ComputedCitation,
    CriterionVerdict,
)
from precertly.live.models import LiveComputed, LiveVerdict
from precertly.live.runner import build_result
from precertly.llm.client import LLMUsage


def _verdict(criterion: str, decided_by: str, computed: list[ComputedCitation]) -> CriterionVerdict:
    return CriterionVerdict(
        criterion=criterion,
        outcome="met" if computed else "insufficient",
        confidence=1.0 if computed else 0.0,
        rationale="r",
        model_outcome="met",
        usage=LLMUsage(model_id="m"),
        decided_by=decided_by,  # type: ignore[arg-type]
        rule_kind="decided" if decided_by == "rule" else None,
        computed=computed,
    )


def test_build_result_carries_computed_facts_and_adds_no_label() -> None:
    case = load_cases()["facet-01"]
    fact = ComputedCitation(
        statement="Coded disability score 48 % on 2026-08-10, before 2026-08-24.",
        inputs=["Observation/odi-baseline"],
        rule="disability-scale-at-baseline",
        rules_version="v-test",
    )
    judgment = CaseJudgment(
        policy=case.policy,
        variant=case.variant,
        verdicts=[
            _verdict("rule-with-inputs", "rule", [fact]),
            _verdict("rule-without", "rule", []),
            _verdict("model-insufficient", "model", []),
        ],
        overall="insufficient",
        reviewer_checklist=[ChecklistItem(criterion="c", evidence_stage="payer", text="t")],
    )
    result, _, _ = build_result(case, judgment, 1.0)
    by = {v.criterion: v for v in result.verdicts}
    assert [(c.statement, c.inputs, c.rule) for c in by["rule-with-inputs"].computed] == [
        (fact.statement, ["Observation/odi-baseline"], "disability-scale-at-baseline")
    ]
    assert by["rule-without"].computed == []
    assert by["model-insufficient"].computed == []
    dumped = result.model_dump()
    names = {k for v in dumped["verdicts"] for c in v["computed"] for k in c}
    assert names == {
        "statement",
        "inputs",
        "rule",
        "rules_version",
        "branch",
        "cited_by_model",
        "quote",
    }


def test_a_computed_fact_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        LiveComputed.model_validate({"statement": "s", "inputs": [], "expected": "met"})


def test_a_verdict_without_computed_facts_defaults_to_none() -> None:
    v = LiveVerdict(
        criterion="c",
        outcome="insufficient",
        applicable=True,
        decided_by="model",
        confidence=0.0,
        rationale="r",
        citations=[],
    )
    assert v.computed == []
