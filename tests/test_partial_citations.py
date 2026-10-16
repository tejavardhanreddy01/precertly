"""The partial path: the computed fact is evidence item [C1], and PARTIAL_BRANCHES fixes
which outcomes may rest on it alone. One test per cell of the reviewed table."""

import asyncio
from datetime import date

import pytest

from precertly.judgment import (
    Citation,
    EvidenceChunk,
    ModelJudgment,
    PriorAuthRequest,
    RuleResult,
    build_prompt,
    judge_criterion,
    judge_with_rule,
)
from precertly.judgment.rules import PARTIAL_BRANCHES, RULES_VERSION
from precertly.llm import LLMUsage
from precertly.policies import load_policies

FACET = load_policies()["facet-joint-interventions"]
REQUEST = PriorAuthRequest(
    date=date(2026, 10, 5),
    service_date=date(2026, 10, 8),
    procedure="medial branch block",
    technique="medial_branch_block",
    codes=["64493"],
    region="lumbar",
    levels=["L4-5", "L5-S1"],
)
NOTE = EvidenceChunk(
    id="DocumentReference/note-planning#0",
    text="Anticoagulation starts on 2026-10-12, so the second block cannot wait.",
    effective_date=date(2026, 9, 30),
)
CHUNKS = [*REQUEST.chunks(), NOTE]
E_NOTE = f"E{len(CHUNKS)}"  # the chart note's handle
E_SERVICE_DATE = "E2"  # request.service_date
STATEMENT = "The planned service date 2026-10-08 is 10 days after the coded injection."

# The reviewed design table, as approved: "alone" means [C1] alone may support the
# outcome, "chart" means a verified chart citation is required, "contradicts" means the
# outcome contradicts [C1] (it establishes the condition) and is always invalid.
APPROVED = {
    "1a": {"met": "chart", "not_met": "alone"},
    "1b": {"met": "chart", "not_met": "chart"},
    "2a": {"met": "alone", "not_met": "chart"},
    "2b": {"met": "chart", "not_met": "chart"},
    "3a": {"met": "alone", "not_met": "chart"},
    "3b": {"met": "chart", "not_met": "chart"},
    "4": {"met": "chart", "not_met": "chart"},
    "5a": {"met": "chart", "not_met": "chart"},
    "5b": {"met": "chart", "not_met": "chart"},
    "6": {"met": "chart", "not_met": "chart"},
    "7": {"met": "chart", "not_met": "alone", "not_applicable": "contradicts"},
    "8": {"met": "chart", "not_met": "alone", "not_applicable": "contradicts"},
    "9": {"met": "chart", "not_met": "chart", "not_applicable": "chart"},
    "10": {"met": "chart", "not_met": "chart", "not_applicable": "chart"},
}
CELLS = [
    (key, outcome, need)
    for key, branch in PARTIAL_BRANCHES.items()
    for outcome, need in APPROVED[branch.row].items()
]


class Response:
    def __init__(self, data):
        self.data = data
        self.usage = LLMUsage(model_id="fake", input_tokens=100, output_tokens=20)
        self.exchanges = []


class FakeLLM:
    def __init__(self, answer):
        self.answer = answer
        self.prompts = []

    async def structured(self, prompt, schema, **options):
        self.prompts.append(prompt)
        return Response(self.answer)


def _partial(key):
    return RuleResult(kind="partial", branch=key, statement=STATEMENT, inputs=["Procedure/mbb1"])


def _answer(outcome, *citations):
    """outcome is a final outcome; not_applicable is met with condition_holds false."""
    return ModelJudgment(
        condition_holds=outcome != "not_applicable",
        outcome="met" if outcome == "not_applicable" else outcome,
        confidence=0.9,
        rationale="because",
        citations=[Citation(evidence=handle, first_words=words) for handle, words in citations],
    )


C1 = ("C1", "The planned service")
CHART = (f"{E_NOTE}.1", "Anticoagulation starts on")
REQUEST_FIELD = (f"{E_SERVICE_DATE}.1", "Planned date of")


def _judge(key, answer):
    criterion = FACET.criterion(PARTIAL_BRANCHES[key].criterion)
    llm = FakeLLM(answer)
    verdict = asyncio.run(judge_with_rule(llm, criterion, REQUEST, CHUNKS, _partial(key)))
    return verdict, llm


def _final(verdict):
    return verdict.outcome if verdict.applicable else "not_applicable"


def test_the_table_in_code_is_the_reviewed_table():
    assert {b.row for b in PARTIAL_BRANCHES.values()} == set(APPROVED)
    for branch in PARTIAL_BRANCHES.values():
        criterion = FACET.criterion(branch.criterion)
        expected = APPROVED[branch.row]
        assert ("not_applicable" in expected) == bool(criterion.condition), branch.row
        for outcome, need in expected.items():
            assert branch.needs_chart(outcome) == (need != "alone"), (branch.row, outcome)
        assert branch.condition_fixed == (expected.get("not_applicable") == "contradicts")


