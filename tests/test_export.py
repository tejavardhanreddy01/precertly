"""The recorded-mode export (src/precertly/export): one test per invariant, no database."""

import hashlib
import json
from functools import cache
from pathlib import Path

import pytest
from pydantic import ValidationError

from precertly.evals import load_cases
from precertly.export import checks
from precertly.export import models as m
from precertly.export.__main__ import main
from precertly.export.build import DEFAULT_OUT, SET_ORDER, build_files, to_text
from precertly.judgment.judge import find_quote
from precertly.judgment.logic import JUDGED_STAGES
from precertly.pas.__main__ import SOURCE
from precertly.policies import load_policies

CASES = load_cases()
POLICIES = load_policies()


@cache
def built() -> dict[str, str]:
    return build_files()


def files() -> dict[str, str]:
    return dict(built())


def case(case_id: str) -> dict:
    return json.loads(built()[f"cases/{case_id}.json"])


@cache
def source() -> dict:
    return json.loads(SOURCE.read_text(encoding="utf-8"))


def scored_ids() -> list[str]:
    return [entry["case_id"] for entry in source()["cases"]]


def verdict_entries():
    """(case id, verdict as recorded in the source run)."""
    for entry in source()["cases"]:
        for verdict in entry["verdicts"]["verdicts"]:
            yield entry["case_id"], verdict


def edit(name: str, change) -> dict[str, str]:
    """The files with one JSON file changed by change(parsed)."""
    out = files()
    data = json.loads(out[name])
    change(data)
    out[name] = to_text(data)
    return out


# --- a. citation round trip -----------------------------------------------------------------


def test_every_recorded_citation_is_at_its_offsets_in_the_exported_chunk():
    seen = 0
    for case_id, verdict in verdict_entries():
        chunks = {c["chunk_id"]: c for c in case(case_id)["chart"]}
        for citation in verdict["citations"]:
            text = chunks[citation["chunk_id"]]["text"]
            start, end = citation["char_start"], citation["char_end"]
            # The verifier matches ignoring whitespace only and records the span of the
            # match, relative to the chunk text; so the slice differs from the quote by
            # whitespace at most, and find_quote gives back the same span.
            assert text[start:end].split() == citation["quote"].split(), (case_id, citation)
            assert find_quote(citation["quote"], text) == (start, end), (case_id, citation)
            seen += 1
    assert seen == 276


def test_exported_citations_match_the_recorded_ones_and_are_located():
    for case_id, verdict in verdict_entries():
        exported = next(
            v for v in case(case_id)["result"]["verdicts"] if v["criterion"] == verdict["criterion"]
        )
        assert [
            (c["chunk_id"], c["quote"], c["char_start"], c["char_end"])
            for c in exported["citations"]
        ] == [
            (c["chunk_id"], c["quote"], c["char_start"], c["char_end"])
            for c in verdict["citations"]
        ]
        assert all(c["located"] for c in exported["citations"])


def test_invalidated_verdicts_keep_the_recorded_reason_and_citations_are_not_invented():
    invalidated = [(c, v) for c, v in verdict_entries() if v["invalidated"]]
    assert len(invalidated) == 5
    for case_id, verdict in invalidated:
        exported = next(
            v for v in case(case_id)["result"]["verdicts"] if v["criterion"] == verdict["criterion"]
        )
        assert exported["invalidated"] == verdict["invalidated"]
        assert exported["outcome"] == "insufficient"
        # The run records no citations for an invalidated verdict (the verifier returns
        # none when one fails), so the export has none either.
        assert exported["citations"] == verdict["citations"] == []


