"""The live API contract fixture the web tests read stays in step with the Pydantic models."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from precertly.live.models import MESSAGES, NOTICE, Reason, RunState, RunStatus

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_live_contract.py"


@pytest.fixture(scope="module")
def contract() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_live_contract", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_fixture_is_current(contract: ModuleType) -> None:
    # If this fails, a model changed: run `uv run python scripts/build_live_contract.py`, read the
    # diff, and make web/src/lib/live/guards.ts agree.
    assert contract.FIXTURE.read_text() == contract.render()
    assert contract.main(["--check"]) == 0


def test_check_fails_when_the_fixture_is_stale(
    contract: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = tmp_path / "contract.fixture.json"
    stale.write_text("{}\n")
    monkeypatch.setattr(contract, "FIXTURE", stale)
    assert contract.main(["--check"]) == 1


def test_every_status_and_reason_is_in_the_fixture(contract: ModuleType) -> None:
    data = json.loads(contract.render())
    assert data["enums"]["statuses"] == [s.value for s in RunStatus]
    assert data["enums"]["reasons"] == [r.value for r in Reason]
    assert data["notice"] == NOTICE
    assert data["messages"] == {r.value: text for r, text in MESSAGES.items()}
    states = {s["status"] for s in data["run_states"].values()} | {
        s["status"] for s in data["ended_states"]
    }
    assert states == {s.value for s in RunStatus}
    assert {s["reason"] for s in data["ended_states"]} == {r.value for r in Reason}


def test_each_state_validates_against_the_model_and_only_done_has_a_result(
    contract: ModuleType,
) -> None:
    data = json.loads(contract.render())
    for state in [*data["run_states"].values(), *data["ended_states"]]:
        parsed = RunState.model_validate(state)
        assert (parsed.result is not None) == (parsed.status is RunStatus.DONE)
        assert (parsed.message is not None) == (parsed.reason is not None)


def test_the_result_carries_no_label_or_score(contract: ModuleType) -> None:
    data = json.loads(contract.render())
    result = data["run_states"]["done"]["result"]

    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value) | {k for v in value.values() for k in keys(v)}
        if isinstance(value, list):
            return {k for v in value for k in keys(v)}
        return set()

    # Field names only: the notice itself says "no accuracy claim".
    for word in ("expected", "correct", "accuracy", "label", "score"):
        assert not any(word in key for key in keys(result))
    assert set(result) == {
        "case_id",
        "policy",
        "variant",
        "overall",
        "verdicts",
        "reviewer_checklist",
        "input_tokens",
        "output_tokens",
        "latency_ms",
        "notice",
    }
