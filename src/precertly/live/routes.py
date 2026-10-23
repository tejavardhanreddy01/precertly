"""POST /live/runs and GET /live/runs/{run_id}. Registered only when live mode is enabled.

The gateway in front of the service verifies the user's token and puts the user id in the
x-precertly-actor header; the service accepts requests only from the gateway, so this code
trusts that header and nothing else. A missing or malformed header is a 401 on every route.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException

from precertly.evals.cases import EvalCase, load_cases
from precertly.live.models import (
    ACTOR_HEADER,
    ACTOR_PATTERN,
    RunState,
    Started,
    StartRequest,
)
from precertly.live.registry import RunActive
from precertly.live.runner import LiveDeps, LiveRunner, ProductionDeps, utc_now
from precertly.policies import Policy, load_policies
from precertly.settings import Settings

logger = logging.getLogger(__name__)


def require_actor(
    actor: Annotated[str | None, Header(alias=ACTOR_HEADER)] = None,
) -> str:
    """The caller, from the gateway's header. Fails closed."""
    if actor is None or ACTOR_PATTERN.fullmatch(actor) is None:
        # Never the value: it is whatever the caller sent.
        logger.warning("live request without a valid actor header")
        raise HTTPException(status_code=401, detail="unauthorized")
    return actor


def build_router(runner: LiveRunner) -> APIRouter:
    router = APIRouter(prefix="/live", dependencies=[Depends(require_actor)])

    @router.post("/runs", status_code=202, response_model=Started)
    async def start_run(
        body: StartRequest, actor: Annotated[str, Depends(require_actor)]
    ) -> Started:
        if body.case_id not in runner.cases:
            raise HTTPException(status_code=404, detail="not found")
        try:
            record = runner.start(actor, body.case_id)
        except RunActive:
            raise HTTPException(
                status_code=409, detail="a live run is already in progress"
            ) from None
        return Started(run_id=record.id, status=record.status)

    @router.get("/runs/{run_id}", response_model=RunState)
    async def get_run(run_id: str, actor: Annotated[str, Depends(require_actor)]) -> RunState:
        record = runner.registry.get(run_id)
        # The same answer for an unknown run and for someone else's.
        if record is None or record.actor != actor:
            raise HTTPException(status_code=404, detail="not found")
        return record.state()

    return router


@dataclass(frozen=True)
class LiveMountOptions:
    """Replacements for the production wiring, for tests. Anything left out is the real one."""

    cases: Mapping[str, EvalCase] | None = None
    policies: Mapping[str, Policy] | None = None
    make_deps: Callable[[], LiveDeps] | None = None
    clock: Callable[[], datetime] | None = None
    monotonic: Callable[[], float] | None = None


def mount_live(
    app: FastAPI, settings: Settings, options: LiveMountOptions | None = None
) -> LiveRunner:
    """Add the live routes to the app. Reads the case and policy files now, so a missing file
    stops the start instead of failing a run; builds no database or AWS client until the
    first run."""
    options = options or LiveMountOptions()
    runner = LiveRunner(
        settings,
        options.cases if options.cases is not None else load_cases(),
        options.policies if options.policies is not None else load_policies(),
        options.make_deps or ProductionDeps(settings),
        clock=options.clock or utc_now,
        monotonic=options.monotonic or time.monotonic,
    )
    app.state.live = runner
    app.include_router(build_router(runner))
    return runner
