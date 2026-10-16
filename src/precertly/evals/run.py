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
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from precertly.db import get_engine, session_factory
from precertly.evals.cases import EVALS_DIR, EvalCase, load_cases, scored_criteria
from precertly.evals.rules_check import check_cases
from precertly.evals.scoring import CaseScore, CriterionScore, Summary, score_case, summarize
from precertly.fhir import chunk_bundle, load_bundle
from precertly.ingest import estimate_embedding, ingest_bundle
from precertly.judgment import (
    RULES_VERSION,
    SYSTEM,
    CaseJudgment,
    ChartEvidence,
    StructuredChart,
    judge_case,
    model_only,
)
from precertly.judgment.judge import MAX_TOKENS, TEMPERATURE
from precertly.judgment.prompt import PROMPT_VERSION
from precertly.judgment.rules import PARTIAL_BRANCHES
from precertly.llm import BedrockClient, BedrockEmbedder
from precertly.policies import load_policies
from precertly.retrieval.search import DEFAULT_CANDIDATES, RRF_K
from precertly.settings import get_settings


def _estimate(cases: list[EvalCase], rules: bool = False) -> None:
    settings = get_settings()
    policies = load_policies()
    criteria = sum(len(scored_criteria(policies[c.policy], c.variant)) for c in cases)
    calls = criteria
    if rules:
        # Every criterion still gets its model-only call (as the verdict itself, or in
        # shadow); a partial rule result adds a second call with the computed fact.
        partial = sum(c.result.kind == "partial" for c in check_cases(cases))
        calls += partial
        print(f"Rules on: {partial} partial rule results add {partial} model calls")
    chunks = [chunk for c in cases for chunk in chunk_bundle(load_bundle(c.bundle_path()))]
    embedding = estimate_embedding(chunks)
    # Measured on baseline v2 (k = 8, prompt version 2): 3,439 input and about 270 output
    # tokens per model call, and 184 calls in 13 minutes at the 20 requests/minute quota.
    input_tokens, output_tokens = calls * 3440, calls * 270
    llm_minutes = calls / (184 / 13)
    embed_calls = embedding.unique_texts + criteria
    print(
        f"{len(cases)} cases, {criteria} criteria to judge\n"
        f"Model calls ({settings.bedrock_model_id}): {calls}, about "
        f"{input_tokens:,} input tokens and {output_tokens:,} output tokens\n"
        f"Embedding calls: up to {embed_calls} ({embedding.unique_texts} chunk texts before "
        f"any cached vectors, {criteria} search queries), about "
        f"{embedding.approx_tokens + criteria * 60:,} tokens\n"
        f"Time: about {llm_minutes:.0f} minutes (embedding calls run inside that time)"
    )


Result = tuple[CaseScore, CaseJudgment]


async def _run(
    cases: list[EvalCase], save: Callable[[list[Result]], object], rules: bool = False
) -> list[Result]:
    """Judge and score the cases, calling save after each one so a crash loses one case.

    With rules, criteria that have a deterministic rule are decided or informed by code,
    and the model-only call is still made for them in shadow.
    """
    settings = get_settings()
    policies = load_policies()
    llm, embedder = BedrockClient(settings), BedrockEmbedder(settings)
    sessions = session_factory()
    results: list[Result] = []
    try:
        for case in cases:
            async with sessions() as session:
                # All cases share one synthetic patient, so each ingest replaces the
                # previous case's chunks; vectors for unchanged text come from the cache.
                bundle = load_bundle(case.bundle_path())
                ingested = await ingest_bundle(
                    session,
                    bundle,
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
                    llm,
                    policies[case.policy],
                    case.variant,
                    case.request,
                    evidence,
                    structured=StructuredChart.from_bundle(bundle) if rules else None,
                    shadow=rules,
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
            save(results)
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


def run_metadata(rules: bool = False) -> dict[str, object]:
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
            "method": "hybrid: Postgres full-text + exact pgvector cosine, reciprocal rank fusion",
            "tie_break": "resource type, resource id, chunk index",
            "rrf_k": RRF_K,
            "candidates_per_ranking": DEFAULT_CANDIDATES,
            # RRF sums 1 / (rrf_k + rank) over both rankings with no per-ranking weight
            "weights": {"full_text": 1.0, "vector": 1.0},
            "query": "criterion text + policy quote",
            "embedding_model_id": settings.embedding_model_id,
            "embedding_dimensions": settings.embedding_dimensions,
        },
        "judgment": {
            "max_tokens": MAX_TOKENS,
            "temperature": TEMPERATURE,
            "top_k": settings.judgment_top_k,
        },
        "rules": {"enabled": rules, "version": RULES_VERSION if rules else None, "shadow": rules},
        "policies": {
            policy.id: {source.document: source.version for source in policy.sources}
            for policy in load_policies().values()
        },
        "annotation": "single annotator; not clinically validated",
    }


