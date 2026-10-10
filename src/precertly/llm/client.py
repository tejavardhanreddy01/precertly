"""Thin async client for the Bedrock Converse API.

Two calls: converse() for text and structured() for JSON validated against a Pydantic
model. Every Bedrock call records its tokens and latency in BedrockClient.calls so evals
can report cost and latency per case.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from pydantic import BaseModel, ValidationError

from precertly.settings import Settings, get_settings

Message = dict[str, Any]


class LLMUsage(BaseModel):
    model_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    stop_reason: str | None = None
    calls: int = 1
    # The model rejected toolChoice {"tool": ...}, so {"any": {}} was used instead.
    tool_choice_fallback: bool = False

    def __add__(self, other: LLMUsage) -> LLMUsage:
        return LLMUsage(
            model_id=other.model_id,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            latency_ms=self.latency_ms + other.latency_ms,
            stop_reason=other.stop_reason,
            calls=self.calls + other.calls,
            tool_choice_fallback=self.tool_choice_fallback or other.tool_choice_fallback,
        )


class LLMResponse(BaseModel):
    text: str
    usage: LLMUsage


class StructuredResponse[T: BaseModel](BaseModel):
    data: T
    usage: LLMUsage  # summed over retries


class StructuredOutputError(Exception):
    """The model did not return output matching the schema, even after a retry."""


def _rejects_tool_choice(error: ClientError) -> bool:
    detail = error.response.get("Error", {})
    message = detail.get("Message", "").lower().replace("_", "")
    return detail.get("Code") == "ValidationException" and "toolchoice" in message


def runtime_client(settings: Settings, *, max_attempts: int = 3) -> Any:
    """boto3 bedrock-runtime client for the configured profile and region."""
    session = boto3.Session(
        profile_name=settings.aws_profile or None, region_name=settings.aws_region
    )
    return session.client(
        "bedrock-runtime",
        config=Config(retries={"max_attempts": max_attempts, "mode": "standard"}, read_timeout=120),
    )


class BedrockClient:
    def __init__(self, settings: Settings | None = None, client: Any = None) -> None:
        self.settings = settings or get_settings()
        self._client = client
        self._named_tool_choice = True
        self.calls: list[LLMUsage] = []

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = runtime_client(self.settings)
        return self._client

    async def converse(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> LLMResponse:
        message, usage = await self._call(
            [{"role": "user", "content": [{"text": prompt}]}],
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = "".join(block["text"] for block in message["content"] if "text" in block)
        return LLMResponse(text=text, usage=usage)

    async def structured[T: BaseModel](
        self,
        prompt: str,
        schema: type[T],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        retries: int = 1,
    ) -> StructuredResponse[T]:
        """Ask for JSON matching schema, via a single tool the model is made to call.

        On invalid output the validation error is sent back and the model gets `retries`
        more attempts before StructuredOutputError is raised.
        """
        name = re.sub(r"[^a-zA-Z0-9_-]", "_", schema.__name__)[:64]
        spec = {
            "name": name,
            "description": (schema.__doc__ or "Return the answer as structured data.").strip(),
            "inputSchema": {"json": schema.model_json_schema()},
        }
        messages: list[Message] = [{"role": "user", "content": [{"text": prompt}]}]
        total: LLMUsage | None = None
        problem = "no attempt made"

        for _ in range(retries + 1):
            message, usage = await self._call_tool(
                messages, spec, system=system, max_tokens=max_tokens, temperature=temperature
            )
            total = usage if total is None else total + usage
            tool_use = next((b["toolUse"] for b in message["content"] if "toolUse" in b), None)
            if tool_use is None:
                problem = "the model answered without calling the tool"
                feedback: dict[str, Any] = {"text": f"Call the {name} tool with your answer."}
            else:
                try:
                    return StructuredResponse[schema](
                        data=schema.model_validate(tool_use["input"]), usage=total
                    )
                except ValidationError as error:
                    problem = str(error)
                    feedback = {
                        "toolResult": {
                            "toolUseId": tool_use["toolUseId"],
                            "content": [{"text": f"Invalid input, fix and call again:\n{error}"}],
                            "status": "error",
                        }
                    }
            messages += [message, {"role": "user", "content": [feedback]}]

        raise StructuredOutputError(f"{schema.__name__}: {problem}")

    async def _call_tool(
        self, messages: list[Message], spec: dict[str, Any], **options: Any
    ) -> tuple[Message, LLMUsage]:
        tools = [{"toolSpec": spec}]
        if self._named_tool_choice:
            forced = {"tools": tools, "toolChoice": {"tool": {"name": spec["name"]}}}
            try:
                return await self._call(messages, tool_config=forced, **options)
            except ClientError as error:
                if not _rejects_tool_choice(error):
                    raise
                # Remember for later calls. With one tool, "any" forces the same tool.
                self._named_tool_choice = False
        relaxed = {"tools": tools, "toolChoice": {"any": {}}}
        return await self._call(messages, tool_config=relaxed, fallback=True, **options)

    async def _call(
        self,
        messages: list[Message],
        *,
        system: str | None,
        max_tokens: int,
        temperature: float,
        tool_config: dict[str, Any] | None = None,
        fallback: bool = False,
    ) -> tuple[Message, LLMUsage]:
        request: dict[str, Any] = {
            "modelId": self.settings.bedrock_model_id,
            "messages": messages,
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
        }
        if system:
            request["system"] = [{"text": system}]
        if tool_config:
            request["toolConfig"] = tool_config

        started = time.perf_counter()
        # boto3 is synchronous; keep the event loop free.
        response = await asyncio.to_thread(self.client.converse, **request)
        latency_ms = (time.perf_counter() - started) * 1000

        tokens = response.get("usage", {})
        usage = LLMUsage(
            model_id=self.settings.bedrock_model_id,
            input_tokens=tokens.get("inputTokens", 0),
            output_tokens=tokens.get("outputTokens", 0),
            latency_ms=latency_ms,
            stop_reason=response.get("stopReason"),
            tool_choice_fallback=fallback,
        )
        self.calls.append(usage)
        return response["output"]["message"], usage
