import pytest

from precertly.evals import load_cases
from precertly.evals.scoring import score_case, summarize
from precertly.judgment import CaseJudgment, CriterionVerdict, Outcome, VerifiedCitation
from precertly.llm import LLMUsage

CASES = load_cases()
USAGE = LLMUsage(model_id="fake", input_tokens=1000, output_tokens=100, latency_ms=50)


def _verdict(label, outcome=None, *, applicable=True, cite=None, invalidated=None):
    """A verdict that agrees with the label unless told otherwise."""
    if outcome is None and label.outcome is Outcome.NOT_APPLICABLE:
        outcome, applicable = "met", False
    elif outcome is None:
        outcome = label.outcome.value
    resources = label.evidence if cite is None else cite
    return CriterionVerdict(
        criterion=label.criterion,
        outcome=outcome,
        applicable=applicable,
        confidence=0.9,
        rationale="r",
        citations=[
            VerifiedCitation(
                chunk_id=f"{r}#0" if "/" in r else r, quote="q", char_start=0, char_end=1
            )
            for r in resources
        ],
        invalidated=invalidated,
        model_outcome=outcome,
        usage=USAGE,
    )


def _judgment(case, overrides=None, overall=None):
    overrides = overrides or {}
    verdicts = [overrides.get(label.criterion) or _verdict(label) for label in case.expected]
    return CaseJudgment(
        policy=case.policy,
        variant=case.variant,
        verdicts=verdicts,
        overall=overall or case.expected_overall,
        reviewer_checklist=[],
    )


def test_a_judgment_that_matches_every_label_scores_perfectly():
    scores = [score_case(case, _judgment(case)) for case in CASES.values()]
    summary = summarize(scores)
    assert summary.cases == summary.cases_correct == 11
    assert summary.criterion_accuracy == 1.0
    assert (summary.false_approvals, summary.case_false_approvals) == (0, 0)
    assert summary.citation_precision == 1.0
    # every label counts, not_applicable ones included
    labels = [label for case in CASES.values() for label in case.expected]
    assert summary.scored_criteria == summary.criteria_correct == len(labels)
    assert summary.llm_calls == len(labels)
    assert summary.input_tokens == 1000 * len(labels)


def test_marking_a_failed_criterion_met_is_a_false_approval():
    case = CASES["facet-02"]
    label = next(x for x in case.expected if x.criterion == "two-mbbs-80pct-sustained-relief")
    wrong = {label.criterion: _verdict(label, "met")}
    score = score_case(case, _judgment(case, wrong, overall=Outcome.MET))
    summary = summarize([score])
    assert (summary.false_approvals, summary.negative_labels) == (1, 1)
    assert summary.false_approval_rate == 1.0
    assert summary.case_false_approvals == 1
    assert summary.criteria_correct == summary.scored_criteria - 1


def test_calling_a_failed_criterion_not_applicable_is_also_a_false_approval():
    case = CASES["facet-03"]
    label = next(x for x in case.expected if x.criterion == "second-diagnostic-2-weeks-after-first")
    skipped = {label.criterion: _verdict(label, "met", applicable=False, cite=[])}
    summary = summarize([score_case(case, _judgment(case, skipped))])
    assert summary.false_approvals == 1


def test_too_cautious_is_wrong_but_not_a_false_approval():
    case = CASES["facet-01"]
    label = case.expected[0]
    cautious = {label.criterion: _verdict(label, "insufficient", cite=[], invalidated="x")}
    summary = summarize([score_case(case, _judgment(case, cautious, overall=Outcome.INSUFFICIENT))])
    assert (summary.false_approvals, summary.cases_correct) == (0, 0)
    assert summary.criteria_correct == summary.scored_criteria - 1
    assert summary.invalidated_verdicts == 1


