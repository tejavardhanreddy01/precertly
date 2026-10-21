"""scripts/deploy.sh with stubs only: no AWS, Docker, credentials or network involved.

Dry-run tests use stubs that fail and log, so any call shows up. The others give the stubs
canned answers (tests/stubs/fake_tools.py) and run the script in a throwaway git repository,
so a developer's real .env.local never reaches a test.
"""

import json
import os
import re
import shlex
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
WEB_DOMAIN = "precertly.example.org"
WEB_ARN = "arn:aws:acm:us-east-1:210987654321:certificate/aaaabbbb-cccc-4ddd-8eee-ffff00001111"
WEB_ENV = {"WEB_DOMAIN": WEB_DOMAIN, "WEB_CERTIFICATE_ARN": WEB_ARN}
PLACEHOLDER_DOMAIN = "site.example.invalid"
PLACEHOLDER_ARN = (
    "arn:aws:acm:us-east-1:000000000000:certificate/00000000-0000-0000-0000-000000000000"
)

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


def _env(stub_bin: Path, email: str | None, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        "PATH": f"{stub_bin}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(stub_bin.parent),
        "DEPLOY_POLL_SECONDS": "0",
        "DEPLOY_WAIT_TIMEOUT": "2",
    }
    if email is not None:
        env["BUDGET_EMAIL"] = email
    env.update(extra or {})
    return env


