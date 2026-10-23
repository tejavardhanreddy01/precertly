"""The live API without a database: switch, actor header, case ids, single flight, secrets."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from precertly.api.main import create_app
from precertly.evals import load_cases
from precertly.live import LiveDeps, LiveMountOptions
from precertly.live.registry import KEEP_RUNS, RunActive, RunRegistry
from precertly.settings import Settings

ACTOR = {"x-precertly-actor": "user-1"}
ROOT = Path(__file__).resolve().parents[1]


def app_with(live: bool = True, make_deps=None, **settings):
    config = Settings(_env_file=None, live_enabled=live, **settings)
    return create_app(config, LiveMountOptions(make_deps=make_deps))


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def broken_deps():
    raise RuntimeError("no deps in this test")


def test_there_are_29_curated_cases():
    assert len(load_cases()) == 29


def test_live_is_off_by_default():
    assert Settings(_env_file=None).live_enabled is False


def test_live_setting_defaults():
    s = Settings(_env_file=None)
    assert (s.live_max_runs_per_day, s.live_max_runs_per_user) == (10, 3)
    assert s.live_max_tokens_per_day == 2_000_000
    assert s.live_run_timeout_seconds == 600


@pytest.mark.parametrize("name", ["max_runs_per_day", "max_runs_per_user", "max_tokens_per_day"])
@pytest.mark.parametrize("value", ["0", "-1", "ten", "1.5", ""])
def test_live_limits_must_be_positive_integers(monkeypatch, name, value):
    monkeypatch.setenv(f"PRECERTLY_LIVE_{name.upper()}", value)
    with pytest.raises(ValidationError, match=f"PRECERTLY_LIVE_{name.upper()} must be a positive"):
        Settings(_env_file=None)


@pytest.mark.parametrize("value", ["0", "-5", "soon", "nan", "inf"])
def test_live_timeout_must_be_positive(monkeypatch, value):
    monkeypatch.setenv("PRECERTLY_LIVE_RUN_TIMEOUT_SECONDS", value)
    with pytest.raises(ValidationError, match="LIVE_RUN_TIMEOUT_SECONDS must be a positive"):
        Settings(_env_file=None)


def test_live_disabled_has_no_live_routes_and_health_works():
    async def scenario():
        async with client_for(app_with(live=False)) as client:
            assert (
                await client.post("/live/runs", json={"case_id": "facet-01"})
            ).status_code == 404
            assert (await client.post("/live/runs", json={}, headers=ACTOR)).status_code == 404
            assert (await client.get("/live/runs/x", headers=ACTOR)).status_code == 404
            health = await client.get("/health")
            assert health.status_code == 200 and health.json()["status"] == "ok"

    asyncio.run(scenario())


def test_module_app_has_no_live_routes_by_default():
    from precertly.api.main import app

    assert not any(getattr(r, "path", "").startswith("/live") for r in app.routes)


def test_health_makes_no_database_or_aws_call_with_live_enabled(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("touched the database or AWS")

    monkeypatch.setattr("precertly.db.get_engine", refuse)
    monkeypatch.setattr("precertly.db.session.get_engine", refuse)
    monkeypatch.setattr("sqlalchemy.ext.asyncio.create_async_engine", refuse)
    monkeypatch.setattr("boto3.Session", refuse)
    # The production wiring, not a fake: nothing is built until a run starts.
    app = create_app(Settings(_env_file=None, live_enabled=True))

    async def scenario():
        async with client_for(app) as client:
            health = await client.get("/health")
            assert health.status_code == 200
            assert (await client.get("/policies")).status_code == 200

    asyncio.run(scenario())


BAD_ACTORS = {
    "empty": "",
    "too long": "a" * 129,
    "space": "user 1",
    "slash": "user/1",
    "semicolon": "user;1",
    "non-ascii": "usér".encode("latin-1"),
    "quote": "user'1",
}


@pytest.mark.parametrize("actor", [None, *BAD_ACTORS.values()], ids=["missing", *BAD_ACTORS])
def test_bad_actor_header_is_401_on_every_live_route(actor):
    headers = {} if actor is None else {"x-precertly-actor": actor}

    async def scenario():
        async with client_for(app_with(make_deps=broken_deps)) as client:
            posted = await client.post("/live/runs", json={"case_id": "facet-01"}, headers=headers)
            # The actor is checked before the body: a bad body does not turn it into a 422.
            bad_body = await client.post("/live/runs", json={"nope": 1}, headers=headers)
            got = await client.get("/live/runs/anything", headers=headers)
            return posted, bad_body, got

    for response in asyncio.run(scenario()):
        assert response.status_code == 401, response.text
        assert response.json() == {"detail": "unauthorized"}


def test_valid_actor_characters_are_accepted():
    async def scenario():
        async with client_for(app_with(make_deps=broken_deps)) as client:
            for actor in ("a" * 128, "u-1_x.y+z@example.org:9", "0b1c2d3e-aaaa-bbbb-cccc-ddddeeee"):
                response = await client.get("/live/runs/none", headers={"x-precertly-actor": actor})
                assert response.status_code == 404  # authorized, and there is no such run

    asyncio.run(scenario())


def test_actor_value_is_never_logged_in_full(caplog):
    caplog.set_level(logging.DEBUG)
    secret = "abcdefgh-SECRET-TAIL-1234567890"

    async def scenario():
        async with client_for(app_with(make_deps=broken_deps)) as client:
            await client.post(
                "/live/runs", json={"case_id": "facet-01"}, headers={"x-precertly-actor": secret}
            )
            await client.post(
                "/live/runs",
                json={"case_id": "facet-01"},
                headers={"x-precertly-actor": "bad " + secret},
            )

    asyncio.run(scenario())
    assert "SECRET-TAIL" not in caplog.text
    assert "bad " + secret not in caplog.text


def test_unknown_case_is_404_and_extra_fields_are_rejected():
    async def scenario():
        async with client_for(app_with(make_deps=broken_deps)) as client:
            unknown = await client.post("/live/runs", json={"case_id": "facet-99"}, headers=ACTOR)
            extra = await client.post(
                "/live/runs", json={"case_id": "facet-01", "bundle": {}}, headers=ACTOR
            )
            missing = await client.post("/live/runs", json={}, headers=ACTOR)
            text = await client.post(
                "/live/runs", json={"case_id": "x", "note": "hi"}, headers=ACTOR
            )
            return unknown, extra, missing, text

    unknown, extra, missing, text = asyncio.run(scenario())
    assert unknown.status_code == 404 and unknown.json() == {"detail": "not found"}
    assert {extra.status_code, missing.status_code, text.status_code} == {422}


def test_a_second_run_while_one_is_active_is_409_and_creates_no_run():
    release = asyncio.Event()

    async def blocked(engine):
        await release.wait()
        raise ConnectionRefusedError("done waiting")

    deps = LiveDeps(engine=None, sessions=None, llm=None, embedder=None, wait_for_db=blocked)  # type: ignore[arg-type]

    async def scenario():
        app = app_with(make_deps=lambda: deps)
        async with client_for(app) as client:
            first = await client.post("/live/runs", json={"case_id": "facet-01"}, headers=ACTOR)
            second = await client.post("/live/runs", json={"case_id": "facet-02"}, headers=ACTOR)
            other = await client.post(
                "/live/runs", json={"case_id": "facet-02"}, headers={"x-precertly-actor": "user-2"}
            )
            runs_while_active = len(app.state.live.registry._runs)
            state = await client.get(f"/live/runs/{first.json()['run_id']}", headers=ACTOR)
            release.set()
            await app.state.live.idle()
            third = await client.post("/live/runs", json={"case_id": "facet-02"}, headers=ACTOR)
            await app.state.live.idle()
            return first, second, other, runs_while_active, state, third

    first, second, other, runs_while_active, state, third = asyncio.run(scenario())
    assert first.status_code == 202
    assert first.json()["status"] == "waking_database"
    assert second.status_code == 409 and second.json() == {
        "detail": "a live run is already in progress"
    }
    assert other.status_code == 409
    assert runs_while_active == 1
    assert state.json()["status"] == "waking_database"
    assert third.status_code == 202  # the first one ended, so the process is free again


def test_one_actor_cannot_read_another_actors_run_and_unknown_is_404():
    async def blocked(engine):
        raise ConnectionRefusedError("down")

    deps = LiveDeps(engine=None, sessions=None, llm=None, embedder=None, wait_for_db=blocked)  # type: ignore[arg-type]

    async def scenario():
        app = app_with(make_deps=lambda: deps)
        async with client_for(app) as client:
            run_id = (
                await client.post("/live/runs", json={"case_id": "facet-01"}, headers=ACTOR)
            ).json()["run_id"]
            await app.state.live.idle()
            mine = await client.get(f"/live/runs/{run_id}", headers=ACTOR)
            theirs = await client.get(
                f"/live/runs/{run_id}", headers={"x-precertly-actor": "user-2"}
            )
            unknown = await client.get("/live/runs/not-a-run", headers=ACTOR)
            return mine, theirs, unknown

    mine, theirs, unknown = asyncio.run(scenario())
    assert mine.status_code == 200
    assert mine.json()["status"] == "failed" and mine.json()["reason"] == "database_unavailable"
    assert theirs.status_code == 404 and theirs.json() == unknown.json() == {"detail": "not found"}
    assert "user-1" not in mine.text  # the state never carries the actor


def test_error_text_never_reaches_the_response_or_the_logs(caplog):
    caplog.set_level(logging.DEBUG)
    marker = "MARKER-db.internal:5432 password=hunter2 SELECT secret FROM t"

    def factory():
        raise RuntimeError(marker)

    async def scenario():
        app = app_with(make_deps=factory)
        async with client_for(app) as client:
            started = await client.post("/live/runs", json={"case_id": "facet-01"}, headers=ACTOR)
            await app.state.live.idle()
            state = await client.get(f"/live/runs/{started.json()['run_id']}", headers=ACTOR)
            return started, state

    started, state = asyncio.run(scenario())
    body = state.json()
    assert body["status"] == "failed" and body["reason"] == "internal_error"
    assert body["message"] == "The run failed."
    for text in (started.text, state.text, caplog.text):
        assert "MARKER" not in text and "hunter2" not in text
    assert "RuntimeError" in caplog.text  # the class name is logged


def test_database_wait_failure_text_is_not_shown(caplog):
    caplog.set_level(logging.DEBUG)

    async def refuse(engine):
        raise OSError("MARKER connect to 10.0.0.5 failed")

    deps = LiveDeps(engine=None, sessions=None, llm=None, embedder=None, wait_for_db=refuse)  # type: ignore[arg-type]

    async def scenario():
        app = app_with(make_deps=lambda: deps)
        async with client_for(app) as client:
            started = await client.post("/live/runs", json={"case_id": "facet-01"}, headers=ACTOR)
            await app.state.live.idle()
            return await client.get(f"/live/runs/{started.json()['run_id']}", headers=ACTOR)

    state = asyncio.run(scenario())
    assert state.json()["reason"] == "database_unavailable"
    assert (
        "MARKER" not in state.text and "MARKER" not in caplog.text and "10.0.0.5" not in caplog.text
    )


def test_registry_keeps_the_last_20_and_never_drops_the_active_run():
    registry = RunRegistry()
    ids = []
    for i in range(KEEP_RUNS + 5):
        record = registry.create("u", "facet-01", float(i))
        ids.append(record.id)
        if i < KEEP_RUNS + 4:
            registry.finish(record, record.status.__class__.DONE, float(i))
    assert len(registry._runs) <= KEEP_RUNS
    assert registry.get(ids[-1]) is not None  # the newest, still active
    assert registry.get(ids[0]) is None  # the oldest finished one went first
    with pytest.raises(RunActive):
        registry.create("u", "facet-01", 99.0)


def test_registry_drops_finished_runs_older_than_an_hour():
    from datetime import UTC, datetime, timedelta

    from precertly.live.models import RunStatus

    now = [datetime(2026, 10, 23, 12, 0, tzinfo=UTC)]
    registry = RunRegistry(lambda: now[0])
    old = registry.create("u", "facet-01", 0.0)
    registry.finish(old, RunStatus.DONE, 1.0)
    now[0] += timedelta(minutes=59)
    recent = registry.create("u", "facet-01", 2.0)
    registry.finish(recent, RunStatus.FAILED, 3.0)
    now[0] += timedelta(minutes=2)  # `old` finished 61 minutes ago, `recent` 2
    registry.create("u", "facet-01", 4.0)
    assert registry.get(old.id) is None
    assert registry.get(recent.id) is recent


def test_the_dockerfile_copies_the_eval_cases():
    lines = (ROOT / "Dockerfile").read_text().splitlines()
    assert "COPY evals/cases ./evals/cases" in lines
    ignored = (ROOT / ".dockerignore").read_text().splitlines()
    assert not any(line.strip().rstrip("/") in ("evals", "evals/cases") for line in ignored)


def test_the_lock_key_is_derived_from_a_fixed_name():
    import hashlib

    from precertly.live.limits import LOCK_KEY

    digest = hashlib.sha256(b"precertly:live-run").digest()[:8]
    assert int.from_bytes(digest, "big", signed=True) == LOCK_KEY
    assert -(2**63) <= LOCK_KEY < 2**63  # a Postgres bigint
