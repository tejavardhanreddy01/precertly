import asyncio
import io
import json
import threading
import time
from typing import Any

import pytest
from botocore.exceptions import ClientError

from precertly.llm import BedrockEmbedder
from precertly.settings import Settings

SETTINGS = Settings(_env_file=None, embedding_requests_per_minute=None)


def _throttle() -> ClientError:
    return ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow"}}, "InvokeModel")


class FakeTitan:
    """Stands in for bedrock-runtime invoke_model. Fails the first `failures` calls."""

    def __init__(self, *, failures: int = 0, error: ClientError | None = None, delay: float = 0):
        self.failures = failures
        self.error = error or _throttle()
        self.delay = delay
        self.requests: list[dict[str, Any]] = []
        self.in_flight = 0
        self.peak = 0
        self._lock = threading.Lock()

    def invoke_model(self, **request: Any) -> dict[str, Any]:
        with self._lock:
            self.requests.append(request)
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
            fail = len(self.requests) <= self.failures
        try:
            time.sleep(self.delay)
            if fail:
                raise self.error
            text = json.loads(request["body"])["inputText"]
            payload = {"embedding": [float(len(text))] * 1024, "inputTextTokenCount": len(text)}
            return {"body": io.BytesIO(json.dumps(payload).encode())}
        finally:
            with self._lock:
                self.in_flight -= 1


def _embedder(fake: FakeTitan, **options: Any) -> tuple[BedrockEmbedder, list[float]]:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return BedrockEmbedder(SETTINGS, client=fake, sleep=sleep, **options), sleeps


def test_embed_requests_1024_normalized_dims_and_records_usage():
    fake = FakeTitan()
    embedder, _ = _embedder(fake)
    vector = asyncio.run(embedder.embed("hello"))

    assert len(vector) == 1024
    (request,) = fake.requests
    assert request["modelId"] == "amazon.titan-embed-text-v2:0"
    assert json.loads(request["body"]) == {
        "inputText": "hello",
        "dimensions": 1024,
        "normalize": True,
    }
    (usage,) = embedder.calls
    assert (usage.input_tokens, usage.attempts) == (5, 1)


def test_throttling_is_retried_with_exponential_backoff():
    fake = FakeTitan(failures=3)
    embedder, sleeps = _embedder(fake, base_delay=1.0)
    asyncio.run(embedder.embed("hello"))

    assert len(fake.requests) == 4
    assert embedder.calls[0].attempts == 4
    # 1s, 2s, 4s, each with +/-50% jitter
    assert [0.5 <= s / d <= 1.5 for s, d in zip(sleeps, (1, 2, 4), strict=True)] == [True] * 3


def test_throttling_gives_up_after_max_attempts():
    fake = FakeTitan(failures=99)
    embedder, sleeps = _embedder(fake, max_attempts=3)
    with pytest.raises(ClientError):
        asyncio.run(embedder.embed("hello"))
    assert (len(fake.requests), len(sleeps)) == (3, 2)


def test_other_errors_are_not_retried():
    denied = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "InvokeModel"
    )
    fake = FakeTitan(failures=99, error=denied)
    embedder, sleeps = _embedder(fake)
    with pytest.raises(ClientError):
        asyncio.run(embedder.embed("hello"))
    assert (len(fake.requests), sleeps) == (1, [])


def test_embed_many_keeps_order_and_bounds_concurrency():
    fake = FakeTitan(delay=0.01)
    embedder, _ = _embedder(fake, concurrency=5)
    texts = ["x" * n for n in range(1, 31)]
    vectors = asyncio.run(embedder.embed_many(texts))

    assert [v[0] for v in vectors] == [float(n) for n in range(1, 31)]
    assert 1 < fake.peak <= 5


def test_requests_are_paced_to_the_per_minute_quota():
    fake = FakeTitan()
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    paced = Settings(_env_file=None, embedding_requests_per_minute=60)
    embedder = BedrockEmbedder(paced, client=fake, sleep=sleep)
    asyncio.run(embedder.embed_many(["a", "b", "c", "d"]))

    # first request goes at once; each later one is scheduled a second after the previous
    assert len(sleeps) == 3
    assert [round(s) for s in sorted(sleeps)] == [1, 2, 3]


def test_backoff_delay_is_capped():
    fake = FakeTitan(failures=6)
    embedder, sleeps = _embedder(fake, base_delay=1.0, max_delay=4.0)
    asyncio.run(embedder.embed("hello"))
    assert max(sleeps) <= 4.0 * 1.5
