"""One live run from inside the live task, for `scripts/deploy.sh live smoke --run CASE_ID`.

Runs in the container over ECS Exec, so it uses only the standard library and talks to the API
on localhost, past the gateway: it sends the x-precertly-actor header itself, with the fixed
value `deploy-smoke` (the gateway is what sets that header for real users).

It starts a run for the curated case, polls until the run is done, failed or rejected (or the
time limit passes) and prints ONE line:

    LIVE_RUN status=<status> reason=<reason or -> overall=<outcome or -> tokens=<in>/<out or ->
             timings=<status>:<seconds>,...

Never the result body: no verdicts, quotes or rationale leave the container. Every value that is
printed is checked against a narrow pattern first, so nothing the server sends can add a line.
Exit status 0 only if the run finished with status done.

    python scripts/live_smoke_run.py facet-01
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

BASE_URL = "http://127.0.0.1:8000"
ACTOR = "deploy-smoke"
TIME_LIMIT_SECONDS = 300.0
POLL_SECONDS = 3.0
REQUEST_TIMEOUT_SECONDS = 20.0
FINISHED = frozenset({"done", "failed", "rejected"})
TIMING_ORDER = ("waking_database", "checking_limits", "ingesting", "judging")
CASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
WORD = re.compile(r"[a-z0-9_]{1,40}")


def _word(value: object) -> str:
    """A short lower-case word, or '?' (never text from the server as it is)."""
    return value if isinstance(value, str) and WORD.fullmatch(value) else "?"


def _request(
    base: str, method: str, path: str, body: dict[str, Any] | None
) -> tuple[int, dict[str, Any] | None]:
    """(status, JSON object or None). A network failure is status 0."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(  # noqa: S310 - the URL is the local API
        base + path,
        data=data,
        method=method,
        headers={"x-precertly-actor": ACTOR, "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, b""
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, None
    try:
        parsed = json.loads(raw) if raw else None
    except ValueError:
        parsed = None
    return status, parsed if isinstance(parsed, dict) else None


def format_line(state: dict[str, Any] | None, *, status: str, reason: str = "-") -> str:
    overall, tokens, timings = "-", "-", "-"
    if state is not None:
        found = state.get("reason")
        if found is not None:
            reason = _word(found)
        result = state.get("result")
        if isinstance(result, dict):
            overall = _word(result.get("overall"))
            used_in, used_out = result.get("input_tokens"), result.get("output_tokens")
            if isinstance(used_in, int) and isinstance(used_out, int):
                tokens = f"{used_in}/{used_out}"
        seen = state.get("timings")
        if isinstance(seen, dict):
            parts = [
                f"{key}:{float(seen[key]):.1f}"
                for key in TIMING_ORDER
                if isinstance(seen.get(key), (int, float))
            ]
            timings = ",".join(parts) or "-"
    return (
        f"LIVE_RUN status={status} reason={reason} overall={overall} "
        f"tokens={tokens} timings={timings}"
    )


def run(
    case_id: str,
    *,
    base: str = BASE_URL,
    time_limit: float = TIME_LIMIT_SECONDS,
    poll: float = POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[bool, str]:
    """(finished with status done, the one line)."""
    code, started = _request(base, "POST", "/live/runs", {"case_id": case_id})
    if code != 202 or started is None or not isinstance(started.get("run_id"), str):
        label = "unreachable" if code == 0 else f"start_http_{code}"
        return False, format_line(None, status=label)
    run_id = started["run_id"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id):
        return False, format_line(None, status="start_bad_run_id")

    deadline = clock() + time_limit
    last = "-"
    while True:
        code, state = _request(base, "GET", f"/live/runs/{run_id}", None)
        if code == 200 and state is not None:
            status = _word(state.get("status"))
            last = status
            if status in FINISHED:
                return status == "done", format_line(state, status=status)
        elif code != 0:
            return False, format_line(None, status=f"poll_http_{code}")
        if clock() >= deadline:
            return False, format_line(None, status="timeout", reason=f"still_{last}")
        sleep(poll)


def main(argv: Sequence[str]) -> int:
    if len(argv) != 2 or not CASE_ID.fullmatch(argv[1]):
        print("usage: live_smoke_run.py CASE_ID", file=sys.stderr)
        return 2
    ok, line = run(argv[1])
    print(line, flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