def test_citation_precision_counts_resources_outside_the_label():
    case = CASES["facet-01"]
    label = next(x for x in case.expected if x.criterion == "disability-scale-at-baseline")
    extra = {label.criterion: _verdict(label, cite=["Observation/odi-baseline", "Condition/other"])}
    score = score_case(case, _judgment(case, extra))
    scored = next(c for c in score.criteria if c.criterion == label.criterion)
    assert (scored.cited_in_label, len(scored.cited)) == (1, 2)


def test_citation_precision_is_reported_per_criterion_and_macro_averaged():
    case = CASES["facet-01"]
    odi = next(x for x in case.expected if x.criterion == "disability-scale-at-baseline")
    levels = next(x for x in case.expected if x.criterion == "levels-1-to-2")
    overrides = {
        # 1 of 4 cited resources is in the label
        odi.criterion: _verdict(
            odi, cite=["Observation/odi-baseline", "Condition/a", "Condition/b", "Condition/c"]
        ),
        # 1 of 1
        levels.criterion: _verdict(levels, cite=["request.levels"]),
    }
    judgment = _judgment(case, overrides)
    for verdict in judgment.verdicts:  # leave only the two criteria citing anything
        if verdict.criterion not in overrides:
            verdict.citations = []
    summary = summarize([score_case(case, judgment)])

    by_criterion = summary.citation_precision_by_criterion
    assert set(by_criterion) == {odi.criterion, levels.criterion}
    assert (by_criterion[odi.criterion].in_label, by_criterion[odi.criterion].cited) == (1, 4)
    assert by_criterion[odi.criterion].precision == 0.25
    assert by_criterion[levels.criterion].precision == 1.0
    assert summary.citation_precision == 0.4  # micro: 2 of 5
    assert summary.citation_precision_macro == 0.625  # macro: mean of 0.25 and 1.0


def test_not_applicable_label_is_correct_only_if_the_agent_says_the_condition_does_not_hold():
    case = CASES["facet-01"]
    label = next(x for x in case.expected if x.outcome is Outcome.NOT_APPLICABLE)

    def scored(verdict):
        score = score_case(case, _judgment(case, {label.criterion: verdict}))
        return next(c for c in score.criteria if c.criterion == label.criterion), score

    agrees, _ = scored(_verdict(label, "met", applicable=False, cite=[]))
    assert (agrees.correct, agrees.false_approval) == (True, False)

    for outcome in ("met", "not_met", "insufficient"):
        judged, score = scored(_verdict(label, outcome, applicable=True, cite=[]))
        assert (judged.correct, judged.false_approval) == (False, False)
        summary = summarize([score])
        assert summary.criteria_correct == summary.scored_criteria - 1


def test_citation_precision_ignores_not_applicable_labels():
    case = CASES["facet-01"]
    label = next(x for x in case.expected if x.outcome is Outcome.NOT_APPLICABLE)
    cited = {
        label.criterion: _verdict(label, "met", applicable=False, cite=["request.same_day_blocks"])
    }
    summary = summarize([score_case(case, _judgment(case, cited))])
    assert summary.citation_precision == 1.0


def test_results_metadata_records_what_is_needed_to_repeat_a_run():
    from precertly.evals.run import run_metadata

    metadata = run_metadata()
    assert {"run_date", "git_sha", "git_dirty", "model_id", "prompt_version"} <= set(metadata)
    retrieval = metadata["retrieval"]
    assert retrieval["top_k"] == 8
    assert (retrieval["rrf_k"], retrieval["candidates_per_ranking"]) == (60, 50)
    assert retrieval["weights"] == {"full_text": 1.0, "vector": 1.0}


def test_resume_requires_identical_code_and_settings():
    from precertly.evals.run import resume_conflicts, run_metadata

    current = run_metadata() | {"git_dirty": False}
    assert resume_conflicts(dict(current), current) == []

    other_commit = current | {"git_sha": "0000000"}
    assert any("git_sha" in c for c in resume_conflicts(other_commit, current))

    other_k = current | {"retrieval": current["retrieval"] | {"top_k": 12}}
    assert any("retrieval" in c for c in resume_conflicts(other_k, current))

    other_prompt = current | {"prompt_version": "1"}
    assert any("prompt_version" in c for c in resume_conflicts(other_prompt, current))

    dirty = current | {"git_dirty": True}
    assert any("uncommitted" in c for c in resume_conflicts(dirty, current))
    assert any("uncommitted" in c for c in resume_conflicts(dict(current), dirty))


