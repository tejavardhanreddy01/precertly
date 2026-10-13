import asyncio
from datetime import date

import pytest

from precertly.judgment import (
    Citation,
    EvidenceChunk,
    ModelJudgment,
    Outcome,
    PriorAuthRequest,
    build_prompt,
    find_quote,
    judge_case,
    judge_criterion,
    judged_criteria,
)
from precertly.llm import LLMUsage
from precertly.policies import load_policies

FACET = load_policies()["facet-joint-interventions"]
REQUEST = PriorAuthRequest(
    date=date(2026, 10, 5),
    service_date=date(2026, 10, 19),
    procedure="thermal radiofrequency ablation",
    technique="radiofrequency_ablation",
    codes=["64635", "64636"],
    region="lumbar",
    levels=["L4-5", "L5-S1"],
)
NOTE = EvidenceChunk(
    id="DocumentReference/note-mbb2#0",
    text="Before the block: 7/10.\nAfter the block: 2/10.\nRelief: (7 - 2) / 7 = 71%.",
    effective_date=date(2026, 9, 14),
)
CHUNKS = [*REQUEST.chunks(), NOTE]


class Response:
    def __init__(self, data):
        self.data = data
        self.usage = LLMUsage(model_id="fake", input_tokens=100, output_tokens=20, latency_ms=5)


class FakeLLM:
    """Answers each criterion from a table; anything else gets the default answer."""

    def __init__(self, answers=None, default=None):
        self.answers = answers or {}
        self.default = default
        self.prompts = []

    async def structured(self, prompt, schema, **options):
        self.prompts.append((prompt, options))
        assert schema is ModelJudgment
        criterion_id = prompt.split("Id: ")[1].split("\n")[0]
        return Response(self.answers.get(criterion_id, self.default))


def _handle(chunk_id):
    """The handle the prompt gives a chunk; anything else is passed through as written."""
    ids = [chunk.id for chunk in CHUNKS]
    return f"E{ids.index(chunk_id) + 1}" if chunk_id in ids else chunk_id


def _answer(outcome, citations=(), condition_holds=None):
    return ModelJudgment(
        condition_holds=condition_holds,
        outcome=outcome,
        confidence=0.9,
        rationale="because",
        citations=[Citation(evidence=_handle(c), quote=q) for c, q in citations],
    )


def _judge(criterion_id, answer):
    criterion = FACET.criterion(criterion_id)
    llm = FakeLLM(default=answer)
    return asyncio.run(judge_criterion(llm, criterion, REQUEST, CHUNKS)), llm


def test_find_quote_ignores_whitespace_but_not_words():
    start, end = find_quote("After the block:  2/10. Relief:", NOTE.text)
    assert NOTE.text[start:end] == "After the block: 2/10.\nRelief:"
    assert find_quote("After the block: 3/10", NOTE.text) is None
    assert find_quote("   ", NOTE.text) is None


def test_verified_citation_keeps_the_verdict_and_records_offsets():
    answer = _answer("not_met", [(NOTE.id, "Relief: (7 - 2) / 7 = 71%.")])
    verdict, _ = _judge("two-mbbs-80pct-sustained-relief", answer)
    assert (verdict.outcome, verdict.invalidated, verdict.applicable) == ("not_met", None, True)
    (citation,) = verdict.citations
    assert NOTE.text[citation.char_start : citation.char_end] == "Relief: (7 - 2) / 7 = 71%."
    assert citation.resource == "DocumentReference/note-mbb2"
    assert verdict.usage.input_tokens == 100


def test_a_quote_that_is_not_in_the_chunk_invalidates_the_verdict():
    answer = _answer("met", [(NOTE.id, "Relief: 86%")])
    verdict, _ = _judge("two-mbbs-80pct-sustained-relief", answer)
    assert verdict.outcome == "insufficient"
    assert verdict.model_outcome == "met"
    assert "quote not found" in verdict.invalidated
    assert verdict.citations == []


