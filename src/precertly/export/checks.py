"""The export invariants. `python -m precertly.export --check` runs all of them, and
tests/test_export.py runs each on its own. A check raises CheckFailed naming itself."""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from precertly.evals.run import load_results
from precertly.export import models as m
from precertly.export.build import (
    SET_ORDER,
    ExportError,
    build_files,
    locate,
    schema_text,
    write_files,
)
from precertly.pas.__main__ import SOURCE

EXPECTED_CASES = 29
EXPECTED_POLICIES = 4
EXPECTED_CRITERIA = 76
EXPECTED_LABELS = 326
EXPECTED_SCORED = 11
EXPECTED_VERDICTS = 184
MAX_CASE_BYTES = 1_000_000

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
AWS_KEY = re.compile(r"AKIA[0-9A-Z]{16}")


class CheckFailed(Exception):
    def __init__(self, check: str, detail: str) -> None:
        super().__init__(f"{check}: {detail}")
        self.check = check
        self.detail = detail


Files = dict[str, str]


def _json(files: Files, name: str) -> Any:
    return json.loads(files[name])


def parsed(files: Files) -> tuple[m.Index, dict[str, m.PolicyFile], dict[str, m.CaseFile]]:
    """The files parsed with their models, which forbid extra fields."""
    index = m.Index.model_validate(_json(files, "index.json"))
    policies = {
        name.removeprefix("policies/").removesuffix(".json"): m.PolicyFile.model_validate(
            _json(files, name)
        )
        for name in sorted(files)
        if name.startswith("policies/")
    }
    cases = {
        name.removeprefix("cases/").removesuffix(".json"): m.CaseFile.model_validate(
            _json(files, name)
        )
        for name in sorted(files)
        if name.startswith("cases/")
    }
    return index, policies, cases


def check_models(files: Files) -> None:
    """g. The files parse with models that forbid extra fields; schema.json matches them."""
    for model in m.MODELS.values():
        if model.model_config.get("extra") != "forbid":
            raise CheckFailed("models", f"{model.__name__} does not forbid extra fields")
    try:
        parsed(files)
    except ValueError as error:
        raise CheckFailed("models", f"a file does not match its model: {error}") from error
    if files.get("schema.json") != schema_text():
        raise CheckFailed("models", "schema.json does not match the models")


def check_citations(files: Files) -> None:
    """a, b. Every recorded citation is in a chunk of its case's exported chart, at the
    recorded offsets, as the verifier guarantees (see build.locate)."""
    _, _, cases = parsed(files)
    for case in cases.values():
        if case.result is None:
            continue
        chunks = {c.chunk_id: c for c in case.chart}
        for verdict in case.result.verdicts:
            for chunk_id in (*verdict.shown_chunk_ids, *(c.chunk_id for c in verdict.citations)):
                if chunk_id not in chunks:
                    raise CheckFailed(
                        "chunk ids",
                        f"{case.id} {verdict.criterion}: {chunk_id} is not in the chart",
                    )
            for c in verdict.citations:
                try:
                    located = locate(
                        chunks[c.chunk_id], c.chunk_id, c.quote, c.char_start, c.char_end
                    )
                except ExportError as error:
                    raise CheckFailed("citation round trip", str(error)) from error
                if located != c.located:
                    raise CheckFailed(
                        "citation round trip",
                        f"{case.id} {verdict.criterion}: located is {c.located} but the quote "
                        f"is {'' if located else 'not '}at the offsets of {c.chunk_id}",
                    )
                if not located and verdict.invalidated is None:
                    raise CheckFailed(
                        "citation round trip",
                        f"{case.id} {verdict.criterion}: an unlocated citation of {c.chunk_id} "
                        "on a verdict that was not invalidated",
                    )