def test_an_unlocated_citation_fails_the_check_unless_the_verdict_was_invalidated():
    def shift(data):
        citation = next(
            c for v in data["result"]["verdicts"] for c in v["citations"] if c["char_start"] > 0
        )
        citation["char_start"] -= 1

    with pytest.raises(checks.CheckFailed, match="citation round trip"):
        checks.check_citations(edit("cases/facet-01.json", shift))

    # An unlocated citation on an invalidated verdict is exported with located false.
    def invalidate(data):
        verdict = next(v for v in data["result"]["verdicts"] if v["citations"])
        verdict["citations"][0]["quote"] = "words that are not in the chunk"
        verdict["citations"][0]["located"] = False
        verdict["invalidated"] = "quote not found"

    checks.check_citations(edit("cases/facet-01.json", invalidate))

    def claim_located(data):
        verdict = next(v for v in data["result"]["verdicts"] if v["citations"])
        verdict["citations"][0]["quote"] = "words that are not in the chunk"

    with pytest.raises(checks.CheckFailed, match="located is True"):
        checks.check_citations(edit("cases/facet-01.json", claim_located))


# --- b. citation chunk ids exist in the chart ------------------------------------------------


def test_every_citation_and_shown_chunk_id_is_in_the_chart_of_its_case():
    for case_id in scored_ids():
        ids = {c["chunk_id"] for c in case(case_id)["chart"]}
        for verdict in case(case_id)["result"]["verdicts"]:
            assert set(verdict["shown_chunk_ids"]) <= ids, (case_id, verdict["criterion"])
            assert {c["chunk_id"] for c in verdict["citations"]} <= ids
    assert any(c["chunk_id"].startswith("request.") for c in case("facet-01")["chart"])


def test_a_citation_of_a_chunk_that_is_not_in_the_chart_fails_the_check():
    def rename(data):
        verdict = next(v for v in data["result"]["verdicts"] if v["citations"])
        verdict["citations"][0]["chunk_id"] = "DocumentReference/nope#0"

    with pytest.raises(checks.CheckFailed, match="chunk ids"):
        checks.check_citations(edit("cases/facet-01.json", rename))


def test_request_chunks_are_exactly_what_the_judgment_step_shows():
    for case_id, eval_case in CASES.items():
        shown = {c.id: c.text for c in eval_case.request.chunks()}
        exported = {
            c["chunk_id"]: c["text"] for c in case(case_id)["chart"] if c["source"] == "request"
        }
        assert exported == shown


def test_note_chunks_carry_their_own_offsets_into_the_note():
    notes = [c for c in case("facet-01")["chart"] if c["char_start"] is not None]
    assert notes
    assert all(c["char_end"] - c["char_start"] == len(c["text"]) for c in notes)
    structured = [
        c for c in case("facet-01")["chart"] if c["source"] == "chart" and c["char_start"] is None
    ]
    assert structured and all(c["char_end"] is None for c in structured)


# --- c. counts -------------------------------------------------------------------------------


def test_counts():
    exported = {name: json.loads(text) for name, text in built().items()}
    cases = [v for k, v in exported.items() if k.startswith("cases/")]
    policies = [v for k, v in exported.items() if k.startswith("policies/")]
    assert len(cases) == 29 and len(policies) == 4
    # 76 criteria, not the 75 the task prompt gave: every policy criterion is exported.
    assert sum(len(p["criteria"]) for p in policies) == 76
    assert sum(len(p.criteria) for p in POLICIES.values()) == 76
    assert sum(len(c["expected"]) for c in cases) == 326
    scored = [c for c in cases if c["scored"]]
    assert len(scored) == 11
    assert sum(len(c["result"]["verdicts"]) for c in scored) == 184
    assert [c["scored"] for c in cases if c["result"] is None] == [False] * 18


