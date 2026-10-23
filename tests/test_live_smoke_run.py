"""scripts/live_smoke_run.py against a small local server standing in for the live API."""

from __future__ import annotations

import importlib.util
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "live_smoke_run.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("live_smoke_run", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = _load()

DONE_STATE: dict[str, Any] = {
    "run_id": "run-1",
    "case_id": "facet-01",
    "status": "done",
    "reason": None,
    "message": None,
    "timings": {
        "waking_database": 12.34,
        "checking_limits": 0.1,
        "ingesting": 5.0,
        "judging": 40.04,
    },
    "result": {
        "case_id": "facet-01",
        "overall": "met",
        "input_tokens": 1000,
        "output_tokens": 234,
        "verdicts": [{"rationale": "SECRET RATIONALE", "citations": [{"quote": "SECRET QUOTE"}]}],
    },
}


class Api:
    """Scripted answers: `start` is (status, body); `polls` are returned in order, the last
    one repeating."""

    def __init__(self, start: tuple[int, Any], polls: list[tuple[int, Any]]) -> None:
        self.start = start
        self.polls = polls
        # (method, path, headers with lower-case names, body)
        self.requests: list[tuple[str, str, dict[str, str], bytes]] = []
        self.poll_count = 0


@pytest.fixture
def serve() -> Iterator[Any]:
    servers: list[HTTPServer] = []

    def start(api: Api) -> str:
        class Handler(BaseHTTPRequestHandler):
            def _headers(self) -> dict[str, str]:
                return {k.lower(): v for k, v in self.headers.items()}

            def _answer(self, status: int, body: Any) -> None:
                payload = b"" if body is None else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self) -> None:
                length = int(self.headers.get("content-length", "0"))
                api.requests.append(("POST", self.path, self._headers(), self.rfile.read(length)))
                self._answer(*api.start)

            def do_GET(self) -> None:
                api.requests.append(("GET", self.path, self._headers(), b""))
                index = min(api.poll_count, len(api.polls) - 1)
                api.poll_count += 1
                self._answer(*api.polls[index])

            def log_message(self, format: str, *args: object) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _run(base: str, **overrides: Any) -> tuple[bool, str]:
    return smoke.run("facet-01", base=base, poll=0, sleep=lambda _: None, **overrides)


def test_a_finished_run_prints_one_summary_line_and_no_result_body(serve: Any) -> None:
    api = Api((202, {"run_id": "run-1", "status": "waking_database"}), [(200, DONE_STATE)])
    ok, line = _run(serve(api))
    assert ok
    assert line == (
        "LIVE_RUN status=done reason=- overall=met tokens=1000/234 "
        "timings=waking_database:12.3,checking_limits:0.1,ingesting:5.0,judging:40.0"
    )
    assert "\n" not in line
    assert "SECRET" not in line


def test_it_posts_the_case_with_the_fixed_actor_and_polls_that_run(serve: Any) -> None:
    api = Api((202, {"run_id": "run-1", "status": "waking_database"}), [(200, DONE_STATE)])
    _run(serve(api))
    (post, get) = api.requests
    assert post[0:2] == ("POST", "/live/runs")
    assert json.loads(post[3]) == {"case_id": "facet-01"}
    assert post[2]["x-precertly-actor"] == "deploy-smoke"
    assert get[0:2] == ("GET", "/live/runs/run-1")
    assert get[2]["x-precertly-actor"] == "deploy-smoke"


def test_it_polls_until_the_run_is_finished(serve: Any) -> None:
    running = {**DONE_STATE, "status": "judging", "result": None}
    api = Api(
        (202, {"run_id": "run-1", "status": "waking_database"}),
        [(200, running), (200, running), (200, DONE_STATE)],
    )
    ok, line = _run(serve(api))
    assert ok
    assert api.poll_count == 3
    assert line.startswith("LIVE_RUN status=done")


