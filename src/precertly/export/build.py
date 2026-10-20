"""Build the export files in memory: relative path -> text.

Nothing here calls a model, a database or the network. The chart is chunked from the case
bundle with the chunker, and the request fields are the same pseudo-chunks the judgment step
shows the model (PriorAuthRequest.chunks), so the text in the export is the text it saw.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from precertly.evals.cases import EVALS_DIR, EvalCase, load_case
from precertly.evals.run import load_results
from precertly.evals.scoring import CaseScore
from precertly.export import models as m
from precertly.fhir import chunk_bundle, load_bundle
from precertly.judgment import CaseJudgment
from precertly.judgment.judge import find_quote
from precertly.judgment.logic import JUDGED_STAGES
from precertly.pas.__main__ import SOURCE
from precertly.policies import LogicNode, Policy, load_policies

ROOT = EVALS_DIR.parent
DEFAULT_OUT = ROOT / "web" / "public" / "data"

# Sets in the order the index lists them; within a set, cases are ordered by id.
SET_ORDER = ["facet", "pap", "bariatric", "spinal-cord-stimulators"]


class ExportError(Exception):
    """The export cannot be written honestly: something recorded does not check out."""


def to_text(value: Any) -> str:
    """Canonical JSON: sorted keys, two-space indent, UTF-8, trailing newline."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def schema_text() -> str:
    return to_text({name: model.model_json_schema() for name, model in m.MODELS.items()})


def _logic(node: LogicNode) -> m.Logic:
    def child(c: str | LogicNode) -> str | m.Logic:
        return c if isinstance(c, str) else _logic(c)

    return m.Logic(
        all_of=None if node.all_of is None else [child(c) for c in node.all_of],
        any_of=None if node.any_of is None else [child(c) for c in node.any_of],
    )


def policy_file(policy: Policy) -> m.PolicyFile:
    return m.PolicyFile(
        id=policy.id,
        title=policy.title,
        document=policy.document,
        verified_on=policy.verified_on,
        sources=[
            m.PolicySource(
                document=s.document,
                title=s.title,
                url=str(s.url),
                version=s.version,
                effective_date=s.effective_date,
                contractor=s.contractor,
            )
            for s in policy.sources
        ],
        criteria=[
            m.PolicyCriterion(
                id=c.id,
                text=c.text,
                kind=c.kind.value,
                evidence_stage=c.evidence_stage.value,
                severity=c.severity.value,
                quote=c.quote,
                source_document=c.source_document,
                source_section=c.source_section,
                condition=c.condition,
                condition_evidence=c.condition_evidence,
                note=c.note,
            )
            for c in policy.criteria
        ],
        variants=[
            m.PolicyVariant(id=v.id, title=v.title, codes=v.codes, logic=_logic(v.logic))
            for v in policy.variants
        ],
        not_covered=policy.not_covered,
        notes=policy.notes,
    )


def chart_chunks(case: EvalCase, as_of: date) -> list[m.ChartChunk]:
    """Request fields and every chart chunk of a case, by effective date then chunk id.

    as_of fixes the age in the Patient chunk (chunk_bundle would use today's date, so the
    export would change with the calendar); the run date is the day the model saw it.
    """
    chunks = [
        m.ChartChunk(
            chunk_id=c.id,
            source="request",
            resource_type="Request",
            resource_id=c.id.removeprefix("request."),
            chunk_index=0,
            effective_date=c.effective_date,
            text=c.text,
            char_start=None,
            char_end=None,
        )
        for c in case.request.chunks()
    ]
    chunks += [
        m.ChartChunk(
            chunk_id=c.id,
            source="chart",
            resource_type=c.resource_type,
            resource_id=c.resource_id,
            chunk_index=c.chunk_index,
            effective_date=c.effective_date,
            text=c.text,
            char_start=c.char_start,
            char_end=c.char_end,
        )
        for c in chunk_bundle(load_bundle(case.bundle_path()), as_of=as_of)
    ]
    ids = [c.chunk_id for c in chunks]
    if len(ids) != len(set(ids)):
        raise ExportError(f"{case.id}: duplicate chunk ids in the chart")
    return sorted(
        chunks,
        key=lambda c: (c.effective_date is not None, c.effective_date or date.min, c.chunk_id),
    )


