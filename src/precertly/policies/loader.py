"""Load and validate policy YAML files from data/policies."""

from __future__ import annotations

from pathlib import Path

import yaml

from precertly.policies.schema import Policy

DEFAULT_POLICY_DIR = Path(__file__).resolve().parents[3] / "data" / "policies"


def load_policy(path: Path) -> Policy:
    with path.open(encoding="utf-8") as f:
        return Policy.model_validate(yaml.safe_load(f))


def load_policies(directory: Path = DEFAULT_POLICY_DIR) -> dict[str, Policy]:
    policies = [load_policy(p) for p in sorted(directory.glob("*.yaml"))]
    by_id = {p.id: p for p in policies}
    if len(by_id) != len(policies):
        raise ValueError("duplicate policy ids across files")
    return by_id
