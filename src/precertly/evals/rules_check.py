"""Run the deterministic rules over the labeled cases, offline, and compare with the labels.

    uv run python -m precertly.evals.rules_check
    uv run python -m precertly.evals.rules_check --table        # the partial branches
    uv run python -m precertly.evals.rules_check --recompute evals/results/<file>.json

No model, no database, no AWS. A decided result, including "not applicable", that
contradicts its label is a bug in a rule or a question about a label, and the command
exits non-zero. Partial and absent results are left to the model, so they are only counted.

--recompute runs the rules again for every computed citation in a results file and
exits non-zero unless each one is reproduced exactly: statement, inputs and branch.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from precertly.evals.cases import EvalCase, load_cases
from precertly.fhir import load_bundle
from precertly.judgment import CaseJudgment
from precertly.judgment.models import Outcome
from precertly.judgment.rules import (
    PARTIAL_BRANCHES,
    RULES,
    FinalOutcome,
    PartialBranch,
    RuleResult,
    evaluate,
)
from precertly.judgment.structured import StructuredChart
from precertly.policies import load_policies


class RuleCheck(BaseModel):
    case_id: str
    criterion: str
    label: Outcome
    result: RuleResult

    @property
    def decided(self) -> Outcome | None:
        if self.result.kind != "decided":
            return None
        return Outcome(self.result.outcome) if self.result.applicable else Outcome.NOT_APPLICABLE

    @property
    def contradicts_label(self) -> bool:
        return self.decided is not None and self.decided != self.label


def check_cases(cases: list[EvalCase]) -> list[RuleCheck]:
    policies = load_policies()
    checks = []
    for case in cases:
        chart = StructuredChart.from_bundle(load_bundle(case.bundle_path()))
        policy = policies[case.policy]
        for label in case.expected:
            result = evaluate(policy.criterion(label.criterion), chart, case.request)
            if result is not None:
                checks.append(
                    RuleCheck(
                        case_id=case.id,
                        criterion=label.criterion,
                        label=label.outcome,
                        result=result,
                    )
                )
    return checks


def recompute(payload: dict[str, Any]) -> tuple[int, list[str]]:
    """Computed citations checked, and every one the rules no longer reproduce."""
    cases, policies = load_cases(), load_policies()
    checked, problems = 0, []
    for entry in payload["cases"]:
        case = cases[entry["case_id"]]
        chart = StructuredChart.from_bundle(load_bundle(case.bundle_path()))
        policy = policies[case.policy]
        for verdict in CaseJudgment.model_validate(entry["verdicts"]).verdicts:
            for computed in verdict.computed:
                checked += 1
                result = evaluate(policy.criterion(verdict.criterion), chart, case.request)
                recorded = (computed.statement, computed.inputs, computed.branch)
                if result is None or (result.statement, result.inputs, result.branch) != recorded:
                    problems.append(f"{case.id} {verdict.criterion}: not reproduced")
    return checked, problems


def print_table() -> None:
    """PARTIAL_BRANCHES as a table: what may rest on [C1] alone, and what needs the chart."""

    def need(branch: PartialBranch, outcome: FinalOutcome) -> str:
        return "chart req." if branch.needs_chart(outcome) else "C1 ok"

    policy = load_policies()["facet-joint-interventions"]
    print("| Row | Branch | Open question | met | not_met | not applicable | insufficient |")
    print("|---|---|---|---|---|---|---|")
    for key, branch in PARTIAL_BRANCHES.items():
        if not policy.criterion(branch.criterion).condition:
            not_applicable = "n/a (no condition)"
        elif branch.condition_fixed:
            not_applicable = "invalid ([C1] fixes the condition)"
        else:
            not_applicable = need(branch, "not_applicable")
        print(
            f"| {branch.row} | `{key}` | {branch.open_question} | {need(branch, 'met')} | "
            f"{need(branch, 'not_met')} | {not_applicable} | no citation needed |"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").partition("\n")[0])
    parser.add_argument("--table", action="store_true", help="print the partial branches")
    parser.add_argument("--recompute", type=Path, help="re-check a results file's computed facts")
    args = parser.parse_args(argv)
    if args.table:
        print_table()
        return 0
    if args.recompute:
        checked, problems = recompute(json.loads(args.recompute.read_text(encoding="utf-8")))
        print(f"{checked} computed citations checked, {len(problems)} not reproduced")
        for problem in problems:
            print(f"  {problem}")
        return 1 if problems or not checked else 0

    checks = check_cases(list(load_cases().values()))
    print(f"{'criterion':<42}{'decided':>8}{'partial':>8}{'absent':>8}")
    for criterion in RULES:
        kinds = Counter(c.result.kind for c in checks if c.criterion == criterion)
        print(f"{criterion:<42}{kinds['decided']:>8}{kinds['partial']:>8}{kinds['absent']:>8}")
    kinds = Counter(c.result.kind for c in checks)
    print(f"{'total':<42}{kinds['decided']:>8}{kinds['partial']:>8}{kinds['absent']:>8}")

    print("\ndecided results:")
    for check in checks:
        if check.decided is not None:
            flag = "  <<< CONTRADICTS LABEL" if check.contradicts_label else ""
            print(
                f"  {check.case_id} {check.criterion}: {check.decided.value} "
                f"(label {check.label.value}){flag}"
            )
    wrong = [c for c in checks if c.contradicts_label]
    print(f"\n{len(wrong)} decided result(s) contradict a label")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
