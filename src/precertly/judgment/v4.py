"""The model's answer under prompt version 4 (line-level evidence handles, experimental).

Prompt version 4 is opt-in (PRECERTLY_PROMPT_VERSION=4 or --prompt-version 4); version 3 is
the default. The classes keep the names Citation and ModelJudgment, because the Bedrock
client names the forced tool after the schema class and the schema's definitions after the
nested class, so a version 4 run sends exactly the tool that experiment 2 sent.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    """What the model returns: a line handle from the prompt and the first words of that
    line. The quoted text is extracted from the chunk by the verifier, never copied."""

    evidence: str = Field(
        description=(
            "Line handle, for example E3.2, or a range of up to 3 adjacent lines of one "
            "item, for example E3.2-E3.4"
        )
    )
    first_words: str = Field(
        description=(
            "The first three words of the first cited line, as they appear after its "
            "handle (all of its words if it has fewer)"
        )
    )


class ModelJudgment(BaseModel):
    """Judgment of one coverage criterion against the evidence provided."""

    condition_holds: bool | None = Field(
        default=None,
        description=(
            "Only for a criterion that has a condition: whether the condition holds in "
            "this case. Null when the criterion has no condition."
        ),
    )
    outcome: Literal["met", "not_met", "insufficient"]
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(description="One to three sentences")
    citations: list[Citation] = Field(
        default_factory=list, description="The evidence the outcome rests on"
    )