def test_results_file_round_trips(tmp_path):
    import json

    from precertly.evals.run import load_results

    case = CASES["facet-02"]
    judgment = _judgment(case)
    score = score_case(case, judgment)
    entry = {**score.model_dump(mode="json"), "verdicts": judgment.model_dump(mode="json")}
    path = tmp_path / "results.json"
    path.write_text(json.dumps({"run_date": "2026-10-13", "cases": [entry]}))

    _, ((loaded_score, loaded_judgment),) = load_results(path)
    assert loaded_score == score
    assert loaded_judgment == judgment


def test_runner_saves_after_every_case_and_can_be_resumed(tmp_path, monkeypatch):
    """A run that dies part-way leaves a file --resume can finish."""
    import json
    import sys

    from precertly.evals import run

    metadata = {"run_date": "2026-10-13", "git_sha": "abc"}
    monkeypatch.setattr(run, "run_metadata", lambda rules=False: dict(metadata))
    out = tmp_path / "results.json"
    seen_on_disk = []

    def fake_run(crash_after=None):
        async def _run(cases, save, rules=False):
            results = []
            for number, case in enumerate(cases, start=1):
                judgment = _judgment(case)
                results.append((score_case(case, judgment), judgment))
                save(results)
                seen_on_disk.append(json.loads(out.read_text())["cases_run"])
                if number == crash_after:
                    raise RuntimeError("Bedrock went away")
            return results

        return _run

    ids = "facet-01,facet-02,facet-03"
    monkeypatch.setattr(run, "_run", fake_run(crash_after=2))
    monkeypatch.setattr(sys, "argv", ["run", "--cases", ids, "--out", str(out)])
    with pytest.raises(RuntimeError):
        run.main()
    partial = json.loads(out.read_text())
    assert partial["cases_run"] == ["facet-01", "facet-02"]
    assert partial["complete"] is False
    assert seen_on_disk == [["facet-01"], ["facet-01", "facet-02"]]

    monkeypatch.setattr(run, "_run", fake_run())
    monkeypatch.setattr(sys, "argv", ["run", "--cases", ids, "--resume", str(out)])
    assert run.main() == 0
    finished = json.loads(out.read_text())
    assert finished["cases_run"] == ["facet-01", "facet-02", "facet-03"]
    assert finished["complete"] is True
    assert finished["summary"]["cases"] == 3


def test_unusable_answers_are_counted_apart_from_quote_invalidations():
    case = CASES["facet-01"]
    first, second = case.expected[0], case.expected[1]
    no_answer = _verdict(first, "insufficient", cite=[], invalidated="no structured answer: x")
    no_answer.model_outcome = "none"
    bad_quote = _verdict(second, "insufficient", cite=[], invalidated="quote not found in E3")
    overrides = {first.criterion: no_answer, second.criterion: bad_quote}
    summary = summarize(
        [score_case(case, _judgment(case, overrides, overall=Outcome.INSUFFICIENT))]
    )

    assert summary.invalidated_verdicts == 2
    assert (summary.quote_invalidations, summary.unusable_answers) == (1, 1)
    assert summary.unusable_answers_by_case == {"facet-01": 1}
    assert summary.unusable_answers_by_criterion == {first.criterion: 1}


