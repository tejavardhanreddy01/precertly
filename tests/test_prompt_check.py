"""prompt_check rebuilds every recorded prompt offline. These tests run the whole judgment
step over the 11 facet cases with a fake Bedrock behind the real client (a dry run), and
confirm that the chunks rebuilt from a bundle are byte for byte what ingest stores."""

import asyncio
import json

import pytest
from conftest import FakeEmbedder

from precertly.evals import load_cases
from precertly.evals.prompt_check import case_chunks, check_results, main
from precertly.evals.rules_check import recompute
from precertly.evals.scoring import score_case
from precertly.fhir import load_bundle
from precertly.ingest import ingest_bundle
from precertly.judgment import ChartEvidence, StructuredChart, judge_case
from precertly.llm import BedrockClient
from precertly.policies import load_policies
from precertly.settings import Settings

CASES = load_cases()
POLICIES = load_policies()


class FakeBedrock:
    """Answers every call with an insufficient verdict through the forced tool."""

    def __init__(self):
        self.calls = 0

    def converse(self, **request):
        self.calls += 1
        answer = {"outcome": "insufficient", "confidence": 0.5, "rationale": "dry run"}
        block = {"toolUse": {"toolUseId": "t", "name": "ModelJudgment", "input": answer}}
        return {
            "output": {"message": {"role": "assistant", "content": [block]}},
            "stopReason": "tool_use",
            "usage": {"inputTokens": 1, "outputTokens": 1},
        }


class LabelEvidence:
    """Offline stand-in for retrieval: the chart chunks of the label's evidence."""

    def __init__(self, case):
        self.case, self.chunks = case, case_chunks(case)

    async def for_criterion(self, criterion):
        label = next((x for x in self.case.expected if x.criterion == criterion.id), None)
        refs = set(label.evidence) if label else set()
        found = [c for i, c in sorted(self.chunks.items()) if i.split("#")[0] in refs]
        return [c for c in found if not c.id.startswith("request.")][:8]


@pytest.fixture(scope="module")
def dry_run():
    fake = FakeBedrock()
    llm = BedrockClient(Settings(_env_file=None, llm_requests_per_minute=None), client=fake)
    cases = []
    for case in CASES.values():
        bundle = load_bundle(case.bundle_path())
        judgment = asyncio.run(
            judge_case(
                llm,
                POLICIES[case.policy],
                case.variant,
                case.request,
                LabelEvidence(case),
                structured=StructuredChart.from_bundle(bundle),
                shadow=True,
            )
        )
        score = score_case(case, judgment)
        cases.append(
            {**score.model_dump(mode="json"), "verdicts": judgment.model_dump(mode="json")}
        )
    return {"cases": cases}, fake.calls


def test_every_prompt_in_a_dry_run_rebuilds_to_its_recorded_hash(dry_run, tmp_path):
    payload, calls = dry_run
    result = check_results(payload)
    assert result.problems == []
    assert result.exchanges_checked == calls > 100
    path = tmp_path / "dry-run.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert main([str(path)]) == 0


def test_a_changed_prompt_input_is_caught(dry_run):
    payload, _ = dry_run
    tampered = json.loads(json.dumps(payload))
    verdict = next(
        v
        for case in tampered["cases"]
        for v in case["verdicts"]["verdicts"]
        if v["rule_kind"] == "partial" and len(v["chunk_ids"]) > 9
    )
    verdict["chunk_ids"][-1], verdict["chunk_ids"][-2] = (
        verdict["chunk_ids"][-2],
        verdict["chunk_ids"][-1],
    )
    problems = check_results(tampered).problems
    assert any("prompt hash differs" in p for p in problems)
    assert any("(shadow)" in p for p in problems)


def test_a_missing_exchange_is_a_problem(dry_run):
    payload, _ = dry_run
    tampered = json.loads(json.dumps(payload))
    verdict = next(
        v
        for case in tampered["cases"]
        for v in case["verdicts"]["verdicts"]
        if v["decided_by"] == "model"
    )
    verdict["exchanges"] = []
    assert any("no exchange is recorded" in p for p in check_results(tampered).problems)


def test_every_computed_fact_in_a_dry_run_is_reproduced(dry_run):
    payload, _ = dry_run
    checked, problems = recompute(payload)
    assert problems == [] and checked >= 48  # 38 decided and 10 partial
    tampered = json.loads(json.dumps(payload))
    verdict = next(
        v for case in tampered["cases"] for v in case["verdicts"]["verdicts"] if v["computed"]
    )
    verdict["computed"][0]["statement"] += " "
    assert len(recompute(tampered)[1]) == 1


def test_rebuilt_chunks_match_ingested_chunks_byte_for_byte(db):
    """prompt_check relies on this: the chunk text and date retrieval gives the model are
    exactly what the chunker produces from the bundle."""
    policy = POLICIES["facet-joint-interventions"]

    async def work(session):
        compared = 0
        for case in CASES.values():
            ingested = await ingest_bundle(
                session, load_bundle(case.bundle_path()), embedder=FakeEmbedder()
            )
            evidence = ChartEvidence(session, ingested.case_id, FakeEmbedder(), k=500)
            rebuilt = case_chunks(case)
            for criterion in policy.criteria:
                for chunk in await evidence.for_criterion(criterion):
                    assert chunk == rebuilt[chunk.id], (case.id, chunk.id)
                    assert chunk.text.encode() == rebuilt[chunk.id].text.encode()
                    compared += 1
        return compared

    assert db(work) > 1000
