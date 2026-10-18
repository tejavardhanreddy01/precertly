"""scripts/deploy.sh with stubs only: no AWS, Docker, credentials or network involved.

Dry-run tests use stubs that fail and log, so any call shows up. The others give the stubs
canned answers (tests/stubs/fake_tools.py) and run the script in a throwaway git repository,
so a developer's real .env.local never reaches a test.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "deploy.sh"
FAKE_TOOLS = ROOT / "tests" / "stubs" / "fake_tools.py"
FAKE_EMAIL = "fake.owner@secret-domain.test"
ACCOUNT = "123456789012"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None or not (ROOT / ".git").exists(),
    reason="needs bash and a git checkout",
)


@pytest.fixture
def stub_bin(tmp_path: Path) -> Path:
    """Stub tools on a bin directory, and a throwaway git repository next to it.

    Without a stub.json every stub exits 97 and logs the call.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("aws", "docker", "npx", "session-manager-plugin"):
        stub = bin_dir / tool
        stub.write_text(f"#!{sys.executable}\n{FAKE_TOOLS.read_text()}")
        stub.chmod(0o755)
    repo = tmp_path / "repo"
    (repo / "infra").mkdir(parents=True)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "initial"], cwd=repo, check=True)
    return bin_dir


def _repo(stub_bin: Path) -> Path:
    return stub_bin.parent / "repo"


def _configure(stub_bin: Path, **config: Any) -> None:
    (stub_bin.parent / "stub.json").write_text(json.dumps(config))


def _calls(stub_bin: Path) -> list[str]:
    log = stub_bin.parent / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def _env(stub_bin: Path, email: str | None) -> dict[str, str]:
    env = {
        "PATH": f"{stub_bin}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(stub_bin.parent),
        "DEPLOY_POLL_SECONDS": "0",
        "DEPLOY_WAIT_TIMEOUT": "2",
    }
    if email is not None:
        env["BUDGET_EMAIL"] = email
    return env


