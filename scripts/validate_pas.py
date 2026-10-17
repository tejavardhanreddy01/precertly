"""Validate the committed PAS drafts with the official HL7 FHIR Validator.

    uv run python scripts/validate_pas.py          # offline (no terminology server); gates CI
    uv run python scripts/validate_pas.py --tx     # also ask tx.fhir.org; a report, not a gate

The validator and the PAS package are pinned and checked against their sha256 before use,
like the Synthea jar, and kept out of the repository (data/fhir-validator/bin). Needs
Java 11 or later and, on first use, network access to download them and the IG's
dependencies.

Fails on any error, and on any warning not in evals/pas/validator-allowlist.yaml; every
allowed warning carries the reason it is expected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "data" / "fhir-validator" / "bin"
DRAFTS = ROOT / "evals" / "pas"
ALLOWLIST = DRAFTS / "validator-allowlist.yaml"

VALIDATOR_VERSION = "6.10.4"
VALIDATOR_URL = (
    "https://github.com/hapifhir/org.hl7.fhir.core/releases/download/"
    f"{VALIDATOR_VERSION}/validator_cli.jar"
)
VALIDATOR_SHA256 = "1106b9d58f9e363e47bea7c4fc065841e5fc91fe9d062775c3bfdd212bd653cc"

PAS_VERSION = "2.2.1"
PAS_URL = f"https://hl7.org/fhir/us/davinci-pas/{PAS_VERSION}/package.tgz"
PAS_SHA256 = "da2aac8e480ce66fe27812af18e3c9bc190b08a33d5d7e1c850caf9097fb210e"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def pinned(url: str, name: str, sha256: str) -> Path:
    """The file at url, downloaded once into BIN, refused unless it matches sha256."""
    path = BIN / name
    if not path.exists():
        BIN.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(path.suffix + ".part")
        with urllib.request.urlopen(url, timeout=300) as response, partial.open("wb") as f:
            while block := response.read(1 << 20):
                f.write(block)
        partial.rename(path)
    found = _sha256(path)
    if found != sha256:
        raise SystemExit(f"{name} does not match the pinned sha256 ({found}); refusing to use it")
    return path


def run_validator(drafts: list[Path], tx: bool) -> list[dict]:
    jar = pinned(VALIDATOR_URL, f"validator_cli-{VALIDATOR_VERSION}.jar", VALIDATOR_SHA256)
    package = pinned(PAS_URL, f"davinci-pas-{PAS_VERSION}.tgz", PAS_SHA256)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "outcome.json"
        command = ["java", "-jar", str(jar), *map(str, drafts), "-version", "4.0.1"]
        command += ["-ig", str(package), "-output", str(out)]
        if not tx:
            command += ["-tx", "n/a"]
        subprocess.run(command, check=False, stdout=subprocess.DEVNULL)
        if not out.exists():
            raise SystemExit("the validator wrote no output")
        result = json.loads(out.read_text(encoding="utf-8"))
    outcomes = (
        [entry["resource"] for entry in result["entry"]]
        if result["resourceType"] == "Bundle"
        else [result]
    )
    issues = []
    for outcome in outcomes:
        source = next(
            (
                ext["valueString"]
                for ext in outcome.get("extension", [])
                if ext["url"].endswith("operationoutcome-file")
            ),
            "?",
        )
        for issue in outcome.get("issue", []):
            issues.append(
                {
                    "file": Path(source).name,
                    "severity": issue["severity"],
                    "where": (issue.get("expression") or [""])[0],
                    "message": issue.get("details", {}).get("text", ""),
                }
            )
    return issues


def load_allowlist() -> list[dict]:
    with ALLOWLIST.open(encoding="utf-8") as f:
        entries = yaml.safe_load(f)["warnings"]
    for entry in entries:
        entry["pattern"] = re.compile(entry["message"])
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").partition("\n")[0])
    parser.add_argument("--tx", action="store_true", help="use tx.fhir.org (report only)")
    args = parser.parse_args(argv)
    drafts = sorted(DRAFTS.glob("*.draft.json"))
    if not drafts:
        raise SystemExit("no drafts in evals/pas; run python -m precertly.pas first")
    issues = run_validator(drafts, args.tx)
    allow = load_allowlist()
    errors = [i for i in issues if i["severity"] in ("error", "fatal")]
    warnings = [i for i in issues if i["severity"] == "warning"]
    unexpected = [w for w in warnings if not any(a["pattern"].search(w["message"]) for a in allow)]
    used = {a["message"] for a in allow for w in warnings if a["pattern"].search(w["message"])}

    mode = "with tx.fhir.org" if args.tx else "offline"
    print(f"{len(drafts)} drafts validated {mode} against {PAS_URL}")
    print(
        f"errors: {len(errors)}, warnings: {len(warnings)} ({len(unexpected)} not in the allowlist)"
    )
    for issue in errors + unexpected:
        print(f"  {issue['severity']}: {issue['file']} {issue['where']}: {issue['message']}")
    for entry in allow:
        if entry["message"] in used:
            count = sum(bool(entry["pattern"].search(w["message"])) for w in warnings)
            print(f"  allowed ({count}): {entry['reason']}")
    if args.tx:
        return 0
    return 1 if errors or unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
