"""Rescore a recorded eval results file with the current verifier, with no model calls.

This is exact only for a change that adds a check on recorded answers: prompt, model,
retrieval and rules must be the ones the file was produced with, so every model answer
stands as recorded and only the decision on whether it holds can change. Verifier
version 2 adds one check (a not-applicable verdict must cite its criterion's
condition_evidence) after all the earlier ones, so it is applied to the verdicts that
passed them; a verdict already invalidated stays as it was.

The source file is never changed. The derived file records the source, its sha256, the
verifier versions, the commit that rescored it and every verdict that changed.

    python -m precertly.evals.rescore <results.json> --out <derived.json>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from precertly.evals.cases import load_cases
from precertly.evals.run import (
    _git,
    _model_only_view,
    load_results,
    partial_review,
)
from precertly.evals.scoring import score_case, summarize
from precertly.judgment import (
    RULES_VERSION,
    VERIFIER_VERSION,
    CaseJudgment,
    CriterionVerdict,
    condition_citation_problem,
    overall_outcome,
)
from precertly.judgment.models import ShadowVerdict
from precertly.judgment.prompt import PROMPT_VERSION
from precertly.policies import Criterion, load_policies

# A results file without a verifier_version was produced by version 1.
SOURCE_VERIFIER_VERSION = "1"


def _problem(
    applicable: bool,
    invalidated: str | None,
    verdict: CriterionVerdict | ShadowVerdict,
    criterion: Criterion,
) -> str | None:
    if applicable or invalidated is not None:
        return None
    return condition_citation_problem(criterion.condition_evidence, verdict.citations)


def reverify(verdict: CriterionVerdict, criterion: Criterion) -> list[tuple[str, str]]:
    """Apply the version 2 check to one verdict and its shadow, in place.

    Returns (which, reason) for each part that changed: "main" or "shadow".
    """
    changed = []
    if verdict.decided_by != "rule":
        reason = _problem(verdict.applicable, verdict.invalidated, verdict, criterion)
        if reason is not None:
            verdict.outcome, verdict.applicable, verdict.invalidated = "insufficient", True, reason
            for computed in verdict.computed:
                computed.cited_by_model, computed.quote = False, None
            changed.append(("main", reason))
    shadow: ShadowVerdict | None = verdict.shadow
    if shadow is not None:
        reason = _problem(shadow.applicable, shadow.invalidated, shadow, criterion)
        if reason is not None:
            shadow.outcome, shadow.applicable, shadow.invalidated = "insufficient", True, reason
            changed.append(("shadow", reason))
    return changed


def source_problems(payload: dict[str, Any]) -> list[str]:
    """Why this file cannot be rescored exactly by the current code; empty if it can."""
    problems = []
    source_version = payload["judgment"].get("verifier_version", SOURCE_VERIFIER_VERSION)
    if source_version != SOURCE_VERIFIER_VERSION:
        problems.append(f"file was verified by version {source_version}, not 1")
    if payload["prompt_version"] != PROMPT_VERSION:
        problems.append(f"prompt version {payload['prompt_version']}, current {PROMPT_VERSION}")
    rules = payload["rules"]
    if rules["enabled"] and rules["version"] != RULES_VERSION:
        problems.append(f"rules version {rules['version']}, current {RULES_VERSION}")
    if not payload.get("complete"):
        problems.append("file is not complete")
    return problems


def rescore(source: Path) -> dict[str, Any]:
    payload, results = load_results(source)
    problems = source_problems(payload)
    if problems:
        raise ValueError(f"{source}: " + "; ".join(problems))
    cases, policies = load_cases(), load_policies()
    rescored, changes = [], []
    for score, judgment in results:
        case = cases[score.case_id]
        policy = policies[case.policy]
        labels = {label.criterion: label.outcome.value for label in case.expected}
        for verdict in judgment.verdicts:
            # Only a not-applicable verdict can change, and only to insufficient.
            for which, reason in reverify(verdict, policy.criterion(verdict.criterion)):
                changes.append(
                    {
                        "case": score.case_id,
                        "criterion": verdict.criterion,
                        "which": which,
                        "decided_by": verdict.decided_by if which == "main" else "model",
                        "label": labels.get(verdict.criterion),
                        "before": "not_applicable",
                        "after": "insufficient",
                        "reason": reason,
                    }
                )
        judgment = CaseJudgment(
            policy=judgment.policy,
            variant=judgment.variant,
            verdicts=judgment.verdicts,
            overall=overall_outcome(policy.variant(judgment.variant).logic, judgment.outcomes()),
            reviewer_checklist=judgment.reviewer_checklist,
        )
        rescored.append((score_case(case, judgment), judgment))

    derived = {k: v for k, v in payload.items() if k != "pair"}
    derived["judgment"] = payload["judgment"] | {"verifier_version": VERIFIER_VERSION}
    derived["summary"] = summarize([score for score, _ in rescored]).model_dump()
    derived["cases"] = [
        {**score.model_dump(mode="json"), "verdicts": judgment.model_dump(mode="json")}
        for score, judgment in rescored
    ]
    if payload["rules"]["enabled"]:
        derived |= _model_only_view(rescored)
        derived["partial_review"] = partial_review(rescored)
    derived["rescored"] = {
        "source": source.as_posix(),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_pair": payload.get("pair"),
        "source_verifier_version": SOURCE_VERIFIER_VERSION,
        "verifier_version": VERIFIER_VERSION,
        "git_sha": _git("rev-parse", "--short", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain", "--untracked-files=no")),
        "method": "recorded answers re-checked offline; no model calls",
        "changes": changes,
    }
    return derived


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").partition("\n")[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.resolve() == args.source.resolve():
        print("--out must not be the source file", file=sys.stderr)
        return 1
    try:
        derived = rescore(args.source)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    args.out.write_text(json.dumps(derived, indent=2) + "\n", encoding="utf-8")
    for change in derived["rescored"]["changes"]:
        print(
            f"{change['case']} {change['criterion']} ({change['which']}): label "
            f"{change['label']}, {change['before']} -> {change['after']}: {change['reason']}"
        )
    print(f"{len(derived['rescored']['changes'])} verdicts changed; wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