def test_index_summary_equals_the_source_summary_and_run_block_is_copied():
    index = json.loads(built()["index.json"])
    source_summary = source()["summary"]
    for key in m.RunSummary.model_fields:
        assert index["summary"][key] == source_summary[key], key
    run = index["run"]
    for key in ("run_date", "git_sha", "git_dirty", "complete", "model_id", "prompt_version"):
        assert run[key] == source()[key]
    assert run["system_prompt_sha256"] == source()["system_prompt_sha256"]
    assert len(run["system_prompt_sha256"]) == 64
    assert run["judgment"]["verifier_version"] == source()["judgment"]["verifier_version"]
    assert run["rules"]["version"] == source()["rules"]["version"]
    assert run["retrieval"] == {
        "top_k": source()["retrieval"]["top_k"],
        "method": source()["retrieval"]["method"],
    }
    assert run["rescored"] == {
        "source": source()["rescored"]["source"],
        "source_sha256": source()["rescored"]["source_sha256"],
    }
    assert run["source_file"] == SOURCE.name
    assert run["source_sha256"] == hashlib.sha256(SOURCE.read_bytes()).hexdigest()


def test_index_lists_cases_in_set_order_and_has_the_fixed_banners():
    index = json.loads(built()["index.json"])
    order = [(SET_ORDER.index(row["set"]), row["id"]) for row in index["cases"]]
    assert order == sorted(order)
    assert [row["set"] for row in index["cases"]][0] == "facet"
    assert {row["set"] for row in index["cases"]} == set(SET_ORDER)
    assert index["schema_version"] == 1
    assert index["banners"] == m.BANNERS and len(m.BANNERS) == 5
    assert [p["id"] for p in index["policies"]] == sorted(POLICIES)
    for row in index["cases"]:
        exported = case(row["id"])
        assert row["scored"] is exported["scored"]
        assert row["predicted_overall"] == (exported["result"] or {}).get("predicted_overall")
        assert row["criteria"] == len(exported["criteria"])
        assert row["labeled_criteria"] == len(exported["expected"])


def test_unscored_cases_show_labels_and_no_verdicts():
    unscored = [c for c in CASES if c not in scored_ids()]
    assert len(unscored) == 18
    for case_id in unscored:
        exported = case(case_id)
        assert exported["scored"] is False and exported["result"] is None
        assert exported["expected"]
    index = json.loads(built()["index.json"])
    for row in index["cases"]:
        if not row["scored"]:
            assert row["predicted_overall"] is None and row["overall_correct"] is None


def test_unjudged_criteria_are_listed_with_judged_false():
    for case_id, eval_case in CASES.items():
        exported = case(case_id)
        policy = POLICIES[eval_case.policy]
        in_variant = policy.variant(eval_case.variant).logic.criterion_ids()
        assert [c["criterion"] for c in exported["criteria"]] == [
            c.id for c in policy.criteria if c.id in in_variant
        ]
        for c in exported["criteria"]:
            stage = policy.criterion(c["criterion"]).evidence_stage
            assert c["evidence_stage"] == stage.value
            assert c["judged"] is (stage in JUDGED_STAGES)
        # Labels exist exactly for the judged criteria.
        assert {x["criterion"] for x in exported["expected"]} == {
            c["criterion"] for c in exported["criteria"] if c["judged"]
        }
        if exported["result"]:
            assert [i["criterion"] for i in exported["result"]["reviewer_checklist"]] == [
                c["criterion"] for c in exported["criteria"] if not c["judged"]
            ]
    assert any(not c["judged"] for c in case("facet-01")["criteria"])


# --- d. flags and overall equal the source run -----------------------------------------------


