"""One test per branch of each deterministic rule, on small hand-made charts."""

from datetime import date, timedelta

import pytest

from precertly.evals import load_cases
from precertly.evals.rules_check import check_cases
from precertly.judgment import rules
from precertly.judgment.models import PriorAuthRequest
from precertly.judgment.rules import evaluate, normalize_level, parse_site, same_site
from precertly.judgment.structured import StructuredChart
from precertly.policies import load_policies

FACET = load_policies()["facet-joint-interventions"]
SERVICE = date(2026, 10, 19)
NRS = {"system": "http://loinc.org", "code": "72514-3"}
ODI = {"system": "http://loinc.org", "code": "97909-6"}
VAS = {"system": "http://loinc.org", "code": "38214-3"}
SITE = "Lumbar facet joints L4-5 and L5-S1, bilateral"


def request(**overrides):
    fields = {
        "date": date(2026, 10, 5),
        "service_date": SERVICE,
        "procedure": "x",
        "technique": "radiofrequency_ablation",
        "codes": ["64635"],
        "region": "lumbar",
        "levels": ["L4-5", "L5-S1"],
        "same_day_blocks": [],
    }
    return PriorAuthRequest(**(fields | overrides))


def procedure(ref, day, cpt, *, clock="09:30", site=SITE, site_coding=None, encounter=None):
    body_site = {"text": site} if site is not None else {}
    if site_coding:
        body_site["coding"] = site_coding
    when = f"{day.isoformat()}T{clock}:00-04:00" if clock else day.isoformat()
    return {
        "resourceType": "Procedure",
        "id": ref,
        "status": "completed",
        "code": {"coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": cpt}]},
        "performedDateTime": when,
        "encounter": {"reference": f"Encounter/{encounter or ref}"},
        "bodySite": [body_site],
    }


def score(ref, day, value, *, clock=None, coding=NRS, unit="{score}", encounter=None):
    when = f"{day.isoformat()}T{clock}:00-04:00" if clock else day.isoformat()
    resource = {
        "resourceType": "Observation",
        "id": ref,
        "category": [{"coding": [{"code": "survey"}]}],
        "code": {"coding": [coding]} if coding else {"text": "Pain severity, VAS 0-100 mm"},
        "valueQuantity": {"value": value, "unit": unit},
        "effectiveDateTime": when,
    }
    if encounter:
        resource["encounter"] = {"reference": f"Encounter/{encounter}"}
    return resource


def block(name, day, pre=7, post=1, **post_options):
    """A coded facet injection with a score before and, unless post is None, one after."""
    resources = [
        procedure(name, day, "64493"),
        score(f"{name}-pre", day, pre, clock="09:00", encounter=name),
    ]
    if post is not None:
        resources.append(
            score(f"{name}-post", day, post, clock="10:00", encounter=name, **post_options)
        )
    return resources


def run(criterion_id, resources, **request_overrides):
    chart = StructuredChart.from_bundle({"entry": [{"resource": r} for r in resources]})
    return evaluate(FACET.criterion(criterion_id), chart, request(**request_overrides))


def outcome(result):
    if result.kind != "decided":
        return result.kind
    return result.outcome if result.applicable else "not_applicable"


def days_before(days):
    return SERVICE - timedelta(days=days)


BASELINE = score("baseline", date(2026, 8, 1), 7)


# --- conditions ------------------------------------------------------------------------


def test_same_day_blocks():
    assert outcome(run("same-day-blocks-justified", [])) == "not_applicable"
    listed = run("same-day-blocks-justified", [], same_day_blocks=["epidural injection"])
    assert listed.kind == "partial" and "epidural injection" in listed.statement
    assert listed.inputs == ["request.same_day_blocks"]


def test_intraarticular_condition_comes_from_the_structured_technique():
    criterion = "ia-diagnostic-only-if-mbb-not-possible"
    assert outcome(run(criterion, [], technique="medial_branch_block")) == "not_applicable"
    assert outcome(run(criterion, [], technique="intraarticular_injection")) == "partial"