@pytest.mark.parametrize(("key", "outcome", "need"), CELLS)
def test_c1_alone(key, outcome, need):
    verdict, _ = _judge(key, _answer(outcome, C1))
    if need == "alone":
        assert (_final(verdict), verdict.invalidated) == (outcome, None)
        (computed,) = verdict.computed
        assert computed.cited_by_model and computed.quote == STATEMENT
    else:
        assert verdict.outcome == "insufficient" and verdict.invalidated
        assert verdict.computed[0].cited_by_model is False


@pytest.mark.parametrize(("key", "outcome", "need"), CELLS)
def test_c1_with_a_request_field_is_not_a_chart_citation(key, outcome, need):
    verdict, _ = _judge(key, _answer(outcome, C1, REQUEST_FIELD))
    if need == "alone":
        assert _final(verdict) == outcome
    else:
        assert verdict.outcome == "insufficient"
        if need == "chart":
            assert "needs a chart citation; cited only [C1] or request items" in (
                verdict.invalidated
            )


@pytest.mark.parametrize(("key", "outcome", "need"), CELLS)
def test_a_verified_chart_citation(key, outcome, need):
    verdict, _ = _judge(key, _answer(outcome, CHART))
    if need == "contradicts":
        assert verdict.outcome == "insufficient"
        assert "establishes that the condition holds" in verdict.invalidated
    else:
        assert (_final(verdict), verdict.invalidated) == (outcome, None)
        assert [c.chunk_id for c in verdict.citations] == [NOTE.id]


@pytest.mark.parametrize("key", PARTIAL_BRANCHES)
def test_insufficient_needs_no_citation_on_any_branch(key):
    verdict, _ = _judge(key, _answer("insufficient"))
    assert (verdict.outcome, verdict.invalidated) == ("insufficient", None)


@pytest.mark.parametrize("key", PARTIAL_BRANCHES)
def test_an_outcome_with_no_citation_at_all_is_invalid(key):
    verdict, _ = _judge(key, _answer("not_met"))
    assert verdict.outcome == "insufficient" and verdict.invalidated


def _requirements_in_prompt(prompt):
    block = prompt.split("Citations for this criterion:\n")[1].split("\n\n")[0]
    labels = {"condition does not hold": "not_applicable"}
    found = {}
    for line in block.splitlines():
        if not line.startswith("- "):
            continue
        label, text = line[2:].split(": ", 1)
        found[labels.get(label, label)] = (
            "alone"
            if "may rest on [C1] alone" in text
            else ("chart" if "needs at least one citation of a chart item" in text else text)
        )
    return found


@pytest.mark.parametrize("key", PARTIAL_BRANCHES)
def test_the_prompt_states_exactly_what_the_verifier_enforces(key):
    branch = PARTIAL_BRANCHES[key]
    criterion = FACET.criterion(branch.criterion)
    prompt = build_prompt(criterion, REQUEST, CHUNKS, _partial(key))
    stated = _requirements_in_prompt(prompt)
    assert stated.pop("insufficient") == "needs no citation."
    # the verifier's behavior, cell by cell, from a [C1]-only answer
    enforced = {}
    for outcome in ("met", "not_met", "not_applicable"):
        if outcome == "not_applicable" and (not criterion.condition or branch.condition_fixed):
            continue
        verdict, _ = _judge(key, _answer(outcome, C1))
        enforced[outcome] = "alone" if verdict.invalidated is None else "chart"
    assert stated == enforced
    if branch.condition_fixed:
        assert "[C1] establishes that the condition holds" in prompt
    if branch.note:
        assert branch.note in prompt


def test_the_partial_prompt_lists_c1_as_evidence_after_the_e_items():
    prompt = build_prompt(
        FACET.criterion("same-day-blocks-justified"),
        REQUEST,
        CHUNKS,
        _partial("same-day-blocks/listed"),
    )
    evidence = prompt.split("EVIDENCE\n")[1]
    assert evidence.index(f"[{E_NOTE}]") < evidence.index("[C1] (computed from the coded record)")
    assert f"[C1] (computed from the coded record)\n[C1] {STATEMENT}\n" in prompt
    assert "COMPUTED FACT" not in prompt and "without a citation" not in prompt
    assert "medical necessity of each block listed in the request" in prompt


@pytest.mark.parametrize("handle", ["COMPUTED FACT", "computed", "C2", "C 1", "[E99]"])
def test_other_handles_still_invalidate(handle):
    verdict, _ = _judge("second-diagnostic/one-session-short", _answer("not_met", (handle, "10")))
    assert verdict.outcome == "insufficient"
    assert verdict.invalidation == "unknown_handle"


