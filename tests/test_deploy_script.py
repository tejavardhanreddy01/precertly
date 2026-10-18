"""scripts/deploy.sh in dry-run mode: no AWS, Docker or credentials involved."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "deploy.sh"
FAKE_EMAIL = "fake.owner@secret-domain.test"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None or not (ROOT / ".git").exists(),
    reason="needs bash and a git checkout",
)


@pytest.fixture
def stub_bin(tmp_path: Path) -> Path:
    """aws, docker and npx stubs that record a call and fail, so a dry run can't use them."""
    log = tmp_path / "calls.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("aws", "docker", "npx"):
        stub = bin_dir / tool
        stub.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{log}"\nexit 97\n')
        stub.chmod(0o755)
    return bin_dir


def _deploy(
    stub_bin: Path, *args: str, email: str | None = FAKE_EMAIL
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{stub_bin}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(stub_bin.parent),
    }
    if email is not None:
        env["BUDGET_EMAIL"] = email
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


def _assert_no_calls(stub_bin: Path) -> None:
    assert not (stub_bin.parent / "calls.log").exists(), "a stubbed tool was called"


def test_image_dry_run(stub_bin: Path) -> None:
    result = _deploy(stub_bin, "--dry-run", "image")
    assert result.returncode == 0, _output(result)
    head = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    tag = f"sha-{head}"
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
    assert "cdk deploy precertly-base -c budgetEmail=REDACTED" in result.stdout
    assert "--require-approval" not in result.stdout
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