def test_diagnostics_repeated_condition_at_the_two_year_boundary():
    criterion = "diagnostics-repeated-after-2-years"
    two_years_ago = date(2024, 10, 19)
    assert outcome(run(criterion, [])) == "absent"
    one_day_short = [procedure("rfa", two_years_ago + timedelta(days=1), "64635")]
    assert outcome(run(criterion, one_day_short)) == "not_applicable"
    # exactly two years: "two years or more" may hold, so the model judges the rest
    exactly = run(criterion, [procedure("rfa", two_years_ago, "64635")])
    assert exactly.kind == "partial" and "2 years or more" in exactly.statement


def test_diagnostics_repeated_uses_the_last_ablation_in_the_requested_region():
    criterion = "diagnostics-repeated-after-2-years"
    old_lumbar = procedure("old", date(2023, 1, 9), "64635")
    recent_cervical = procedure("neck", date(2026, 6, 1), "64633", site="Cervical C4-5")
    assert outcome(run(criterion, [old_lumbar, recent_cervical])) == "partial"
    recent_lumbar = procedure("new", date(2026, 6, 1), "64635")
    assert outcome(run(criterion, [old_lumbar, recent_lumbar])) == "not_applicable"


def test_successful_rfa_exclusion_is_never_decided():
    criterion = "no-diagnostic-at-level-of-successful-rfa"
    assert outcome(run(criterion, [])) == "absent"
    assert outcome(run(criterion, [procedure("rfa", date(2025, 8, 4), "64635")])) == "partial"


# --- windows and counts ----------------------------------------------------------------


def test_rfa_sessions_count_distinct_days_in_the_requested_region():
    criterion = "rfa-sessions-max-2"
    assert outcome(run(criterion, [])) == "absent"

    one = [procedure("a", days_before(100), "64635")]
    assert outcome(run(criterion, one)) == "partial"  # within the limit: never "met" by code

    # primary and add-on code on one day are one session
    same_day = [
        procedure("a", days_before(100), "64635"),
        procedure("b", days_before(100), "64636"),
    ]
    result = run(criterion, same_day)
    assert result.kind == "partial" and "sessions in the requested region" in result.statement
    assert ": 1." in result.statement

    two = [procedure("a", days_before(100), "64635"), procedure("b", days_before(200), "64636")]
    over = run(criterion, two)
    assert outcome(over) == "not_met" and "3 against a limit of 2" in over.statement

    other_region = [*one, procedure("neck", days_before(50), "64633", site="Cervical C4-5")]
    assert outcome(run(criterion, other_region)) == "partial"

    outside = [procedure("a", days_before(100), "64635"), procedure("b", days_before(500), "64635")]
    assert outcome(run(criterion, outside)) == "partial"


def test_a_session_exactly_twelve_months_back_never_decides_the_limit():
    criterion = "rfa-sessions-max-2"
    opening_day = date(2025, 10, 19)  # exactly 12 months before the service date
    inside = procedure("in", days_before(100), "64635")
    # two sessions only if the opening day counts: the policy does not say, so not decided
    on_boundary = run(criterion, [inside, procedure("edge", opening_day, "64635")])
    assert on_boundary.kind == "partial" and "exactly on the first day" in on_boundary.statement
    # one day later it is plainly inside, and the limit is exceeded
    just_inside = [inside, procedure("edge", opening_day + timedelta(days=1), "64635")]
    assert outcome(run(criterion, just_inside)) == "not_met"
    # one day earlier it is plainly outside
    just_outside = [inside, procedure("edge", opening_day - timedelta(days=1), "64635")]
    assert outcome(run(criterion, just_outside)) == "partial"


def test_diagnostic_sessions_are_counted_but_never_decided():
    criterion = "diagnostic-sessions-max-4"
    assert outcome(run(criterion, [])) == "absent"
    many = [procedure(f"b{i}", days_before(30 * i), "64493") for i in range(1, 7)]
    result = run(criterion, many)
    # six coded sessions, but the codes cannot say which were diagnostic
    assert result.kind == "partial" and ": 6." in result.statement