def test_c1_echo_must_match_the_statement():
    paraphrase = ("C1", "ten days after")
    verdict, _ = _judge("second-diagnostic/one-session-short", _answer("not_met", paraphrase))
    assert verdict.outcome == "insufficient"
    assert verdict.invalidation == "handle_mismatch"
    assert "[C1]" in verdict.invalidated


@pytest.mark.parametrize("handle", ["C1", "[C1]", "c1", "C1.1"])
def test_c1_is_accepted_in_the_usual_handle_spellings(handle):
    verdict, _ = _judge("second-diagnostic/one-session-short", _answer("not_met", (handle, C1[1])))
    assert (verdict.outcome, verdict.invalidated) == ("not_met", None)


def test_c1_on_a_verdict_without_a_partial_rule_invalidates():
    criterion = FACET.criterion("second-diagnostic-2-weeks-after-first")
    llm = FakeLLM(_answer("not_met", C1, CHART))
    verdict = asyncio.run(judge_criterion(llm, criterion, REQUEST, CHUNKS))
    assert verdict.outcome == "insufficient"
    assert verdict.invalidated == "cited evidence 'C1' was not in the prompt"
    assert verdict.invalidation == "unknown_handle"
    assert "[C1]" not in llm.prompts[0]


def test_c1_in_the_shadow_call_invalidates_the_shadow_only():
    criterion = FACET.criterion("second-diagnostic-2-weeks-after-first")
    llm = FakeLLM(_answer("not_met", C1))
    verdict = asyncio.run(
        judge_with_rule(
            llm,
            criterion,
            REQUEST,
            CHUNKS,
            _partial("second-diagnostic/one-session-short"),
            shadow=True,
        )
    )
    assert (verdict.outcome, verdict.invalidated) == ("not_met", None)
    assert verdict.shadow.outcome == "insufficient"
    assert "'C1' was not in the prompt" in verdict.shadow.invalidated
    with_c1, without = llm.prompts
    assert "[C1]" in with_c1 and "[C1]" not in without


def test_the_computed_citation_records_rule_branch_and_version():
    verdict, _ = _judge("rfa-sessions/within-limit", _answer("met", C1))
    (computed,) = verdict.computed
    assert computed.model_dump() == {
        "computed": True,
        "statement": STATEMENT,
        "inputs": ["Procedure/mbb1"],
        "rule": "rfa-sessions-max-2",
        "rules_version": RULES_VERSION,
        "branch": "rfa-sessions/within-limit",
        "cited_by_model": True,
        "quote": STATEMENT,
    }
    assert verdict.decided_by == "rule+model"


def test_a_branch_from_another_criterion_is_refused():
    criterion = FACET.criterion("rfa-sessions-max-2")
    llm = FakeLLM(_answer("met", C1))
    with pytest.raises(ValueError, match="belongs elsewhere"):
        asyncio.run(
            judge_criterion(
                llm,
                criterion,
                REQUEST,
                CHUNKS,
                partial=_partial("diagnostic-sessions/within-limit"),
            )
        )


def test_partial_review_lists_chart_required_met_verdicts_with_their_quotes():
    from types import SimpleNamespace

    from precertly.evals.run import partial_review
    from precertly.judgment import CaseJudgment, Outcome

    chart_met, _ = _judge("second-diagnostic/one-session-short", _answer("met", C1, CHART))
    alone_met, _ = _judge("rfa-sessions/within-limit", _answer("met", C1))
    alone_not_met, _ = _judge("second-diagnostic/one-session-short", _answer("not_met", C1))
    # a verdict that bypassed the verifier, to show the check would catch it
    forged = alone_met.model_copy(deep=True)
    forged.criterion = "diagnostic-sessions-max-4"
    forged.computed[0].branch = "diagnostic-sessions/at-or-over-limit"
    judgment = CaseJudgment(
        policy=FACET.id,
        variant="x",
        verdicts=[chart_met, alone_met, alone_not_met, forged],
        overall=Outcome.MET,
        reviewer_checklist=[],
    )
    review = partial_review([(SimpleNamespace(case_id="facet-04"), judgment)])
    (entry,) = review["chart_required_met"]
    assert (entry["case"], entry["row"], entry["outcome"], entry["c1_cited"]) == (
        "facet-04",
        "1a",
        "met",
        True,
    )
    assert entry["citations"] == [
        {"handle": f"{E_NOTE}.1", "chunk_id": NOTE.id, "quote": NOTE.text}
    ]
    (violation,) = review["c1_alone_where_chart_required"]
    assert violation["row"] == "2b"