@pytest.mark.parametrize("handle", ["E99", "note-mbb2#0", "DocumentReference/note-mbb2#0"])
def test_citing_something_that_is_not_a_handle_from_the_prompt_invalidates(handle):
    answer = _answer("met")
    answer.citations = [Citation(evidence=handle, quote="Before the block: 7/10.")]
    verdict, _ = _judge("two-mbbs-80pct-sustained-relief", answer)
    assert verdict.outcome == "insufficient"
    assert "was not in the prompt" in verdict.invalidated


def test_handles_map_back_to_chunk_ids_and_tolerate_brackets():
    answer = _answer("met", [(f"[{_handle(NOTE.id).lower()}]", "Before the block: 7/10.")])
    verdict, _ = _judge("two-mbbs-80pct-sustained-relief", answer)
    assert verdict.invalidated is None
    assert verdict.citations[0].chunk_id == NOTE.id


def test_a_verified_handle_with_the_wrong_quote_is_still_invalid():
    # the quote is real, but it is in a different evidence item than the one cited
    answer = _answer("met", [("request.levels", "Before the block: 7/10.")])
    verdict, _ = _judge("two-mbbs-80pct-sustained-relief", answer)
    assert "quote not found in request.levels" in verdict.invalidated


@pytest.mark.parametrize("outcome", ["met", "not_met"])
def test_a_decision_without_any_citation_is_invalid(outcome):
    verdict, _ = _judge("two-mbbs-80pct-sustained-relief", _answer(outcome))
    assert verdict.outcome == "insufficient"
    assert verdict.invalidated == f"{outcome} with no citation"


def test_insufficient_needs_no_citation():
    verdict, _ = _judge("two-mbbs-80pct-sustained-relief", _answer("insufficient"))
    assert (verdict.outcome, verdict.invalidated) == ("insufficient", None)


def test_request_fields_can_be_cited():
    answer = _answer("met", [("request.levels", "Levels: L4-5, L5-S1")])
    verdict, _ = _judge("levels-1-to-2", answer)
    assert verdict.outcome == "met"
    assert verdict.citations[0].resource == "request.levels"


def test_condition_that_does_not_hold_makes_the_criterion_not_applicable():
    answer = _answer("met", condition_holds=False)
    verdict, _ = _judge("same-day-blocks-justified", answer)
    assert (verdict.outcome, verdict.applicable, verdict.invalidated) == ("met", False, None)


def test_condition_holds_is_ignored_for_unconditional_criteria():
    answer = _answer("met", condition_holds=False)
    verdict, _ = _judge("levels-1-to-2", answer)
    assert verdict.applicable is True
    assert verdict.invalidated == "met with no citation"


def test_prompt_carries_the_policy_text_the_condition_and_the_evidence():
    criterion = FACET.criterion("same-day-blocks-justified")
    prompt = build_prompt(criterion, REQUEST, CHUNKS)
    assert criterion.quote in prompt
    assert criterion.condition in prompt
    assert "REQUEST DATE: 2026-10-05" in prompt
    assert "[E4] (request)\nTechnique: radiofrequency_ablation" in prompt
    assert "[E8] (request)\nOther blocks planned for the same day: none" in prompt
    assert "[E9] (chart, DocumentReference, dated 2026-09-14)" in prompt
    # long chunk ids are not shown, so there is nothing for the model to abbreviate
    assert NOTE.id not in prompt and "request.levels" not in prompt


class FixedEvidence:
    def __init__(self):
        self.asked = []

    async def for_criterion(self, criterion):
        self.asked.append(criterion.id)
        return [NOTE]


def _judge_case(answers):
    cited = _answer("met", [(NOTE.id, "Before the block: 7/10.")])
    llm, evidence = FakeLLM(answers, default=cited), FixedEvidence()
    judgment = asyncio.run(judge_case(llm, FACET, "initial-rfa", REQUEST, evidence))
    return judgment, llm, evidence