def test_second_diagnostic_interval_in_calendar_days():
    criterion = "second-diagnostic-2-weeks-after-first"
    assert outcome(run(criterion, [])) == "absent"
    # 14 days by date, although less than 14 * 24 hours by timestamp
    exactly = [procedure("mbb1", days_before(14), "64493", clock="23:30")]
    assert outcome(run(criterion, exactly)) == "met"
    short = run(criterion, [procedure("mbb1", days_before(13), "64493")])
    assert short.kind == "partial" and "13 days" in short.statement
    two = [procedure("a", days_before(40), "64493"), procedure("b", days_before(20), "64493")]
    assert outcome(run(criterion, two)) == "partial"


# --- relief ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pre", "post", "expected"),
    [
        (5, 1, "partial"),
        (10, 2, "partial"),
        (7, 1, "partial"),
        (7, 2, "not_met"),
        (10, 3, "not_met"),
    ],
)
def test_first_diagnostic_relief_threshold(pre, post, expected):
    # 5 -> 1 and 10 -> 2 are exactly 80%, which passes; passing is partial, never "met"
    resources = block("mbb1", days_before(21), pre, post)
    assert outcome(run("first-diagnostic-80pct-relief", resources)) == expected


def test_first_diagnostic_relief_other_branches():
    criterion = "first-diagnostic-80pct-relief"
    day = days_before(21)
    assert outcome(run(criterion, [])) == "absent"
    assert outcome(run(criterion, block("mbb1", day, post=None))) == "absent"
    # the only score after the block is on another scale: relief cannot be calculated
    other_scale = block("mbb1", day, post=10, coding=VAS, unit="mm")
    assert outcome(run(criterion, other_scale)) == "insufficient"
    # 85% and 60% both recorded: one reaches the minimum, so code does not fail it
    mixed = [*block("mbb1", day, 20, 3), score("late", day, 8, clock="11:00", encounter="mbb1")]
    assert outcome(run(criterion, mixed)) == "partial"
    # date-only scores cannot be placed before or after the procedure
    untimed = [procedure("mbb1", day, "64493"), score("a", day, 7, encounter="mbb1")]
    assert outcome(run(criterion, untimed)) == "absent"
    # a score timed exactly at the procedure is neither before nor after
    equal = [*block("mbb1", day), score("same", day, 3, clock="09:30", encounter="mbb1")]
    assert outcome(run(criterion, equal)) == "absent"


def test_relief_uses_the_same_sessions_pre_procedure_score():
    # baseline was 9, but the score before this block was 5: 5 -> 1 is 80%
    resources = [score("baseline", date(2026, 8, 1), 9), *block("mbb1", days_before(21), 5, 1)]
    assert outcome(run("first-diagnostic-80pct-relief", resources)) == "partial"


def test_two_blocks_relief():
    criterion = "two-mbbs-80pct-sustained-relief"
    first, second = days_before(42), days_before(21)
    good, bad = block("mbb1", first), block("mbb2", second, 7, 2)
    other_scale = block("mbb2", second, post=10, coding=VAS, unit="mm")
    unscored = block("mbb2", second, post=None)

    assert outcome(run(criterion, [])) == "absent"
    assert outcome(run(criterion, good)) == "absent"  # one coded block: others may be uncoded
    assert outcome(run(criterion, [*good, *block("mbb2", second)])) == "partial"
    assert outcome(run(criterion, [*good, *bad])) == "not_met"
    assert outcome(run(criterion, [*good, *other_scale])) == "insufficient"
    assert outcome(run(criterion, [*good, *unscored])) == "absent"
    # one block fails on the same scale and the other is not comparable: one failure is enough
    failing_first = block("mbb1", first, 7, 3)
    assert outcome(run(criterion, [*failing_first, *other_scale])) == "not_met"
    assert outcome(run(criterion, [*failing_first, *unscored])) == "not_met"
    # three coded blocks: which two count is not for code to say
    third = block("mbb3", days_before(7), 7, 2)
    assert outcome(run(criterion, [*good, *block("mbb2", second), *third])) == "partial"


