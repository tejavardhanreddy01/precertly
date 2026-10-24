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
# Placeholders that CI, scripts/deploy.sh (for the other stacks) and these tests share; the
# domain is reserved (RFC 2606) and the account and certificate ids are all zeros.
WEB_DOMAIN = "site.example.invalid"
WEB_CERTIFICATE_ARN = (
    "arn:aws:acm:us-east-1:000000000000:certificate/00000000-0000-0000-0000-000000000000"
)
# Likewise for the Cognito domain prefix: valid, but clearly fake (scripts/deploy.sh refuses it
# for precertly-auth).
AUTH_DOMAIN_PREFIX = "placeholder-auth-domain"


def context(**extra: Any) -> dict[str, Any]:
    """cdk.json's context plus the required keys, with `extra` overriding or adding."""
    merged: dict[str, Any] = {
        **CDK_JSON_CONTEXT,
        "budgetEmail": EMAIL,
        "imageTag": IMAGE_TAG,
        "webDomain": WEB_DOMAIN,
        "webCertificateArn": WEB_CERTIFICATE_ARN,
        "authDomainPrefix": AUTH_DOMAIN_PREFIX,
    }
    merged.update(extra)
    return {k: v for k, v in merged.items() if v is not None}
