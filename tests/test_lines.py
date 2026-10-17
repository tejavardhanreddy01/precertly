"""Line-level evidence handles: segmentation, handle parsing, extraction, the cap and the
first-words echo (judgment/lines.py and judge.verify)."""

import json

import pytest

from precertly.evals import load_cases
from precertly.evals.cases import EVALS_DIR
from precertly.evals.prompt_check import case_chunks
from precertly.judgment import EvidenceChunk, verify
from precertly.judgment.judge import invalidation_kind
from precertly.judgment.lines import (
    MAX_CITATION_LINES,
    MAX_LINE_CHARS,
    HandleError,
    echo_matches,
    lines_of,
    parse_handle,
    segment,
    words,
)
from precertly.judgment.v4 import Citation, ModelJudgment


def _texts(text, note=True):
    return [line.text for line in segment(text, note=note)]


# --- segmentation -------------------------------------------------------------------


def test_a_note_is_split_on_newlines_and_blank_lines_get_no_handle():
    lines = segment("2026-08-10\nPain clinic\n\n   \nPlan: block.\n", note=True)
    assert [(line.number, line.text) for line in lines] == [
        (1, "2026-08-10"),
        (2, "Pain clinic"),
        (3, "Plan: block."),
    ]


def test_lines_are_trimmed_and_crlf_is_handled():
    assert _texts("  first line  \r\nsecond\r\n") == ["first line", "second"]


def test_bullets_stay_separate_lines_with_their_markers():
    text = "Pain and function at baseline:\n- Pain: 7/10.\n- Disability: 48%."
    assert _texts(text) == ["Pain and function at baseline:", "- Pain: 7/10.", "- Disability: 48%."]


def test_a_long_line_is_split_at_sentence_ends():
    sentences = ["History: low back pain for 4 months.", "It does not radiate.", "No surgery."]
    long = " ".join(sentences) + " " + "Padding words are here. " * 6
    assert len(long) > MAX_LINE_CHARS
    assert _texts(long)[:3] == sentences


def test_a_short_line_is_never_split():
    text = "Pain: 7/10. Disability: 48%."
    assert len(text) <= MAX_LINE_CHARS
    assert _texts(text) == [text]


@pytest.mark.parametrize(
    "joint",
    [
        "Dose 5.5 mg daily",  # a decimal has no whitespace after the point
        "Seen by Dr. Lee today",  # an abbreviation
        "e.g. Naproxen was tried",
        "Relief noted. then it faded",  # a lowercase next word
        "Pain eased; Function improved",  # a semicolon
    ],
)
def test_no_split_inside_these(joint):
    long = joint + " " + "x" * MAX_LINE_CHARS
    assert _texts(long)[0].startswith(joint)


def test_sentence_ends_are_unicode_aware():
    long = "Pain improved. Édema resolved. “Better,” she said. " + "Y" * MAX_LINE_CHARS
    assert _texts(long)[:3] == ["Pain improved.", "Édema resolved.", "“Better,” she said."]


def test_unicode_spaces_are_trimmed_and_offsets_are_code_points():
    text = " Schmerz – 7/10 \nÜbelkeit: keine"
    lines = segment(text, note=True)
    assert [line.text for line in lines] == ["Schmerz – 7/10", "Übelkeit: keine"]
    assert all(text[line.start : line.end] == line.text for line in lines)


def test_free_text_is_split_by_the_chunk_flag_not_by_resource_type():
    text = "2026-07-20\nMRI lumbar spine\n\nImpression: Facet arthropathy."
    report = EvidenceChunk(id="DiagnosticReport/mri#1", text=text, free_text=True)
    assert [line.text for line in lines_of(report)][-1] == "Impression: Facet arthropathy."
    rendered = EvidenceChunk(id="DocumentReference/note#0", text=text)
    assert len(lines_of(rendered)) == 1


def test_fixture_chunks_are_free_text_exactly_when_the_chunker_sliced_them():
    from precertly.fhir import chunk_bundle, load_bundle

    for case in load_cases().values():
        rebuilt = case_chunks(case)
        for chunk in chunk_bundle(load_bundle(case.bundle_path())):
            assert rebuilt[chunk.id].free_text == (chunk.char_start is not None), chunk.id
    radiology = case_chunks(load_cases()["facet-01"])["DiagnosticReport/mri-lumbar#1"]
    assert radiology.free_text and len(lines_of(radiology)) > 1


def test_coded_and_request_items_are_one_line_whatever_their_layout():
    coded = "Observation: Pain (LOINC 72514-3)\nValue: 2 {score}\nDate: 2026-09-14\n"
    (line,) = segment(coded, note=False)
    assert (line.number, line.text) == (1, coded.strip())
    request = EvidenceChunk(id="request.levels", text="Levels: L4-5, L5-S1")
    assert len(lines_of(request)) == 1


def test_empty_text_has_no_lines():
    assert segment("", note=True) == [] and segment(" \n ", note=False) == []


def test_every_fixture_chunk_segments_to_exact_slices_deterministically():
    for case in load_cases().values():
        for chunk in case_chunks(case).values():
            lines = lines_of(chunk)
            assert lines == lines_of(chunk), chunk.id
            assert [line.number for line in lines] == list(range(1, len(lines) + 1))
            for line in lines:
                assert chunk.text[line.start : line.end] == line.text != ""