def test_an_uncoded_score_is_missing_data_not_a_different_scale():
    """Code decides nothing on an observation it cannot identify as a pain score."""
    day, earlier = days_before(21), days_before(42)
    uncoded = block("mbb2", day, post=10, coding=None, unit="mm")
    good = block("mbb1", earlier)
    assert (
        outcome(
            run(
                "first-diagnostic-80pct-relief", block("mbb1", day, post=10, coding=None, unit="mm")
            )
        )
        == "absent"
    )
    assert outcome(run("two-mbbs-80pct-sustained-relief", [*good, *uncoded])) == "absent"
    assert outcome(run("pain-scale-after-each-diagnostic", [BASELINE, *good, *uncoded])) == "absent"
    # an uncoded score beside a coded one changes nothing
    extra = score("extra", day, 55, clock="10:30", coding=None, unit="mm", encounter="mbb2")
    both_coded = [BASELINE, *good, *block("mbb2", day), extra]
    assert outcome(run("pain-scale-after-each-diagnostic", both_coded)) == "met"
    uncoded_follow_up = [*prior_rfa(), score("f", date(2026, 2, 4), 3, coding=None, unit="mm")]
    assert outcome(run("prior-rfa-50pct-6-months", uncoded_follow_up)) == "absent"


def test_same_scale_means_the_same_pain_code_whatever_the_unit():
    day = days_before(21)
    resources = [
        procedure("mbb1", day, "64493"),
        score("pre", day, 7, clock="09:00", encounter="mbb1"),
    ]
    other_unit = score("post", day, 1, clock="10:00", unit="score", encounter="mbb1")
    assert outcome(run("first-diagnostic-80pct-relief", [*resources, other_unit])) == "partial"


# --- same scale after each diagnostic block --------------------------------------------


def test_pain_scale_after_each_diagnostic():
    criterion = "pain-scale-after-each-diagnostic"
    first, second = days_before(42), days_before(21)
    both = [BASELINE, *block("mbb1", first), *block("mbb2", second)]
    assert outcome(run(criterion, both)) == "met"

    vas_only = block("mbb2", second, post=10, coding=VAS, unit="mm")
    result = run(criterion, [BASELINE, *block("mbb1", first), *vas_only])
    assert outcome(result) == "not_met" and "Procedure/mbb2" in result.statement

    # a block with both a same-scale and a different-scale score is satisfied
    also_nrs = score("extra", second, 1, clock="10:30", encounter="mbb2")
    assert outcome(run(criterion, [BASELINE, *block("mbb1", first), *vas_only, also_nrs])) == "met"


def test_pain_scale_missing_data_is_absent_never_not_met_and_never_met():
    criterion = "pain-scale-after-each-diagnostic"
    day = days_before(21)
    assert outcome(run(criterion, [BASELINE])) == "absent"  # no prior blocks
    assert outcome(run(criterion, block("mbb1", day))) == "absent"  # no baseline score
    assert outcome(run(criterion, [BASELINE, *block("mbb1", day, post=None)])) == "absent"
    # the post score has a date but no time, so it cannot be placed after the procedure
    untimed = [BASELINE, procedure("mbb1", day, "64493"), score("post", day, 1, encounter="mbb1")]
    assert outcome(run(criterion, untimed)) == "absent"
    # a score in a different encounter is not linked to the procedure
    elsewhere = [
        BASELINE,
        procedure("mbb1", day, "64493"),
        score("post", day, 1, clock="10:00", encounter="other"),
    ]
    assert outcome(run(criterion, elsewhere)) == "absent"
    # the procedure itself is date-only
    no_time = [
        BASELINE,
        procedure("mbb1", day, "64493", clock=None),
        score("p", day, 1, clock="10:00", encounter="mbb1"),
    ]
    assert outcome(run(criterion, no_time)) == "absent"


