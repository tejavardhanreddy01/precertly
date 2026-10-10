"""Titan Text Embeddings V2 on Bedrock, with bounded concurrency and throttle backoff."""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from botocore.exceptions import ClientError
from pydantic import BaseModel

from precertly.llm.client import runtime_client
from precertly.settings import Settings, get_settings


class EmbeddingUsage(BaseModel):
    model_id: str
    input_tokens: int = 0
    latency_ms: float = 0.0  # of the successful attempt
    attempts: int = 1


class BedrockEmbedder:
    def __init__(
        self,
        settings: Settings | None = None,
        client: Any = None,
        *,
        concurrency: int = 5,
        max_attempts: int = 6,
        base_delay: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client
        self.concurrency = concurrency
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self._sleep = sleep
        self.calls: list[EmbeddingUsage] = []

    @property
    def client(self) -> Any:
        if self._client is None:
            # botocore retries are off so the backoff below is the only retry policy.
            self._client = runtime_client(self.settings, max_attempts=1)
        return self._client

    async def embed(self, text: str) -> list[float]:
        body = json.dumps(
            {
                "inputText": text,
                "dimensions": self.settings.embedding_dimensions,
                "normalize": True,
            }
        )
        for attempt in range(1, self.max_attempts + 1):
            started = time.perf_counter()
            try:
                response = await asyncio.to_thread(
                    self.client.invoke_model,
                    modelId=self.settings.embedding_model_id,
                    body=body,
                    contentType="application/json",
                    accept="application/json",
                )
            except ClientError as error:
                throttled = error.response.get("Error", {}).get("Code") == "ThrottlingException"
                if not throttled or attempt == self.max_attempts:
                    raise
                # 0.5s, 1s, 2s, ... with jitter so concurrent workers do not retry in step.
                await self._sleep(self.base_delay * 2 ** (attempt - 1) * random.uniform(0.5, 1.5))
                continue
            payload = json.loads(response["body"].read())
            self.calls.append(
                EmbeddingUsage(
                    model_id=self.settings.embedding_model_id,
                    input_tokens=payload.get("inputTextTokenCount", 0),
                    latency_ms=(time.perf_counter() - started) * 1000,
                    attempts=attempt,
                )
            )
            return payload["embedding"]
        raise AssertionError("unreachable")  # the loop returns or raises

    async def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        """Embeddings in input order, at most `concurrency` requests in flight."""
        gate = asyncio.Semaphore(self.concurrency)

        async def one(text: str) -> list[float]:
            async with gate:
                return await self.embed(text)

        return list(await asyncio.gather(*(one(text) for text in texts)))