# --- handle parsing -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "parsed"),
    [
        ("E3.2", ("E3", 2, 2)),
        ("[E3.2]", ("E3", 2, 2)),
        ("e3.2", ("E3", 2, 2)),
        ("E3.2-E3.4", ("E3", 2, 4)),
        ("[E3.2]-[E3.4]", ("E3", 2, 4)),
        ("E3.2–3.4", ("E3", 2, 4)),
        ("E3.2 - 4", ("E3", 2, 4)),
        ("E5", ("E5", None, None)),
        ("C1", ("C1", None, None)),
        ("[c1]", ("C1", None, None)),
    ],
)
def test_handles_parse(text, parsed):
    handle = parse_handle(text)
    assert (handle.item, handle.first, handle.last) == parsed


@pytest.mark.parametrize(
    "text",
    ["E3.0", "E3.4-E3.2", "E3.2-E4.1", "E3-E5", "3.2", "E 3.2", "note#0", "", "E3.2.1"],
)
def test_bad_handles_are_refused(text):
    with pytest.raises(HandleError):
        parse_handle(text)


# --- extraction, the cap and the echo -------------------------------------------------

NOTE = EvidenceChunk(
    id="DocumentReference/note#0",
    text="Results:\n- Block 1: 90%.\n\n- Block 2: 85%.\n- Both at L4-5.\nPlan: ablation.",
    free_text=True,
)
CODED = EvidenceChunk(id="Procedure/mbb1#0", text="Procedure: block\nStatus: completed")


def _verify(*citations, computed=None):
    answer = ModelJudgment(
        outcome="met",
        confidence=0.9,
        rationale="because",
        citations=[Citation(evidence=e, first_words=w) for e, w in citations],
    )
    return verify(answer, [NOTE, CODED], computed)


def test_a_cited_range_is_extracted_verbatim_across_a_blank_line():
    (citation,), _, invalid = _verify(("E1.2-E1.3", "- Block 1: 90%."))
    assert invalid is None
    assert citation.quote == "- Block 1: 90%.\n\n- Block 2: 85%."
    assert NOTE.text[citation.char_start : citation.char_end] == citation.quote
    assert (citation.first_line, citation.last_line) == (2, 3)


def test_the_cap_allows_three_lines_and_refuses_four():
    assert MAX_CITATION_LINES == 3
    cited, _, invalid = _verify(("E1.1-E1.3", "Results:"))
    assert invalid is None and cited[0].last_line == 3
    _, _, invalid = _verify(("E1.1-E1.4", "Results:"))
    assert invalid is not None and invalid[0] == "citation_too_long"


@pytest.mark.parametrize("handle", ["E1.6", "E1.5-E1.6", "E3.1", "E1"])
def test_handles_beyond_the_prompt_are_unknown(handle):
    _, _, invalid = _verify((handle, "Plan: ablation."))
    assert invalid is not None and invalid[0] == "unknown_handle"


def test_a_one_line_item_may_be_cited_bare_or_as_line_one():
    for handle in ("E2", "E2.1"):
        (citation,), _, invalid = _verify((handle, "Procedure: block Status:"))
        assert invalid is None and citation.quote == CODED.text


def test_the_echo_must_be_the_first_cited_line():
    _, _, invalid = _verify(("E1.3", "- Block 1: 90%."))
    assert invalid is not None and invalid[0] == "handle_mismatch"


def test_c1_is_extracted_whole_and_its_echo_checked():
    statement = "Two sessions are coded before the request date."
    _, quote, invalid = _verify(("C1", "two sessions are"), computed=statement)
    assert (quote, invalid) == (statement, None)
    _, _, invalid = _verify(("C1", "three sessions"), computed=statement)
    assert invalid is not None and invalid[0] == "handle_mismatch"


@pytest.mark.parametrize(
    ("echo", "line", "matches"),
    [
        ("Pain: 7/10 on", "- Pain: 7/10 on the NRS 0-10.", True),  # bullet ignored
        ("PAIN 7/10 ON", "Pain: 7/10 on the scale", True),  # case and punctuation ignored
        ("Pain: 7/10", "Pain: 7/10 on the scale", True),  # two words are enough
        ("Pain: 7/10 on the scale", "Pain: 7/10 on the scale", True),  # the whole line
        ("Pain: 7/10 on the NRS", "Pain: 7/10 on the scale", False),  # every word must match
        ("Pain", "Pain: 7/10 on the scale", False),  # one word is too few
        ("2026-07-20", "2026-07-20\nMRI lumbar spine", True),  # a one-word first row
        ("Pain and", "- Pain: 7/10 on the NRS", False),  # the misnumbering seen in the canary
        ("Disability: 48%", "Pain: 7/10 on the scale", False),
        ("2026-08-10", "2026-08-10", True),  # a line of fewer than three words
        ("ﬁbromyalgia none", "Fibromyalgia: none.", True),  # NFKC folds the ligature
        ("", "Pain: 7/10", False),
    ],
)
def test_echo_matching(echo, line, matches):
    assert echo_matches(echo, line) is matches


def test_words_strip_a_leading_bullet_and_punctuation():
    assert words("- Pain: 7/10, severe.") == ["pain", "710", "severe"]
    assert words("2) Block one") == ["block", "one"]


# --- invalidation kinds of earlier files ----------------------------------------------


def test_every_recorded_invalidation_reason_has_a_kind():
    reasons = set()
    for path in sorted((EVALS_DIR / "results").glob("*.json")):
        for case in json.loads(path.read_text(encoding="utf-8"))["cases"]:
            for verdict in case["verdicts"]["verdicts"]:
                reasons.add(verdict.get("invalidated"))
                if verdict.get("shadow"):
                    reasons.add(verdict["shadow"].get("invalidated"))
    reasons.discard(None)
    kinds = {invalidation_kind(reason) for reason in reasons}
    assert reasons and None not in kinds
    assert {"quote_not_found", "unknown_handle", "condition_evidence"} <= kinds