def test_a_documented_wrong_scale_outranks_a_missing_score():
    first, second = days_before(42), days_before(21)
    vas_only = block("mbb1", first, post=10, coding=VAS, unit="mm")
    resources = [BASELINE, *vas_only, *block("mbb2", second, post=None)]
    assert outcome(run("pain-scale-after-each-diagnostic", resources)) == "not_met"


# --- body-site levels, failing closed --------------------------------------------------


@pytest.mark.parametrize(
    ("token", "expected"),
    [("L4-5", "L4-5"), ("L4-L5", "L4-5"), ("L4/5", "L4-5"), ("l5-s1", "L5-S1"), ("C7-T1", "C7-T1")]
    + [(token, None) for token in ("L4-S1", "L3-5", "L4", "L5-4", "L4-C5", "S1-2")],
)
def test_normalize_level(token, expected):
    assert normalize_level(token) == expected


@pytest.mark.parametrize(
    "text",
    [SITE, "L4-L5 and L5-S1", "L4/5, L5-S1, bilateral", "lumbar L4-5 & L5-S1", "L4-5, L5-S1"],
)
def test_site_text_that_matches_the_request(text):
    assert parse_site(text).levels == {"L4-5", "L5-S1"}
    assert same_site(_site(text), request()) == "match"


@pytest.mark.parametrize(
    "text",
    [
        "L4-S1",  # spans more than one segment
        "L4-5 and L5-S1; L3-4 not treated",  # anything else in the text
        "L4-5 and L5-S1 facet joints, see note",
        "left L4-5",
        "Lumbar facet joints",  # a region and no levels
        "Cervical L4-5",  # region word contradicts the levels
        "L4-5 and C5-6",  # two regions
        "",
        None,
    ],
)
def test_site_text_that_is_unclear(text):
    assert parse_site(text) is None
    assert same_site(_site(text), request()) == "unclear"


@pytest.mark.parametrize("text", ["L3-4, L4-5 and L5-S1", "L4-5", "Cervical C4-5 and C5-6"])
def test_site_text_that_states_different_levels(text):
    assert same_site(_site(text), request()) == "mismatch"


def test_site_laterality_is_parsed_but_cannot_differ_without_a_request_laterality():
    assert parse_site("L4-5, left").side == "left"
    assert same_site(_site("L4-5 and L5-S1, left"), request()) == "match"


def test_coded_levels_win_and_disagreement_is_unclear(monkeypatch):
    system = "http://example.org/levels"
    monkeypatch.setattr(rules, "LEVEL_CODES", {f"{system}|a": "L4-5", f"{system}|b": "L5-S1"})
    both = [{"system": system, "code": "a"}, {"system": system, "code": "b"}]
    assert same_site(_site(None, both), request()) == "match"
    assert same_site(_site("L4-5 and L5-S1", both), request()) == "match"
    # the text names a different level than the codes
    assert same_site(_site("L3-4 and L4-5", both), request()) == "unclear"
    # a code we cannot interpret
    assert same_site(_site(SITE, [{"system": system, "code": "zzz"}]), request()) == "unclear"


def _site(text, coding=None):
    resource = procedure("rfa", date(2025, 8, 4), "64635", site=text, site_coding=coding)
    return StructuredChart.from_bundle({"entry": [{"resource": resource}]}).procedures[0]


# --- prior ablation --------------------------------------------------------------------

RFA_DAY = date(2025, 8, 4)


def prior_rfa(*follow_ups, site=SITE, pre=7):
    """An ablation with a score before it and (day, value) scores after it."""
    return [
        procedure("rfa1", RFA_DAY, "64635", site=site),
        score("pre", RFA_DAY, pre, clock="09:00", encounter="rfa1"),
        *(score(f"f{i}", day, value) for i, (day, value) in enumerate(follow_ups)),
    ]


