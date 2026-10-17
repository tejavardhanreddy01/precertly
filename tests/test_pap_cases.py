"""PAP case facts the generic case tests do not check: the usage report, the timelines and
the background."""

import base64
import importlib.util
import json
import re
import sys
from datetime import date

import pytest

from precertly.evals import EVALS_DIR, load_cases
from precertly.fhir import chunk_bundle, load_bundle

CASES = {k: v for k, v in load_cases().items() if v.policy == "pap-sleep-apnea"}


def _build_module():
    spec = importlib.util.spec_from_file_location("build_cases", EVALS_DIR / "build_cases.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BUILD = _build_module()


def _notes(case_id: str) -> dict[str, str]:
    notes = {}
    for entry in load_bundle(CASES[case_id].bundle_path())["entry"]:
        resource = entry["resource"]
        attachments = [c["attachment"] for c in resource.get("content") or []]
        attachments += resource.get("presentedForm") or []
        for attachment in attachments:
            notes[resource["id"]] = base64.b64decode(attachment["data"]).decode("utf-8")
    return notes


def _resources(case_id: str) -> dict[str, dict]:
    return {
        e["resource"]["id"]: e["resource"]
        for e in load_bundle(CASES[case_id].bundle_path())["entry"]
    }


def test_the_pap_cases_are_the_six_planned():
    assert sorted(CASES) == [f"pap-0{n}" for n in range(1, 7)]
    assert {c.request.service_date for c in CASES.values()} == {date(2026, 10, 19)}
    for case in CASES.values():
        assert {c.id for c in case.request.chunks()} == {
            "request.date",
            "request.service_date",
            "request.procedure",
            "request.codes",
        }


def test_the_best_30_night_window_is_exactly_20_and_nothing_beats_it():
    hours = BUILD.usage_hours()
    windows = [sum(h >= 4.0 for h in hours[i : i + 30]) for i in range(len(hours) - 29)]
    assert len(hours) == 92 and max(windows) == 20
    # no night sits near the 4-hour line
    assert not [h for h in hours if 3.4 < h < 4.6]


def test_the_download_report_chunks_keep_the_window_and_the_nightly_table():
    case = CASES["pap-03"]
    chunks = [
        c
        for c in chunk_bundle(load_bundle(case.bundle_path()))
        if c.resource_id == "note-pap-download"
    ]
    text = "\n".join(c.text for c in chunks)
    window = [c for c in chunks if "use of 4 hours or more on 20 of 30 nights" in c.text]
    assert len(window) == 1 and window[0].char_start is not None
    # the nightly values in the chunks give the same best window as the statement
    values = []
    for line in text.splitlines():
        if match := re.match(r"Nights \d+-\d+ \([^)]*\): (.*)", line):
            values += [float(v) for v in match.group(1).split()]
    assert len(values) == 92
    windows = [sum(v >= 4.0 for v in values[i : i + 30]) for i in range(len(values) - 29)]
    assert max(windows) == 20


def test_the_download_is_dated_after_its_last_night_and_the_review_follows_it():
    resources = _resources("pap-03")
    notes = {i: r for i, r in resources.items() if r["resourceType"] == "DocumentReference"}
    start = date.fromisoformat(resources["cpap-use"]["timingPeriod"]["start"])
    last_night = date.fromordinal(start.toordinal() + 91)
    report, review = notes["note-pap-download"], notes["note-adherence-review"]
    assert last_night == date(2026, 9, 21)
    assert date.fromisoformat(report["date"][:10]) > last_night
    assert date.fromisoformat(review["date"][:10]) > date.fromisoformat(report["date"][:10])
    reeval = date.fromisoformat(notes["note-reeval"]["date"][:10])
    assert (reeval - start).days == 61
    assert reeval < date.fromisoformat(report["date"][:10])
    assert "Nights" not in _notes("pap-03")["note-reeval"]  # no usage data in the day-61 visit


def test_pap_06_has_no_usage_data_anywhere():
    text = json.dumps(_notes("pap-06"))
    assert not {"note-pap-download", "note-adherence-review"} & set(_notes("pap-06"))
    assert not re.search(r"download|nights with|usage data", text, re.IGNORECASE)


def test_pap_04_conflicts_only_on_the_duration_of_e0601_use():
    notes = _notes("pap-04")
    assert "2026-01-12" in notes["note-cpap-followup-march"]
    assert "No previous PAP use" in notes["note-cpap-followup-august"]
    first_use = date(2026, 8, 4)
    assert "2026-08-04" in notes["note-cpap-followup-august"]
    assert date(2025, 12, 4) < first_use  # after the evaluation and the test
    # the telephone note is the order, and says nothing about when the use began
    body = notes["note-telephone"].split("\n", 2)[2]  # after the date and title lines
    assert not re.search(r"20\d\d-\d\d-\d\d", body.replace(BUILD.CPAP_REFIT_VISIT.isoformat(), ""))
    assert not {"cpap-use"} & set(_resources("pap-04"))


@pytest.mark.parametrize("case", CASES.values(), ids=list(CASES))
def test_ahi_in_the_notes_is_the_one_in_the_label(case):
    ahi = next(label for label in case.expected if label.criterion == "ahi-15-plus").fact.split()[1]
    assert f"AHI {ahi}" in _notes(case.id)["note-sleep-results"]
    assert f"(AHI): {ahi}" in _notes(case.id)["report-sleep-test"]


def test_the_background_says_nothing_about_sleep_apnea_beyond_the_named_condition():
    template = json.loads((EVALS_DIR / "template" / "patient.json").read_text(encoding="utf-8"))
    pattern = re.compile(r"apnea|sleep|insomnia|somnolence|snor|cpap|hypertension|stroke", re.I)
    for resource in template:
        text = json.dumps(resource)
        for content in resource.get("content") or []:
            data = content.get("attachment", {}).get("data")
            text += base64.b64decode(data).decode("utf-8") if data else ""
        assert not pattern.search(text), f"{resource['resourceType']}/{resource['id']}"
    ids = {f"{r['resourceType']}/{r['id']}" for r in template}
    assert BUILD.IHD_CONDITION in ids