def _model_only_view(results: list[Result]) -> dict[str, object]:
    """The same run scored as if the model had judged every criterion alone, from the
    shadow calls, and every criterion where rule and model ended up apart."""
    cases, policies = load_cases(), load_policies()
    scores, apart = [], []
    for score, judgment in results:
        case = cases[score.case_id]
        alone = model_only(judgment, policies[case.policy])
        alone_score = score_case(case, alone)
        scores.append(alone_score)
        for with_rule, without in zip(score.criteria, alone_score.criteria, strict=True):
            final = (with_rule.predicted, with_rule.agent_not_applicable)
            if with_rule.decided_by != "model" and final != (
                without.predicted,
                without.agent_not_applicable,
            ):
                apart.append(
                    {
                        "case": score.case_id,
                        "criterion": with_rule.criterion,
                        "label": with_rule.expected.value,
                        "decided_by": with_rule.decided_by,
                        "with_rules": _shown(with_rule),
                        "model_only": _shown(without),
                        "with_rules_correct": with_rule.correct,
                        "model_only_correct": without.correct,
                    }
                )
    return {
        "summary_model_only": summarize(scores).model_dump(),
        "model_only_overall": {s.case_id: s.predicted_overall.value for s in scores},
        "rule_vs_model": apart,
    }


def partial_review(results: list[Result]) -> dict[str, list[dict[str, Any]]]:
    """Partial verdicts whose outcome needed a chart citation under PARTIAL_BRANCHES.

    chart_required_met lists each such met verdict with the items it cited, by handle and
    quote, so their relevance can be reviewed by hand: the verifier checks that a quote is
    in the item, not that it supports the outcome. c1_alone_where_chart_required lists
    any such verdict without a verified chart citation; the verifier makes it impossible,
    and this list checks that in the results.
    """
    met, violations = [], []
    for score, judgment in results:
        for verdict in judgment.verdicts:
            if verdict.rule_kind != "partial" or not verdict.computed:
                continue
            computed = verdict.computed[0]
            assert computed.branch is not None
            branch = PARTIAL_BRANCHES[computed.branch]
            final = verdict.outcome if verdict.applicable else "not_applicable"
            if final == "insufficient" or not branch.needs_chart(final):
                continue
            entry = {
                "case": score.case_id,
                "criterion": verdict.criterion,
                "row": branch.row,
                "branch": computed.branch,
                "outcome": final,
                "c1_cited": computed.cited_by_model,
                "citations": [
                    {
                        "handle": f"E{verdict.chunk_ids.index(c.chunk_id) + 1}",
                        "chunk_id": c.chunk_id,
                        "quote": c.quote,
                    }
                    for c in verdict.citations
                ],
            }
            if not any(c.is_chart for c in verdict.citations):
                violations.append(entry)
            elif final == "met":
                met.append(entry)
    return {"chart_required_met": met, "c1_alone_where_chart_required": violations}


def _shown(criterion: CriterionScore) -> str:
    return "not_applicable" if criterion.agent_not_applicable else criterion.predicted.value


# A results file is one run: every case in it must come from the same code and settings.
IDENTITY = (
    "git_sha",
    "model_id",
    "prompt_version",
    "system_prompt_sha256",
    "retrieval",
    "judgment",
    "rules",
    "policies",
)


def resume_conflicts(existing: dict[str, object], current: dict[str, object]) -> list[str]:
    """Why cases cannot be added to an existing results file; empty if they can."""
    conflicts = [
        f"{key}: file has {existing.get(key)!r}, this run has {current.get(key)!r}"
        for key in IDENTITY
        if existing.get(key) != current.get(key)
    ]
    for name, metadata in (("file", existing), ("this run", current)):
        if metadata.get("git_dirty"):
            conflicts.append(f"{name} was produced from uncommitted changes")
    return conflicts


