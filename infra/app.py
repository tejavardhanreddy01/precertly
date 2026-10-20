"""CDK app for Precertly: three stacks, precertly-base, precertly-app and precertly-data.

precertly-data depends on precertly-app (its VPC); precertly-app never refers to it.

The stacks are environment-agnostic: no account or region is set, so synthesis needs no
credentials and makes no lookups. Availability zones are fixed for the same reason.
"""

from typing import Any

import aws_cdk as cdk

from app_stack import IMAGE_TAG_CONTEXT_KEY, AppStack, validate_image_tag
from base_stack import BUDGET_EMAIL_CONTEXT_KEY, BaseStack
from data_stack import DataStack
from precertly_stack import AVAILABILITY_ZONES, PrecertlyStack

__all__ = ["AVAILABILITY_ZONES", "STACK_NAMES", "PrecertlyStack", "build_app"]

STACK_NAMES = ("precertly-base", "precertly-app", "precertly-data")
# Availability zones are fixed on purpose (see the module docstring), so the CDK's advice to
# avoid hard-coded zones does not apply.
FIXED_AZ_RULE = cdk.Acknowledgment(
    id="CloudFormation-Validate::W3010",
    reason="Fixed zones keep the stacks environment-agnostic, so synthesis needs no lookup.",
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
    # Required for every stack, because one `cdk deploy <name>` still runs the whole app.
    image_tag = validate_image_tag(app.node.try_get_context(IMAGE_TAG_CONTEXT_KEY))
    BaseStack(app, "precertly-base", budget_email=email.strip())
    app_stack = AppStack(app, "precertly-app", image_tag=image_tag)
    cdk.Validations.of(app_stack).acknowledge(FIXED_AZ_RULE)
    # The references to the app's VPC and subnets make precertly-data depend on precertly-app.
    DataStack(
        app,
        "precertly-data",
        image_tag=image_tag,
        vpc=app_stack.vpc,
        database_subnets=app_stack.database_subnets,
        tools_subnets=app_stack.public_subnets,
    )
    return app


if __name__ == "__main__":
    build_app().synth()
