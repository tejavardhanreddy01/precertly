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
    for tool in ("aws", "curl", "docker", "npx", "session-manager-plugin", "uv", "npm", "node"):
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
        ("web",),
        ("web", "bogus"),
        ("web", "publish", "extra"),
        ("web", "publish", "--bedrock"),
        ("web", "smoke", "extra"),
        ("web", "smoke", "--bedrock"),
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


def test_publish_is_only_a_web_subcommand(stub_bin: Path) -> None:
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
    stacks = next(
        line for line in SCRIPT.read_text().splitlines() if line.startswith("readonly STACKS=(")
    )
    assert "precertly-web" in stacks
    app = (ROOT / "infra" / "app.py").read_text()
    assert '"precertly-web",' in app


# ---------------------------------------------------------------------------------------------
# web publish
# ---------------------------------------------------------------------------------------------

BUCKET = "precertly-web-sitebucket397a1860-abc123def456"
DIST_FILES = (
    "index.html",
    "404.html",
    "favicon.svg",
    "assets/index-abc123.js",
    "assets/index-abc123.css",
    "data/index.json",
    "data/policies.json",
)
ASSETS_CC = "public,max-age=31536000,immutable"
REST_CC = "public,max-age=300"
INDEX_CC = "public,max-age=60"
WEB_STACK_OK = {"precertly-web": "CREATE_COMPLETE"}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.fixture
def publish_repo(stub_bin: Path) -> Path:
    """The throwaway repository with an origin, HEAD pushed, and a built-looking web/dist.

    web/dist is git-ignored, as in the real repository. The stub npm builds nothing, so the
    files are created here.
    """
    repo = _repo(stub_bin)
    origin = stub_bin.parent / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    (repo / ".gitignore").write_text("web/dist/\n")
    (repo / "web").mkdir()
    (repo / "web" / "package.json").write_text("{}\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "web")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "origin", "HEAD:refs/heads/main")
    for name in DIST_FILES:
        path = repo / "web" / "dist" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{name}\n")
    return repo


def _publish(
    stub_bin: Path, *args: str, stdin: str = "y\n", env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    # No BUDGET_EMAIL and no web settings: publishing needs neither.
    return _deploy(stub_bin, "web", "publish", *args, email=None, stdin=stdin, env=env)


def _s3_writes(stub_bin: Path) -> list[str]:
    """The calls that upload: every s3 sync or cp except the --dryrun previews."""
    return [
        c
        for c in _calls(stub_bin)
        if c.startswith(("aws s3 sync", "aws s3 cp")) and "--dryrun" not in c
    ]


def _index_of(calls: list[str], prefix: str, nth: int = 0) -> int:
    found = [i for i, c in enumerate(calls) if c.startswith(prefix)]
    assert len(found) > nth, f"no call starting with {prefix!r}: {calls}"
    return found[nth]


def test_web_publish_is_in_the_usage_text(stub_bin: Path) -> None:
    result = _deploy(stub_bin, "--help")
    assert "scripts/deploy.sh web publish [--yes] [--dry-run]" in result.stdout
    assert "Publish? [y/N]" in result.stdout
    assert "--yes does NOT skip" in result.stdout


def test_web_publish_dry_run_prints_every_command_and_calls_nothing(stub_bin: Path) -> None:
    result = _deploy(stub_bin, "web", "publish", "--dry-run", email=None)
    assert result.returncode == 0, _output(result)
    out = result.stdout
    assert re.search(r"^commit: [0-9a-f]{12}$", out, re.MULTILINE)
    for command in (
        "+ uv sync",
        "+ (cd web && npm ci)",
        "+ (cd web && npm run prepare-data)",
        "+ (cd web && npm run build)",
        "+ (cd web && node scripts/check-dist.mjs)",
        "aws s3 sync web/dist/assets s3://BUCKET_NAME/assets",
        "aws s3 sync web/dist s3://BUCKET_NAME --exclude assets/\\* --exclude index.html --delete",
        "aws s3 cp web/dist/index.html s3://BUCKET_NAME/index.html",
        "aws cloudfront create-invalidation --distribution-id DISTRIBUTION_ID --paths /index.html "
        "/404.html /favicon.svg /data/\\*",
        "aws cloudfront get-invalidation --distribution-id DISTRIBUTION_ID",
    ):
        assert command in out, command
    # in the order the real run uses: build, preview, the question, then the three uploads
    preview, _, uploads = out.partition("dry-run: upload, in this order:")
    assert preview.index("node scripts/check-dist.mjs") < preview.index("--dryrun")
    assert "Publish? [y/N]" in preview
    positions = [
        uploads.index(m)
        for m in (
            "s3://BUCKET_NAME/assets --cache-control",
            "--delete --cache-control",
            "s3 cp",
            "head-object",
            "create-invalidation",
            "get-invalidation",
        )
    ]
    assert positions == sorted(positions)
    assert "Publish? [y/N]" in out
    assert "--yes does not skip it" in out
    assert "DNS" not in out
    assert ACCOUNT not in _output(result)
    _assert_no_calls(stub_bin)


def test_web_publish_dry_run_ignores_a_dirty_or_unpushed_tree(stub_bin: Path) -> None:
    (_repo(stub_bin) / "untracked.txt").write_text("x")
    result = _deploy(stub_bin, "web", "publish", "--dry-run", email=None)
    assert result.returncode == 0, _output(result)
    assert "skipping the git clean and pushed checks" in result.stdout
    _assert_no_calls(stub_bin)


def test_web_publish_refuses_a_dirty_tree(stub_bin: Path, publish_repo: Path) -> None:
    (publish_repo / "web" / "package.json").write_text('{"changed": true}\n')
    result = _publish(stub_bin)
    assert result.returncode != 0
    assert "working tree is not clean" in result.stderr
    _assert_no_calls(stub_bin)


def test_web_publish_refuses_an_untracked_file(stub_bin: Path, publish_repo: Path) -> None:
    (publish_repo / "scratch.txt").write_text("x")
    result = _publish(stub_bin)
    assert result.returncode != 0
    assert "working tree is not clean" in result.stderr
    _assert_no_calls(stub_bin)


def test_web_publish_refuses_a_head_that_is_not_pushed(stub_bin: Path, publish_repo: Path) -> None:
    _git(publish_repo, "commit", "-q", "--allow-empty", "-m", "local only")
    result = _publish(stub_bin)
    assert result.returncode != 0
    assert "HEAD is not pushed" in result.stderr
    _assert_no_calls(stub_bin)


def test_web_publish_refuses_when_the_stack_is_not_deployed(
    stub_bin: Path, publish_repo: Path
) -> None:
    _configure(stub_bin)
    result = _publish(stub_bin)
    assert result.returncode != 0
    assert "precertly-web is not deployed" in result.stderr
    assert "scripts/deploy.sh up precertly-web" in result.stderr
    calls = _calls(stub_bin)
    assert all(c.startswith("aws cloudformation") for c in calls), calls


@pytest.mark.parametrize(
    "state",
    ["CREATE_IN_PROGRESS", "UPDATE_IN_PROGRESS", "UPDATE_ROLLBACK_COMPLETE", "DELETE_FAILED"],
)
def test_web_publish_refuses_a_stack_that_is_not_complete(
    stub_bin: Path, publish_repo: Path, state: str
) -> None:
    _configure(stub_bin, stacks={"precertly-web": state})
    result = _publish(stub_bin)
    assert result.returncode != 0
    assert f"precertly-web is {state}" in result.stderr
    calls = _calls(stub_bin)
    assert all(c.startswith("aws cloudformation") for c in calls), calls


def test_web_publish_accepts_update_complete(stub_bin: Path, publish_repo: Path) -> None:
    _configure(stub_bin, stacks={"precertly-web": "UPDATE_COMPLETE"})
    result = _publish(stub_bin)
    assert result.returncode == 0, _output(result)


def test_web_publish_refuses_a_stack_with_no_outputs(stub_bin: Path, publish_repo: Path) -> None:
    _configure(stub_bin, stacks=WEB_STACK_OK, no_outputs=True)
    result = _publish(stub_bin)
    assert result.returncode != 0
    assert "no output BucketName" in result.stderr
    assert not _s3_writes(stub_bin)
    assert not any(c.startswith(("uv", "npm")) for c in _calls(stub_bin))


def test_web_publish_refuses_a_malformed_output(stub_bin: Path, publish_repo: Path) -> None:
    _configure(stub_bin, stacks=WEB_STACK_OK, outputs={"SiteUrl": "http://x y"})
    result = _publish(stub_bin)
    assert result.returncode != 0
    assert "output SiteUrl of precertly-web has an unexpected value" in result.stderr
    assert not any(c.startswith(("uv", "npm")) for c in _calls(stub_bin))


@pytest.mark.parametrize(
    "step, label",
    [
        ("uv sync", "uv sync"),
        ("npm ci", "npm ci"),
        ("npm run prepare-data", "npm run prepare-data"),
        ("npm run build", "npm run build"),
        ("node scripts/check-dist.mjs", "check-dist"),
    ],
)
def test_a_failing_build_step_aborts_before_any_s3_call(
    stub_bin: Path, publish_repo: Path, step: str, label: str
) -> None:
    _configure(stub_bin, stacks=WEB_STACK_OK, build_fail={step: "boom"})
    result = _publish(stub_bin)
    assert result.returncode != 0
    assert f"build step failed: {label}; nothing was uploaded" in result.stderr
    calls = _calls(stub_bin)
    assert not any(c.startswith(("aws s3", "aws cloudfront")) for c in calls), calls
    # and nothing after the failing step ran
    order = [
        "uv sync",
        "npm ci",
        "npm run prepare-data",
        "npm run build",
        "node scripts/check-dist",
    ]
    later = order[order.index(next(o for o in order if step.startswith(o))) + 1 :]
    assert not any(c.startswith(tuple(later)) for c in calls), calls


def test_the_build_uses_npm_ci_and_never_npm_install(stub_bin: Path, publish_repo: Path) -> None:
    _configure(stub_bin, stacks=WEB_STACK_OK)
    result = _publish(stub_bin)
    assert result.returncode == 0, _output(result)
    npm = [c for c in _calls(stub_bin) if c.startswith("npm")]
    assert npm == ["npm ci", "npm run prepare-data", "npm run build"]


def test_a_build_that_changes_a_tracked_file_aborts(stub_bin: Path, publish_repo: Path) -> None:
    _configure(stub_bin, stacks=WEB_STACK_OK, build_touches="package.json")
    result = _publish(stub_bin)
    assert result.returncode != 0
    assert "the build changed tracked files" in result.stderr
    assert not _s3_writes(stub_bin)
    assert not any(c.startswith("aws s3") for c in _calls(stub_bin))


def test_a_build_with_no_index_html_aborts(stub_bin: Path, publish_repo: Path) -> None:
    (publish_repo / "web" / "dist" / "index.html").unlink()
    _configure(stub_bin, stacks=WEB_STACK_OK)
    result = _publish(stub_bin)
    assert result.returncode != 0
    assert "did not produce web/dist/index.html" in result.stderr
    assert not any(c.startswith("aws s3") for c in _calls(stub_bin))


def test_web_publish_runs_in_order_and_ends_with_the_invalidation(
    stub_bin: Path, publish_repo: Path
) -> None:
    _configure(stub_bin, stacks=WEB_STACK_OK)
    result = _publish(stub_bin)
    assert result.returncode == 0, _output(result)
    calls = _calls(stub_bin)
    order = [
        _index_of(
            calls,
            "aws cloudformation describe-stacks --stack-name precertly-web --query "
            "Stacks[0].StackStatus",
        ),
        _index_of(calls, "uv sync"),
        _index_of(calls, "npm ci"),
        _index_of(calls, "npm run prepare-data"),
        _index_of(calls, "npm run build"),
        _index_of(calls, "node scripts/check-dist.mjs"),
        _index_of(calls, "aws s3 sync web/dist/assets", 0),  # preview
        _index_of(calls, "aws s3 sync web/dist s3://", 0),  # preview
        _index_of(calls, "aws s3 sync web/dist/assets", 1),  # upload
        _index_of(calls, "aws s3 sync web/dist s3://", 1),
        _index_of(calls, "aws s3 cp web/dist/index.html"),
        _index_of(calls, "aws s3api head-object"),
        _index_of(calls, "aws cloudfront create-invalidation"),
        _index_of(calls, "aws cloudfront get-invalidation"),
    ]
    assert order == sorted(order), order
    assert "--dryrun" in calls[order[6]] and "--dryrun" in calls[order[7]]
    assert "--dryrun" not in calls[order[8]] and "--dryrun" not in calls[order[9]]
    assert len(_s3_writes(stub_bin)) == 3


def test_web_publish_uses_these_cache_control_values_and_only_these(
    stub_bin: Path, publish_repo: Path
) -> None:
    _configure(stub_bin, stacks=WEB_STACK_OK)
    result = _publish(stub_bin)
    assert result.returncode == 0, _output(result)
    assets, rest, index = _s3_writes(stub_bin)
    assert assets == (
        f"aws s3 sync web/dist/assets s3://{BUCKET}/assets --cache-control {ASSETS_CC}"
    )
    assert rest == (
        f"aws s3 sync web/dist s3://{BUCKET} --exclude assets/* --exclude index.html "
        f"--delete --cache-control {REST_CC}"
    )
    assert (
        index
        == f"aws s3 cp web/dist/index.html s3://{BUCKET}/index.html --cache-control {INDEX_CC}"
    )
    # the previews carry the same values, so they show the real command minus the writes
    for call in _calls(stub_bin):
        if call.startswith("aws s3 sync") and "--dryrun" in call:
            assert call.removesuffix(" --dryrun") in (assets, rest)


def test_delete_is_never_applied_to_assets(stub_bin: Path, publish_repo: Path) -> None:
    _configure(
        stub_bin,
        stacks=WEB_STACK_OK,
        s3_stale=["assets/index-old999.js", "data/retired.json", "old-page.html"],
    )
    result = _publish(stub_bin)
    assert result.returncode == 0, _output(result)
    s3_calls = [c for c in _calls(stub_bin) if c.startswith("aws s3 ")]
    for call in s3_calls:
        if "--delete" in call:
            # the only sync that may delete leaves assets/ and index.html alone
            assert "--exclude assets/*" in call and "--exclude index.html" in call
            assert "web/dist/assets" not in call
        if "web/dist/assets" in call or f"s3://{BUCKET}/assets" in call:
            assert "--delete" not in call
    # the preview lists what would go, and an old hashed asset is not among it
    assert "data/retired.json" in result.stdout
    assert "old-page.html" in result.stdout
    assert "index-old999.js" not in result.stdout
    assert "2 to delete" in result.stdout


def test_web_publish_invalidates_only_the_files_that_are_not_hashed(
    stub_bin: Path, publish_repo: Path
) -> None:
    _configure(stub_bin, stacks=WEB_STACK_OK)
    result = _publish(stub_bin)
    assert result.returncode == 0, _output(result)
    invalidations = [c for c in _calls(stub_bin) if "create-invalidation" in c]
    assert invalidations == [
        "aws cloudfront create-invalidation --distribution-id E1A2B3C4D5E6F7 "
        "--paths /index.html /404.html /favicon.svg /data/* "
        "--query Invalidation.Id --output text"
    ]
    assert "/assets" not in invalidations[0]
    assert "/*" not in invalidations[0].replace("/data/*", "")


def test_web_publish_reads_back_the_content_type_of_each_kind(
    stub_bin: Path, publish_repo: Path
) -> None:
    _configure(stub_bin, stacks=WEB_STACK_OK)
    result = _publish(stub_bin)
    assert result.returncode == 0, _output(result)
    keys = [
        c.split("--key ")[1].split(" ")[0]
        for c in _calls(stub_bin)
        if c.startswith("aws s3api head-object")
    ]
    assert keys == [
        "index.html",
        "404.html",
        "favicon.svg",
        "assets/index-abc123.js",
        "assets/index-abc123.css",
        "data/index.json",
    ]


@pytest.mark.parametrize(
    "key, wrong",
    [
        ("data/index.json", "text/plain"),
        ("favicon.svg", "text/xml"),
        ("assets/index-abc123.js", "text/plain"),
        ("assets/index-abc123.css", "application/octet-stream"),
        ("index.html", "binary/octet-stream"),
    ],
)
def test_a_wrong_content_type_stops_before_the_invalidation(
    stub_bin: Path, publish_repo: Path, key: str, wrong: str
) -> None:
    _configure(stub_bin, stacks=WEB_STACK_OK, content_types={key: wrong})
    result = _publish(stub_bin)
    assert result.returncode != 0
    assert f"{key} was uploaded with content type '{wrong}'" in result.stderr
    assert "CloudFront was not invalidated" in result.stderr
    assert not any("cloudfront" in c for c in _calls(stub_bin))


def test_web_publish_prints_the_commit_the_file_count_and_the_site_url(
    stub_bin: Path, publish_repo: Path
) -> None:
    _configure(stub_bin, stacks=WEB_STACK_OK)
    result = _publish(stub_bin)
    assert result.returncode == 0, _output(result)
    sha = _git(publish_repo, "rev-parse", "--short=12", "HEAD").strip()
    assert f"commit: {sha}" in result.stdout
    assert f"published commit {sha}" in result.stdout
    assert f"files in the build: {len(DIST_FILES)}" in result.stdout
    assert "site: https://precertly.example.org" in result.stdout
    assert "DNS" not in _output(result)
    # the bucket, the distribution and the account are for the AWS calls, not for the summary
    assert BUCKET not in result.stdout.split("published commit")[1]
    assert ACCOUNT not in _output(result)


def test_web_publish_asks_and_yes_does_not_answer_for_you(
    stub_bin: Path, publish_repo: Path
) -> None:
    _configure(stub_bin, stacks=WEB_STACK_OK)
    result = _publish(stub_bin, "--yes", stdin="")
    assert result.returncode != 0
    assert "Publish? [y/N]" in result.stderr
    assert "not confirmed; nothing uploaded" in result.stderr
    assert not _s3_writes(stub_bin)
    assert not any("cloudfront" in c for c in _calls(stub_bin))


@pytest.mark.parametrize("answer", ["n\n", "\n", "yes\n", "Y es\n"])
def test_web_publish_stops_on_anything_but_y(
    stub_bin: Path, publish_repo: Path, answer: str
) -> None:
    _configure(stub_bin, stacks=WEB_STACK_OK)
    result = _publish(stub_bin, stdin=answer)
    assert result.returncode != 0
    assert not _s3_writes(stub_bin)


def test_web_publish_with_yes_still_goes_ahead_when_you_answer_y(
    stub_bin: Path, publish_repo: Path
) -> None:
    _configure(stub_bin, stacks=WEB_STACK_OK)
    result = _publish(stub_bin, "--yes", stdin="y\n")
    assert result.returncode == 0, _output(result)
    assert len(_s3_writes(stub_bin)) == 3


def test_the_question_comes_after_the_preview_and_the_build(
    stub_bin: Path, publish_repo: Path
) -> None:
    _configure(stub_bin, stacks=WEB_STACK_OK)
    result = _publish(stub_bin, stdin="n\n")
    calls = _calls(stub_bin)
    assert _index_of(calls, "node scripts/check-dist.mjs") < _index_of(calls, "aws s3 sync")
    assert "what would change" in result.stdout
    assert re.search(r"assets/\s+\d+ to upload, nothing is ever deleted here", result.stdout)


@pytest.mark.parametrize("failing", ["assets", "rest", "index"])
def test_a_failed_upload_stops_the_later_steps(
    stub_bin: Path, publish_repo: Path, failing: str
) -> None:
    _configure(stub_bin, stacks=WEB_STACK_OK, s3_fail=failing)
    result = _publish(stub_bin)
    assert result.returncode != 0
    assert len(_s3_writes(stub_bin)) == ["assets", "rest", "index"].index(failing) + 1
    assert not any("cloudfront" in c for c in _calls(stub_bin))
    if failing != "index":
        assert "index.html was not replaced" in result.stderr


def test_a_failed_preview_uploads_nothing(stub_bin: Path, publish_repo: Path) -> None:
    _configure(stub_bin, stacks=WEB_STACK_OK, s3_fail="dryrun")
    result = _publish(stub_bin)
    assert result.returncode != 0
    assert "nothing was uploaded" in result.stderr
    assert not _s3_writes(stub_bin)


def test_a_slow_invalidation_is_waited_for(stub_bin: Path, publish_repo: Path) -> None:
    _configure(stub_bin, stacks=WEB_STACK_OK, inval_polls=3)
    result = _publish(stub_bin)
    assert result.returncode == 0, _output(result)
    assert len([c for c in _calls(stub_bin) if "get-invalidation" in c]) == 4
    assert "warning" not in result.stderr


def test_an_invalidation_that_does_not_finish_is_a_warning_not_a_failure(
    stub_bin: Path, publish_repo: Path
) -> None:
    _configure(stub_bin, stacks=WEB_STACK_OK, inval_never=True)
    started = time.monotonic()
    result = _publish(stub_bin)
    assert time.monotonic() - started < 30
    assert result.returncode == 0, _output(result)
    assert "had not completed after 2s" in result.stderr
    assert "last status: InProgress" in result.stderr
    assert "The publish is done" in result.stderr
    # the summary still prints
    assert "published commit" in result.stdout
    assert "site: https://precertly.example.org" in result.stdout


def test_an_invalidation_that_cannot_be_created_says_the_files_are_uploaded(
    stub_bin: Path, publish_repo: Path
) -> None:
    _configure(stub_bin, stacks=WEB_STACK_OK, invalidate_fail="AccessDenied")
    result = _publish(stub_bin)
    assert result.returncode != 0
    assert "the files are uploaded, but the CloudFront invalidation could not be created" in (
        result.stderr
    )
    assert len(_s3_writes(stub_bin)) == 3


def test_publishing_never_touches_the_service_or_the_other_stacks(
    stub_bin: Path, publish_repo: Path
) -> None:
    _configure(stub_bin, stacks=WEB_STACK_OK)
    result = _publish(stub_bin)
    assert result.returncode == 0, _output(result)
    calls = _calls(stub_bin)
    assert not any(c.startswith(("aws ecs", "aws ecr", "aws rds", "docker", "npx")) for c in calls)
    stack_names = {m.group(1) for c in calls if (m := re.search(r"--stack-name (\S+)", c))}
    assert stack_names == {"precertly-web"}


def test_status_is_unchanged_by_the_web_publish_command() -> None:
    script = SCRIPT.read_text()
    status = script[script.index("cmd_status() {") : script.index("cmd_down() {")]
    assert "web site (${WEB_STACK})" in status
    assert "s3" not in status.replace("s3_", "")


# ---------------------------------------------------------------------------------------------
# web smoke
# ---------------------------------------------------------------------------------------------

SITE_URL = "https://precertly.example.org"
HOST = "precertly.example.org"
ASSET_JS = "/assets/index-abc123.js"
ASSET_CSS = "/assets/index-abc123.css"
# /, the script and the stylesheet, four data files, the favicon, /nope and the plain-HTTP request.
REQUESTS = 10
DATA_PATHS = (
    "/data/index.json",
    "/data/schema.json",
    "/data/policies/facet-joint-interventions.json",
    "/data/cases/facet-01.json",
)


def _smoke(stub_bin: Path, *args: str, **config: Any) -> subprocess.CompletedProcess[str]:
    """web smoke against a complete stack, with the site as the stub serves it unless `site`
    (and the other stub keys) say otherwise. Needs no budget email and no git state."""
    config.setdefault("stacks", WEB_STACK_OK)
    _configure(stub_bin, **config)
    return _deploy(stub_bin, "web", "smoke", *args, email=None)


def _curl_calls(stub_bin: Path) -> list[str]:
    return [c for c in _calls(stub_bin) if c.startswith("curl ")]


def _curl_urls(stub_bin: Path) -> list[str]:
    return [c.split()[-1] for c in _curl_calls(stub_bin)]


def _lines(result: subprocess.CompletedProcess[str], prefix: str) -> list[str]:
    return [line for line in result.stdout.splitlines() if line.startswith(prefix)]


def test_web_smoke_is_in_the_usage_text(stub_bin: Path) -> None:
    out = _deploy(stub_bin, "--help").stdout
    assert "scripts/deploy.sh web smoke [--dry-run]" in out
    assert "Read-only check of the published site" in out
    assert _deploy(stub_bin, "web", "bogus").stderr.count("web publish|smoke") == 1
    assert _deploy(stub_bin, "web").stderr.count("web publish|smoke") == 1


def test_web_smoke_passes_on_a_healthy_site(stub_bin: Path) -> None:
    result = _smoke(stub_bin)
    assert result.returncode == 0, _output(result)
    assert _lines(result, "FAIL") == []
    assert "result: PASS" in result.stdout
    assert re.search(r"^\d+ passed, 0 failed$", result.stdout, re.MULTILINE)
    # every kind of check ran
    for expected in (
        "PASS GET / status (200)",
        "PASS GET / content-type: text/html (text/html)",
        "PASS GET / cache-control: max-age=60 (public,max-age=60)",
        "PASS GET / strict-transport-security: max-age=31536000 (max-age=31536000)",
        "PASS GET / strict-transport-security: no includeSubDomains and no preload "
        "(max-age=31536000)",
        "PASS GET / content-security-policy: default-src 'none' (",
        "PASS GET / content-security-policy: frame-ancestors 'none' (",
        "PASS GET / content-security-policy: the header to be present (",
        "PASS GET / referrer-policy: the header to be present (",
        "PASS GET / permissions-policy: the header to be present (",
        "PASS GET / x-content-type-options: nosniff (nosniff)",
        "PASS GET / x-frame-options: DENY (DENY)",
        f"PASS GET {ASSET_JS} content-type: text/javascript or application/javascript "
        "(text/javascript)",
        f"PASS GET {ASSET_JS} cache-control: immutable (public,max-age=31536000,immutable)",
        f"PASS GET {ASSET_CSS} content-type: text/css (text/css)",
        "PASS GET /favicon.svg content-type: image/svg+xml (image/svg+xml)",
        "PASS GET /nope status (404)",
        'PASS GET /nope body contains "This page does not exist"',
        f"PASS GET http://{HOST}/ status (301)",
        f"PASS GET http://{HOST}/ location (https://{HOST}/)",
    ):
        assert expected in result.stdout, expected
    for path in DATA_PATHS:
        assert f"PASS GET {path} content-type: application/json (application/json)" in result.stdout
    assert _curl_urls(stub_bin) == [
        f"{SITE_URL}/",
        f"{SITE_URL}{ASSET_JS}",
        f"{SITE_URL}{ASSET_CSS}",
        *(f"{SITE_URL}{p}" for p in DATA_PATHS),
        f"{SITE_URL}/favicon.svg",
        f"{SITE_URL}/nope",
        f"http://{HOST}/",
    ]


def test_web_smoke_pass_lines_name_each_header_check_once(stub_bin: Path) -> None:
    result = _smoke(stub_bin)
    passes = [line.split(" (", 1)[0] for line in _lines(result, "PASS")]
    assert len(passes) == len(set(passes)), "two PASS lines carry the same label"
    csp = [p for p in passes if p.startswith("PASS GET / content-security-policy")]
    assert csp == [
        "PASS GET / content-security-policy: the header to be present",
        "PASS GET / content-security-policy: default-src 'none'",
        "PASS GET / content-security-policy: frame-ancestors 'none'",
    ]


def test_web_smoke_prints_the_tls_result_once(stub_bin: Path) -> None:
    result = _smoke(stub_bin)
    tls = [line for line in result.stdout.splitlines() if "TLS" in line]
    assert len(tls) == 1, tls  # a pass is printed once and is not repeated in the summary
    assert tls[0].startswith(f"PASS TLS certificate for {HOST} verified")
    assert "ssl_verify_result 0" in tls[0]


def test_web_smoke_never_prints_a_body_on_success(stub_bin: Path) -> None:
    result = _smoke(stub_bin)
    assert "<!doctype" not in _output(result)
    assert "This page does not exist" not in result.stdout.replace(
        'body contains "This page does not exist"', ""
    )
    assert "contents of" not in _output(result)


def test_web_smoke_header_names_are_case_insensitive(stub_bin: Path) -> None:
    result = _smoke(stub_bin, site_lowercase_headers=True)
    assert result.returncode == 0, _output(result)
    assert _lines(result, "FAIL") == []


def test_web_smoke_asks_nothing_and_yes_has_no_effect(stub_bin: Path) -> None:
    result = _smoke(stub_bin, "--yes")
    assert result.returncode == 0, _output(result)
    assert "[y/N]" not in _output(result)


def _site(path: str, **override: Any) -> dict[str, Any]:
    return {"site": {path: override}}


FAILURES: list[tuple[str, dict[str, Any], str]] = [
    ("wrong status", _site("/", status=500), "FAIL GET / status: expected 200, got 500"),
    (
        "wrong content type",
        _site("/", headers={"Content-Type": "application/json"}),
        "FAIL GET / content-type: expected text/html, got application/json",
    ),
    (
        "no max-age=60",
        _site("/", headers={"Cache-Control": "public,max-age=300"}),
        "FAIL GET / cache-control: expected max-age=60, got public,max-age=300",
    ),
    (
        "max-age=600 is not max-age=60",
        _site("/", headers={"Cache-Control": "public,max-age=600"}),
        "FAIL GET / cache-control: expected max-age=60, got public,max-age=600",
    ),
    (
        "no csp",
        _site("/", headers={"Content-Security-Policy": None}),
        "FAIL GET / content-security-policy: expected the header to be present, got no "
        "content-security-policy header",
    ),
    (
        "csp without default-src none",
        _site("/", headers={"Content-Security-Policy": "default-src *; frame-ancestors 'none'"}),
        "FAIL GET / content-security-policy: expected default-src 'none', got default-src *;",
    ),
    (
        "csp without frame-ancestors none",
        _site("/", headers={"Content-Security-Policy": "default-src 'none'"}),
        "FAIL GET / content-security-policy: expected frame-ancestors 'none', got default-src "
        "'none'",
    ),
    (
        "no hsts",
        _site("/", headers={"Strict-Transport-Security": None}),
        "FAIL GET / strict-transport-security: expected max-age=31536000, got no "
        "strict-transport-security header",
    ),
    (
        "hsts with another max-age",
        _site("/", headers={"Strict-Transport-Security": "max-age=315360000"}),
        "FAIL GET / strict-transport-security: expected max-age=31536000, got max-age=315360000",
    ),
    (
        "hsts with includeSubDomains",
        _site("/", headers={"Strict-Transport-Security": "max-age=31536000; includeSubDomains"}),
        "FAIL GET / strict-transport-security: expected no includeSubDomains and no preload, "
        "got max-age=31536000; includeSubDomains",
    ),
    (
        "hsts with preload",
        _site("/", headers={"Strict-Transport-Security": "max-age=31536000; preload"}),
        "FAIL GET / strict-transport-security: expected no includeSubDomains and no preload, "
        "got max-age=31536000; preload",
    ),
    (
        "no nosniff",
        _site("/", headers={"X-Content-Type-Options": None}),
        "FAIL GET / x-content-type-options: expected nosniff, got no x-content-type-options header",
    ),
    (
        "frame options not DENY",
        _site("/", headers={"X-Frame-Options": "SAMEORIGIN"}),
        "FAIL GET / x-frame-options: expected DENY, got SAMEORIGIN",
    ),
    (
        "no referrer policy",
        _site("/", headers={"Referrer-Policy": None}),
        "FAIL GET / referrer-policy: expected the header to be present, got no referrer-policy "
        "header",
    ),
    (
        "no permissions policy",
        _site("/", headers={"Permissions-Policy": None}),
        "FAIL GET / permissions-policy: expected the header to be present, got no "
        "permissions-policy header",
    ),
    (
        "asset not immutable",
        _site(ASSET_JS, headers={"Cache-Control": "public,max-age=300"}),
        f"FAIL GET {ASSET_JS} cache-control: expected immutable, got public,max-age=300",
    ),
    (
        "css not found",
        _site(ASSET_CSS, status=404),
        f"FAIL GET {ASSET_CSS} status: expected 200, got 404",
    ),
    (
        "css with the wrong type",
        _site(ASSET_CSS, headers={"Content-Type": "text/plain"}),
        f"FAIL GET {ASSET_CSS} content-type: expected text/css, got text/plain",
    ),
    (
        "js with the wrong type",
        _site(ASSET_JS, headers={"Content-Type": "text/html"}),
        f"FAIL GET {ASSET_JS} content-type: expected text/javascript or application/javascript, "
        "got text/html",
    ),
    (
        "data file missing",
        _site("/data/schema.json", status=404),
        "FAIL GET /data/schema.json status: expected 200, got 404",
    ),
    (
        "data file served as text",
        _site("/data/cases/facet-01.json", headers={"Content-Type": "text/plain"}),
        "FAIL GET /data/cases/facet-01.json content-type: expected application/json, got "
        "text/plain",
    ),
    (
        "favicon with the wrong type",
        _site("/favicon.svg", headers={"Content-Type": "image/png"}),
        "FAIL GET /favicon.svg content-type: expected image/svg+xml, got image/png",
    ),
    (
        "a missing path answers 200",
        _site("/nope", status=200),
        "FAIL GET /nope status: expected 404, got 200",
    ),
    (
        "the 404 page has no text",
        _site("/nope", body="<h1>Not found</h1>"),
        'FAIL GET /nope body: expected text containing "This page does not exist", '
        "got a body without it",
    ),
    (
        "http does not redirect",
        {"site": {"http:/": {"status": 200, "headers": {"Location": None}}}},
        f"FAIL GET http://{HOST}/ status: expected one of 301 302 307 308, got 200",
    ),
    (
        "http redirects somewhere else",
        {"site": {"http:/": {"headers": {"Location": "https://other.example.net/"}}}},
        f"FAIL GET http://{HOST}/ location: expected https://{HOST}/..., got https://other.example.net/",
    ),
    (
        "http redirects to http",
        {"site": {"http:/": {"headers": {"Location": f"http://{HOST}/"}}}},
        f"FAIL GET http://{HOST}/ location: expected https://{HOST}/..., got http://{HOST}/",
    ),
    (
        "one request cannot complete",
        _site("/data/index.json", exit=28, stderr="curl: (28) Operation timed out"),
        "FAIL GET /data/index.json: expected a response, got curl exit 28: curl: (28) Operation "
        "timed out",
    ),
]


@pytest.mark.parametrize(("name", "config", "line"), FAILURES, ids=[f[0] for f in FAILURES])
def test_web_smoke_one_failing_check_fails_that_check_and_the_others_still_run(
    stub_bin: Path, name: str, config: dict[str, Any], line: str
) -> None:
    result = _smoke(stub_bin, **config)
    assert result.returncode == 1, _output(result)
    assert line in result.stdout, result.stdout
    assert "result: FAIL" in result.stdout
    assert re.search(r"^\d+ passed, [1-9]\d* failed$", result.stdout, re.MULTILINE)
    # the summary repeats the failure
    assert result.stdout.split("failed checks:")[1].count(line.removeprefix("FAIL ")) == 1
    # every request was still made
    assert len(_curl_calls(stub_bin)) == len(_curl_urls(stub_bin)) == REQUESTS
    # and every failure belongs to the one request that was changed: the rest still passed
    request = line.split()[2].rstrip(":")
    assert {f.split()[2].rstrip(":") for f in _lines(result, "FAIL")} == {request}


def test_web_smoke_a_missing_asset_name_is_a_failed_check(stub_bin: Path) -> None:
    body = '<!doctype html><script type="module" src="./app.js"></script>'
    result = _smoke(stub_bin, **_site("/", body=body))
    assert result.returncode == 1, _output(result)
    assert "FAIL first .js file under assets/ named in the body of /: expected" in result.stdout
    assert "FAIL first .css file under assets/ named in the body of /: expected" in result.stdout
    assert not any("/assets/" in url for url in _curl_urls(stub_bin))
    assert "PASS GET /favicon.svg status" in result.stdout


def test_web_smoke_takes_the_first_asset_of_each_kind(stub_bin: Path) -> None:
    body = (
        '<link rel="stylesheet" href="./assets/first-1.css">'
        '<script type="module" src="./assets/first-1.js"></script>'
        '<link rel="modulepreload" href="./assets/second-2.js">'
        '<link rel="stylesheet" href="./assets/second-2.css">'
        '<link rel="preload" href="./assets/data-3.json">'
    )
    first_js = {"headers": {"Content-Type": "text/javascript"}}
    result = _smoke(stub_bin, site={"/": {"body": body}, "/assets/first-1.js": first_js})
    assert result.returncode == 1, _output(result)  # the stub only knows the default names
    urls = _curl_urls(stub_bin)
    assert f"{SITE_URL}/assets/first-1.js" in urls
    assert f"{SITE_URL}/assets/first-1.css" in urls
    assert not any("second-2" in url or "data-3" in url for url in urls)


def test_web_smoke_a_failing_response_shows_only_its_first_200_characters(
    stub_bin: Path,
) -> None:
    result = _smoke(stub_bin, **_site("/data/index.json", status=500, body="x" * 500))
    assert result.returncode == 1, _output(result)
    assert "x" * 200 in result.stdout
    assert "x" * 201 not in result.stdout
    assert result.stdout.count("response body starts") == 1


def test_web_smoke_shows_the_body_once_for_several_failures_of_one_response(
    stub_bin: Path,
) -> None:
    result = _smoke(
        stub_bin,
        **_site("/", status=503, headers={"Cache-Control": None, "X-Frame-Options": None}),
    )
    assert len(_lines(result, "FAIL")) >= 3
    assert result.stdout.count("response body starts") == 1


def test_web_smoke_does_not_print_identifiers_from_a_failing_response(stub_bin: Path) -> None:
    body = (
        f"<p>bucket precertly-web-sitebucket397a1860-abc123def456 account {ACCOUNT} "
        "distribution E1A2B3C4D5E6F7 \x1b[31mred\x1b[0m</p>"
    )
    result = _smoke(stub_bin, **_site("/data/index.json", status=403, body=body))
    out = _output(result)
    assert "response body starts" in out
    for secret in ("sitebucket397a1860", ACCOUNT, "E1A2B3C4D5E6F7", "\x1b"):
        assert secret not in out, secret
    assert "REDACTED" in out


def test_web_smoke_a_network_failure_is_one_failed_check_not_an_abort(stub_bin: Path) -> None:
    config = _site("/favicon.svg", exit=6, stderr="curl: (6) Could not resolve host")
    result = _smoke(stub_bin, **config)
    assert result.returncode == 1, _output(result)
    assert (
        "FAIL GET /favicon.svg: expected a response, got curl exit 6: curl: (6) Could not resolve "
        "host" in result.stdout
    )
    assert "result: FAIL" in result.stdout
    assert len(_curl_calls(stub_bin)) == REQUESTS


def test_web_smoke_every_request_failing_still_prints_the_summary(stub_bin: Path) -> None:
    result = _smoke(stub_bin, site={"*": {"exit": 7, "stderr": "curl: (7) Failed to connect"}})
    assert result.returncode == 1, _output(result)
    assert "result: FAIL" in result.stdout
    assert re.search(r"^0 passed, \d+ failed$", result.stdout, re.MULTILINE)
    assert "FAIL first .js file under assets/" in result.stdout


def test_web_smoke_a_certificate_problem_fails_the_tls_line_once(stub_bin: Path) -> None:
    config = {"site": {"*": {"exit": 60, "stderr": "curl: (60) SSL certificate problem"}}}
    result = _smoke(stub_bin, **config)
    tls = _lines(result, "FAIL TLS")
    assert len(tls) == 1 and tls[0].startswith("FAIL TLS certificate for"), tls
    assert "TLS" not in "".join(_lines(result, "PASS"))
    assert "curl exit 60" in tls[0]
    assert result.returncode == 1


def test_web_smoke_a_nonzero_verify_result_fails_tls(stub_bin: Path) -> None:
    result = _smoke(stub_bin, tls_verify=20)
    assert result.returncode == 1
    assert "FAIL TLS certificate for" in result.stdout
    assert "ssl_verify_result 20" in result.stdout


def test_web_smoke_never_weakens_tls_or_follows_redirects(stub_bin: Path) -> None:
    _smoke(stub_bin)
    calls = _curl_calls(stub_bin)
    assert len(calls) == REQUESTS
    for call in calls:
        words = call.split()
        assert words[1] == "--disable"
        assert "--insecure" not in words and "-k" not in words
        assert not {"--location", "-L", "--cookie", "-b", "--cookie-jar", "-c"} & set(words)
        assert not {"--user", "-u", "--header", "-H", "--data", "-d", "--request", "-X"} & set(
            words
        )
        assert "--max-time" in words and words[words.index("--max-time") + 1] == "20"
        assert "--silent" in words and "--show-error" in words
        # nothing in the URL that would bypass a cache
        assert "?" not in words[-1]
        scheme = words[-1].split("://")[0]
        assert words[words.index("--proto") + 1] == f"={scheme}"
    assert not any("=https" in c for c in calls if c.endswith(f"http://{HOST}/"))
    assert all("--proto =https" in c for c in calls if c.split()[-1].startswith("https://"))


def test_web_smoke_makes_no_aws_call_but_two_stack_reads(stub_bin: Path) -> None:
    _smoke(stub_bin)
    aws_calls = [c for c in _calls(stub_bin) if c.startswith("aws ")]
    assert len(aws_calls) == 2
    assert all(
        c.startswith("aws cloudformation describe-stacks --stack-name precertly-web")
        for c in aws_calls
    )
    others = [c for c in _calls(stub_bin) if not c.startswith(("aws ", "curl "))]
    assert others == []
    assert _s3_writes(stub_bin) == []


def test_web_smoke_reads_the_stack_before_it_sends_anything(stub_bin: Path) -> None:
    _smoke(stub_bin)
    calls = _calls(stub_bin)
    assert calls[0].startswith("aws cloudformation describe-stacks")
    assert calls[1].startswith("aws cloudformation describe-stacks") and "SiteUrl" in calls[1]
    assert calls[2].startswith("curl ")


@pytest.mark.parametrize(
    "state", ["CREATE_IN_PROGRESS", "UPDATE_ROLLBACK_COMPLETE", "ROLLBACK_COMPLETE"]
)
def test_web_smoke_refuses_a_stack_that_is_not_complete_before_any_request(
    stub_bin: Path, state: str
) -> None:
    result = _smoke(stub_bin, stacks={"precertly-web": state})
    assert result.returncode != 0
    assert f"is {state}, not CREATE_COMPLETE or UPDATE_COMPLETE" in result.stderr
    assert "run web smoke again" in result.stderr
    assert _curl_calls(stub_bin) == []


def test_web_smoke_accepts_update_complete(stub_bin: Path) -> None:
    result = _smoke(stub_bin, stacks={"precertly-web": "UPDATE_COMPLETE"})
    assert result.returncode == 0, _output(result)


def test_web_smoke_refuses_a_stack_that_is_not_deployed(stub_bin: Path) -> None:
    result = _smoke(stub_bin, stacks={})
    assert result.returncode != 0
    assert "precertly-web is not deployed" in result.stderr
    assert _curl_calls(stub_bin) == []


def test_web_smoke_refuses_a_stack_with_no_site_url(stub_bin: Path) -> None:
    result = _smoke(stub_bin, no_outputs=True)
    assert result.returncode != 0
    assert "no output SiteUrl" in result.stderr
    assert _curl_calls(stub_bin) == []


@pytest.mark.parametrize(
    "value",
    ["http://precertly.example.org", "precertly.example.org", "https://", "ftp://x.example.org"],
)
def test_web_smoke_refuses_a_site_url_that_is_not_https(stub_bin: Path, value: str) -> None:
    result = _smoke(stub_bin, outputs={"SiteUrl": value})
    assert result.returncode != 0
    assert "not https://<hostname>" in result.stderr
    assert _curl_calls(stub_bin) == []


@pytest.mark.parametrize(
    "value",
    [
        "https://precertly.example.org/",
        "https://precertly.example.org/path",
        "https://a b.example.org",
    ],
)
def test_web_smoke_refuses_a_site_url_with_a_path(stub_bin: Path, value: str) -> None:
    result = _smoke(stub_bin, outputs={"SiteUrl": value})
    assert result.returncode != 0
    assert _curl_calls(stub_bin) == []


def test_web_smoke_refuses_when_the_aws_read_fails(stub_bin: Path) -> None:
    result = _smoke(stub_bin, fail_all="An error occurred (ExpiredToken): the token has expired")
    assert result.returncode != 0
    assert "ExpiredToken" in result.stderr
    assert _curl_calls(stub_bin) == []


def test_web_smoke_dry_run_makes_no_call_of_any_kind(stub_bin: Path) -> None:
    result = _deploy(stub_bin, "web", "smoke", "--dry-run", email=None)
    assert result.returncode == 0, _output(result)
    _assert_no_calls(stub_bin)
    out = result.stdout
    assert "no AWS call and no request is made" in out
    assert "aws cloudformation describe-stacks --stack-name precertly-web" in out
    assert "SiteUrl" in out
    for request in (
        "GET https://SITE_HOST/\n",
        "GET https://SITE_HOST/assets/<first .js file named in the body of />",
        "GET https://SITE_HOST/assets/<first .css file named in the body of />",
        "GET https://SITE_HOST/data/index.json",
        "GET https://SITE_HOST/data/schema.json",
        "GET https://SITE_HOST/data/policies/facet-joint-interventions.json",
        "GET https://SITE_HOST/data/cases/facet-01.json",
        "GET https://SITE_HOST/favicon.svg",
        "GET https://SITE_HOST/nope",
        "GET http://SITE_HOST/\n",
    ):
        assert request in out, request
    assert out.count("asserts:") == 10
    assert "content-security-policy" in out and "without includeSubDomains or preload" in out
    assert "never --insecure" in out
    assert "TLS" not in out.replace("tls", "")  # nothing to report: no handshake happened
    assert ACCOUNT not in _output(result)


def test_web_smoke_dry_run_writes_nothing(stub_bin: Path) -> None:
    result = _deploy(stub_bin, "web", "smoke", "--dry-run", "--yes", email=None)
    assert result.returncode == 0, _output(result)
    _assert_no_calls(stub_bin)
    assert not any(
        word in result.stdout
        for word in ("s3 sync", "s3 cp", "create-invalidation", "put-", "delete")
    )


def test_web_smoke_cleans_up_its_temporary_directory(stub_bin: Path, tmp_path: Path) -> None:
    tmp = tmp_path / "scratch"
    tmp.mkdir()
    _configure(stub_bin, stacks=WEB_STACK_OK)
    result = subprocess.run(
        ["bash", str(SCRIPT), "web", "smoke"],
        cwd=_repo(stub_bin),
        env={**_env(stub_bin, None), "TMPDIR": str(tmp)},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, _output(result)
    assert list(tmp.iterdir()) == []


def test_the_api_smoke_command_is_not_changed_by_web_smoke(stub_bin: Path) -> None:
    script = SCRIPT.read_text()
    smoke = script[
        script.index("cmd_smoke() {") : script.index("# tools: one precertly-tools task")
    ]
    assert "curl" not in smoke
    assert "web_smoke" not in smoke
    result = _deploy(stub_bin, "smoke", "--dry-run")
    assert result.returncode == 0, _output(result)
    assert "execute-command" in result.stdout
    assert "curl" not in result.stdout