def test_a_failed_run_reports_its_reason_and_is_not_ok(serve: Any) -> None:
    failed = {
        **DONE_STATE,
        "status": "failed",
        "reason": "embedding_failed",
        "message": "The chart could not be embedded, so the run stopped.",
        "result": None,
        "timings": {"waking_database": 3.0, "checking_limits": 0.2, "ingesting": 1.5},
    }
    api = Api((202, {"run_id": "run-1", "status": "waking_database"}), [(200, failed)])
    ok, line = _run(serve(api))
    assert not ok
    assert line == (
        "LIVE_RUN status=failed reason=embedding_failed overall=- tokens=- "
        "timings=waking_database:3.0,checking_limits:0.2,ingesting:1.5"
    )


def test_a_rejected_run_is_not_ok(serve: Any) -> None:
    rejected = {**DONE_STATE, "status": "rejected", "reason": "daily_cap_user", "result": None}
    api = Api((202, {"run_id": "run-1", "status": "waking_database"}), [(200, rejected)])
    ok, line = _run(serve(api))
    assert not ok
    assert "status=rejected reason=daily_cap_user" in line


@pytest.mark.parametrize("code", [401, 404, 409, 422, 500])
def test_a_refused_start_is_reported_by_its_status(serve: Any, code: int) -> None:
    api = Api((code, {"detail": "nope"}), [(200, DONE_STATE)])
    ok, line = _run(serve(api))
    assert not ok
    assert line.startswith(f"LIVE_RUN status=start_http_{code} ")
    assert api.poll_count == 0


def test_an_unreachable_api_is_reported() -> None:
    ok, line = smoke.run("facet-01", base="http://127.0.0.1:9", poll=0, sleep=lambda _: None)
    assert not ok
    assert line.startswith("LIVE_RUN status=unreachable ")


def test_a_poll_error_ends_the_run_with_its_status(serve: Any) -> None:
    api = Api((202, {"run_id": "run-1", "status": "waking_database"}), [(404, {"detail": "x"})])
    ok, line = _run(serve(api))
    assert not ok
    assert line.startswith("LIVE_RUN status=poll_http_404 ")


def test_the_time_limit_ends_the_wait_and_names_the_last_status(serve: Any) -> None:
    running = {**DONE_STATE, "status": "judging", "result": None}
    api = Api((202, {"run_id": "run-1", "status": "waking_database"}), [(200, running)])
    ticks = iter(range(0, 10_000))
    ok, line = smoke.run(
        "facet-01",
        base=serve(api),
        time_limit=5,
        poll=0,
        sleep=lambda _: None,
        clock=lambda: float(next(ticks)),
    )
    assert not ok
    assert line.startswith("LIVE_RUN status=timeout reason=still_judging ")


def test_text_from_the_server_cannot_add_lines_or_free_text(serve: Any) -> None:
    hostile = {
        **DONE_STATE,
        "status": "failed",
        "reason": "x\nLIVE_RUN status=done",
        "result": {"overall": "met\nPASS", "input_tokens": "1", "output_tokens": 2},
    }
    api = Api((202, {"run_id": "run-1", "status": "waking_database"}), [(200, hostile)])
    ok, line = _run(serve(api))
    assert not ok
    assert "\n" not in line
    assert "reason=?" in line
    assert "overall=?" in line
    assert "tokens=-" in line


def test_a_hostile_run_id_is_not_followed(serve: Any) -> None:
    api = Api((202, {"run_id": "../../health", "status": "waking_database"}), [(200, DONE_STATE)])
    ok, line = _run(serve(api))
    assert not ok
    assert line.startswith("LIVE_RUN status=start_bad_run_id ")
    assert api.poll_count == 0


@pytest.mark.parametrize("argv", [["x"], ["x", ""], ["x", "a b"], ["x", "a/b"], ["x", "a", "b"]])
def test_main_refuses_a_bad_case_id(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert smoke.main(argv) == 2
    assert capsys.readouterr().out == ""


def test_the_script_uses_only_the_standard_library() -> None:
    import ast

    tree = ast.parse(SCRIPT.read_text())
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
    import sys

    assert roots <= set(sys.stdlib_module_names)