def _deploy(
    stub_bin: Path,
    *args: str,
    email: str | None = FAKE_EMAIL,
    stdin: str = "",
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=_repo(stub_bin),
        env=_env(stub_bin, email, env),
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
    assert (
        f"cdk deploy --exclusively precertly-base -c budgetEmail=REDACTED -c imageTag={tag}"
        in result.stdout
    )
    assert "is in ECR" not in result.stdout
    assert "--require-approval" not in result.stdout
    assert FAKE_EMAIL not in _output(result)
    _assert_no_calls(stub_bin)


def test_app_stack_dry_run_passes_the_image_tag(stub_bin: Path) -> None:
    result = _deploy(stub_bin, "stack", "precertly-app", "--dry-run")
    assert result.returncode == 0, _output(result)
    tag = _head_tag(stub_bin)
    assert (
        f"cdk deploy --exclusively precertly-app -c budgetEmail=REDACTED -c imageTag={tag}"
        in result.stdout
    )
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
    assert f'readonly TOOLS_FAMILY="{names["TOOLS_FAMILY"]}"' in script
    assert f'readonly TOOLS_CONTAINER="{names["TOOLS_CONTAINER"]}"' in script
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
    diff = out.index(
        f"cdk diff --exclusively precertly-app -c budgetEmail=REDACTED -c imageTag={tag}"
    )
    ask = out.index("Deploy? [y/N]")
    deploy = out.index(
        f"cdk deploy --exclusively precertly-app -c budgetEmail=REDACTED -c imageTag={tag}"
    )
    assert build < diff < ask < deploy
    assert FAKE_EMAIL not in _output(result)
    _assert_no_calls(stub_bin)


def test_up_base_dry_run_has_no_image_step(stub_bin: Path) -> None:
    result = _deploy(stub_bin, "up", "precertly-base", "--dry-run", "--yes")
    assert result.returncode == 0, _output(result)
    assert "docker" not in result.stdout
    assert "cdk diff --exclusively precertly-base" in result.stdout
    assert "cdk deploy --exclusively precertly-base" in result.stdout
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
    assert any(
        c.startswith("npx --no-install cdk diff --exclusively precertly-base") for c in calls
    )
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
    assert "lastStatus RUNNING, healthStatus HEALTHY" in out
    assert re.search(r"PASS\s+ECS Exec agent running", out)
    assert re.search(r"PASS\s+ECS health check HEALTHY", out)
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


# ---------------------------------------------------------------------------------------------
# smoke: ECS Exec readiness, retries and the health check
# ---------------------------------------------------------------------------------------------


def _exec_calls(stub_bin: Path, needle: str) -> list[str]:
    return [c for c in _calls(stub_bin) if "execute-command" in c and needle in c]


def test_smoke_dry_run_lists_the_agent_and_health_queries(stub_bin: Path) -> None:
    out = _deploy(stub_bin, "smoke", "--dry-run").stdout
    assert "ExecuteCommandAgent" in out
    assert out.count("describe-tasks") == 3  # task state, agent status, health (after the exec)
    assert out.rindex("describe-tasks") > out.rindex("execute-command")


def test_smoke_waits_for_the_exec_agent(stub_bin: Path) -> None:
    _configure(stub_bin, agent_polls=3)
    result = _deploy(stub_bin, "smoke")
    assert result.returncode == 0, _output(result)
    assert re.search(r"PASS\s+ECS Exec agent running", result.stdout)
    agent_polls = [c for c in _calls(stub_bin) if "ExecuteCommandAgent" in c]
    assert len(agent_polls) == 4
    # The agent is checked before the first exec call.
    calls = _calls(stub_bin)
    first_exec = next(i for i, c in enumerate(calls) if "execute-command" in c)
    assert all(calls.index(c) < first_exec for c in agent_polls[:1])
    assert _desired_counts(stub_bin) == ["1", "0"]


def test_smoke_fails_when_the_exec_agent_never_runs_and_still_scales_to_zero(
    stub_bin: Path,
) -> None:
    _configure(stub_bin, agent_never_running=True)
    result = _deploy(stub_bin, "smoke")
    assert result.returncode != 0
    assert re.search(
        r"FAIL\s+ECS Exec agent not RUNNING within 2s \(last status: PENDING\)", result.stdout
    )
    assert not any("execute-command" in c for c in _calls(stub_bin))
    assert _desired_counts(stub_bin) == ["1", "0"]
    assert re.search(r"PASS\s+scaled back to 0", result.stdout)
    assert "result: FAIL" in result.stdout


def test_smoke_retries_an_exec_that_is_not_ready_yet(stub_bin: Path) -> None:
    _configure(stub_bin, exec_errors={"/health": 2})
    result = _deploy(stub_bin, "smoke")
    assert result.returncode == 0, _output(result)
    assert len(_exec_calls(stub_bin, "/health")) == 3
    assert len(_exec_calls(stub_bin, "/policies")) == 1
    assert re.search(r"PASS\s+GET /health returned 200", result.stdout)
    assert "ECS Exec not ready (attempt 1 of 3)" in result.stdout
    assert "ECS Exec not ready (attempt 2 of 3)" in result.stdout
    # No output tail for attempts that were retried.
    assert "agent isn't running" not in result.stderr


def test_smoke_gives_up_after_three_not_ready_attempts_and_shows_the_last_tail(
    stub_bin: Path,
) -> None:
    _configure(stub_bin, exec_errors={"/health": 5})
    result = _deploy(stub_bin, "smoke")
    assert result.returncode != 0
    assert len(_exec_calls(stub_bin, "/health")) == 3
    assert re.search(r"FAIL\s+GET /health returned 200", result.stdout)
    assert result.stderr.count("agent isn't running") == 1  # the final attempt only
    assert _desired_counts(stub_bin) == ["1", "0"]


@pytest.mark.parametrize(
    "output",
    [
        "urllib.error.HTTPError: HTTP Error 500: Internal Server Error",
        "Traceback (most recent call last):\nValueError: boom",
        "SMOKE_HTTP /health 204",
    ],
)
def test_smoke_does_not_retry_other_failures(stub_bin: Path, output: str) -> None:
    _configure(stub_bin, exec={"/health": output})
    result = _deploy(stub_bin, "smoke")
    assert result.returncode != 0
    assert len(_exec_calls(stub_bin, "/health")) == 1
    assert "retrying" not in result.stdout
    assert _desired_counts(stub_bin) == ["1", "0"]


def test_smoke_waits_for_the_ecs_health_check(stub_bin: Path) -> None:
    _configure(stub_bin, health_after_polls=3)
    result = _deploy(stub_bin, "smoke")
    assert result.returncode == 0, _output(result)
    assert re.search(r"PASS\s+ECS health check HEALTHY", result.stdout)
    assert "healthStatus UNKNOWN" in result.stdout  # as first seen, before it settled


@pytest.mark.parametrize("status", ["UNKNOWN", "UNHEALTHY"])
def test_smoke_fails_when_the_health_check_never_passes_and_still_scales_to_zero(
    stub_bin: Path, status: str
) -> None:
    _configure(stub_bin, health=status)
    result = _deploy(stub_bin, "smoke")
    assert result.returncode != 0
    out = result.stdout
    # The application checks passed first; the health check is judged after them.
    assert re.search(r"PASS\s+GET /policies returned 200", out)
    assert re.search(rf"FAIL\s+ECS health check HEALTHY within 2s \(last status: {status}\)", out)
    assert out.index("GET /policies") < out.index("ECS health check")
    assert _desired_counts(stub_bin) == ["1", "0"]
    assert re.search(r"PASS\s+scaled back to 0", out)
    assert "final counts: desired 0, running 0" in out
    assert "result: FAIL" in out


def test_smoke_health_check_does_not_hide_an_application_failure(stub_bin: Path) -> None:
    _configure(stub_bin, health="UNKNOWN", exec={"/health": "urllib.error.URLError: refused"})
    result = _deploy(stub_bin, "smoke")
    assert result.returncode != 0
    assert re.search(r"FAIL\s+GET /health returned 200", result.stdout)
    assert "ECS health check" not in result.stdout  # never reached, and no 2 minute wait


# ---------------------------------------------------------------------------------------------
# --exclusively and the data stack
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["precertly-base", "precertly-app", "precertly-data"])
def test_stack_and_up_dry_runs_never_deploy_dependencies(stub_bin: Path, name: str) -> None:
    tag = _head_tag(stub_bin)
    stack = _deploy(stub_bin, "stack", name, "--dry-run")
    assert f"cdk deploy --exclusively {name} -c budgetEmail=REDACTED -c imageTag={tag}" in (
        stack.stdout
    )
    up = _deploy(stub_bin, "up", name, "--dry-run")
    assert f"cdk diff --exclusively {name} -c" in up.stdout
    assert f"cdk deploy --exclusively {name} -c" in up.stdout
    _assert_no_calls(stub_bin)


