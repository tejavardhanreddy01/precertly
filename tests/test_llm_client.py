import asyncio
from typing import Any, Literal

import pytest
from botocore.exceptions import ClientError
from pydantic import BaseModel

from precertly.llm import BedrockClient, StructuredOutputError
from precertly.settings import Settings

SETTINGS = Settings(_env_file=None)


class Verdict(BaseModel):
    """Verdict for one criterion."""

    outcome: Literal["met", "not_met", "insufficient"]
    reason: str


class FakeBedrock:
    """Stands in for the boto3 bedrock-runtime client: replays queued responses."""

    def __init__(self, *responses: dict[str, Any] | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def converse(self, **request: Any) -> dict[str, Any]:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _response(*content: dict[str, Any], stop: str = "end_turn") -> dict[str, Any]:
    return {
        "output": {"message": {"role": "assistant", "content": list(content)}},
        "stopReason": stop,
        "usage": {"inputTokens": 40, "outputTokens": 12, "totalTokens": 52},
    }


def _tool_use(**tool_input: Any) -> dict[str, Any]:
    block = {"toolUse": {"toolUseId": "t1", "name": "Verdict", "input": tool_input}}
    return _response(block, stop="tool_use")


def _client(*responses: dict[str, Any] | Exception) -> tuple[BedrockClient, FakeBedrock]:
    fake = FakeBedrock(*responses)
    return BedrockClient(SETTINGS, client=fake), fake


def test_settings_defaults_and_env_override(monkeypatch):
    assert SETTINGS.bedrock_model_id == "us.amazon.nova-2-lite-v1:0"
    assert SETTINGS.aws_region == "us-east-2"
    assert SETTINGS.aws_profile == "precertly"
    monkeypatch.setenv("PRECERTLY_BEDROCK_MODEL_ID", "some.other-model-v1:0")
    monkeypatch.setenv("PRECERTLY_AWS_PROFILE", "")
    overridden = Settings(_env_file=None)
    assert overridden.bedrock_model_id == "some.other-model-v1:0"
    assert not overridden.aws_profile


def test_converse_builds_request_and_records_usage():
    client, fake = _client(_response({"text": "hello"}))
    result = asyncio.run(client.converse("hi", system="be brief", max_tokens=50))

    assert result.text == "hello"
    assert fake.requests == [
        {
            "modelId": "us.amazon.nova-2-lite-v1:0",
            "messages": [{"role": "user", "content": [{"text": "hi"}]}],
            "inferenceConfig": {"maxTokens": 50, "temperature": 0.0},
            "system": [{"text": "be brief"}],
        }
    ]
    usage = result.usage
    assert (usage.input_tokens, usage.output_tokens, usage.stop_reason) == (40, 12, "end_turn")
    assert usage.latency_ms >= 0
    assert usage.model_id == "us.amazon.nova-2-lite-v1:0"
    assert client.calls == [usage]


def test_structured_forces_the_tool_and_validates():
    client, fake = _client(_tool_use(outcome="met", reason="BMI 36.2"))
    result = asyncio.run(client.structured("judge", Verdict))

    assert result.data == Verdict(outcome="met", reason="BMI 36.2")
    assert result.usage.calls == 1
    assert not result.usage.tool_choice_fallback
    config = fake.requests[0]["toolConfig"]
    assert config["toolChoice"] == {"tool": {"name": "Verdict"}}
    spec = config["tools"][0]["toolSpec"]
    assert spec["description"] == "Verdict for one criterion."
    assert spec["inputSchema"]["json"] == Verdict.model_json_schema()


def test_structured_retries_once_with_the_validation_error():
    client, fake = _client(
        _tool_use(outcome="denied", reason="x"), _tool_use(outcome="not_met", reason="AHI 3")
    )
    result = asyncio.run(client.structured("judge", Verdict))

    assert result.data.outcome == "not_met"
    assert (result.usage.calls, result.usage.input_tokens, result.usage.output_tokens) == (
        2,
        80,
        24,
    )
    assert len(client.calls) == 2
    assistant, feedback = fake.requests[1]["messages"][1:]
    assert assistant["role"] == "assistant"
    tool_result = feedback["content"][0]["toolResult"]
    assert (tool_result["toolUseId"], tool_result["status"]) == ("t1", "error")
    assert "outcome" in tool_result["content"][0]["text"]


def test_structured_raises_after_retries():
    client, _ = _client(_tool_use(outcome="denied", reason="x"), _response({"text": "sorry"}))
    with pytest.raises(StructuredOutputError, match="without calling the tool"):
        asyncio.run(client.structured("judge", Verdict))
    assert len(client.calls) == 2


def _error(code: str, message: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "Converse")


def test_structured_falls_back_to_any_when_named_tool_choice_is_rejected():
    rejected = _error("ValidationException", "This model doesn't support toolChoice.tool.")
    client, fake = _client(
        rejected,
        _tool_use(outcome="met", reason="a"),
        _tool_use(outcome="met", reason="b"),
    )
    first = asyncio.run(client.structured("judge", Verdict))
    second = asyncio.run(client.structured("judge", Verdict))

    assert first.usage.tool_choice_fallback and second.usage.tool_choice_fallback
    choices = [r["toolConfig"]["toolChoice"] for r in fake.requests]
    # the rejection is remembered, so the second call goes straight to "any"
    assert choices == [{"tool": {"name": "Verdict"}}, {"any": {}}, {"any": {}}]
    assert len(client.calls) == 2  # the rejected request used no tokens


def test_other_client_errors_are_not_swallowed():
    client, fake = _client(_error("AccessDeniedException", "no access to model"))
    with pytest.raises(ClientError):
        asyncio.run(client.structured("judge", Verdict))
    assert len(fake.requests) == 1