def load_results(path: Path) -> tuple[dict[str, Any], list[tuple[CaseScore, CaseJudgment]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    results = [
        (
            CaseScore.model_validate({k: v for k, v in entry.items() if k != "verdicts"}),
            CaseJudgment.model_validate(entry["verdicts"]),
        )
        for entry in payload["cases"]
    ]
    return payload, results


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
        f"Citation precision: {percent(summary.citation_precision)} overall"
        f" ({summary.citations_in_label} of {summary.citations} cited resources are in the label),"
        f" {percent(summary.citation_precision_macro)} macro-averaged over criteria",
        f"Invalidated verdicts: {summary.invalidated_verdicts} of {summary.scored_criteria}"
        f" ({summary.quote_invalidations} citation or quote, "
        f"{summary.unusable_answers} unusable model answers)",
        f"Tokens: {summary.input_tokens:,} in, {summary.output_tokens:,} out"
        f" over {summary.llm_calls} model calls",
        f"Decided by: {summary.decided_by}",
    ]
    if summary.unusable_answers:
        lines += [
            f"Unusable answers by case: {summary.unusable_answers_by_case}",
            f"Unusable answers by criterion: {summary.unusable_answers_by_criterion}",
        ]
    lines.append("Citation precision by criterion:")
    for criterion, precision in sorted(
        summary.citation_precision_by_criterion.items(), key=lambda item: item[1].precision
    ):
        counts = f"{precision.in_label}/{precision.cited}"
        lines.append(f"  {percent(precision.precision):>6}  {counts}  {criterion}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").partition("\n")[0])
    parser.add_argument("--cases", help="comma-separated case ids (default: all)")
    parser.add_argument("--estimate", action="store_true", help="print the size of the run")
    parser.add_argument("--out", type=Path, help="results file (default: evals/results/...)")
    parser.add_argument(
        "--rules",
        action="store_true",
        help="decide or inform criteria with the deterministic rules, with shadow model calls",
    )
    parser.add_argument(
        "--resume",
        type=Path,
        help="add the cases not yet in this results file; refused unless the git sha, "
        "model, prompt and retrieval settings are identical",
    )
    args = parser.parse_args()

    available = load_cases()
    wanted = args.cases.split(",") if args.cases else list(available)
    unknown = [case_id for case_id in wanted if case_id not in available]
    if unknown:
        parser.error(f"unknown cases: {', '.join(unknown)}")
    cases = [available[case_id] for case_id in wanted]

    if args.estimate:
        _estimate(cases, args.rules)
        return 0

    metadata = run_metadata(args.rules)
    previous: list[tuple[CaseScore, CaseJudgment]] = []
    seconds_so_far = 0.0
    if args.resume:
        existing, previous = load_results(args.resume)
        conflicts = resume_conflicts(existing, metadata)
        if conflicts:
            print("cannot resume into " + str(args.resume) + ":", file=sys.stderr)
            for conflict in conflicts:
                print(f"  {conflict}", file=sys.stderr)
            return 1
        done = {score.case_id for score, _ in previous}
        cases = [case for case in cases if case.id not in done]
        seconds_so_far = float(existing.get("wall_seconds", 0.0))
        metadata["run_date"] = existing["run_date"]

    settings = get_settings()
    out = (
        args.resume
        or args.out
        or (
            EVALS_DIR
            / "results"
            / f"{date.today().isoformat()}-{settings.bedrock_model_id.replace(':', '-')}.json"
        )
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    order = list(available)
    wanted_ids = {case.id for case in cases} | {score.case_id for score, _ in previous}
    started = time.perf_counter()

    def save(new_results: list[Result]) -> Summary:
        results = sorted(previous + new_results, key=lambda r: order.index(r[0].case_id))
        summary = summarize([score for score, _ in results])
        payload = {
            **metadata,
            # False while cases are still to come; such a file can be finished with --resume
            "complete": {score.case_id for score, _ in results} == wanted_ids,
            "wall_seconds": round(seconds_so_far + time.perf_counter() - started, 1),
            "cases_run": [score.case_id for score, _ in results],
            "summary": summary.model_dump(),
            "cases": [
                {**score.model_dump(mode="json"), "verdicts": judgment.model_dump(mode="json")}
                for score, judgment in results
            ],
        }
        if args.rules:
            payload |= _model_only_view(results)
            payload["partial_review"] = partial_review(results)
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return summary

    new_results = asyncio.run(_run(cases, save, args.rules))
    summary = save(new_results)
    scores = sorted(
        (score for score, _ in previous + new_results), key=lambda s: order.index(s.case_id)
    )
    print("\n" + _report(summary, scores))
    if args.rules:
        review = partial_review(
            sorted(previous + new_results, key=lambda r: order.index(r[0].case_id))
        )
        print(
            f"\nPartial verdicts resting on [C1] alone where the chart is required: "
            f"{len(review['c1_alone_where_chart_required'])}"
        )
        print("Met verdicts that needed a chart citation, for review by hand:")
        for entry in review["chart_required_met"]:
            print(f"  {entry['case']} {entry['criterion']} (row {entry['row']}):")
            for citation in entry["citations"]:
                print(f"    [{citation['handle']}] {citation['chunk_id']}: {citation['quote']!r}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