def test_data_stack_needs_the_image_like_the_app_stack(stub_bin: Path) -> None:
    dry = _deploy(stub_bin, "stack", "precertly-data", "--dry-run")
    assert f"skipping the check that {_head_tag(stub_bin)} is in ECR" in dry.stdout
    up = _deploy(stub_bin, "up", "precertly-data", "--dry-run")
    assert up.stdout.index("docker buildx build") < up.stdout.index("cdk diff --exclusively")
    base = _deploy(stub_bin, "up", "precertly-base", "--dry-run")
    assert "docker" not in base.stdout


def test_data_stack_refuses_when_the_image_is_not_in_ecr(stub_bin: Path) -> None:
    _configure(stub_bin, ecr_missing=True)
    result = _deploy(stub_bin, "stack", "precertly-data")
    assert result.returncode != 0
    assert "is not in ECR" in result.stderr or "could not check" in result.stderr
    assert not any("cdk deploy" in c for c in _calls(stub_bin))


# ---------------------------------------------------------------------------------------------
# status: the Aurora line
# ---------------------------------------------------------------------------------------------


def _dry_run_command(out: str, prefix: str) -> list[str]:
    """The words of the one dry-run line that starts with `+ <prefix>`, as the shell sees them."""
    (line,) = [ln for ln in out.splitlines() if ln.startswith(f"+ {prefix}")]
    return shlex.split(line[2:])


def test_status_dry_run_lists_the_aurora_queries(stub_bin: Path) -> None:
    result = _deploy(stub_bin, "status", "--dry-run")
    outputs = [
        words
        for line in result.stdout.splitlines()
        if line.startswith("+ aws cloudformation describe-stacks --stack-name precertly-data")
        and (words := shlex.split(line[2:]))
    ]
    assert any("OutputKey=='ClusterIdentifier'" in w[-3] for w in outputs)
    cluster = _dry_run_command(result.stdout, "aws rds describe-db-clusters")
    assert cluster[cluster.index("--db-cluster-identifier") + 1] == "CLUSTER_ID"
    assert "ServerlessV2ScalingConfiguration.MinCapacity" in " ".join(cluster)
    assert "ServerlessV2ScalingConfiguration.MaxCapacity" in " ".join(cluster)
    _assert_no_calls(stub_bin)


def test_status_reports_the_aurora_cluster(stub_bin: Path) -> None:
    _configure(stub_bin, stacks={"precertly-data": "CREATE_COMPLETE"})
    result = _deploy(stub_bin, "status")
    assert result.returncode == 0, _output(result)
    assert "aurora cluster\n  status available, engine version 16.13, capacity 0.0 to 2.0 ACU" in (
        result.stdout
    )
    assert not any("update-service" in c or "run-task" in c for c in _calls(stub_bin))


@pytest.mark.parametrize("config", [{}, {"aurora_missing": True}, {"no_outputs": True}])
def test_status_says_when_there_is_no_aurora_cluster(
    stub_bin: Path, config: dict[str, Any]
) -> None:
    stacks = {"precertly-data": "CREATE_COMPLETE"} if config else {}
    _configure(stub_bin, stacks=stacks, **config)
    result = _deploy(stub_bin, "status")
    assert result.returncode == 0, _output(result)
    assert "aurora cluster\n  not deployed" in result.stdout


# ---------------------------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------------------------

DATA_DEPLOYED = {"precertly-data": "CREATE_COMPLETE"}


def _tools(
    stub_bin: Path, *args: str, stdin: str = "", env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), "tools", *args],
        cwd=_repo(stub_bin),
        env={**_env(stub_bin, None), "DEPLOY_TOOLS_TIMEOUT": "5", **(env or {})},
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _ops(stub_bin: Path, *needles: str) -> list[str]:
    return [c for c in _calls(stub_bin) if any(n in c for n in needles)]


