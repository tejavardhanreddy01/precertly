"""Types of the live run API: the request, the run's state, and the result.

The result is its own model, not the export models: it carries no expected labels, correct
flags or scores, because a label shown next to a live verdict would be an accuracy claim.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from precertly.judgment.models import Outcome

NOTICE = "Single unregistered run, not part of the eval, no accuracy claim. Results are not stored."

ACTOR_HEADER = "x-precertly-actor"
ACTOR_MAX_LENGTH = 128
# Letters, digits, hyphen, underscore, dot, plus, at-sign and colon: enough for a Cognito
# subject or an email address, and nothing that could break a log line or a query.
ACTOR_PATTERN = re.compile(r"[A-Za-z0-9_.+@:-]{1,128}")


class RunStatus(StrEnum):
    WAKING_DATABASE = "waking_database"
    CHECKING_LIMITS = "checking_limits"
    INGESTING = "ingesting"
    JUDGING = "judging"
    DONE = "done"
    FAILED = "failed"
    REJECTED = "rejected"

    @property
    def finished(self) -> bool:
        return self in FINISHED


FINISHED = frozenset({RunStatus.DONE, RunStatus.FAILED, RunStatus.REJECTED})


class Reason(StrEnum):
    """Why a run failed or was rejected. The only text a user sees comes from MESSAGES."""

    DATABASE_UNAVAILABLE = "database_unavailable"
    RUN_IN_PROGRESS = "run_in_progress"
    DAILY_CAP_TOTAL = "daily_cap_total"
    DAILY_CAP_USER = "daily_cap_user"
    DAILY_TOKENS = "daily_tokens"
    EMBEDDING_FAILED = "embedding_failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    INTERNAL_ERROR = "internal_error"


# Fixed texts. An exception's own text is never shown: it could carry a host or a statement.
MESSAGES: dict[Reason, str] = {
    Reason.DATABASE_UNAVAILABLE: "The database did not become available in time. Try again.",
    Reason.RUN_IN_PROGRESS: "Another live run is in progress. Try again when it has finished.",
    Reason.DAILY_CAP_TOTAL: "The daily limit of live runs for everyone has been reached.",
    Reason.DAILY_CAP_USER: "Your daily limit of live runs has been reached.",
    Reason.DAILY_TOKENS: "The daily limit of model tokens for live runs has been reached.",
    Reason.EMBEDDING_FAILED: "The chart could not be embedded, so the run stopped.",
    Reason.TIMEOUT: "The run took too long and was stopped.",
    Reason.CANCELLED: "The run was cancelled.",
    Reason.INTERNAL_ERROR: "The run failed.",
}


class StartRequest(BaseModel):
    """The only input: which curated case to run. Nothing else is accepted."""

    model_config = ConfigDict(extra="forbid")

    case_id: str


class Started(BaseModel):
    run_id: str
    status: RunStatus


class LiveCitation(BaseModel):
    # 'Type/id#n' for a chart chunk (the id the exported chart chunks use), 'request.<field>'
    # for a field of the request.
    chunk_id: str
    quote: str
    char_start: int
    char_end: int


class LiveVerdict(BaseModel):
    criterion: str
    outcome: Literal["met", "not_met", "insufficient"]
    applicable: bool
    decided_by: Literal["model", "rule", "rule+model"]
    confidence: float
    rationale: str
    citations: list[LiveCitation]


class LiveChecklistItem(BaseModel):
    criterion: str
    evidence_stage: str
    text: str


class LiveResult(BaseModel):
    case_id: str
    policy: str
    variant: str
    overall: Outcome
    verdicts: list[LiveVerdict]
    reviewer_checklist: list[LiveChecklistItem]
    input_tokens: int
    output_tokens: int
    latency_ms: float
    notice: str = NOTICE


class RunState(BaseModel):
    run_id: str
    case_id: str
    status: RunStatus
    reason: Reason | None = None
    message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    # Seconds spent in each status, for the statuses the run has left.
    timings: dict[str, float]
    result: LiveResult | None = None