def _deploy(
    stub_bin: Path,
    *args: str,
    email: str | None = FAKE_EMAIL,
    stdin: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=_repo(stub_bin),
        env=_env(stub_bin, email),
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


def _assert_no_calls(stub_bin: Path) -> None:
    assert _calls(stub_bin) == [], "a stubbed tool was called"


def _head_tag(stub_bin: Path) -> str:
    head = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=_repo(stub_bin),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return f"sha-{head}"


def _desired_counts(stub_bin: Path) -> list[str]:
    """The --desired-count of every update-service call, in order."""
    return [
        m.group(1)
        for c in _calls(stub_bin)
        if "update-service" in c and (m := re.search(r"--desired-count (\d+)", c))
    ]


# ---------------------------------------------------------------------------------------------
# image and stack (dry run)
# ---------------------------------------------------------------------------------------------


def test_image_dry_run(stub_bin: Path) -> None:
    result = _deploy(stub_bin, "--dry-run", "image")
    assert result.returncode == 0, _output(result)
    tag = _head_tag(stub_bin)
    assert re.fullmatch(r"sha-[0-9a-f]{12}", tag)
    out = result.stdout
    assert f"tag: {tag}" in out
    assert f"ACCOUNT_ID.dkr.ecr.us-east-2.amazonaws.com/precertly-api:{tag}" in out
    assert "--platform linux/arm64" in out
    assert "--provenance=false" in out
    assert "--sbom=false" in out
    assert "--push" in out
    assert "describe-images" in out
    assert FAKE_EMAIL not in _output(result)
    _assert_no_calls(stub_bin)


def test_stack_dry_run(stub_bin: Path) -> None:
    result = _deploy(stub_bin, "stack", "precertly-base", "--dry-run")
    assert result.returncode == 0, _output(result)
    tag = _head_tag(stub_bin)
    assert f"cdk deploy precertly-base -c budgetEmail=REDACTED -c imageTag={tag}" in result.stdout
    assert "is in ECR" not in result.stdout
    assert "--require-approval" not in result.stdout
    assert FAKE_EMAIL not in _output(result)
    _assert_no_calls(stub_bin)


def test_app_stack_dry_run_passes_the_image_tag(stub_bin: Path) -> None:
    result = _deploy(stub_bin, "stack", "precertly-app", "--dry-run")
    assert result.returncode == 0, _output(result)
    tag = _head_tag(stub_bin)
    assert f"cdk deploy precertly-app -c budgetEmail=REDACTED -c imageTag={tag}" in result.stdout
    assert f"skipping the check that {tag} is in ECR" in result.stdout
    assert FAKE_EMAIL not in _output(result)
    _assert_no_calls(stub_bin)


@pytest.mark.parametrize(
    "args",
    [
        ("stack", "--all"),
        ("stack", "--all", "--dry-run"),
        ("stack", "bogus"),
        ("stack", "precertly-base", "--require-approval=never"),
        ("stack",),
        ("stack", "precertly-base", "precertly-app"),
        ("up", "--all"),
        ("up", "bogus"),
        ("up",),
        ("up", "precertly-base", "precertly-app"),
        ("status", "extra"),
        ("smoke", "extra"),
        ("down", "extra"),
        ("status", "--yes"),
        ("smoke", "--yes"),
        ("down", "--yes"),
        ("image", "--bedrock"),
        ("up", "precertly-base", "--bedrock"),
        ("bogus",),
        ("image", "--bogus"),
    ],
)
def test_bad_invocations_fail_without_leaking_the_email(
    stub_bin: Path, args: tuple[str, ...]
) -> None:
    result = _deploy(stub_bin, *args)
    assert result.returncode != 0
    assert FAKE_EMAIL not in _output(result)
    _assert_no_calls(stub_bin)


def test_stack_all_message_is_clear(stub_bin: Path) -> None:
    result = _deploy(stub_bin, "stack", "--all")
    assert "--all is not allowed" in result.stderr


def test_stack_requires_the_budget_email(stub_bin: Path) -> None:
    result = _deploy(stub_bin, "stack", "precertly-base", "--dry-run", email=None)
    assert result.returncode != 0
    assert "BUDGET_EMAIL" in result.stderr
    _assert_no_calls(stub_bin)


def test_usage(stub_bin: Path) -> None:
    assert _deploy(stub_bin, "-h").returncode == 0
    no_args = _deploy(stub_bin)
    assert no_args.returncode != 0
    assert "Usage:" in no_args.stderr


def test_script_names_match_the_cdk_names() -> None:
    names = dict(
        re.findall(r'^(\w+) = "([^"]+)"$', (ROOT / "infra" / "names.py").read_text(), re.M)
    )
    script = SCRIPT.read_text()
    assert f'readonly CLUSTER="{names["CLUSTER"]}"' in script
    assert f'readonly SERVICE="{names["SERVICE"]}"' in script
    assert f'readonly REPO_NAME="{names["ECR_REPO"]}"' in script
    app_stack = (ROOT / "infra" / "app_stack.py").read_text()
    assert 'add_container(\n            "api",' in app_stack
    assert 'readonly CONTAINER="api"' in script
    assert re.search(r"^API_PORT = 8000$", app_stack, re.M)
    assert "readonly API_PORT=8000" in script


# ---------------------------------------------------------------------------------------------
# .env.local
# ---------------------------------------------------------------------------------------------


def test_env_local_is_gitignored() -> None:
    result = subprocess.run(
        ["git", "check-ignore", ".env.local"], cwd=ROOT, capture_output=True, check=False
    )
    assert result.returncode == 0
    assert ".env.local" in (ROOT / ".gitignore").read_text().splitlines()


def test_budget_email_is_read_from_env_local_and_never_printed(stub_bin: Path) -> None:
    (_repo(stub_bin) / ".env.local").write_text(
        f'# the budget address\n\nOTHER=ignored\nBUDGET_EMAIL="{FAKE_EMAIL}"\n'
    )
    dry = _deploy(stub_bin, "stack", "precertly-base", "--dry-run", email=None)
    assert dry.returncode == 0, _output(dry)
    assert "budgetEmail=REDACTED" in dry.stdout
    assert FAKE_EMAIL not in _output(dry)
    _assert_no_calls(stub_bin)

    # Really reached the CDK (only the call log has it), and still not in any output.
    _configure(stub_bin)
    live = _deploy(stub_bin, "stack", "precertly-base", email=None)
    assert live.returncode == 0, _output(live)
    assert FAKE_EMAIL not in _output(live)
    assert any(f"budgetEmail={FAKE_EMAIL}" in c and "cdk deploy" in c for c in _calls(stub_bin))


def test_environment_wins_over_env_local(stub_bin: Path) -> None:
    (_repo(stub_bin) / ".env.local").write_text("BUDGET_EMAIL=file@file.test\n")
    _configure(stub_bin)
    result = _deploy(stub_bin, "stack", "precertly-base")
    assert result.returncode == 0, _output(result)
    assert any(f"budgetEmail={FAKE_EMAIL}" in c for c in _calls(stub_bin))
    assert not any("file@file.test" in c for c in _calls(stub_bin))


def test_env_local_is_parsed_not_sourced(stub_bin: Path) -> None:
    repo = _repo(stub_bin)
    (repo / ".env.local").write_text("BUDGET_EMAIL=x@y.test;touch${IFS}pwned\n")
    _configure(stub_bin)
    result = _deploy(stub_bin, "stack", "precertly-base", email=None)
    assert result.returncode == 0, _output(result)
    assert not (repo / "pwned").exists()
    assert not (stub_bin.parent / "pwned").exists()


@pytest.mark.parametrize(
    "content",
    [
        f"echo hello {FAKE_EMAIL}\n",
        f"export BUDGET_EMAIL={FAKE_EMAIL}\n",
        "BUDGET_EMAIL=not an address\n",
        "BUDGET_EMAIL=nobody\n",
    ],
)
def test_malformed_env_local_is_refused_without_echoing_it(stub_bin: Path, content: str) -> None:
    (_repo(stub_bin) / ".env.local").write_text(content)
    result = _deploy(stub_bin, "stack", "precertly-base", "--dry-run", email=None)
    assert result.returncode != 0
    assert ".env.local" in result.stderr
    assert FAKE_EMAIL not in _output(result)
    assert "nobody" not in _output(result)
    _assert_no_calls(stub_bin)


# ---------------------------------------------------------------------------------------------
# up
# ---------------------------------------------------------------------------------------------


def test_up_app_dry_run_pushes_the_image_then_diffs_then_deploys(stub_bin: Path) -> None:
    result = _deploy(stub_bin, "up", "precertly-app", "--dry-run")
    assert result.returncode == 0, _output(result)
    out = result.stdout
    tag = _head_tag(stub_bin)
    build = out.index("docker buildx build")
    diff = out.index(f"cdk diff precertly-app -c budgetEmail=REDACTED -c imageTag={tag}")
    ask = out.index("Deploy? [y/N]")
    deploy = out.index(f"cdk deploy precertly-app -c budgetEmail=REDACTED -c imageTag={tag}")
    assert build < diff < ask < deploy
    assert FAKE_EMAIL not in _output(result)
    _assert_no_calls(stub_bin)


def test_up_base_dry_run_has_no_image_step(stub_bin: Path) -> None:
    result = _deploy(stub_bin, "up", "precertly-base", "--dry-run", "--yes")
    assert result.returncode == 0, _output(result)
    assert "docker" not in result.stdout
    assert "cdk diff precertly-base" in result.stdout
    assert "cdk deploy precertly-base" in result.stdout
    _assert_no_calls(stub_bin)


@pytest.mark.parametrize("answer", ["", "n\n", "yes please\n"])
def test_up_with_yes_still_shows_the_diff_and_asks(stub_bin: Path, answer: str) -> None:
    _configure(stub_bin)
    result = _deploy(stub_bin, "up", "precertly-base", "--yes", stdin=answer)
    assert result.returncode != 0
    assert "subscriber REDACTED" in result.stdout  # the diff, with the address redacted
    assert FAKE_EMAIL not in _output(result)
    assert "Deploy? [y/N]" in result.stderr
    calls = _calls(stub_bin)
    assert any(c.startswith("npx --no-install cdk diff precertly-base") for c in calls)
    assert not any("cdk deploy" in c for c in calls)


def test_up_deploys_after_the_diff_when_confirmed(stub_bin: Path) -> None:
    _configure(stub_bin)
    result = _deploy(stub_bin, "up", "precertly-base", "--yes", stdin="y\n")
    assert result.returncode == 0, _output(result)
    assert FAKE_EMAIL not in _output(result)
    calls = [c for c in _calls(stub_bin) if c.startswith("npx")]
    assert [c.split()[3] for c in calls] == ["diff", "deploy"]
    assert "--require-approval" not in " ".join(calls)


def test_up_stops_and_redacts_when_the_diff_fails(stub_bin: Path) -> None:
    _configure(stub_bin, diff_fail="synth error for {email}")
    result = _deploy(stub_bin, "up", "precertly-base", "--yes", stdin="y\n")
    assert result.returncode != 0
    assert "cdk diff failed" in result.stderr
    assert "synth error for REDACTED" in result.stderr
    assert FAKE_EMAIL not in _output(result)
    assert not any("cdk deploy" in c for c in _calls(stub_bin))


# ---------------------------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------------------------


def test_status_dry_run(stub_bin: Path) -> None:
    result = _deploy(stub_bin, "status", "--dry-run")
    assert result.returncode == 0, _output(result)
    for needle in (
        "describe-stacks --stack-name precertly-base",
        "describe-stacks --stack-name precertly-app",
        "describe-stacks --stack-name precertly-data",
        "ecs describe-services --cluster precertly --services precertly-api",
        "describe-task-definition",
        "describe-nat-gateways",
        "describe-db-instances",
        "ecr describe-images --repository-name precertly-api",
    ):
        assert needle in result.stdout, needle
    assert FAKE_EMAIL not in _output(result)
    _assert_no_calls(stub_bin)


def test_status_reports_what_exists_and_what_does_not(stub_bin: Path) -> None:
    _configure(
        stub_bin,
        stacks={"precertly-base": "CREATE_COMPLETE", "precertly-app": "UPDATE_COMPLETE"},
        running=0,
        health_check=True,
    )
    result = _deploy(stub_bin, "status")
    assert result.returncode == 0, _output(result)
    out = result.stdout
    assert re.search(r"precertly-base\s+CREATE_COMPLETE", out)
    assert re.search(r"precertly-app\s+UPDATE_COMPLETE", out)
    assert re.search(r"precertly-data\s+not deployed", out)
    assert "status ACTIVE, desired 0, running 0, pending 0, rollout COMPLETED" in out
    assert "task definition revision 3, image tag sha-abc123def456, health check set" in out
    assert "nat gateways available: 0" in out
    assert "rds instances: 0" in out
    assert "ecr precertly-api: 1 image(s) sha-abc123def456" in out
    assert ACCOUNT not in _output(result)
    assert _desired_counts(stub_bin) == []  # read-only
    assert not any("update-service" in c or "cdk" in c for c in _calls(stub_bin))


def test_status_says_when_the_health_check_is_not_set(stub_bin: Path) -> None:
    _configure(stub_bin, stacks={"precertly-app": "CREATE_COMPLETE"})
    result = _deploy(stub_bin, "status")
    assert "health check not set" in result.stdout


def test_status_prints_not_deployed_for_missing_stacks_and_resources(stub_bin: Path) -> None:
    _configure(stub_bin, service_missing=True, ecr_missing=True)
    result = _deploy(stub_bin, "status")
    assert result.returncode == 0, _output(result)
    out = result.stdout
    for stack in ("precertly-base", "precertly-app", "precertly-data"):
        assert re.search(rf"{stack}\s+not deployed", out)
    assert re.search(r"service precertly/precertly-api\n\s+not deployed", out)
    assert "ecr precertly-api: not deployed" in out
    assert "task definition" not in out


def test_status_treats_a_missing_cluster_as_not_deployed(stub_bin: Path) -> None:
    _configure(stub_bin, cluster_missing=True)
    result = _deploy(stub_bin, "status")
    assert result.returncode == 0, _output(result)
    assert re.search(r"service precertly/precertly-api\n\s+not deployed", result.stdout)


def test_status_does_not_hide_a_real_failure_as_not_deployed(stub_bin: Path) -> None:
    _configure(stub_bin, fail_all="An error occurred (ExpiredToken): The security token expired")
    result = _deploy(stub_bin, "status")
    assert result.returncode != 0
    assert "ExpiredToken" in result.stderr
    assert "not deployed" not in result.stdout


# ---------------------------------------------------------------------------------------------
# down
# ---------------------------------------------------------------------------------------------


def test_down_dry_run(stub_bin: Path) -> None:
    result = _deploy(stub_bin, "down", "--dry-run")
    assert result.returncode == 0, _output(result)
    assert "update-service --cluster precertly --service precertly-api --desired-count 0" in (
        result.stdout
    )
    _assert_no_calls(stub_bin)


def test_down_scales_to_zero_and_confirms(stub_bin: Path) -> None:
    _configure(stub_bin, desired=1, running=1)
    result = _deploy(stub_bin, "down")
    assert result.returncode == 0, _output(result)
    assert _desired_counts(stub_bin) == ["0"]
    assert "desired 0, running 0" in result.stdout


def test_down_fails_when_a_task_will_not_stop(stub_bin: Path) -> None:
    _configure(stub_bin, desired=1, running=1, stuck_running=True)
    result = _deploy(stub_bin, "down")
    assert result.returncode != 0
    assert "did not reach 0" in result.stderr


def test_down_with_no_service_is_not_an_error(stub_bin: Path) -> None:
    _configure(stub_bin, service_missing=True)
    result = _deploy(stub_bin, "down")
    assert result.returncode == 0, _output(result)
    assert "not deployed" in result.stdout
    assert _desired_counts(stub_bin) == []


# ---------------------------------------------------------------------------------------------
# smoke
# ---------------------------------------------------------------------------------------------


def test_smoke_dry_run(stub_bin: Path) -> None:
    result = _deploy(stub_bin, "smoke", "--dry-run")
    assert result.returncode == 0, _output(result)
    out = result.stdout
    assert (
        out.index("--desired-count 1")
        < out.index("execute-command")
        < out.index("--desired-count 0")
    )
    assert "SMOKE_HTTP" in out
    assert "/health" in out and "/policies" in out
    assert "smoke_llm" not in out
    assert "--task TASK_ARN" in out
    assert ACCOUNT not in out
    _assert_no_calls(stub_bin)


def test_smoke_bedrock_dry_run_mentions_the_question_and_the_call(stub_bin: Path) -> None:
    result = _deploy(stub_bin, "smoke", "--bedrock", "--dry-run")
    assert result.returncode == 0, _output(result)
    assert "One real Bedrock call, costs a fraction of a cent. Continue? [y/N]" in result.stdout
    assert "scripts/smoke_llm.py" in result.stdout
    _assert_no_calls(stub_bin)


def test_smoke_refuses_when_a_task_is_running(stub_bin: Path) -> None:
    _configure(stub_bin, desired=1, running=1)
    result = _deploy(stub_bin, "smoke")
    assert result.returncode != 0
    assert "refusing" in result.stderr
    # It must not touch a service it did not start: no scale-up and no scale-down.
    assert _desired_counts(stub_bin) == []
    assert not any("execute-command" in c for c in _calls(stub_bin))


def test_smoke_refuses_when_the_service_is_not_deployed(stub_bin: Path) -> None:
    _configure(stub_bin, service_missing=True)
    result = _deploy(stub_bin, "smoke")
    assert result.returncode != 0
    assert "not deployed" in result.stderr
    assert _desired_counts(stub_bin) == []


def test_smoke_passes_and_scales_back_to_zero(stub_bin: Path) -> None:
    _configure(stub_bin)
    result = _deploy(stub_bin, "smoke")
    assert result.returncode == 0, _output(result)
    out = result.stdout
    assert _desired_counts(stub_bin) == ["1", "0"]
    assert "lastStatus RUNNING, healthStatus UNKNOWN" in out
    assert re.search(r"PASS\s+GET /health returned 200", out)
    assert re.search(r"PASS\s+GET /policies returned 200", out)
    assert re.search(r"PASS\s+scaled back to 0", out)
    assert "final counts: desired 0, running 0, pending 0" in out
    assert "result: PASS" in out
    assert "smoke_llm" not in " ".join(_calls(stub_bin))
    assert ACCOUNT not in _output(result)


def test_smoke_scales_to_zero_when_the_health_check_fails(stub_bin: Path) -> None:
    _configure(
        stub_bin,
        exec={
            "/health": "Traceback (most recent call last):\nurllib.error.HTTPError: HTTP Error 503"
        },
    )
    result = _deploy(stub_bin, "smoke")
    assert result.returncode != 0
    out = result.stdout
    assert _desired_counts(stub_bin) == ["1", "0"]
    # The last write to the service is the scale-down, and it was confirmed.
    update_calls = [c for c in _calls(stub_bin) if "update-service" in c]
    assert update_calls[-1].endswith("--output text") and "--desired-count 0" in update_calls[-1]
    assert re.search(r"FAIL\s+GET /health returned 200", out)
    assert "HTTP Error 503" in result.stderr
    assert re.search(r"PASS\s+scaled back to 0", out)
    assert "final counts: desired 0, running 0" in out
    assert "result: FAIL" in out
    # It stops at the first failed check.
    assert not any("/policies" in c for c in _calls(stub_bin) if "execute-command" in c)


def test_smoke_does_not_accept_a_non_200_status(stub_bin: Path) -> None:
    _configure(stub_bin, exec={"/health": "SMOKE_HTTP /health 204"})
    result = _deploy(stub_bin, "smoke")
    assert result.returncode != 0
    assert re.search(r"FAIL\s+GET /health", result.stdout)
    assert _desired_counts(stub_bin) == ["1", "0"]


def test_smoke_fails_when_the_second_check_fails(stub_bin: Path) -> None:
    _configure(stub_bin, exec={"/policies": "urllib.error.URLError: refused"})
    result = _deploy(stub_bin, "smoke")
    assert result.returncode != 0
    assert re.search(r"PASS\s+GET /health returned 200", result.stdout)
    assert re.search(r"FAIL\s+GET /policies returned 200", result.stdout)
    assert _desired_counts(stub_bin) == ["1", "0"]


def test_smoke_times_out_waiting_for_the_task_and_still_scales_to_zero(stub_bin: Path) -> None:
    _configure(stub_bin, stuck_running=True)  # the task never starts
    result = _deploy(stub_bin, "smoke")
    assert result.returncode != 0
    assert re.search(r"FAIL\s+service stable within 2s", result.stdout)
    assert _desired_counts(stub_bin) == ["1", "0"]
    assert re.search(r"PASS\s+scaled back to 0", result.stdout)
    assert not any("execute-command" in c for c in _calls(stub_bin))


def test_smoke_reports_a_scale_down_that_cannot_be_confirmed(stub_bin: Path) -> None:
    _configure(stub_bin, stick_on_zero=True)  # the task starts but will not stop
    result = _deploy(stub_bin, "smoke")
    assert result.returncode != 0
    assert re.search(
        r"FAIL\s+scale back to 0 not confirmed: run scripts/deploy.sh down NOW", result.stdout
    )
    assert "final counts: desired 0, running 1" in result.stdout
    assert "result: FAIL" in result.stdout


def test_smoke_bedrock_asks_first_and_runs_the_call(stub_bin: Path) -> None:
    _configure(stub_bin)
    result = _deploy(stub_bin, "smoke", "--bedrock", stdin="y\n")
    assert result.returncode == 0, _output(result)
    assert "One real Bedrock call, costs a fraction of a cent. Continue? [y/N]" in result.stderr
    assert re.search(r"PASS\s+one real Bedrock call", result.stdout)
    assert any("execute-command" in c and "scripts/smoke_llm.py" in c for c in _calls(stub_bin))
    assert _desired_counts(stub_bin) == ["1", "0"]


@pytest.mark.parametrize("answer", ["", "n\n"])
def test_smoke_bedrock_declined_starts_nothing(stub_bin: Path, answer: str) -> None:
    _configure(stub_bin)
    result = _deploy(stub_bin, "smoke", "--bedrock", stdin=answer)
    assert result.returncode != 0
    assert "nothing started" in result.stderr
    assert not any("aws" in c for c in _calls(stub_bin))


def test_smoke_failed_bedrock_call_still_scales_to_zero(stub_bin: Path) -> None:
    _configure(stub_bin, exec={"smoke_llm": "Bedrock call failed: AccessDeniedException"})
    result = _deploy(stub_bin, "smoke", "--bedrock", stdin="y\n")
    assert result.returncode != 0
    assert re.search(r"FAIL\s+one real Bedrock call", result.stdout)
    assert _desired_counts(stub_bin) == ["1", "0"]


@pytest.mark.parametrize(("sig", "status"), [(signal.SIGTERM, 143), (signal.SIGINT, 130)])
def test_smoke_scales_to_zero_when_interrupted(
    stub_bin: Path, sig: signal.Signals, status: int
) -> None:
    _configure(stub_bin, exec_sleep=2)
    proc = subprocess.Popen(
        ["bash", str(SCRIPT), "smoke"],
        cwd=_repo(stub_bin),
        env=_env(stub_bin, FAKE_EMAIL),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 30
    while not any("execute-command" in c for c in _calls(stub_bin)):
        assert time.monotonic() < deadline, "smoke never reached the exec step"
        time.sleep(0.05)
    proc.send_signal(sig)
    stdout, stderr = proc.communicate(timeout=30)
    assert proc.returncode == status, stdout + stderr
    assert _desired_counts(stub_bin) == ["1", "0"]
    assert re.search(r"PASS\s+scaled back to 0", stdout)
    assert "result: FAIL" in stdout
    assert "final counts: desired 0, running 0" in stdout