def _payload(case_ids, *, sha="abc", unusable=(), flip=()):
    from precertly.evals.run import IDENTITY

    cases = []
    for case_id in case_ids:
        judgment = _judgment(CASES[case_id])
        for verdict in judgment.verdicts:
            if (case_id, verdict.criterion) in unusable:
                verdict.model_outcome, verdict.outcome = "none", "insufficient"
            if (case_id, verdict.criterion) in flip:
                verdict.outcome = "insufficient"
        cases.append({"case_id": case_id, "verdicts": judgment.model_dump(mode="json")})
    return {key: "same" for key in IDENTITY} | {"git_sha": sha, "git_dirty": False, "cases": cases}


def test_a_pair_is_clean_only_with_identical_settings_and_no_unusable_answers():
    from precertly.evals.compare import compare

    ids = ["facet-01", "facet-02"]
    criterion = CASES["facet-01"].expected[0].criterion

    same = compare(_payload(ids), _payload(ids))
    assert same["clean"] and same["verdicts_differ"] == []
    assert same["evidence_identical"] == same["verdicts"] == 32

    noisy = compare(_payload(ids), _payload(ids, flip={("facet-01", criterion)}))
    assert noisy["clean"]  # differing verdicts are the noise being measured, not a defect
    assert noisy["verdicts_differ"] == [
        {"case": "facet-01", "criterion": criterion, "a": "met", "b": "insufficient"}
    ]

    broken = compare(_payload(ids), _payload(ids, unusable={("facet-02", criterion)}))
    assert not broken["clean"]
    assert broken["unusable_answers"] == {"a": [], "b": [f"facet-02/{criterion}"]}
    assert "run b has 1 unusable model answer(s)" in broken["not_clean_because"]

    other_commit = compare(_payload(ids), _payload(ids, sha="def"))
    assert "git_sha differs" in other_commit["not_clean_because"]


def test_resume_also_refuses_different_policy_versions_or_judgment_settings():
    from precertly.evals.run import resume_conflicts, run_metadata

    current = run_metadata() | {"git_dirty": False}
    assert {"judgment", "policies"} <= set(current)
    assert current["judgment"] == {"max_tokens": 800, "temperature": 0.0, "top_k": 8}
    assert current["policies"]["facet-joint-interventions"] == {
        "LCD L38773": "32",
        "Article A58364": "42",
    }

    newer_policy = current | {
        "policies": current["policies"] | {"facet-joint-interventions": {"LCD L38773": "33"}}
    }
    assert any("policies" in c for c in resume_conflicts(newer_policy, current))

    other_settings = current | {"judgment": current["judgment"] | {"max_tokens": 400}}
    assert any("judgment" in c for c in resume_conflicts(other_settings, current))


def test_model_only_view_lists_where_rule_and_model_part():
    from precertly.evals import run
    from precertly.judgment.models import ComputedCitation, ShadowVerdict

    case = CASES["facet-05"]
    label = next(x for x in case.expected if x.criterion == "two-mbbs-80pct-sustained-relief")
    by_rule = _verdict(label, "insufficient", cite=[])
    by_rule.decided_by, by_rule.rule_kind = "rule", "decided"
    by_rule.computed = [ComputedCitation(statement="not comparable", inputs=["Procedure/mbb2"])]
    by_rule.shadow = ShadowVerdict(
        outcome="met",
        applicable=True,
        invalidated=None,
        model_outcome="met",
        citations=[],
        usage=USAGE,
    )
    judgment = _judgment(case, {label.criterion: by_rule})
    score = score_case(case, judgment)
    scored = next(c for c in score.criteria if c.criterion == label.criterion)
    assert scored.cited == ["Procedure/mbb2"] and scored.decided_by == "rule"
    assert score.llm_calls == len(case.expected) + 1  # the shadow call is counted

    view = run._model_only_view([(score, judgment)])
    assert view["rule_vs_model"] == [
        {
            "case": "facet-05",
            "criterion": label.criterion,
            "label": "insufficient",
            "decided_by": "rule",
            "with_rules": "insufficient",
            "model_only": "met",
            "with_rules_correct": True,
            "model_only_correct": False,
        }
    ]
    assert view["summary_model_only"]["false_approvals"] == 1
    assert summarize([score]).false_approvals == 0
