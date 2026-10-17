"""Compare two eval results files that are meant to be a pair (same code, same settings).

    uv run python -m precertly.evals.compare A.json B.json          # report only
    uv run python -m precertly.evals.compare A.json B.json --mark   # also record the pair

A pair measures run-to-run noise, so it has to be a clean comparison: identical commit and
settings, and no verdict in either run where the model gave no usable answer. --mark writes
a "pair" block into both files saying what the other file is and whether the pair is clean.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from precertly.evals.run import IDENTITY


def _verdicts(payload: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (case["case_id"], verdict["criterion"]): verdict
        for case in payload["cases"]
        for verdict in case["verdicts"]["verdicts"]
    }


def _final(verdict: dict[str, Any]) -> str:
    return verdict["outcome"] if verdict["applicable"] else "not_applicable"


def difference_cause(a: dict[str, Any], b: dict[str, Any]) -> str:
    """Why two verdicts on the same criterion differ.

    "citation verified in one run only": the model gave the same outcome in both runs and
    one was invalidated. "model outcome changed": the model's own outcome differs.
    "applicability changed": same outcome, both valid, but only one found that the
    criterion's condition does not hold.
    """
    if a["model_outcome"] != b["model_outcome"]:
        return "model outcome changed"
    if (a["invalidated"] is None) != (b["invalidated"] is None):
        return "citation verified in one run only"
    if a["applicable"] != b["applicable"]:
        return "applicability changed"
    return "other"


def compare(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """What differs between two runs, and whether they are a clean pair."""
    va, vb = _verdicts(a), _verdicts(b)
    shared = [key for key in va if key in vb]
    unusable = {
        name: sorted(
            f"{case}/{criterion}"
            for (case, criterion), verdict in verdicts.items()
            if verdict["model_outcome"] == "none"
        )
        for name, verdicts in (("a", va), ("b", vb))
    }
    reasons = [f"{key} differs" for key in IDENTITY if a.get(key) != b.get(key)]
    reasons += [
        f"run {name} came from uncommitted changes"
        for name, p in (("a", a), ("b", b))
        if p.get("git_dirty")
    ]
    reasons += [
        f"run {name} is incomplete"
        for name, p in (("a", a), ("b", b))
        if p.get("complete") is False
    ]
    if set(va) != set(vb):
        reasons.append("the runs do not cover the same verdicts")
    reasons += [
        f"run {name} has {len(found)} unusable model answer(s)"
        for name, found in unusable.items()
        if found
    ]
    return {
        "clean": not reasons,
        "not_clean_because": reasons,
        "verdicts": len(shared),
        "evidence_identical": sum(va[k]["chunk_ids"] == vb[k]["chunk_ids"] for k in shared),
        "verdicts_differ": [
            {
                "case": k[0],
                "criterion": k[1],
                "a": _final(va[k]),
                "b": _final(vb[k]),
                "cause": difference_cause(va[k], vb[k]),
            }
            for k in shared
            if _final(va[k]) != _final(vb[k])
        ],
        "unusable_answers": unusable,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").partition("\n")[0])
    parser.add_argument("a", type=Path)
    parser.add_argument("b", type=Path)
    parser.add_argument("--mark", action="store_true", help="record the pair in both files")
    args = parser.parse_args()

    a = json.loads(args.a.read_text(encoding="utf-8"))
    b = json.loads(args.b.read_text(encoding="utf-8"))
    result = compare(a, b)

    print(f"clean pair: {result['clean']}")
    for reason in result["not_clean_because"]:
        print(f"  not clean: {reason}")
    print(f"evidence identical: {result['evidence_identical']} of {result['verdicts']} verdicts")
    print(f"verdicts that differ: {len(result['verdicts_differ'])} of {result['verdicts']}")
    for d in result["verdicts_differ"]:
        print(f"  {d['case']} {d['criterion']}: {d['a']} vs {d['b']}")
    for name, found in result["unusable_answers"].items():
        print(f"unusable model answers in run {name}: {len(found)} {found or ''}".rstrip())

    if args.mark:
        for path, payload, other in ((args.a, a, args.b), (args.b, b, args.a)):
            payload["pair"] = {
                "with": other.name,
                "clean": result["clean"],
                "not_clean_because": result["not_clean_because"],
                "verdicts_differ": len(result["verdicts_differ"]),
                "unusable_answers": sum(len(v) for v in result["unusable_answers"].values()),
            }
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"marked {args.a.name} and {args.b.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
