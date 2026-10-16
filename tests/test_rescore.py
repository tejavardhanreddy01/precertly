import copy
import hashlib
import json

from precertly.evals.cases import EVALS_DIR
from precertly.evals.rescore import rescore, reverify, source_problems
from precertly.judgment import CriterionVerdict, VerifiedCitation
from precertly.judgment.models import ShadowVerdict
from precertly.llm import LLMUsage
from precertly.policies import load_policies

FACET = load_policies()["facet-joint-interventions"]
RESULTS = EVALS_DIR / "results"
USAGE = LLMUsage(model_id="fake", calls=1)
CHART = VerifiedCitation(chunk_id="DocumentReference/note#0", quote="q", char_start=0, char_end=1)


def _verdict(criterion, citations=(), decided_by="model", applicable=False, invalidated=None):
    return CriterionVerdict(
        criterion=criterion,
        outcome="insufficient" if invalidated else "met",
        applicable=applicable,
        confidence=0.9,
        rationale="because",
        citations=list(citations),
        invalidated=invalidated,
        model_outcome="met",
        usage=USAGE,
        decided_by=decided_by,
    )


def test_reverify_changes_an_uncited_not_applicable_verdict_and_its_shadow():
    verdict = _verdict("same-day-blocks-justified")
    verdict.shadow = ShadowVerdict(
        outcome="met",
        applicable=False,
        invalidated=None,
        model_outcome="met",
        citations=[CHART],
        usage=USAGE,
    )
    changed = reverify(verdict, FACET.criterion("same-day-blocks-justified"))
    assert [which for which, _ in changed] == ["main", "shadow"]
    assert (verdict.outcome, verdict.applicable) == ("insufficient", True)
    assert (verdict.shadow.outcome, verdict.shadow.applicable) == ("insufficient", True)


def test_reverify_leaves_rule_decided_cited_and_already_invalidated_verdicts_alone():
    criterion = FACET.criterion("no-diagnostic-at-level-of-successful-rfa")
    for verdict in (
        _verdict(criterion.id, decided_by="rule"),
        _verdict(criterion.id, [CHART]),
        _verdict(criterion.id, applicable=True, invalidated="quote not found"),
        _verdict(criterion.id, applicable=True),
    ):
        before = copy.deepcopy(verdict)
        assert reverify(verdict, criterion) == []
        assert verdict == before


def test_rescoring_experiment_1_1_changes_only_the_shadow_ia_diagnostic_verdicts():
    expected = {
        "a": {"facet-06", "facet-11"},
        "b": {"facet-06", "facet-07", "facet-11"},
    }
    for run, cases in expected.items():
        source = RESULTS / f"2026-10-14-experiment-1.1{run}.json"
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        derived = rescore(source)
        changes = derived["rescored"]["changes"]
        assert {c["case"] for c in changes} == cases
        assert {(c["criterion"], c["which"], c["label"]) for c in changes} == {
            ("ia-diagnostic-only-if-mbb-not-possible", "shadow", "not_applicable")
        }
        assert derived["model_only_overall"]["facet-11"] == "insufficient"
        assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


def test_a_file_already_rescored_is_refused():
    derived = rescore(RESULTS / "2026-10-14-experiment-1.1a.json")
    assert any("version 2" in problem for problem in source_problems(derived))


def test_committed_derived_files_match_a_fresh_rescore():
    for run in ("a", "b"):
        path = RESULTS / f"2026-10-14-experiment-1.1{run}-rescored-issue4.json"
        committed = json.loads(path.read_text(encoding="utf-8"))
        assert (
            committed["rescored"]["source"] == f"evals/results/2026-10-14-experiment-1.1{run}.json"
        )
        fresh = rescore(EVALS_DIR.parent / committed["rescored"]["source"])
        for key in ("summary", "cases", "summary_model_only", "model_only_overall"):
            assert committed[key] == fresh[key], (run, key)