def test_judge_case_judges_only_chart_and_request_criteria():
    judgment, llm, evidence = _judge_case({})
    judged = judged_criteria(FACET, "initial-rfa")
    assert [v.criterion for v in judgment.verdicts] == judged == evidence.asked
    assert len(llm.prompts) == len(judged) == 16
    assert judgment.overall is Outcome.MET
    # the rest goes to the human reviewer, by stage
    assert {(i.criterion, i.evidence_stage) for i in judgment.reviewer_checklist} == {
        ("ct-or-fluoroscopic-guidance", "post_service"),
        ("thermal-rf-only", "post_service"),
        ("medical-record-elements", "post_service"),
    }
    # every model call saw the request fields as well as the chart evidence
    assert judgment.verdicts[0].chunk_ids[:2] == ["request.date", "request.service_date"]
    assert judgment.verdicts[0].chunk_ids[-1] == NOTE.id


def test_one_failing_criterion_decides_the_case():
    failing = _answer("not_met", [(NOTE.id, "Relief: (7 - 2) / 7 = 71%.")])
    judgment, _, _ = _judge_case({"two-mbbs-80pct-sustained-relief": failing})
    assert judgment.overall is Outcome.NOT_MET


def test_an_unverifiable_answer_cannot_produce_an_approval():
    made_up = _answer("met", [(NOTE.id, "Relief: 95%")])
    judgment, _, _ = _judge_case({"two-mbbs-80pct-sustained-relief": made_up})
    assert judgment.overall is Outcome.INSUFFICIENT


def test_not_applicable_criteria_count_as_satisfied():
    skipped = _answer("met", condition_holds=False)
    judgment, _, _ = _judge_case({"same-day-blocks-justified": skipped})
    assert judgment.overall is Outcome.MET
    assert judgment.outcomes()["same-day-blocks-justified"] is Outcome.NOT_APPLICABLE


def test_no_usable_model_answer_becomes_insufficient_instead_of_crashing():
    from precertly.llm import StructuredOutputError

    class BrokenLLM:
        async def structured(self, prompt, schema, **options):
            usage = LLMUsage(model_id="fake", input_tokens=200, output_tokens=0, latency_ms=1)
            raise StructuredOutputError("ModelJudgment: empty message", usage=usage)

    criterion = FACET.criterion("levels-1-to-2")
    verdict = asyncio.run(judge_criterion(BrokenLLM(), criterion, REQUEST, CHUNKS))
    assert (verdict.outcome, verdict.model_outcome) == ("insufficient", "none")
    assert "no structured answer" in verdict.invalidated
    assert verdict.usage.input_tokens == 200


# --- rules in the judge ------------------------------------------------------------------


def _with_rule(criterion_id, rule, answer, *, shadow=False):
    from precertly.judgment import judge_with_rule

    criterion = FACET.criterion(criterion_id)
    llm = FakeLLM(default=answer)
    verdict = asyncio.run(judge_with_rule(llm, criterion, REQUEST, CHUNKS, rule, shadow=shadow))
    return verdict, llm


def _rule(kind, **fields):
    from precertly.judgment import RuleResult

    return RuleResult(kind=kind, statement="computed: 71%", inputs=["Observation/a"], **fields)


def test_a_decided_rule_is_final_and_the_model_is_not_asked():
    cited = _answer("met", [(NOTE.id, "Before the block: 7/10.")])
    verdict, llm = _with_rule(
        "two-mbbs-80pct-sustained-relief", _rule("decided", outcome="not_met"), cited
    )
    assert (verdict.outcome, verdict.decided_by, verdict.rule_kind) == (
        "not_met",
        "rule",
        "decided",
    )
    assert llm.prompts == []
    (computed,) = verdict.computed
    assert computed.computed is True and computed.inputs == ["Observation/a"]
    assert verdict.citations == [] and verdict.invalidated is None


