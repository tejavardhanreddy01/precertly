"""Deterministic rules: what code can settle about a criterion from structured data.

A rule returns one of three things:

- decided: code's result is final. Code decides toward stricter outcomes freely, and
  toward "met" or "not applicable" only when the structured data fully determines it.
- partial: code fixes a computed fact, and the model judges only what the policy text
  leaves open (an exception clause, an alternative, a qualifier such as "sustained").
  Every partial return names a branch in PARTIAL_BRANCHES, which fixes the outcomes the
  computed fact alone may support; the rest need a verified chart citation.
- absent: the structured data the rule needs is not in the chart; the model judges alone.

Missing data is never a reason for not_met. A chart can lack coded care given elsewhere,
so "no coded procedure" is absent, not "none happened".

Limits and intervals are measured to request.service_date (evals/GUIDELINES.md, rule 8).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from typing import Literal

from dateutil.relativedelta import relativedelta
from pydantic import BaseModel, Field

from precertly.judgment.models import FACET_FIELDS, PriorAuthRequest
from precertly.judgment.structured import (
    FACET_INJECTION,
    FACET_RFA,
    REGIONS,
    ProcedureFact,
    ScoreFact,
    StructuredChart,
)
from precertly.policies import Criterion

RULES_VERSION = "2"

# A final outcome as the citation requirements see it: not_applicable is "met" because the
# criterion's condition does not hold.
FinalOutcome = Literal["met", "not_met", "not_applicable"]


class PartialBranch(BaseModel):
    """One partial return site: what the model is left to judge, and which outcomes may
    rest on the computed fact [C1] alone.

    The principle: an outcome that rests on something being documented needs at least one
    verified chart citation; an outcome that rests on nothing further being found may cite
    [C1] alone. insufficient never needs a citation. The prompt and the verifier both read
    this table, so what the model is told and what is enforced cannot drift apart.
    """

    row: str  # the row in the reviewed design table
    criterion: str
    open_question: str
    alone: frozenset[FinalOutcome] = frozenset()
    # [C1] itself establishes that the criterion's condition holds, so condition_holds =
    # false contradicts it and invalidates the verdict.
    condition_fixed: bool = False
    note: str | None = None  # an extra instruction for the model, shown in the prompt

    def needs_chart(self, outcome: FinalOutcome) -> bool:
        return outcome not in self.alone


PARTIAL_BRANCHES: dict[str, PartialBranch] = {
    "second-diagnostic/one-session-short": PartialBranch(
        row="1a",
        criterion="second-diagnostic-2-weeks-after-first",
        open_question="Is a clinical exception to the two-week interval clearly documented?",
        alone=frozenset({"not_met"}),
    ),
    "second-diagnostic/several-sessions": PartialBranch(
        row="1b",
        criterion="second-diagnostic-2-weeks-after-first",
        open_question="Which coded session was the initial diagnostic procedure, and the "
        "interval from it.",
    ),
    "diagnostic-sessions/within-limit": PartialBranch(
        row="2a",
        criterion="diagnostic-sessions-max-4",
        open_question="Are there diagnostic sessions in the window that are not coded here?",
        alone=frozenset({"met"}),
    ),
    "diagnostic-sessions/at-or-over-limit": PartialBranch(
        row="2b",
        criterion="diagnostic-sessions-max-4",
        open_question="Were some coded sessions therapeutic, and is a session on the "
        "window's first day inside it?",
    ),
    "rfa-sessions/within-limit": PartialBranch(
        row="3a",
        criterion="rfa-sessions-max-2",
        open_question="Are there ablation sessions in the window that are not coded here?",
        alone=frozenset({"met"}),
    ),
    "rfa-sessions/boundary-tips": PartialBranch(
        row="3b",
        criterion="rfa-sessions-max-2",
        open_question="Is the session on the window's first day inside the window?",
    ),
    "first-diagnostic-relief/at-or-above": PartialBranch(
        row="4",
        criterion="first-diagnostic-80pct-relief",
        open_question="Was the response consistent, and did it last as long as the agent "
        "used would predict?",
    ),
    "two-blocks-relief/two-passing": PartialBranch(
        row="5a",
        criterion="two-mbbs-80pct-sustained-relief",
        open_question="Was the relief consistent and sustained, for as long as the agent "
        "used would predict?",
    ),
    "two-blocks-relief/more-than-two": PartialBranch(
        row="5b",
        criterion="two-mbbs-80pct-sustained-relief",
        open_question="Which two coded blocks are the diagnostic blocks that count?",
    ),
    "prior-rfa/pain-half-open": PartialBranch(
        row="6",
        criterion="prior-rfa-50pct-6-months",
        open_question="Is the pain improvement documented outside the coded scores, or is "
        "improvement in previously painful movements and ADLs documented? Silence on ADLs "
        "is insufficient, not failure.",
    ),
    "same-day-blocks/listed": PartialBranch(
        row="7",
        criterion="same-day-blocks-justified",
        open_question="Is the medical necessity of each same-day block clearly documented?",
        alone=frozenset({"not_met"}),
        condition_fixed=True,
        note="For met, the chart must document the medical necessity of each block listed "
        "in the request.",
    ),
    "ia-diagnostic/intraarticular": PartialBranch(
        row="8",
        criterion="ia-diagnostic-only-if-mbb-not-possible",
        open_question="Is an anatomic restriction to medial branch blocks, or an indication "
        "for therapeutic intraarticular injection, documented?",
        alone=frozenset({"not_met"}),
        condition_fixed=True,
    ),
    "diagnostics-repeated/two-years-or-more": PartialBranch(
        row="9",
        criterion="diagnostics-repeated-after-2-years",
        open_question="Is there a question about the source of the recurrent pain, and "
        "were diagnostic procedures repeated?",
    ),
    "no-diagnostic-at-rfa-level/coded-rfa": PartialBranch(
        row="10",
        criterion="no-diagnostic-at-level-of-successful-rfa",
        open_question="Was the prior ablation successful, and is it at the level of the "
        "requested block?",
    ),
}


class RuleResult(BaseModel):
    kind: Literal["decided", "partial", "absent"]
    outcome: Literal["met", "not_met", "insufficient"] | None = None  # decided only
    applicable: bool = True  # False: decided that the criterion's condition does not hold
    branch: str | None = None  # partial only: the key in PARTIAL_BRANCHES
    statement: str = Field(description="The computed fact, or why the rule could not run")
    inputs: list[str] = Field(
        default_factory=list, description="Resources and request fields the rule read"
    )


def _decided(
    outcome: Literal["met", "not_met", "insufficient"], statement: str, inputs: list[str]
) -> RuleResult:
    return RuleResult(kind="decided", outcome=outcome, statement=statement, inputs=inputs)


def _not_applicable(statement: str, inputs: list[str]) -> RuleResult:
    return RuleResult(
        kind="decided", outcome="met", applicable=False, statement=statement, inputs=inputs
    )


def _partial(branch: str, statement: str, inputs: list[str]) -> RuleResult:
    if branch not in PARTIAL_BRANCHES:
        raise KeyError(f"partial branch {branch!r} is not in PARTIAL_BRANCHES")
    return RuleResult(kind="partial", branch=branch, statement=statement, inputs=inputs)


def _absent(statement: str) -> RuleResult:
    return RuleResult(kind="absent", statement=statement)


# --- sessions --------------------------------------------------------------------------


def _sessions(procedures: list[ProcedureFact], before: date) -> dict[date, list[ProcedureFact]]:
    """Procedures grouped into sessions. The LCD defines a session as everything done on
    one day, so a primary code and its add-on code are one session, not two."""
    by_day: dict[date, list[ProcedureFact]] = {}
    for procedure in procedures:
        if procedure.start.day < before:
            by_day.setdefault(procedure.start.day, []).append(procedure)
    return dict(sorted(by_day.items()))


def _required[T](request: PriorAuthRequest, field: str, value: T | None) -> T:
    """A facet field the rule reads. Every rule here is a facet rule, and an unset field
    is a malformed request: raise rather than read it as empty and decide."""
    if value is None or value == []:
        raise ValueError(f"facet rule needs request.{field} (codes {request.codes})")
    return value


def _region(request: PriorAuthRequest) -> str | None:
    return REGIONS.get(_required(request, "region", request.region).strip().lower())


def _injections(chart: StructuredChart, request: PriorAuthRequest):
    family = _region(request)
    if family is None:
        return None
    return _sessions(chart.with_codes(FACET_INJECTION[family]), request.service_date)


def _ablations(chart: StructuredChart, request: PriorAuthRequest):
    family = _region(request)
    if family is None:
        return None
    return _sessions(chart.with_codes(FACET_RFA[family]), request.service_date)


def _refs(sessions: dict[date, list[ProcedureFact]]) -> list[str]:
    return [p.ref for procedures in sessions.values() for p in procedures]


# --- (a) conditions --------------------------------------------------------------------


def same_day_blocks(
    chart: StructuredChart, request: PriorAuthRequest, criterion: Criterion
) -> RuleResult:
    blocks = request.same_day_blocks
    if blocks is None:  # [] is "none listed"; None is a malformed request
        raise ValueError(f"facet rule needs request.same_day_blocks (codes {request.codes})")
    if not blocks:
        return _not_applicable(
            "The request lists no other blocks for the day of the procedure.",
            ["request.same_day_blocks"],
        )
    listed = ", ".join(blocks)
    return _partial(
        "same-day-blocks/listed",
        f"The request lists other blocks for the same day: {listed}.",
        ["request.same_day_blocks"],
    )


def intraarticular_only_if_needed(
    chart: StructuredChart, request: PriorAuthRequest, criterion: Criterion
) -> RuleResult:
    technique = _required(request, "technique", request.technique)
    if technique != "intraarticular_injection":
        return _not_applicable(
            f"The requested technique is {technique}, not an intraarticular injection.",
            ["request.technique"],
        )
    return _partial(
        "ia-diagnostic/intraarticular",
        "The requested technique is an intraarticular injection.",
        ["request.technique"],
    )


def diagnostics_repeated_after_two_years(
    chart: StructuredChart, request: PriorAuthRequest, criterion: Criterion
) -> RuleResult:
    ablations = _ablations(chart, request)
    if not ablations:
        return _absent("No coded radiofrequency ablation in the requested region.")
    last = max(ablations)
    years = criterion.thresholds.get("years_since_last_rfa", 2)
    inputs = [p.ref for p in ablations[last]] + ["request.service_date"]
    # A later ablation that is not coded here could only make the gap shorter, so a coded
    # gap under two years settles that the condition does not hold.
    if request.service_date < last + relativedelta(years=years):
        return _not_applicable(
            f"The last coded ablation in the region was on {last}, less than {years} years "
            f"before the planned service date {request.service_date}.",
            inputs,
        )
    return _partial(
        "diagnostics-repeated/two-years-or-more",
        f"The last coded ablation in the region was on {last}, {years} years or more before "
        f"the planned service date {request.service_date}. Whether the source of the "
        "recurrent pain is in question, and whether diagnostics were repeated, is not computed.",
        inputs,
    )


def no_diagnostic_at_level_of_successful_rfa(
    chart: StructuredChart, request: PriorAuthRequest, criterion: Criterion
) -> RuleResult:
    # Never decided: "successful" and "same level" are judgments, and no coded ablation is
    # not proof that there was none.
    ablations = _ablations(chart, request)
    if not ablations:
        return _absent("No coded radiofrequency ablation in the requested region.")
    dates = ", ".join(str(day) for day in ablations)
    return _partial(
        "no-diagnostic-at-rfa-level/coded-rfa",
        f"Coded ablation in the requested region on: {dates}. Whether it was successful and "
        "at the same level as the requested block is not computed.",
        _refs(ablations),
    )


# --- (b) windows, counts and intervals -------------------------------------------------


def _window_count(
    sessions: dict[date, list[ProcedureFact]], request: PriorAuthRequest, months: int
) -> tuple[int, bool, list[str]]:
    """Sessions strictly inside the rolling window, whether one falls exactly on its
    opening day, and the procedures counted. The policy does not say whether the opening
    day is inside the window, so it is reported separately."""
    opens = request.service_date - relativedelta(months=months)
    inside = {day: procs for day, procs in sessions.items() if day > opens}
    on_boundary = opens in sessions
    counted = _refs(inside) + ([p.ref for p in sessions[opens]] if on_boundary else [])
    return len(inside), on_boundary, counted


def rfa_sessions_max(
    chart: StructuredChart, request: PriorAuthRequest, criterion: Criterion
) -> RuleResult:
    ablations = _ablations(chart, request)
    if not ablations:
        return _absent("No coded radiofrequency ablation in the requested region.")
    limit = criterion.thresholds["max_sessions"]
    months = criterion.thresholds["window_months"]
    inside, on_boundary, counted = _window_count(ablations, request, months)
    inputs = [*counted, "request.service_date"]
    boundary = (
        " One more coded session falls exactly on the first day of that window."
        if on_boundary
        else ""
    )
    statement = (
        f"Coded ablation sessions in the requested region in the {months} months before the "
        f"planned service date {request.service_date}: {inside}.{boundary} "
        f"With the requested session that is {inside + 1} against a limit of {limit}."
    )
    # Over the limit even without the boundary session: settled. If the boundary session
    # is what tips it, the policy text does not settle it.
    if inside + 1 > limit:
        return _decided("not_met", statement, inputs)
    # Within the limit, met may rest on nothing further being found, unless the session
    # on the window's first day would tip it: the policy text does not settle that day.
    tips = on_boundary and inside + 2 > limit
    return _partial(
        "rfa-sessions/boundary-tips" if tips else "rfa-sessions/within-limit",
        statement + " Sessions not coded in this chart are not counted.",
        inputs,
    )


def diagnostic_sessions_max(
    chart: StructuredChart, request: PriorAuthRequest, criterion: Criterion
) -> RuleResult:
    # Never decided: the injection codes are the same for diagnostic and therapeutic
    # injections, so a coded count can include sessions this limit does not apply to.
    injections = _injections(chart, request)
    if not injections:
        return _absent("No coded facet injection in the requested region.")
    limit = criterion.thresholds["max_sessions"]
    months = criterion.thresholds["window_months"]
    inside, on_boundary, counted = _window_count(injections, request, months)
    boundary = (
        " One more coded session falls exactly on the first day of that window."
        if on_boundary
        else ""
    )
    # met may rest on the count alone only if it stays within the limit even with the
    # session on the window's first day; otherwise met needs the chart (for example, a
    # session documented as therapeutic).
    within = inside + 1 + on_boundary <= limit
    return _partial(
        "diagnostic-sessions/within-limit" if within else "diagnostic-sessions/at-or-over-limit",
        f"Coded facet injection sessions in the requested region in the {months} months "
        f"before the planned service date {request.service_date}: {inside}.{boundary} "
        f"The limit is {limit} diagnostic sessions including the requested one. The codes do "
        "not say whether a session was diagnostic or therapeutic.",
        [*counted, "request.service_date"],
    )


def second_diagnostic_interval(
    chart: StructuredChart, request: PriorAuthRequest, criterion: Criterion
) -> RuleResult:
    injections = _injections(chart, request)
    if not injections:
        return _absent("No coded facet injection in the requested region.")
    weeks = criterion.thresholds["min_interval_weeks"]
    inputs = [*_refs(injections), "request.service_date"]
    if len(injections) > 1:
        dates = ", ".join(str(day) for day in injections)
        return _partial(
            "second-diagnostic/several-sessions",
            f"More than one coded facet injection session before the service date ({dates}); "
            "which one is the initial diagnostic procedure is not computed.",
            inputs,
        )
    (first,) = injections
    days = (request.service_date - first).days  # calendar dates, not timestamps
    statement = (
        f"The planned service date {request.service_date} is {days} days after the coded "
        f"facet injection on {first}; the minimum is {weeks * 7} days."
    )
    if days >= weeks * 7:
        return _decided("met", statement, inputs)
    return _partial(
        "second-diagnostic/one-session-short",
        statement + " Only a clearly documented clinical exception can satisfy the criterion.",
        inputs,
    )


# --- (c) relief and scales -------------------------------------------------------------


class _Block(BaseModel):
    status: Literal["pass", "fail", "not_comparable", "absent"]
    statement: str
    inputs: list[str]


def _around(
    chart: StructuredChart, procedure: ProcedureFact
) -> tuple[list[ScoreFact], list[ScoreFact]] | None:
    """Coded pain scores before and after a procedure in its own encounter, by time of day.
    An observation without a recognized pain code is not a pain score to code: it is left
    out, so the rule sees a missing score and returns absent instead of guessing.
    None when the order cannot be established: no encounter, a date-only timestamp on
    either side, or a score timed exactly at the procedure."""
    if procedure.encounter is None or procedure.start.time is None or procedure.end.time is None:
        return None
    scores = [s for s in chart.scores if s.encounter == procedure.encounter and s.is_pain]
    before, after = [], []
    for score in scores:
        if score.when.time is None:
            return None
        if score.when.time < procedure.start.time:
            before.append(score)
        elif score.when.time > procedure.end.time:
            after.append(score)
        else:
            return None
    return sorted(before, key=lambda s: s.when.time), sorted(after, key=lambda s: s.when.time)


def _relief(chart: StructuredChart, procedure: ProcedureFact, minimum_pct: int) -> _Block:
    around = _around(chart, procedure)
    if around is None:
        return _Block(
            status="absent",
            statement=f"{procedure.ref}: scores cannot be ordered against the procedure by time.",
            inputs=[procedure.ref],
        )
    before, after = around
    if not before or not after:
        return _Block(
            status="absent",
            statement=f"{procedure.ref}: no coded score both before and after the procedure.",
            inputs=[procedure.ref],
        )
    pre = before[-1]
    inputs = [procedure.ref, pre.ref, *(s.ref for s in after)]
    comparable = [s for s in after if s.pain_scale == pre.pain_scale]
    if not comparable or pre.value <= 0:
        return _Block(
            status="not_comparable",
            statement=(
                f"{procedure.ref}: the score before the procedure ({pre.value:g} {pre.unit}) and "
                "the scores after it are on different scales, so relief cannot be calculated."
            ),
            inputs=inputs,
        )
    # Integer arithmetic for "at least N%": 100 * (pre - post) >= N * pre.
    passing = [s for s in comparable if 100 * (pre.value - s.value) >= minimum_pct * pre.value]
    shown = passing[0] if passing else comparable[0]
    pct = round(100 * (pre.value - shown.value) / pre.value)
    calculation = f"({pre.value:g} - {shown.value:g}) / {pre.value:g} = {pct}%"
    if passing:
        return _Block(
            status="pass",
            statement=f"{procedure.ref}: relief {calculation}, at or above {minimum_pct}%.",
            inputs=inputs,
        )
    return _Block(
        status="fail",
        statement=(
            f"{procedure.ref}: relief {calculation}, below {minimum_pct}%; no coded score "
            "after the procedure reaches it."
        ),
        inputs=inputs,
    )


def first_diagnostic_relief(
    chart: StructuredChart, request: PriorAuthRequest, criterion: Criterion
) -> RuleResult:
    injections = _injections(chart, request)
    if not injections:
        return _absent("No coded facet injection in the requested region.")
    first = injections[min(injections)]
    block = _relief(chart, first[0], criterion.thresholds["min_relief_pct"])
    if block.status == "absent":
        return _absent(block.statement)
    if block.status == "fail":
        return _decided("not_met", block.statement, block.inputs)
    if block.status == "not_comparable":
        return _decided("insufficient", block.statement, block.inputs)
    return _partial(
        "first-diagnostic-relief/at-or-above",
        block.statement + " Whether the response was consistent and lasted as long as the "
        "agent used would predict is not computed.",
        block.inputs,
    )


def two_blocks_relief(
    chart: StructuredChart, request: PriorAuthRequest, criterion: Criterion
) -> RuleResult:
    injections = _injections(chart, request)
    if not injections:
        return _absent("No coded facet injection in the requested region.")
    minimum = criterion.thresholds["min_relief_pct"]
    blocks = [_relief(chart, procedures[0], minimum) for procedures in injections.values()]
    statement = " ".join(b.statement for b in blocks)
    inputs = [ref for b in blocks for ref in b.inputs]
    if len(blocks) < 2:
        return _absent(f"Only {len(blocks)} coded facet injection session in the region.")
    if len(blocks) > 2:
        return _partial(
            "two-blocks-relief/more-than-two",
            statement + " More than two coded sessions; which two count is not computed.",
            inputs,
        )
    statuses = {b.status for b in blocks}
    # With exactly two blocks, each must qualify, so one failure is enough, whatever the
    # other block shows.
    if "fail" in statuses:
        return _decided("not_met", statement, inputs)
    if "not_comparable" in statuses:
        return _decided("insufficient", statement, inputs)
    if "absent" in statuses:
        return _absent(statement)
    return _partial(
        "two-blocks-relief/two-passing",
        statement + " Whether the relief was consistent, sustained and lasted as long as the "
        "agent used would predict is not computed.",
        inputs,
    )


def pain_scale_after_each_diagnostic(
    chart: StructuredChart, request: PriorAuthRequest, criterion: Criterion
) -> RuleResult:
    injections = _injections(chart, request)
    if not injections:
        # No prior block is never "met": there is nothing yet to have assessed.
        return _absent("No coded facet injection in the requested region.")
    first_day = min(injections)
    earlier = [s for s in chart.scores if s.is_pain and s.when.day < first_day]
    if not earlier:
        return _absent("No coded pain score before the first coded facet injection.")
    baseline = max(earlier, key=lambda s: (s.when.day, s.ref))
    inputs, violations, unscored = [baseline.ref], [], []
    for procedures in injections.values():
        procedure = procedures[0]
        around = _around(chart, procedure)
        after = around[1] if around else []
        inputs += [procedure.ref, *(s.ref for s in after)]
        if any(s.pain_scale == baseline.pain_scale for s in after):
            continue  # assessed on the baseline scale, whatever else was also recorded
        (violations if after else unscored).append(procedure.ref)
    scale = f"{baseline.value:g} {baseline.unit} at baseline ({baseline.ref})"
    # A documented assessment on the wrong scale violates "after each" on its own, so it
    # outranks a block whose score is simply missing.
    if not violations and unscored:
        return _absent(f"{', '.join(unscored)}: no coded, timed score after the procedure.")
    if violations:
        return _decided(
            "not_met",
            f"After {', '.join(violations)} the only coded score is on a different scale from "
            f"the baseline score, {scale}.",
            inputs,
        )
    return _decided(
        "met",
        f"Every coded facet injection has a coded score after it on the baseline scale, {scale}.",
        inputs,
    )


# --- levels, from free text, failing closed --------------------------------------------

# This is the only place code reads free text to decide "met". bodySite is free text in
# practice, and the repeat-RFA criterion is about the same anatomic site, so without it
# code could never confirm that criterion. It fails closed: the whole string must match a
# small grammar, and anything else is "unclear", which sends the criterion to the model.
_LEVEL = r"[CTL]\d{1,2}\s*[-/]\s*[CTLS]?\d{1,2}"
_SITE = re.compile(
    rf"\s*(?:(?P<region>lumbar|cervical|thoracic)\s+)?(?:facet\s+joints?\s+)?"
    rf"(?P<levels>{_LEVEL}(?:\s*(?:,\s*and|,|&|and)\s*{_LEVEL})*)"
    rf"(?:\s*,?\s*(?P<side>bilateral|left|right))?\s*",
    re.IGNORECASE,
)
_JUNCTIONS = {("C", 7, "T", 1): "C7-T1", ("T", 12, "L", 1): "T12-L1", ("L", 5, "S", 1): "L5-S1"}
_LEVEL_REGION = {"C": "cervical", "T": "thoracic", "L": "lumbar"}

# Coded body-site levels ("system|code" -> level). Empty: no code system for facet levels
# is in use here yet, so any coded body site is unclear.
LEVEL_CODES: dict[str, str] = {}


def normalize_level(token: str) -> str | None:
    """'L4-5', 'L4-L5' and 'L4/5' -> 'L4-5'. One motion segment only; None otherwise."""
    match = re.fullmatch(
        r"\s*([CTL])(\d{1,2})\s*[-/]\s*([CTLS])?(\d{1,2})\s*", token, re.IGNORECASE
    )
    if match is None:
        return None
    upper, lower = match.group(1).upper(), (match.group(3) or match.group(1)).upper()
    first, second = int(match.group(2)), int(match.group(4))
    if upper == lower:
        return f"{upper}{first}-{second}" if second == first + 1 else None
    return _JUNCTIONS.get((upper, first, lower, second))


class Site(BaseModel):
    levels: frozenset[str]
    region: str  # of the levels themselves
    side: str | None


def parse_site(text: str | None) -> Site | None:
    """Levels stated by a body-site text, or None if the text says anything else."""
    match = _SITE.fullmatch(text or "")
    if match is None:
        return None
    tokens = re.findall(_LEVEL, match.group("levels"), re.IGNORECASE)
    parsed = [normalize_level(token) for token in tokens]
    levels = [level for level in parsed if level is not None]
    if not levels or len(levels) != len(parsed):
        return None
    regions = {_LEVEL_REGION[level[0]] for level in levels}
    stated = (match.group("region") or "").lower()
    if len(regions) != 1 or (stated and REGIONS[stated] != REGIONS[next(iter(regions))]):
        return None
    side = match.group("side")
    return Site(levels=frozenset(levels), region=regions.pop(), side=side.lower() if side else None)


def same_site(
    procedure: ProcedureFact, request: PriorAuthRequest
) -> Literal["match", "mismatch", "unclear"]:
    site = parse_site(procedure.body_site_text)
    if procedure.body_site_codes:
        mapped = [LEVEL_CODES.get(code) for code in procedure.body_site_codes]
        coded = frozenset(level for level in mapped if level is not None)
        if len(coded) != len(set(mapped)) or (site is not None and site.levels != coded):
            return "unclear"
        levels, region = coded, _LEVEL_REGION[next(iter(coded))[0]]
    elif site is not None:
        levels, region = site.levels, site.region
    else:
        return "unclear"
    requested = [normalize_level(level) for level in _required(request, "levels", request.levels)]
    family = _region(request)
    if None in requested or family is None:
        return "unclear"
    # The request has no laterality field, so laterality cannot differ.
    if REGIONS[region] != family or levels != frozenset(requested):
        return "mismatch"
    return "match"


def prior_rfa_improvement(
    chart: StructuredChart, request: PriorAuthRequest, criterion: Criterion
) -> RuleResult:
    """The pain half of the criterion only. The policy text also allows improved movement
    and daily activities, which code cannot check, so this rule never decides not_met."""
    ablations = _ablations(chart, request)
    if not ablations:
        return _absent("No coded radiofrequency ablation in the requested region.")
    day = max(ablations)
    ablation = ablations[day][0]
    months = criterion.thresholds["min_duration_months"]
    minimum = criterion.thresholds["min_pain_improvement_pct"]

    later = sorted(
        (s for s in chart.scores if s.is_pain and day < s.when.day < request.service_date),
        key=lambda s: (s.when.day, s.ref),
    )
    if not later:
        return _absent(f"No coded pain score after the ablation on {day}.")
    inputs = [ablation.ref, "request.levels", "request.region"]

    around = _around(chart, ablation)
    same_day_before = around[0] if around else []
    earlier = [s for s in chart.scores if s.is_pain and s.when.day < day]
    baseline = (same_day_before or sorted(earlier, key=lambda s: (s.when.day, s.ref)) or [None])[-1]
    if baseline is None or not baseline.is_pain or baseline.value <= 0:
        return _partial(
            "prior-rfa/pain-half-open",
            f"No coded pain score before the ablation on {day} to compare with.",
            inputs,
        )

    # The window runs to the first score at least `months` calendar months after the
    # ablation. Later scores are ignored: the pain coming back is why a repeat is requested.
    reaches = day + relativedelta(months=months)
    closing = next((s for s in later if s.when.day >= reaches), None)
    window = [s for s in later if closing is None or s.when.day <= closing.when.day]
    inputs += [baseline.ref, *(s.ref for s in window)]
    listed = ", ".join(f"{s.value:g} on {s.when.day}" for s in window)
    statement = (
        f"Ablation on {day}; pain {baseline.value:g} before it. Coded scores after it, up to "
        f"the first at least {months} months later: {listed}."
    )
    problems = []
    if closing is None:
        problems.append(f"no coded score {months} months or more after the ablation")
    if any(s.pain_scale != baseline.pain_scale for s in window):
        problems.append("a score in the window is on a different scale")
    # Integer arithmetic for "at least N%": 100 * (baseline - score) >= N * baseline.
    elif any(100 * (baseline.value - s.value) < minimum * baseline.value for s in window):
        problems.append(f"a score in the window shows less than {minimum}% improvement")
    # Two scores is our own conservative floor for calling the improvement "consistent"
    # from coded data alone. It is a routing choice, not a threshold the policy states.
    if len(window) < 2:
        problems.append("fewer than 2 coded scores in the window")
    site = same_site(ablation, request)
    if site != "match":
        problems.append(f"the ablation's levels versus the requested levels: {site}")
    else:
        statement += f' Ablation site as recorded: "{ablation.body_site_text}".'
    if problems:
        return _partial(
            "prior-rfa/pain-half-open",
            statement + " The pain half is not established (" + "; ".join(problems) + "). "
            "The alternative of improved movement and daily activities is not computed.",
            inputs,
        )
    return _decided(
        "met",
        statement + f" Every one shows at least {minimum}% improvement on the same scale.",
        inputs,
    )


def disability_scale_at_baseline(
    chart: StructuredChart, request: PriorAuthRequest, criterion: Criterion
) -> RuleResult:
    """A coded disability score in the baseline window. Never not_met: a missing coded
    score may simply be recorded in a note."""
    injections = _injections(chart, request) or {}
    ablations = _ablations(chart, request) or {}
    if ablations:
        # A repeat request: baseline is the current episode, after the last ablation and
        # before the next facet procedure, if there has been one.
        opens = max(ablations)
        after = [d for d in (*injections, *ablations) if d > opens]
        closes = min(after) if after else request.service_date
    else:
        opens = date.min
        closes = min(injections) if injections else request.service_date
    found = [s for s in chart.scores if s.is_disability and opens < s.when.day < closes]
    if not found:
        return _absent("No coded disability score in the baseline window.")
    score = max(found, key=lambda s: (s.when.day, s.ref))
    return _decided(
        "met",
        f"Coded disability score {score.value:g} {score.unit} on {score.when.day}, "
        f"before {closes}.",
        [score.ref],
    )


Rule = Callable[[StructuredChart, PriorAuthRequest, Criterion], RuleResult]

RULES: dict[str, Rule] = {
    "same-day-blocks-justified": same_day_blocks,
    "ia-diagnostic-only-if-mbb-not-possible": intraarticular_only_if_needed,
    "diagnostics-repeated-after-2-years": diagnostics_repeated_after_two_years,
    "no-diagnostic-at-level-of-successful-rfa": no_diagnostic_at_level_of_successful_rfa,
    "rfa-sessions-max-2": rfa_sessions_max,
    "diagnostic-sessions-max-4": diagnostic_sessions_max,
    "second-diagnostic-2-weeks-after-first": second_diagnostic_interval,
    "first-diagnostic-80pct-relief": first_diagnostic_relief,
    "two-mbbs-80pct-sustained-relief": two_blocks_relief,
    "pain-scale-after-each-diagnostic": pain_scale_after_each_diagnostic,
    "prior-rfa-50pct-6-months": prior_rfa_improvement,
    "disability-scale-at-baseline": disability_scale_at_baseline,
}


def evaluate(
    criterion: Criterion, chart: StructuredChart, request: PriorAuthRequest
) -> RuleResult | None:
    """The rule's result for a criterion, or None if no rule covers it."""
    rule = RULES.get(criterion.id)
    if rule is None:
        return None
    # Every rule is a facet rule (key this on the policy if that changes). Check the whole
    # facet request up front, so a malformed one fails even for a rule that reads none of
    # these fields.
    missing = [f for f in FACET_FIELDS if getattr(request, f) is None]
    if missing or not request.levels:
        raise ValueError(
            f"{criterion.id}: facet request is missing {', '.join(missing) or 'levels'}"
        )
    result = rule(chart, request, criterion)
    if result.branch is not None and PARTIAL_BRANCHES[result.branch].criterion != criterion.id:
        raise ValueError(f"{criterion.id}: partial branch {result.branch!r} belongs elsewhere")
    return result