SIX_MONTHS = date(2026, 2, 4)
FACET_09 = [
    (date(2025, 9, 1), 3),
    (date(2025, 11, 3), 3),
    (date(2026, 2, 2), 3),
    (date(2026, 4, 6), 3),
]


def test_prior_rfa_met_on_the_facet_09_shape():
    result = run("prior-rfa-50pct-6-months", prior_rfa(*FACET_09))
    assert outcome(result) == "met"
    assert f'"{SITE}"' in result.statement  # the site text is cited verbatim
    assert "Procedure/rfa1" in result.inputs


def test_prior_rfa_ignores_scores_after_the_window():
    # the pain coming back later is why a repeat is requested
    recurrence = (date(2026, 9, 21), 7)
    assert outcome(run("prior-rfa-50pct-6-months", prior_rfa(*FACET_09, recurrence))) == "met"


def test_prior_rfa_six_months_is_calendar_months():
    criterion = "prior-rfa-50pct-6-months"
    early = (date(2025, 11, 3), 3)
    assert outcome(run(criterion, prior_rfa(early, (SIX_MONTHS, 3)))) == "met"  # to the day
    two_days_short = prior_rfa(early, (date(2026, 2, 2), 3))
    assert outcome(run(criterion, two_days_short)) == "partial"


def test_prior_rfa_fifty_percent_boundary():
    criterion = "prior-rfa-50pct-6-months"
    exactly = prior_rfa((date(2025, 11, 3), 4), (SIX_MONTHS, 4), pre=8)
    assert outcome(run(criterion, exactly)) == "met"
    # 49% at one visit inside the window
    below = prior_rfa((date(2025, 11, 3), 51), (SIX_MONTHS, 40), pre=100)
    assert outcome(run(criterion, below)) == "partial"


def test_prior_rfa_never_decides_not_met():
    criterion = "prior-rfa-50pct-6-months"
    assert outcome(run(criterion, [])) == "absent"
    # nothing coded after the ablation: the result is in free text, if anywhere
    assert outcome(run(criterion, prior_rfa())) == "absent"
    worse = prior_rfa((date(2025, 11, 3), 7), (SIX_MONTHS, 7))
    result = run(criterion, worse)
    assert result.kind == "partial" and "daily activities is not computed" in result.statement
    single = prior_rfa((SIX_MONTHS, 3))
    assert outcome(run(criterion, single)) == "partial"  # one score is too thin to call it
    vas = [*prior_rfa((date(2025, 11, 3), 3)), score("vas", SIX_MONTHS, 30, coding=VAS, unit="mm")]
    assert outcome(run(criterion, vas)) == "partial"
    no_baseline = [
        procedure("rfa1", RFA_DAY, "64635"),
        score("a", date(2025, 11, 3), 3),
        score("b", SIX_MONTHS, 3),
    ]
    assert outcome(run(criterion, no_baseline)) == "partial"


def test_prior_rfa_needs_the_same_levels():
    criterion = "prior-rfa-50pct-6-months"
    assert outcome(run(criterion, prior_rfa(*FACET_09, site="L3-4 and L4-5"))) == "partial"
    assert outcome(run(criterion, prior_rfa(*FACET_09, site="lower lumbar facets"))) == "partial"
    assert outcome(run(criterion, prior_rfa(*FACET_09), levels=["L4-5"])) == "partial"


def test_prior_rfa_does_not_count_the_disability_score_as_a_pain_score():
    odi = score("odi", date(2025, 12, 1), 22, coding=ODI, unit="%")
    assert outcome(run("prior-rfa-50pct-6-months", [*prior_rfa(*FACET_09), odi])) == "met"


# --- disability score at baseline ------------------------------------------------------


