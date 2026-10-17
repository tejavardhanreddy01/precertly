"""Build the draft PAS request for every facet case from the recorded verdicts.

    uv run python -m precertly.pas            # write evals/pas/<case>.draft.json and .envelope.json
    uv run python -m precertly.pas --check    # fail if a committed draft is stale

The verdicts come from a recorded results file, so no model is called. The drafts are for
human review and are never submitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from precertly.evals.cases import EVALS_DIR, load_cases
from precertly.evals.run import load_results
from precertly.fhir import load_bundle
from precertly.pas.builder import Source, build
from precertly.policies import load_policies

SOURCE = EVALS_DIR / "results" / "2026-10-14-experiment-1.1a-rescored-issue4.json"
OUT = EVALS_DIR / "pas"


def _json(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def drafts(source_path: Path = SOURCE) -> dict[str, str]:
    """Every draft and envelope, by file name, as text."""
    _, results = load_results(source_path)
    source = Source(
        path=source_path.relative_to(EVALS_DIR.parent).as_posix(),
        sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
    )
    cases, policies = load_cases(), load_policies()
    files = {}
    for score, judgment in results:
        case = cases[score.case_id]
        chart = [entry["resource"] for entry in load_bundle(case.bundle_path())["entry"]]
        draft = build(case, policies[case.policy], judgment, chart, source)
        bundle = _json(draft.bundle)
        draft.envelope["draft"]["sha256"] = hashlib.sha256(bundle.encode()).hexdigest()
        files[f"{case.id}.draft.json"] = bundle
        files[f"{case.id}.envelope.json"] = _json(draft.envelope)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").partition("\n")[0])
    parser.add_argument("--check", action="store_true", help="fail if a committed file is stale")
    args = parser.parse_args(argv)
    files = drafts()
    if args.check:
        stale = [
            name
            for name, text in files.items()
            if not (OUT / name).exists() or (OUT / name).read_text(encoding="utf-8") != text
        ]
        extra = sorted(p.name for p in OUT.glob("facet-*.json") if p.name not in files)
        for name in stale + extra:
            print(f"stale: evals/pas/{name}", file=sys.stderr)
        return 1 if stale or extra else 0
    OUT.mkdir(exist_ok=True)
    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8")
    print(f"wrote {len(files)} files to {OUT.relative_to(EVALS_DIR.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
