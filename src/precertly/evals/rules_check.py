"""Run the deterministic rules over the labeled cases, offline, and compare with the labels.

    uv run python -m precertly.evals.rules_check

No model, no database, no AWS. A decided result, including "not applicable", that
contradicts its label is a bug in a rule or a question about a label, and the command
exits non-zero. Partial and absent results are left to the model, so they are only counted.
"""

from __future__ import annotations

import sys
from collections import Counter

from pydantic import BaseModel

from precertly.evals.cases import EvalCase, load_cases
from precertly.fhir import load_bundle
from precertly.judgment.models import Outcome
from precertly.judgment.rules import RULES, RuleResult, evaluate
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


def main() -> int:
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
