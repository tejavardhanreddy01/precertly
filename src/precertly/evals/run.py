"""Run the judgment step over the labeled cases and score it.

    uv run python -m precertly.evals.run --estimate          # size of the run, no AWS, no DB
    uv run python -m precertly.evals.run                     # all cases (real Bedrock calls)
    uv run python -m precertly.evals.run --cases facet-01,facet-02

A real run needs the local database (docker compose up -d db; alembic upgrade head) and
AWS credentials, and costs money. Each case is ingested, every chart- and request-stage
criterion is judged, and the verdicts are compared with the labels. Results are written
to evals/results/ as JSON, with every verdict, rationale and citation.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from precertly.db import get_engine, session_factory
from precertly.evals.cases import EVALS_DIR, EvalCase, load_cases, scored_criteria
from precertly.evals.scoring import CaseScore, Summary, score_case, summarize
from precertly.fhir import chunk_bundle, load_bundle
from precertly.ingest import estimate_embedding, ingest_bundle
from precertly.judgment import SYSTEM, CaseJudgment, ChartEvidence, judge_case
from precertly.judgment.prompt import PROMPT_VERSION
from precertly.llm import BedrockClient, BedrockEmbedder
from precertly.policies import load_policies
from precertly.retrieval.search import DEFAULT_CANDIDATES, RRF_K
from precertly.settings import get_settings


def _estimate(cases: list[EvalCase]) -> None:
    settings = get_settings()
    policies = load_policies()
    calls = sum(len(scored_criteria(policies[c.policy], c.variant)) for c in cases)
    chunks = [chunk for c in cases for chunk in chunk_bundle(load_bundle(c.bundle_path()))]
    embedding = estimate_embedding(chunks)
    average_chunk = sum(len(c.text) for c in chunks) / len(chunks)
    # system prompt + criterion + request fields + k chart chunks, at ~4 characters per token
    prompt_chars = len(SYSTEM) + 1500 + settings.judgment_top_k * average_chunk
    llm_minutes = calls / (settings.llm_requests_per_minute or 60)
    embed_calls = embedding.unique_texts + calls
    embed_minutes = embed_calls / (settings.embedding_requests_per_minute or 600)
    print(
        f"{len(cases)} cases, {calls} criteria to judge\n"
        f"Model calls ({settings.bedrock_model_id}): {calls}, about "
        f"{int(calls * prompt_chars / 4):,} input tokens and {calls * 250:,} output tokens\n"
        f"Embedding calls: up to {embed_calls} ({embedding.unique_texts} chunk texts before "
        f"any cached vectors, {calls} search queries), about "
        f"{embedding.approx_tokens + calls * 60:,} tokens\n"
        f"Time at the configured rates: about {llm_minutes + embed_minutes:.0f} minutes"
    )


async def _run(cases: list[EvalCase]) -> list[tuple[CaseScore, CaseJudgment]]:
    settings = get_settings()
    policies = load_policies()
    llm, embedder = BedrockClient(settings), BedrockEmbedder(settings)
    sessions = session_factory()
    results = []
    try:
        for case in cases:
            async with sessions() as session:
                # All cases share one synthetic patient, so each ingest replaces the
                # previous case's chunks; vectors for unchanged text come from the cache.
                ingested = await ingest_bundle(
                    session,
                    load_bundle(case.bundle_path()),
                    embedder=embedder,
                    commit_batches=True,
                )
                if ingested.embedding_error:
                    raise RuntimeError(f"{case.id}: embedding stopped: {ingested.embedding_error}")
                await session.commit()
                evidence = ChartEvidence(
                    session, ingested.case_id, embedder, k=settings.judgment_top_k
                )
                judgment = await judge_case(
                    llm, policies[case.policy], case.variant, case.request, evidence
                )
            score = score_case(case, judgment)
            wrong = [c.criterion for c in score.criteria if not c.correct]
            print(
                f"{case.id}: overall {score.predicted_overall.value} "
                f"(expected {score.expected_overall.value}); "
                f"{len(wrong)} of {len(score.criteria)} criteria wrong"
                + (f": {', '.join(wrong)}" if wrong else ""),
                flush=True,
            )
            results.append((score, judgment))
    finally:
        await get_engine().dispose()
    return results


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def run_metadata() -> dict[str, object]:
    """What a results file needs for the run to be compared or repeated."""
    settings = get_settings()
    return {
        "run_date": date.today().isoformat(),
        "git_sha": _git("rev-parse", "--short", "HEAD"),
        # True if the code that ran differs from that commit
        "git_dirty": bool(_git("status", "--porcelain", "--untracked-files=no")),
        "model_id": settings.bedrock_model_id,
        "prompt_version": PROMPT_VERSION,
        "system_prompt_sha256": hashlib.sha256(SYSTEM.encode()).hexdigest(),
        "retrieval": {
            "top_k": settings.judgment_top_k,
            "method": "hybrid: Postgres full-text + pgvector cosine, reciprocal rank fusion",
            "rrf_k": RRF_K,
            "candidates_per_ranking": DEFAULT_CANDIDATES,
            # RRF sums 1 / (rrf_k + rank) over both rankings with no per-ranking weight
            "weights": {"full_text": 1.0, "vector": 1.0},
            "query": "criterion text + policy quote",
            "embedding_model_id": settings.embedding_model_id,
            "embedding_dimensions": settings.embedding_dimensions,
        },
        "annotation": "single annotator; not clinically validated",
    }


def _report(summary: Summary, scores: list[CaseScore]) -> str:
    def percent(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.1%}"

    lines = [
        f"Cases correct: {summary.cases_correct} of {summary.cases}"
        f" (case-level false approvals: {summary.case_false_approvals})",
        f"Criterion accuracy: {percent(summary.criterion_accuracy)}"
        f" ({summary.criteria_correct} of {summary.scored_criteria})",
        f"False-approval rate: {percent(summary.false_approval_rate)}"
        f" ({summary.false_approvals} of {summary.negative_labels} not_met/insufficient labels)",
        f"Citation precision: {percent(summary.citation_precision)}"
        f" ({summary.citations_in_label} of {summary.citations} cited resources are in the label)",
        f"Invalidated verdicts: {summary.invalidated_verdicts} of {summary.llm_calls}",
        f"Tokens: {summary.input_tokens:,} in, {summary.output_tokens:,} out",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", help="comma-separated case ids (default: all)")
    parser.add_argument("--estimate", action="store_true", help="print the size of the run")
    parser.add_argument("--out", type=Path, help="results file (default: evals/results/...)")
    args = parser.parse_args()

    available = load_cases()
    wanted = args.cases.split(",") if args.cases else list(available)
    unknown = [case_id for case_id in wanted if case_id not in available]
    if unknown:
        parser.error(f"unknown cases: {', '.join(unknown)}")
    cases = [available[case_id] for case_id in wanted]

    if args.estimate:
        _estimate(cases)
        return 0

    results = asyncio.run(_run(cases))
    scores = [score for score, _ in results]
    summary = summarize(scores)
    settings = get_settings()
    out = args.out or (
        EVALS_DIR
        / "results"
        / f"{date.today().isoformat()}-{settings.bedrock_model_id.replace(':', '-')}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **run_metadata(),
        "cases_run": [case.id for case in cases],
        "summary": summary.model_dump(),
        "cases": [
            {**score.model_dump(mode="json"), "verdicts": judgment.model_dump(mode="json")}
            for score, judgment in results
        ],
    }
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("\n" + _report(summary, scores))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