def test_flags_and_overall_equal_the_source_run_and_follow_from_the_labels():
    negative = {"not_met", "insufficient"}
    for entry in source()["cases"]:
        exported = case(entry["case_id"])
        result = exported["result"]
        assert result["predicted_overall"] == entry["predicted_overall"]
        assert result["overall_correct"] == (
            exported["expected_overall"] == result["predicted_overall"]
        )
        assert result["reviewer_checklist"] == entry["verdicts"]["reviewer_checklist"]
        recorded = {c["criterion"]: c for c in entry["criteria"]}
        assert [v["criterion"] for v in result["verdicts"]] == [
            v["criterion"] for v in entry["verdicts"]["verdicts"]
        ]
        for verdict in result["verdicts"]:
            flags = recorded[verdict["criterion"]]
            assert verdict["correct"] == flags["correct"]
            assert verdict["false_approval"] == flags["false_approval"]
            assert verdict["expected"] == flags["expected"]
            if verdict["expected"] == "not_applicable":
                assert verdict["correct"] == (not verdict["applicable"])
            else:
                assert verdict["correct"] == (verdict["outcome"] == verdict["expected"])
            assert verdict["false_approval"] == (
                verdict["outcome"] == "met" and verdict["expected"] in negative
            )
    correct = sum(v["correct"] for c in scored_ids() for v in case(c)["result"]["verdicts"])
    assert correct == source()["summary"]["criteria_correct"]
    assert (
        sum(case(c)["result"]["overall_correct"] for c in scored_ids())
        == source()["summary"]["cases_correct"]
    )


def test_verdicts_are_exported_as_recorded():
    for case_id, recorded in verdict_entries():
        exported = next(
            v
            for v in case(case_id)["result"]["verdicts"]
            if v["criterion"] == recorded["criterion"]
        )
        for key in (
            "outcome",
            "model_outcome",
            "decided_by",
            "rule_kind",
            "applicable",
            "confidence",
            "rationale",
            "invalidated",
        ):
            assert exported[key] == recorded[key], (case_id, recorded["criterion"], key)
        assert exported["computed"] == recorded["computed"]
        first = recorded["exchanges"][0]["prompt_sha256"] if recorded["exchanges"] else None
        assert exported["prompt_sha256"] == first
    dumped = "".join(built().values())
    assert "tooluse_" not in dumped and "input_tokens" not in dumped and "latency" not in dumped


def test_a_changed_flag_fails_the_check():
    def flip(data):
        verdict = data["result"]["verdicts"][0]
        verdict["correct"] = not verdict["correct"]

    with pytest.raises(checks.CheckFailed, match="flags"):
        checks.check_flags(edit("cases/facet-01.json", flip))


# --- e. determinism --------------------------------------------------------------------------


def test_two_builds_into_two_directories_are_byte_identical(tmp_path: Path):
    one, two = tmp_path / "one", tmp_path / "two"
    checks.build_to(one)
    checks.build_to(two)
    names = sorted(p.relative_to(one).as_posix() for p in one.rglob("*") if p.is_file())
    assert names == sorted(p.relative_to(two).as_posix() for p in two.rglob("*") if p.is_file())
    assert len(names) == 1 + 1 + 4 + 29
    for name in names:
        assert (one / name).read_bytes() == (two / name).read_bytes(), name
    checks.check_determinism()


def test_files_are_canonical_json():
    for name, text in built().items():
        assert text.endswith("\n") and not text.endswith("\n\n")
        assert to_text(json.loads(text)) == text, name


# --- f. no emails or access keys -------------------------------------------------------------


def test_no_exported_string_looks_like_an_email_or_an_access_key():
    checks.check_secrets(built())
    for text in built().values():
        assert not checks.EMAIL.search(text)
        assert not checks.AWS_KEY.search(text)


@pytest.mark.parametrize("planted", ["write to someone@example.org", "AKIA" + "ABCDEFGH12345678"])
def test_the_secret_check_catches_a_planted_string(planted: str):
    def plant(data):
        data["description"] += " " + planted

    with pytest.raises(checks.CheckFailed, match="secrets"):
        checks.check_secrets(edit("cases/facet-01.json", plant))


# --- g. models forbid extra fields, schema.json matches --------------------------------------


def test_models_forbid_extra_fields_and_the_files_parse():
    for model in m.MODELS.values():
        assert model.model_config["extra"] == "forbid"
    assert m.Request.model_config["extra"] == "forbid"
    for cls in (m.Index, m.PolicyFile, m.CaseFile, m.Verdict, m.Citation, m.ChartChunk):
        assert cls.model_config["extra"] == "forbid"
    checks.check_models(built())
    with pytest.raises(ValidationError):
        m.Citation.model_validate(
            {"chunk_id": "a", "quote": "b", "char_start": 0, "char_end": 1, "located": True, "x": 1}
        )