def test_disability_score_before_the_first_block():
    criterion = "disability-scale-at-baseline"
    odi = score("odi", date(2026, 8, 10), 48, coding=ODI, unit="%")
    blocks = block("mbb1", date(2026, 8, 24))
    result = run(criterion, [odi, *blocks])
    assert outcome(result) == "met" and result.inputs == ["Observation/odi"]
    assert outcome(run(criterion, [odi])) == "met"  # no block yet: before the service date

    after_block = score("odi", date(2026, 9, 1), 48, coding=ODI, unit="%")
    assert outcome(run(criterion, [after_block, *blocks])) == "absent"
    # a disability score recorded as text only is not found by code
    text_only = score("odi", date(2026, 8, 10), 48, coding=None, unit="%")
    assert outcome(run(criterion, [text_only, *blocks])) == "absent"
    assert outcome(run(criterion, blocks)) == "absent"  # never not_met


def test_disability_score_for_a_repeat_request_is_from_the_current_episode():
    criterion = "disability-scale-at-baseline"
    before_ablation = score("odi-2025", date(2025, 3, 17), 48, coding=ODI, unit="%")
    ablation = procedure("rfa1", RFA_DAY, "64635")
    assert outcome(run(criterion, [before_ablation, ablation])) == "absent"
    current = score("odi-recurrence", date(2026, 9, 21), 46, coding=ODI, unit="%")
    result = run(criterion, [before_ablation, ablation, current])
    assert outcome(result) == "met" and result.inputs == ["Observation/odi-recurrence"]
    # a facet procedure after the ablation closes the window
    later_block = procedure("mbb", date(2026, 9, 1), "64493")
    assert outcome(run(criterion, [before_ablation, ablation, later_block, current])) == "absent"


# --- the eleven cases ------------------------------------------------------------------


def test_no_decided_result_contradicts_a_label():
    checks = check_cases(list(load_cases().values()))
    assert [f"{c.case_id}/{c.criterion}" for c in checks if c.contradicts_label] == []
    decided = {(c.case_id, c.criterion): c.decided.value for c in checks if c.decided is not None}
    # the stable baseline errors the rules are meant to remove
    assert decided[("facet-05", "two-mbbs-80pct-sustained-relief")] == "insufficient"
    assert decided[("facet-05", "pain-scale-after-each-diagnostic")] == "not_met"
    assert decided[("facet-09", "diagnostics-repeated-after-2-years")] == "not_applicable"
    assert decided[("facet-03", "pain-scale-after-each-diagnostic")] == "met"
    assert decided[("facet-04", "pain-scale-after-each-diagnostic")] == "met"
    assert decided[("facet-09", "prior-rfa-50pct-6-months")] == "met"
    assert decided[("facet-09", "disability-scale-at-baseline")] == "met"


def test_rules_never_decide_the_criteria_the_audit_ruled_out():
    checks = check_cases(list(load_cases().values()))
    never = {"no-diagnostic-at-level-of-successful-rfa", "diagnostic-sessions-max-4"}
    assert not [c for c in checks if c.criterion in never and c.result.kind == "decided"]
    # a rule may decide not_met for prior-rfa under no circumstances
    assert not [
        c
        for c in checks
        if c.criterion == "prior-rfa-50pct-6-months" and c.decided and c.decided.value != "met"
    ]


# --- partial branches ------------------------------------------------------------------


def _branch(criterion_id, resources, **request_overrides):
    result = run(criterion_id, resources, **request_overrides)
    assert result.kind == "partial", result
    return result.branch