def _index(stub_bin: Path, needle: str) -> int:
    calls = _calls(stub_bin)
    return next(i for i, c in enumerate(calls) if needle in c)


@pytest.mark.parametrize(
    ("verb", "command"),
    [
        ("migrate", '["alembic","upgrade","head"]'),
        ("load", '["python","-m","precertly.ingest","evals/bundles","--embed"]'),
        ("check", '["python","-m","precertly.db.check"]'),
    ],
)
def test_tools_dry_run(stub_bin: Path, verb: str, command: str) -> None:
    result = _tools(stub_bin, verb, "--dry-run")
    assert result.returncode == 0, _output(result)
    out = result.stdout
    run_task = _dry_run_command(out, "aws ecs run-task")
    overrides = json.loads(run_task[run_task.index("--overrides") + 1])
    assert overrides == {"containerOverrides": [{"name": "tools", "command": json.loads(command)}]}
    assert run_task[run_task.index("--task-definition") + 1] == "precertly-tools"
    assert run_task[run_task.index("--launch-type") + 1] == "FARGATE"
    assert run_task[run_task.index("--network-configuration") + 1] == (
        "awsvpcConfiguration={subnets=[SUBNET_IDS],securityGroups=[SECURITY_GROUP_ID],"
        "assignPublicIp=ENABLED}"
    )
    for key in ("ToolsSubnetIds", "ToolsSecurityGroupId", "ToolsLogGroupName"):
        assert key in out
    assert (
        out.index("ecs list-tasks")
        < out.index("ecs run-task")
        < out.index("ecs describe-tasks")
        < out.index("always:")
        < out.index("ecs stop-task")
        < out.index("logs get-log-events")
    )
    assert ("Continue? [y/N]" in out) == (verb == "load")
    assert ACCOUNT not in out
    _assert_no_calls(stub_bin)


@pytest.mark.parametrize(("verb", "seconds"), [("migrate", 600), ("load", 1800), ("check", 300)])
def test_tools_time_limits(stub_bin: Path, verb: str, seconds: int) -> None:
    result = _tools(stub_bin, verb, "--dry-run", env={"DEPLOY_TOOLS_TIMEOUT": ""})
    assert f"waits up to {seconds}s" in result.stdout


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("bogus",),
        ("migrate", "extra"),
        ("migrate", "--yes"),
        ("check", "--yes"),
        ("migrate", "--bedrock"),
        ("--all",),
    ],
)
def test_tools_bad_invocations(stub_bin: Path, args: tuple[str, ...]) -> None:
    result = _tools(stub_bin, *args)
    assert result.returncode != 0
    _assert_no_calls(stub_bin)


def test_tools_migrate_runs_one_task_and_passes(stub_bin: Path) -> None:
    _configure(stub_bin, stacks=DATA_DEPLOYED, tools_log="Running upgrade -> head")
    result = _tools(stub_bin, "migrate")
    assert result.returncode == 0, _output(result)
    (run_task,) = _ops(stub_bin, "ecs run-task")
    assert "--cluster precertly" in run_task
    assert "--task-definition precertly-tools" in run_task
    assert "--launch-type FARGATE" in run_task
    assert "subnets=[subnet-0a1b2c,subnet-3d4e5f]" in run_task
    assert "securityGroups=[sg-0123abcd]" in run_task
    assert "assignPublicIp=ENABLED" in run_task
    assert '"command":["alembic","upgrade","head"]' in run_task
    assert '"name":"tools"' in run_task
    assert "--started-by deploy.sh-tools-migrate" in run_task
    assert re.search(r"PASS\s+migrate: container exited 0", result.stdout)
    assert "container exit code 0" in result.stdout
    assert "| Running upgrade -> head" in result.stdout
    assert "result: PASS" in result.stdout
    assert not _ops(stub_bin, "stop-task")  # it stopped by itself, so there was nothing to stop
    assert ACCOUNT not in result.stdout.replace("aaaabbbbccccdddd", "")


def test_tools_need_no_budget_email_and_never_touch_the_service(stub_bin: Path) -> None:
    _configure(stub_bin, stacks=DATA_DEPLOYED)
    result = _tools(stub_bin, "check")
    assert result.returncode == 0, _output(result)
    assert not _ops(stub_bin, "update-service", "execute-command", "npx", "docker")


def test_tools_nonzero_exit_code_fails_and_shows_the_log(stub_bin: Path) -> None:
    _configure(stub_bin, stacks=DATA_DEPLOYED, tools_exit=1, tools_log="not ready: pgvector")
    result = _tools(stub_bin, "check")
    assert result.returncode != 0
    assert re.search(r"FAIL\s+check: container exit code 1", result.stdout)
    assert "| not ready: pgvector" in result.stdout
    assert "result: FAIL" in result.stdout