def test_schema_json_matches_the_models():
    schema = json.loads(built()["schema.json"])
    assert sorted(schema) == ["CaseFile", "Index", "PolicyFile"]
    for name, model in m.MODELS.items():
        assert schema[name] == model.model_json_schema()
        assert schema[name]["additionalProperties"] is False


def test_an_extra_field_or_a_stale_schema_fails_the_check():
    with pytest.raises(checks.CheckFailed, match="models"):
        checks.check_models(edit("cases/facet-01.json", lambda d: d.update(extra=1)))
    stale = files()
    stale["schema.json"] = to_text({"CaseFile": {}})
    with pytest.raises(checks.CheckFailed, match="schema.json"):
        checks.check_models(stale)


def test_policy_logic_is_exactly_the_yaml_tree():
    for policy_id, policy in POLICIES.items():
        exported = json.loads(built()[f"policies/{policy_id}.json"])
        for variant, original in zip(exported["variants"], policy.variants, strict=True):
            assert variant["logic"] == original.logic.model_dump(exclude_none=True)
        assert [c["id"] for c in exported["criteria"]] == [c.id for c in policy.criteria]
        quotes = {c["id"]: c["quote"] for c in exported["criteria"]}
        assert quotes == {c.id: c.quote for c in policy.criteria}


def test_the_request_block_is_as_in_the_case_yaml():
    for case_id, eval_case in CASES.items():
        assert case(case_id)["request"] == eval_case.request.model_dump(
            mode="json", exclude_none=True
        )
    assert "leads" not in case("facet-01")["request"]


# --- h. sizes --------------------------------------------------------------------------------


def test_no_case_file_is_over_one_megabyte_and_the_report_lists_every_file(capsys):
    sizes = checks.sizes(built())
    assert len(sizes) == 35
    assert max(v for k, v in sizes.items() if k.startswith("cases/")) < 1_000_000
    checks.check_sizes(built())
    assert main(["--check"]) == 0
    printed = capsys.readouterr().out
    for name in sizes:
        assert name in printed
    assert f"{sum(sizes.values()):,}  total, 35 files" in printed


def test_an_oversized_case_file_fails_the_check():
    def pad(data):
        data["description"] = "x" * 1_100_000

    with pytest.raises(checks.CheckFailed, match="sizes"):
        checks.check_sizes(edit("cases/facet-01.json", pad))


# --- the command -----------------------------------------------------------------------------


def test_command_writes_the_files_to_out_and_default_out_is_ignored_by_git(tmp_path: Path):
    assert main(["--out", str(tmp_path / "data")]) == 0
    written = {
        p.relative_to(tmp_path / "data").as_posix(): p.read_text(encoding="utf-8")
        for p in (tmp_path / "data").rglob("*.json")
    }
    assert written == built()
    assert DEFAULT_OUT.relative_to(DEFAULT_OUT.parents[2]).as_posix() == "web/public/data"
    ignore = (DEFAULT_OUT.parents[2] / ".gitignore").read_text(encoding="utf-8")
    assert "/web/public/data/" in ignore.splitlines()


def test_a_rerun_removes_case_files_an_earlier_export_left_behind(tmp_path: Path):
    stale = tmp_path / "cases" / "gone.json"
    stale.parent.mkdir()
    stale.write_text("{}", encoding="utf-8")
    assert main(["--out", str(tmp_path)]) == 0
    assert not stale.exists()


def test_the_command_does_not_touch_the_database_or_network(monkeypatch):
    import socket

    def no_network(*args, **kwargs):
        raise AssertionError("the export opened a socket")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    assert build_files() == built()
