"""CDK app for Precertly: three stacks, only precertly-base defines resources so far.

The stacks are environment-agnostic: no account or region is set, so synthesis needs no
credentials and makes no lookups. Availability zones are fixed for the same reason.
"""

from typing import Any

import aws_cdk as cdk

from base_stack import BUDGET_EMAIL_CONTEXT_KEY, BaseStack
from precertly_stack import AVAILABILITY_ZONES, PrecertlyStack

__all__ = ["AVAILABILITY_ZONES", "STACK_NAMES", "PrecertlyStack", "build_app"]

STACK_NAMES = ("precertly-base", "precertly-app", "precertly-data")
# The CDK's built-in template validation rejects a stack with no resources. precertly-app and
# precertly-data are still empty, so the rule is acknowledged on them; delete this (and the
# acknowledgement below) when each gets its first resource.
EMPTY_STACK_RULE = cdk.Acknowledgment(
    id="CloudFormation-Validate::F0001",
    reason="Scaffold: the stack defines no resources yet.",
)


def build_app(context: dict[str, Any] | None = None) -> cdk.App:
    # No version-reporting metadata, so the templates carry nothing but the bootstrap check.
    app = cdk.App(analytics_reporting=False, context=context)
    # The address is never committed: it comes from `-c budgetEmail=...` at synth or deploy.
    email = app.node.try_get_context(BUDGET_EMAIL_CONTEXT_KEY)
    if not isinstance(email, str) or not email.strip():
        raise ValueError(
            f"CDK context '{BUDGET_EMAIL_CONTEXT_KEY}' is required: pass "
            f"`-c {BUDGET_EMAIL_CONTEXT_KEY}=<address>` to cdk synth or cdk deploy."
        )
    BaseStack(app, "precertly-base", budget_email=email.strip())
    for name in STACK_NAMES[1:]:
        stack = PrecertlyStack(app, name)
        cdk.Validations.of(stack).acknowledge(EMPTY_STACK_RULE)
    return app


if __name__ == "__main__":
    build_app().synth()
