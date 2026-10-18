"""Context for building the app in tests the way `cdk synth` does."""

import json
from pathlib import Path
from typing import Any

# The CLI hands cdk.json's feature flags to the app; build_app(context) does not, and the
# flags change what constructs emit (for example the ECS Exec log permissions).
_CDK_JSON = Path(__file__).resolve().parent.parent / "cdk.json"
CDK_JSON_CONTEXT: dict[str, Any] = json.loads(_CDK_JSON.read_text())["context"]

EMAIL = "ci@example.invalid"
IMAGE_TAG = "sha-000000000000"


def context(**extra: Any) -> dict[str, Any]:
    """cdk.json's context plus the required keys, with `extra` overriding or adding."""
    merged: dict[str, Any] = {
        **CDK_JSON_CONTEXT,
        "budgetEmail": EMAIL,
        "imageTag": IMAGE_TAG,
    }
    merged.update(extra)
    return {k: v for k, v in merged.items() if v is not None}