def test_tools_wait_for_the_task_to_stop(stub_bin: Path) -> None:
    _configure(stub_bin, stacks=DATA_DEPLOYED, tools_polls=3)
    result = _tools(stub_bin, "migrate")
    assert result.returncode == 0, _output(result)
    assert len(_ops(stub_bin, "describe-tasks")) >= 4


def test_tools_refuse_when_a_task_is_already_running(stub_bin: Path) -> None:
    _configure(stub_bin, stacks=DATA_DEPLOYED, tools_running=True)
    result = _tools(stub_bin, "migrate")
    assert result.returncode != 0
    assert "already running" in result.stderr
    assert "9999888877776666" in result.stderr
    assert not _ops(stub_bin, "run-task", "stop-task")  # it is not ours to stop


def test_tools_say_so_when_the_data_stack_is_not_deployed(stub_bin: Path) -> None:
    _configure(stub_bin, stacks={})
    result = _tools(stub_bin, "migrate")
    assert result.returncode != 0
    assert "precertly-data is not deployed" in result.stderr
    assert not _ops(stub_bin, "run-task")


def test_tools_refuse_a_malformed_output(stub_bin: Path) -> None:
    _configure(stub_bin, stacks=DATA_DEPLOYED, outputs={"ToolsSecurityGroupId": "sg-1,--evil"})
    result = _tools(stub_bin, "migrate")
    assert result.returncode != 0
    assert "unexpected value" in result.stderr
    assert not _ops(stub_bin, "run-task")


def test_tools_run_task_that_starts_nothing_fails(stub_bin: Path) -> None:
    _configure(stub_bin, stacks=DATA_DEPLOYED, tools_run_fail="RESOURCE:CPU")
    result = _tools(stub_bin, "migrate")
    assert result.returncode != 0
    assert "run-task started nothing (RESOURCE:CPU)" in result.stdout
    assert "result: FAIL" in result.stdout


@pytest.mark.parametrize("answer", ["", "n\n", "yes please\n"])
def test_tools_load_asks_and_yes_does_not_answer_for_you(stub_bin: Path, answer: str) -> None:
    _configure(stub_bin, stacks=DATA_DEPLOYED)
    result = _tools(stub_bin, "load", "--yes", stdin=answer)
    assert result.returncode != 0
    assert "Continue? [y/N]" in result.stderr
    assert "not confirmed; nothing started" in result.stderr
    _assert_no_calls(stub_bin)


def test_tools_load_runs_after_a_yes_and_runs_the_ingest(stub_bin: Path) -> None:
    _configure(stub_bin, stacks=DATA_DEPLOYED)
    result = _tools(stub_bin, "load", stdin="y\n")
    assert result.returncode == 0, _output(result)
    (run_task,) = _ops(stub_bin, "ecs run-task")
    assert '"command":["python","-m","precertly.ingest","evals/bundles","--embed"]' in run_task
    assert "Continue? [y/N]" in result.stderr


def test_tools_stop_a_task_that_outlives_its_time_limit(stub_bin: Path) -> None:
    _configure(stub_bin, stacks=DATA_DEPLOYED, tools_never_stops=True)
    result = _tools(stub_bin, "migrate", env={"DEPLOY_TOOLS_TIMEOUT": "1"})
    assert result.returncode != 0
    assert "after 1s" in result.stdout
    assert _index(stub_bin, "ecs run-task") < _index(stub_bin, "ecs stop-task")
    assert re.search(r"PASS\s+no precertly-tools task running", result.stdout)
    assert "result: FAIL" in result.stdout


def test_tools_stop_the_task_when_a_later_call_fails(stub_bin: Path) -> None:
    """The cleanup trap runs on a die, not only on the paths the script expects."""
    _configure(stub_bin, stacks=DATA_DEPLOYED, tools_never_stops=True, tools_describe_fail=True)
    result = _tools(stub_bin, "migrate")
    assert result.returncode != 0
    assert "aws call failed" in result.stderr
    assert _index(stub_bin, "ecs run-task") < _index(stub_bin, "ecs stop-task")
    assert "result: FAIL" in result.stdout


def test_tools_report_loudly_when_the_stop_cannot_be_confirmed(stub_bin: Path) -> None:
    _configure(stub_bin, stacks=DATA_DEPLOYED, tools_never_stops=True, tools_stop_fail=True)
    result = _tools(stub_bin, "migrate", env={"DEPLOY_TOOLS_TIMEOUT": "1"})
    assert result.returncode != 0
    assert re.search(r"FAIL\s+stop not confirmed", result.stdout)
    assert "NOW" in result.stdout
    assert len(_ops(stub_bin, "stop-task")) == 3