def check_counts(files: Files) -> None:
    """c. Counts, and the summary equals the source run's summary."""
    index, policies, cases = parsed(files)
    scored = [c for c in cases.values() if c.result is not None]
    got = {
        "cases": len(cases),
        "policies": len(policies),
        "criteria": sum(len(p.criteria) for p in policies.values()),
        "labeled criteria": sum(len(c.expected) for c in cases.values()),
        "scored cases": len(scored),
        "verdicts": sum(len(c.result.verdicts) for c in scored if c.result),
    }
    want = {
        "cases": EXPECTED_CASES,
        "policies": EXPECTED_POLICIES,
        "criteria": EXPECTED_CRITERIA,
        "labeled criteria": EXPECTED_LABELS,
        "scored cases": EXPECTED_SCORED,
        "verdicts": EXPECTED_VERDICTS,
    }
    for name, value in want.items():
        if got[name] != value:
            raise CheckFailed("counts", f"{name}: {got[name]}, expected {value}")
    order = [(SET_ORDER.index(r.set), r.id) for r in index.cases]
    if order != sorted(order):
        raise CheckFailed("counts", "index cases are not ordered by set, then id")
    source = json.loads(SOURCE.read_text(encoding="utf-8"))["summary"]
    for key in m.RunSummary.model_fields:
        if getattr(index.summary, key) != source[key]:
            raise CheckFailed(
                "counts", f"summary {key}: {getattr(index.summary, key)} != {source[key]}"
            )


def check_flags(files: Files) -> None:
    """d. Each scored case's flags and overall equal the source run's, and the flags follow
    from the labels and the outcomes."""
    _, _, cases = parsed(files)
    payload, _ = load_results(SOURCE)
    for entry in payload["cases"]:
        case = cases[entry["case_id"]]
        result = case.result
        if result is None:
            raise CheckFailed("flags", f"{case.id} is in the run but has no recorded result")
        if result.predicted_overall != entry["predicted_overall"]:
            raise CheckFailed("flags", f"{case.id}: predicted_overall differs from the run")
        if result.overall_correct != (case.expected_overall == result.predicted_overall):
            raise CheckFailed("flags", f"{case.id}: overall_correct does not follow")
        recorded = {c["criterion"]: c for c in entry["criteria"]}
        if [v.criterion for v in result.verdicts] != [
            v["criterion"] for v in entry["verdicts"]["verdicts"]
        ]:
            raise CheckFailed("flags", f"{case.id}: verdicts differ from the run")
        for v in result.verdicts:
            flags = recorded[v.criterion]
            if (v.correct, v.false_approval) != (flags["correct"], flags["false_approval"]):
                raise CheckFailed("flags", f"{case.id} {v.criterion}: flags differ from the run")
            correct = (
                (not v.applicable) if v.expected == "not_applicable" else v.outcome == v.expected
            )
            false_approval = v.outcome == "met" and v.expected in ("not_met", "insufficient")
            if (v.correct, v.false_approval) != (correct, false_approval):
                raise CheckFailed("flags", f"{case.id} {v.criterion}: flags do not follow")


def build_to(directory: Path) -> Files:
    files = build_files()
    write_files(files, directory)
    return files


def check_determinism(_: Files | None = None) -> None:
    """e. Two builds into two directories are byte-identical."""
    with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
        build_to(Path(one))
        build_to(Path(two))
        names = sorted(p.relative_to(one).as_posix() for p in Path(one).rglob("*") if p.is_file())
        other = sorted(p.relative_to(two).as_posix() for p in Path(two).rglob("*") if p.is_file())
        if names != other:
            raise CheckFailed("determinism", "the two builds wrote different files")
        for name in names:
            if (Path(one) / name).read_bytes() != (Path(two) / name).read_bytes():
                raise CheckFailed("determinism", f"{name} differs between two builds")


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def check_secrets(files: Files) -> None:
    """f. No exported string looks like an email address or an AWS access key id."""
    for name, text in files.items():
        for string in _strings(json.loads(text)):
            for kind, pattern in (("an email address", EMAIL), ("an AWS access key id", AWS_KEY)):
                if match := pattern.search(string):
                    raise CheckFailed("secrets", f"{name} has {kind}: {match.group()!r}")


def sizes(files: Files) -> dict[str, int]:
    return {name: len(text.encode("utf-8")) for name, text in sorted(files.items())}


def check_sizes(files: Files) -> None:
    """h. No case file is over 1 MB. (--check prints every size.)"""
    for name, size in sizes(files).items():
        if name.startswith("cases/") and size > MAX_CASE_BYTES:
            raise CheckFailed("sizes", f"{name} is {size:,} bytes, over {MAX_CASE_BYTES:,}")


CHECKS: list[tuple[str, Callable[[Files], None]]] = [
    ("models", check_models),
    ("citations", check_citations),
    ("counts", check_counts),
    ("flags", check_flags),
    ("secrets", check_secrets),
    ("sizes", check_sizes),
    ("determinism", check_determinism),
]