def test_each_partial_return_site_names_its_branch():
    ia = "ia-diagnostic-only-if-mbb-not-possible"
    rfa_14_months = procedure("rfa-old", date(2025, 8, 4), "64635")
    four_inside = [r for n in range(4) for r in block(f"b{n}", days_before(30 + 30 * n))]
    boundary = date(2025, 10, 19)  # exactly 12 months before the service date
    seen = {
        _branch("second-diagnostic-2-weeks-after-first", block("b", days_before(10))),
        _branch(
            "second-diagnostic-2-weeks-after-first",
            [*block("b1", days_before(40)), *block("b2", days_before(20))],
        ),
        _branch("diagnostic-sessions-max-4", block("b", days_before(10))),
        _branch("diagnostic-sessions-max-4", four_inside),
        _branch("rfa-sessions-max-2", [rfa_14_months]),
        _branch(
            "rfa-sessions-max-2",
            [procedure("rfa1", boundary, "64635"), procedure("rfa2", days_before(30), "64635")],
        ),
        _branch("first-diagnostic-80pct-relief", block("b", days_before(10))),
        _branch(
            "two-mbbs-80pct-sustained-relief",
            [*block("b1", days_before(40)), *block("b2", days_before(20))],
        ),
        _branch(
            "two-mbbs-80pct-sustained-relief",
            [r for n in range(3) for r in block(f"b{n}", days_before(20 + 20 * n))],
        ),
        _branch("prior-rfa-50pct-6-months", [rfa_14_months, score("later", days_before(30), 7)]),
        _branch("same-day-blocks-justified", [], same_day_blocks=["epidural injection"]),
        _branch(ia, [], technique="intraarticular_injection"),
        _branch(
            "diagnostics-repeated-after-2-years", [procedure("rfa", date(2024, 1, 5), "64635")]
        ),
        _branch("no-diagnostic-at-level-of-successful-rfa", [rfa_14_months]),
    }
    assert seen == set(rules.PARTIAL_BRANCHES)


def test_session_limits_route_met_to_the_chart_when_the_count_could_reach_the_limit():
    four_inside = [r for n in range(4) for r in block(f"b{n}", days_before(30 + 30 * n))]
    assert _branch("diagnostic-sessions-max-4", four_inside) == (
        "diagnostic-sessions/at-or-over-limit"
    )
    # three inside plus one exactly on the window's first day: that day would tip it
    three_and_boundary = [
        *(r for n in range(3) for r in block(f"b{n}", days_before(30 + 30 * n))),
        *block("edge", date(2025, 10, 19)),
    ]
    assert _branch("diagnostic-sessions-max-4", three_and_boundary) == (
        "diagnostic-sessions/at-or-over-limit"
    )
    two_and_boundary = [*three_and_boundary[3:]]  # two inside plus the boundary session
    assert _branch("diagnostic-sessions-max-4", two_and_boundary) == (
        "diagnostic-sessions/within-limit"
    )
    # a boundary ablation alone does not tip the RFA limit: 0 + 1 + 1 = 2
    boundary_only = [procedure("rfa1", date(2025, 10, 19), "64635")]
    assert _branch("rfa-sessions-max-2", boundary_only) == "rfa-sessions/within-limit"


def test_every_partial_call_in_rules_names_a_branch_in_the_table():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(rules))
    named = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_partial":
            first = node.args[0]
            options = [first.body, first.orelse] if isinstance(first, ast.IfExp) else [first]
            assert all(isinstance(o, ast.Constant) for o in options), ast.unparse(first)
            named |= {o.value for o in options}
    assert named == set(rules.PARTIAL_BRANCHES)
    for key, branch in rules.PARTIAL_BRANCHES.items():
        assert branch.criterion in rules.RULES, key


def test_the_fixture_partials_take_the_reviewed_branches():
    partials = {
        (c.case_id, c.criterion): rules.PARTIAL_BRANCHES[c.result.branch].row
        for c in check_cases(list(load_cases().values()))
        if c.result.kind == "partial"
    }
    assert partials == {
        ("facet-01", "two-mbbs-80pct-sustained-relief"): "5a",
        ("facet-03", "first-diagnostic-80pct-relief"): "4",
        ("facet-03", "second-diagnostic-2-weeks-after-first"): "1a",
        ("facet-03", "diagnostic-sessions-max-4"): "2a",
        ("facet-04", "first-diagnostic-80pct-relief"): "4",
        ("facet-04", "second-diagnostic-2-weeks-after-first"): "1a",
        ("facet-04", "diagnostic-sessions-max-4"): "2a",
        ("facet-09", "rfa-sessions-max-2"): "3a",
        ("facet-10", "prior-rfa-50pct-6-months"): "6",
        ("facet-10", "rfa-sessions-max-2"): "3a",
    }