def locate(
    citation_chunk: m.ChartChunk | None, chunk_id: str, quote: str, start: int, end: int
) -> bool:
    """Whether the quote is at [start, end) of the chunk text, as the verifier guarantees.

    The verifier matches a quote against the chunk text ignoring differences in whitespace
    only (judge.find_quote) and records the span of that match, relative to the chunk text.
    So the slice must equal the quote up to whitespace, and find_quote must give that span.
    """
    if citation_chunk is None:
        raise ExportError(f"citation of {chunk_id}: the chunk is not in the exported chart")
    return citation_chunk.text[start:end].split() == quote.split() and find_quote(
        quote, citation_chunk.text
    ) == (start, end)


def _verdicts(
    case: EvalCase,
    score: CaseScore,
    judgment: CaseJudgment,
    chart: list[m.ChartChunk],
) -> list[m.Verdict]:
    by_id = {c.chunk_id: c for c in chart}
    flags = {c.criterion: c for c in score.criteria}
    labels = case.outcomes()
    out = []
    for v in judgment.verdicts:
        citations = []
        for c in v.citations:
            located = locate(by_id.get(c.chunk_id), c.chunk_id, c.quote, c.char_start, c.char_end)
            if not located and v.invalidated is None:
                raise ExportError(
                    f"{case.id} {v.criterion}: quote not found at {c.char_start}-{c.char_end} "
                    f"of {c.chunk_id}: {c.quote[:80]!r}"
                )
            citations.append(
                m.Citation(
                    chunk_id=c.chunk_id,
                    quote=c.quote,
                    char_start=c.char_start,
                    char_end=c.char_end,
                    located=located,
                )
            )
        flag = flags[v.criterion]
        out.append(
            m.Verdict(
                criterion=v.criterion,
                outcome=v.outcome,
                model_outcome=v.model_outcome,
                decided_by=v.decided_by,
                rule_kind=v.rule_kind,
                applicable=v.applicable,
                confidence=v.confidence,
                rationale=v.rationale,
                citations=citations,
                invalidated=v.invalidated,
                invalidation=flag.invalidation,
                computed=[
                    m.ComputedFact(
                        computed=k.computed,
                        statement=k.statement,
                        inputs=k.inputs,
                        rule=k.rule,
                        rules_version=k.rules_version,
                        branch=k.branch,
                        cited_by_model=k.cited_by_model,
                        quote=k.quote,
                    )
                    for k in v.computed
                ],
                shown_chunk_ids=v.chunk_ids,
                expected=labels[v.criterion].value,
                correct=flag.correct,
                false_approval=flag.false_approval,
                prompt_sha256=v.exchanges[0].prompt_sha256 if v.exchanges else None,
            )
        )
    return out


def case_file(
    case: EvalCase,
    case_set: str,
    policy: Policy,
    as_of: date,
    recorded: tuple[CaseScore, CaseJudgment] | None,
) -> m.CaseFile:
    chart = chart_chunks(case, as_of)
    in_variant = policy.variant(case.variant).logic.criterion_ids()
    result = None
    if recorded is not None:
        score, judgment = recorded
        result = m.RecordedResult(
            predicted_overall=judgment.overall.value,  # type: ignore[arg-type]
            overall_correct=case.expected_overall == judgment.overall,
            reviewer_checklist=[
                m.ChecklistItem(criterion=i.criterion, evidence_stage=i.evidence_stage, text=i.text)
                for i in judgment.reviewer_checklist
            ],
            verdicts=_verdicts(case, score, judgment, chart),
        )
    return m.CaseFile(
        id=case.id,
        set=case_set,
        policy=case.policy,
        policy_version=case.policy_version,
        variant=case.variant,
        request=m.Request.model_validate(case.request.model_dump(mode="json", exclude_none=True)),
        description=case.notes,
        tags=case.tags,
        expected_overall=case.expected_overall.value,  # type: ignore[arg-type]
        expected=[
            m.ExpectedLabel(
                criterion=label.criterion,
                outcome=label.outcome.value,  # type: ignore[arg-type]
                fact=label.fact,
                evidence=label.evidence,
            )
            for label in case.expected
        ],
        changed_labels=[
            m.ChangedLabel(criterion=c.criterion, quote=c.quote, why=c.why)
            for c in case.changed_labels
        ],
        criteria=[
            m.CaseCriterion(
                criterion=c.id,
                text=c.text,
                evidence_stage=c.evidence_stage.value,
                judged=c.evidence_stage in JUDGED_STAGES,
            )
            for c in policy.criteria
            if c.id in in_variant
        ],
        chart=chart,
        scored=recorded is not None,
        result=result,
    )