@pytest.mark.parametrize(("sig", "status"), [(signal.SIGTERM, 143), (signal.SIGINT, 130)])
def test_tools_stop_the_task_when_interrupted(
    stub_bin: Path, sig: signal.Signals, status: int
) -> None:
    _configure(stub_bin, stacks=DATA_DEPLOYED, tools_never_stops=True)
    proc = subprocess.Popen(
        ["bash", str(SCRIPT), "tools", "migrate"],
        cwd=_repo(stub_bin),
        env={**_env(stub_bin, None), "DEPLOY_TOOLS_TIMEOUT": "60", "DEPLOY_POLL_SECONDS": "1"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 30
    while not _ops(stub_bin, "describe-tasks"):
        assert time.monotonic() < deadline, "tools never reached the wait"
        time.sleep(0.05)
    proc.send_signal(sig)
    stdout, stderr = proc.communicate(timeout=30)
    assert proc.returncode == status, stdout + stderr
    assert _index(stub_bin, "ecs run-task") < _index(stub_bin, "ecs stop-task")
    assert re.search(r"PASS\s+no precertly-tools task running", stdout)
    assert "result: FAIL" in stdout


def test_tools_commands_exist_in_the_image() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "COPY src ./src" in dockerfile  # precertly.db.check and precertly.ingest
    assert "COPY evals/bundles ./evals/bundles" in dockerfile
    assert "COPY migrations ./migrations" in dockerfile and "alembic.ini" in dockerfile
    assert (ROOT / "src" / "precertly" / "db" / "check.py").exists()
    assert (ROOT / "src" / "precertly" / "ingest" / "__main__.py").exists()
    assert any((ROOT / "evals" / "bundles").glob("*.json"))


# ---------------------------------------------------------------------------------------------
# precertly-web
# ---------------------------------------------------------------------------------------------


def test_web_stack_dry_run_passes_the_real_domain_and_hides_the_certificate(
    stub_bin: Path,
) -> None:
    result = _deploy(stub_bin, "stack", "precertly-web", "--dry-run", env=WEB_ENV)
    assert result.returncode == 0, _output(result)
    tag = _head_tag(stub_bin)
    assert (
        f"cdk deploy --exclusively precertly-web -c budgetEmail=REDACTED -c imageTag={tag} "
        f"-c webDomain={WEB_DOMAIN} -c webCertificateArn=REDACTED"
    ) in result.stdout
    assert "is in ECR" not in result.stdout
    assert WEB_ARN not in _output(result)
    assert "210987654321" not in _output(result)
    assert FAKE_EMAIL not in _output(result)
    _assert_no_calls(stub_bin)


@pytest.mark.parametrize("name", ["precertly-base", "precertly-app", "precertly-data"])
def test_the_other_stacks_get_the_placeholders_even_when_web_settings_exist(
    stub_bin: Path, name: str
) -> None:
    result = _deploy(stub_bin, "stack", name, "--dry-run", env=WEB_ENV)
    assert result.returncode == 0, _output(result)
    assert f"-c webDomain={PLACEHOLDER_DOMAIN} -c webCertificateArn={PLACEHOLDER_ARN}" in (
        result.stdout
    )
    assert WEB_DOMAIN not in _output(result)
    assert "210987654321" not in _output(result)
    _assert_no_calls(stub_bin)


@pytest.mark.parametrize("name", ["precertly-base", "precertly-app", "precertly-data"])
def test_the_other_stacks_need_no_web_settings_at_all(stub_bin: Path, name: str) -> None:
    for command in ("stack", "up"):
        result = _deploy(stub_bin, command, name, "--dry-run")
        assert result.returncode == 0, _output(result)
        assert f"-c webDomain={PLACEHOLDER_DOMAIN}" in result.stdout
    _assert_no_calls(stub_bin)


@pytest.mark.parametrize("command", ["stack", "up"])
@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({}, "WEB_DOMAIN is not set"),
        ({"WEB_DOMAIN": WEB_DOMAIN}, "WEB_CERTIFICATE_ARN is not set"),
        ({"WEB_CERTIFICATE_ARN": WEB_ARN}, "WEB_DOMAIN is not set"),
        ({**WEB_ENV, "WEB_DOMAIN": PLACEHOLDER_DOMAIN}, "WEB_DOMAIN looks like a placeholder"),
        ({**WEB_ENV, "WEB_DOMAIN": "other.invalid"}, "WEB_DOMAIN looks like a placeholder"),
        (
            {**WEB_ENV, "WEB_CERTIFICATE_ARN": PLACEHOLDER_ARN},
            "WEB_CERTIFICATE_ARN looks like a placeholder",
        ),
        (
            {**WEB_ENV, "WEB_CERTIFICATE_ARN": WEB_ARN.replace("210987654321", "000000000000")},
            "WEB_CERTIFICATE_ARN looks like a placeholder",
        ),
        ({**WEB_ENV, "WEB_DOMAIN": "Site.Example.org"}, "WEB_DOMAIN is not a plain lowercase"),
        ({**WEB_ENV, "WEB_DOMAIN": "https://site.example.org"}, "WEB_DOMAIN is not a plain"),
        ({**WEB_ENV, "WEB_DOMAIN": "site.example.org/"}, "WEB_DOMAIN is not a plain"),
        ({**WEB_ENV, "WEB_DOMAIN": "1.2.3.4"}, "WEB_DOMAIN is not a plain"),
        (
            {**WEB_ENV, "WEB_CERTIFICATE_ARN": WEB_ARN.replace("us-east-1", "us-east-2")},
            "WEB_CERTIFICATE_ARN is not an ACM certificate ARN in us-east-1",
        ),
        ({**WEB_ENV, "WEB_CERTIFICATE_ARN": "nope"}, "WEB_CERTIFICATE_ARN is not an ACM"),
    ],
)
def test_web_stack_refuses_missing_or_placeholder_settings(
    stub_bin: Path, command: str, env: dict[str, str], message: str
) -> None:
    for extra in ([], ["--dry-run"]):
        result = _deploy(stub_bin, command, "precertly-web", *extra, env=env)
        assert result.returncode != 0
        assert message in result.stderr
        assert WEB_ARN not in _output(result)
        assert FAKE_EMAIL not in _output(result)
    _assert_no_calls(stub_bin)