def test_the_shadow_call_is_the_plain_model_call_and_never_changes_the_outcome():
    model_says_met = _answer("met", [(NOTE.id, "Before the block: 7/10.")])
    rule = _rule("decided", outcome="insufficient")
    verdict, llm = _with_rule("two-mbbs-80pct-sustained-relief", rule, model_says_met, shadow=True)
    assert verdict.outcome == "insufficient"  # the rule stands; the model cannot upgrade it
    assert (verdict.shadow.outcome, verdict.shadow.model_outcome) == ("met", "met")
    ((prompt, _),) = llm.prompts
    criterion = FACET.criterion("two-mbbs-80pct-sustained-relief")
    assert prompt == build_prompt(criterion, REQUEST, CHUNKS)  # exactly the baseline prompt
    assert verdict.model_only().outcome == "met"


def test_a_rule_can_decide_that_a_criterion_does_not_apply():
    rule = _rule("decided", outcome="met", applicable=False)
    verdict, _ = _with_rule("same-day-blocks-justified", rule, _answer("not_met"))
    assert (verdict.outcome, verdict.applicable, verdict.decided_by) == ("met", False, "rule")


def test_a_partial_rule_gives_the_model_the_computed_fact():
    uncited = _answer("not_met")  # the computed fact is the evidence
    verdict, llm = _with_rule("second-diagnostic-2-weeks-after-first", _rule("partial"), uncited)
    assert (verdict.outcome, verdict.decided_by, verdict.invalidated) == (
        "not_met",
        "rule+model",
        None,
    )
    ((prompt, _),) = llm.prompts
    assert "COMPUTED FACT\ncomputed: 71%" in prompt and "Do not recompute it" in prompt
    assert verdict.computed[0].statement == "computed: 71%"


def test_a_partial_rule_with_shadow_makes_both_calls():
    cited = _answer("met", [(NOTE.id, "Before the block: 7/10.")])
    verdict, llm = _with_rule("first-diagnostic-80pct-relief", _rule("partial"), cited, shadow=True)
    with_fact, without = (prompt for prompt, _ in llm.prompts)
    assert "COMPUTED FACT" in with_fact and "COMPUTED FACT" not in without
    assert verdict.shadow is not None and verdict.usage.input_tokens == 100


@pytest.mark.parametrize("rule", [None, "absent"])
def test_no_rule_or_an_absent_rule_is_the_plain_model_verdict(rule):
    rule = _rule("absent") if rule else None
    answer = _answer("met", [(NOTE.id, "Before the block: 7/10.")])
    verdict, llm = _with_rule("levels-1-to-2", rule, answer, shadow=True)
    assert (verdict.outcome, verdict.decided_by, verdict.shadow) == ("met", "model", None)
    assert len(llm.prompts) == 1 and "COMPUTED FACT" not in llm.prompts[0][0]
    assert verdict.model_only() is verdict
    # without a computed fact, a decision still needs a citation
    uncited, _ = _with_rule("levels-1-to-2", rule, _answer("met"))
    assert uncited.invalidated == "met with no citation"


def test_model_only_view_recombines_the_case_from_shadow_verdicts():
    from precertly.evals import load_cases
    from precertly.fhir import load_bundle
    from precertly.judgment import StructuredChart, model_only

    case = load_cases()["facet-05"]  # mixed scales: the model approves, the rule does not
    chart = StructuredChart.from_bundle(load_bundle(case.bundle_path()))
    approve_everything = _answer("met", [(NOTE.id, "Before the block: 7/10.")])
    llm, evidence = FakeLLM(default=approve_everything), FixedEvidence()
    judgment = asyncio.run(
        judge_case(llm, FACET, case.variant, case.request, evidence, structured=chart, shadow=True)
    )
    by_id = {v.criterion: v for v in judgment.verdicts}
    assert by_id["two-mbbs-80pct-sustained-relief"].outcome == "insufficient"
    assert by_id["pain-scale-after-each-diagnostic"].outcome == "not_met"
    assert by_id["same-day-blocks-justified"].applicable is False
    assert judgment.overall is Outcome.NOT_MET
    # the same run, as the model alone judged it, would have approved the case
    assert model_only(judgment, FACET).overall is Outcome.MET