def _run_info(payload: dict[str, Any], source: Path) -> m.RunInfo:
    return m.RunInfo(
        run_date=payload["run_date"],
        git_sha=payload["git_sha"],
        git_dirty=payload["git_dirty"],
        complete=payload["complete"],
        model_id=payload["model_id"],
        prompt_version=payload["prompt_version"],
        system_prompt_sha256=payload["system_prompt_sha256"],
        judgment=m.JudgmentInfo(verifier_version=payload["judgment"]["verifier_version"]),
        rules=m.RulesInfo(version=payload["rules"]["version"]),
        retrieval=m.RetrievalInfo(
            top_k=payload["retrieval"]["top_k"], method=payload["retrieval"]["method"]
        ),
        annotation=payload["annotation"],
        rescored=m.RescoredInfo(
            source=payload["rescored"]["source"], source_sha256=payload["rescored"]["source_sha256"]
        ),
        source_file=source.name,
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    )


def build_files(source: Path = SOURCE) -> dict[str, str]:
    """Every output file, by path relative to the output directory, as text."""
    payload, results = load_results(source)
    if not payload.get("complete"):
        raise ExportError(f"{source.name} is not a complete run")
    as_of = date.fromisoformat(payload["run_date"])
    recorded = {score.case_id: (score, judgment) for score, judgment in results}
    policies = load_policies()

    cases: list[tuple[str, EvalCase]] = []
    for path in sorted((EVALS_DIR / "cases").rglob("*.yaml")):
        cases.append((path.parent.name, load_case(path)))
    unknown = {s for s, _ in cases} - set(SET_ORDER)
    if unknown:
        raise ExportError(f"case sets not in SET_ORDER: {sorted(unknown)}")
    cases.sort(key=lambda sc: (SET_ORDER.index(sc[0]), sc[1].id))
    stray = set(recorded) - {c.id for _, c in cases}
    if stray:
        raise ExportError(f"the run has cases that are not case files: {sorted(stray)}")

    files = {"schema.json": schema_text()}
    rows = []
    for case_set, case in cases:
        exported = case_file(case, case_set, policies[case.policy], as_of, recorded.get(case.id))
        files[f"cases/{case.id}.json"] = to_text(exported)
        rows.append(
            m.CaseRow(
                id=case.id,
                set=case_set,
                policy=case.policy,
                variant=case.variant,
                procedure=case.request.procedure,
                expected_overall=exported.expected_overall,
                scored=exported.scored,
                predicted_overall=exported.result.predicted_overall if exported.result else None,
                overall_correct=exported.result.overall_correct if exported.result else None,
                criteria=len(exported.criteria),
                labeled_criteria=len(exported.expected),
                tags=exported.tags,
            )
        )
    for policy_id in sorted(policies):
        files[f"policies/{policy_id}.json"] = to_text(policy_file(policies[policy_id]))

    summary = {key: payload["summary"][key] for key in m.RunSummary.model_fields}
    index = m.Index(
        schema_version=m.SCHEMA_VERSION,  # type: ignore[arg-type]
        run=_run_info(payload, source),
        summary=m.RunSummary.model_validate(summary),
        banners=m.BANNERS,
        policies=[
            m.PolicyRef(id=p.id, title=p.title, document=p.document)
            for _, p in sorted(policies.items())
        ],
        cases=rows,
    )
    files["index.json"] = to_text(index)
    return files


def write_files(files: dict[str, str], out: Path) -> None:
    """Write the files, and remove case and policy files an earlier export left behind."""
    for folder in ("cases", "policies"):
        for old in (out / folder).glob("*.json"):
            if f"{folder}/{old.name}" not in files:
                old.unlink()
    for name, text in files.items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