def test_web_stack_still_needs_the_budget_email(stub_bin: Path) -> None:
    result = _deploy(stub_bin, "stack", "precertly-web", "--dry-run", email=None, env=WEB_ENV)
    assert result.returncode != 0
    assert "BUDGET_EMAIL" in result.stderr
    _assert_no_calls(stub_bin)


def test_web_settings_are_read_from_env_local(stub_bin: Path) -> None:
    (_repo(stub_bin) / ".env.local").write_text(
        f"# the site\nWEB_DOMAIN={WEB_DOMAIN}\nWEB_CERTIFICATE_ARN='{WEB_ARN}'\nOTHER=x\n"
    )
    dry = _deploy(stub_bin, "stack", "precertly-web", "--dry-run")
    assert dry.returncode == 0, _output(dry)
    assert f"-c webDomain={WEB_DOMAIN} -c webCertificateArn=REDACTED" in dry.stdout
    assert WEB_ARN not in _output(dry)
    _assert_no_calls(stub_bin)

    _configure(stub_bin)
    live = _deploy(stub_bin, "stack", "precertly-web")
    assert live.returncode == 0, _output(live)
    assert WEB_ARN not in _output(live)
    assert any(f"webCertificateArn={WEB_ARN}" in c and "cdk deploy" in c for c in _calls(stub_bin))


def test_web_environment_wins_over_env_local(stub_bin: Path) -> None:
    (_repo(stub_bin) / ".env.local").write_text("WEB_DOMAIN=file.example.org\n")
    _configure(stub_bin)
    result = _deploy(stub_bin, "stack", "precertly-web", env=WEB_ENV)
    assert result.returncode == 0, _output(result)
    # Exactly the environment's domain reached the CDK; the .env.local one (a different
    # hostname) did not.
    domains = [
        token
        for call in _calls(stub_bin)
        if "cdk deploy" in call
        for token in call.split()
        if token.startswith("webDomain=")
    ]
    assert domains == [f"webDomain={WEB_DOMAIN}"]


@pytest.mark.parametrize(
    "line",
    ["WEB_DOMAIN=Not A Domain", f"WEB_CERTIFICATE_ARN={WEB_ARN.replace('us-east-1', 'eu-west-1')}"],
)
def test_malformed_web_settings_in_env_local_are_refused_without_echoing_them(
    stub_bin: Path, line: str
) -> None:
    (_repo(stub_bin) / ".env.local").write_text(f"{line}\n")
    result = _deploy(stub_bin, "stack", "precertly-web", "--dry-run")
    assert result.returncode != 0
    assert "in .env.local" in result.stderr
    assert line.split("=", 1)[1] not in _output(result)
    _assert_no_calls(stub_bin)


def test_a_bad_web_line_in_env_local_does_not_block_the_other_stacks(stub_bin: Path) -> None:
    (_repo(stub_bin) / ".env.local").write_text("WEB_DOMAIN=Not A Domain\n")
    result = _deploy(stub_bin, "stack", "precertly-base", "--dry-run")
    assert result.returncode == 0, _output(result)
    assert f"-c webDomain={PLACEHOLDER_DOMAIN}" in result.stdout
    _assert_no_calls(stub_bin)


