"""Rebuild every prompt in a results file and check it against the recorded hashes.

    uv run python -m precertly.evals.prompt_check evals/results/<file>.json

No model, no database, no AWS. Chart chunks are rebuilt from the case bundle with the
chunker (the same text ingest stores), request chunks from the case request, and partial
rule results by running the rules again. Every recorded model call must hash to the
prompt rebuilt this way, and to the system prompt; the command exits non-zero otherwise.

Results files from before prompt version 3 recorded no exchanges and cannot be checked.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from precertly.evals.cases import EvalCase, load_cases
from precertly.fhir import chunk_bundle, load_bundle
from precertly.judgment import CaseJudgment, EvidenceChunk, StructuredChart, evaluate
from precertly.judgment.models import CriterionVerdict
from precertly.judgment.prompt import PROMPT_VERSIONS, PromptVersion, build_prompt, system_prompt
from precertly.llm import Exchange, sha256_text
from precertly.policies import Policy, load_policies


class PromptCheck(BaseModel):
    exchanges_checked: int = 0
    problems: list[str] = []


def case_chunks(case: EvalCase) -> dict[str, EvidenceChunk]:
    """Every chunk the model could have been shown for a case, by id."""
    chunks = {chunk.id: chunk for chunk in case.request.chunks()}
    for chunk in chunk_bundle(load_bundle(case.bundle_path())):
        chunks[chunk.id] = EvidenceChunk(
            id=chunk.id,
            text=chunk.text,
            effective_date=chunk.effective_date,
            free_text=chunk.char_start is not None,
        )
    return chunks


def _check_exchanges(
    where: str, exchanges: list[Exchange], prompt: str, system_text: str, result: PromptCheck
) -> None:
    if not exchanges:
        result.problems.append(f"{where}: a model call was made but no exchange is recorded")
    expected, system = sha256_text(prompt), sha256_text(system_text)
    for exchange in exchanges:
        result.exchanges_checked += 1
        if exchange.prompt_sha256 != expected:
            result.problems.append(f"{where}, attempt {exchange.attempt}: prompt hash differs")
        if exchange.system_sha256 != system:
            result.problems.append(f"{where}, attempt {exchange.attempt}: system hash differs")


def check_verdict(
    case: EvalCase,
    policy: Policy,
    verdict: CriterionVerdict,
    chunks: dict[str, EvidenceChunk],
    chart: StructuredChart,
    result: PromptCheck,
    version: PromptVersion = "3",
) -> None:
    where = f"{case.id} {verdict.criterion}"
    system = system_prompt(version)
    missing = [chunk_id for chunk_id in verdict.chunk_ids if chunk_id not in chunks]
    if missing:
        result.problems.append(f"{where}: chunks not in the rebuilt case: {missing}")
        return
    shown = [chunks[chunk_id] for chunk_id in verdict.chunk_ids]
    criterion = policy.criterion(verdict.criterion)

    if verdict.decided_by == "rule":
        if verdict.exchanges:
            result.problems.append(f"{where}: decided by rule, but a model call is recorded")
    else:
        partial = None
        if verdict.rule_kind == "partial":
            partial = evaluate(criterion, chart, case.request)
            if partial is None or partial.kind != "partial":
                result.problems.append(f"{where}: the rule no longer returns a partial result")
                return
        prompt = build_prompt(criterion, case.request, shown, partial, version)
        _check_exchanges(where, verdict.exchanges, prompt, system, result)
    if verdict.shadow is not None:
        plain = build_prompt(criterion, case.request, shown, version=version)
        _check_exchanges(f"{where} (shadow)", verdict.shadow.exchanges, plain, system, result)


def check_results(payload: dict[str, Any]) -> PromptCheck:
    cases, policies = load_cases(), load_policies()
    result = PromptCheck()
    # The dry run in tests/test_prompt_check.py builds a payload without metadata.
    version = payload.get("prompt_version", "3")
    if version not in PROMPT_VERSIONS:
        result.problems.append(f"prompt version {version!r} cannot be rebuilt")
        return result
    for entry in payload["cases"]:
        case = cases[entry["case_id"]]
        judgment = CaseJudgment.model_validate(entry["verdicts"])
        chunks = case_chunks(case)
        chart = StructuredChart.from_bundle(load_bundle(case.bundle_path()))
        for verdict in judgment.verdicts:
            check_verdict(case, policies[case.policy], verdict, chunks, chart, result, version)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").partition("\n")[0])
    parser.add_argument("results", type=Path, nargs="+")
    args = parser.parse_args(argv)
    failed = False
    for path in args.results:
        result = check_results(json.loads(path.read_text(encoding="utf-8")))
        print(
            f"{path}: {result.exchanges_checked} model calls checked, "
            f"{len(result.problems)} problems"
        )
        for problem in result.problems:
            print(f"  {problem}")
        failed |= bool(result.problems) or result.exchanges_checked == 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
