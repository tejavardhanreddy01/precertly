"""One real Bedrock call to confirm credentials, region and model access.

uv run python scripts/smoke_llm.py

Costs a fraction of a cent. Uses PRECERTLY_* settings (default profile "precertly").
"""

from __future__ import annotations

import asyncio
import sys
from typing import Literal

from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel

from precertly.llm import BedrockClient
from precertly.settings import get_settings


class SmokeCheck(BaseModel):
    """Result of comparing a BMI value to a threshold."""

    bmi: float
    threshold: float
    outcome: Literal["met", "not_met"]


async def main() -> int:
    settings = get_settings()
    print(
        f"model={settings.bedrock_model_id} region={settings.aws_region} "
        f"profile={settings.aws_profile or '(default chain)'}"
    )
    try:
        result = await BedrockClient(settings).structured(
            "A synthetic patient has a BMI of 36.2. The criterion requires BMI of at least 35. "
            "Report the BMI, the threshold and whether the criterion is met.",
            SmokeCheck,
            max_tokens=200,
        )
    except (BotoCoreError, ClientError) as error:
        print(f"Bedrock call failed: {error}", file=sys.stderr)
        print(
            "Check `aws sso login --profile <profile>` (or your credentials), that the model "
            "is enabled in this region, and that PRECERTLY_BEDROCK_MODEL_ID is an inference "
            "profile id. Credentials from `aws login` need botocore[crt] (in the dev group).",
            file=sys.stderr,
        )
        return 1

    usage = result.usage
    print(result.data.model_dump_json(indent=2))
    print(
        f"tokens in/out: {usage.input_tokens}/{usage.output_tokens}  "
        f"latency: {usage.latency_ms:.0f} ms  calls: {usage.calls}  "
        f"tool_choice_fallback: {usage.tool_choice_fallback}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