def test_up_web_dry_run_has_no_image_step_and_always_asks(stub_bin: Path) -> None:
    result = _deploy(stub_bin, "up", "precertly-web", "--dry-run", "--yes", env=WEB_ENV)
    assert result.returncode == 0, _output(result)
    out = result.stdout
    assert "docker" not in out
    assert "describe-images" not in out
    diff = out.index("cdk diff --exclusively precertly-web")
    ask = out.index("Deploy? [y/N]")
    deploy = out.index("cdk deploy --exclusively precertly-web")
    assert diff < ask < deploy
    assert WEB_ARN not in _output(result)
    _assert_no_calls(stub_bin)


def test_up_web_redacts_the_email_and_certificate_in_the_diff(stub_bin: Path) -> None:
    _configure(stub_bin, diff_text="[+] distribution\n cert {arn}\n alert {email}")
    result = _deploy(stub_bin, "up", "precertly-web", stdin="y\n", env=WEB_ENV)
    assert result.returncode == 0, _output(result)
    assert "cert REDACTED" in result.stdout
    assert "alert REDACTED" in result.stdout
    assert WEB_ARN not in _output(result)
    assert FAKE_EMAIL not in _output(result)
    npx = [c for c in _calls(stub_bin) if c.startswith("npx")]
    assert [c.split()[3] for c in npx] == ["diff", "deploy"]
    assert all(
        "--exclusively precertly-web" in c and f"webCertificateArn={WEB_ARN}" in c for c in npx
    )
    assert not any(c.startswith("docker") for c in _calls(stub_bin))


def test_up_web_stops_when_declined(stub_bin: Path) -> None:
    _configure(stub_bin)
    result = _deploy(stub_bin, "up", "precertly-web", "--yes", stdin="n\n", env=WEB_ENV)
    assert result.returncode != 0
    assert not any("cdk deploy" in c for c in _calls(stub_bin))


def test_up_web_redacts_the_certificate_when_the_diff_fails(stub_bin: Path) -> None:
    _configure(stub_bin, diff_fail="bad certificate {arn}")
    result = _deploy(stub_bin, "up", "precertly-web", stdin="y\n", env=WEB_ENV)
    assert result.returncode != 0
    assert "bad certificate REDACTED" in result.stderr
    assert WEB_ARN not in _output(result)


def test_there_is_no_publish_command_yet(stub_bin: Path) -> None:
    result = _deploy(stub_bin, "publish", env=WEB_ENV)
    assert result.returncode != 0
    assert "unknown command" in result.stderr


def test_status_dry_run_lists_the_web_queries(stub_bin: Path) -> None:
    result = _deploy(stub_bin, "status", "--dry-run")
    assert "describe-stacks --stack-name precertly-web" in result.stdout
    assert "DistributionId" in result.stdout
    assert "BucketName" in result.stdout
    _assert_no_calls(stub_bin)


def test_status_reports_the_web_stack(stub_bin: Path) -> None:
    _configure(stub_bin, stacks={"precertly-web": "CREATE_COMPLETE"})
    result = _deploy(stub_bin, "status")
    assert result.returncode == 0, _output(result)
    assert re.search(r"precertly-web\s+CREATE_COMPLETE", result.stdout)
    assert (
        "web site (precertly-web)\n  stack CREATE_COMPLETE, distribution E1A2B3C4D5E6F7, "
        "bucket precertly-web-sitebucket397a1860-abc123def456"
    ) in result.stdout


def test_status_says_when_the_web_stack_is_not_deployed(stub_bin: Path) -> None:
    _configure(stub_bin)
    result = _deploy(stub_bin, "status")
    assert result.returncode == 0, _output(result)
    assert "web site (precertly-web)\n  not deployed" in result.stdout


def test_placeholders_match_the_cdk_tests_and_ci() -> None:
    script = SCRIPT.read_text()
    support = (ROOT / "infra" / "tests" / "support.py").read_text()
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert f'readonly PLACEHOLDER_WEB_DOMAIN="{PLACEHOLDER_DOMAIN}"' in script
    assert f'readonly PLACEHOLDER_WEB_CERTIFICATE_ARN="{PLACEHOLDER_ARN}"' in script
    assert f'WEB_DOMAIN = "{PLACEHOLDER_DOMAIN}"' in support
    assert PLACEHOLDER_ARN in support.replace('"\n    "', "")
    assert f"-c webDomain={PLACEHOLDER_DOMAIN}" in ci
    assert f"-c webCertificateArn={PLACEHOLDER_ARN}" in ci


def test_web_stack_name_matches_the_cdk_app() -> None:
    assert "readonly STACKS=(precertly-base precertly-app precertly-data precertly-web)" in (
        SCRIPT.read_text()
    )
    app = (ROOT / "infra" / "app.py").read_text()
    assert '"precertly-web")' in app
