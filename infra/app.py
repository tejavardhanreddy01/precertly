"""CDK app for Precertly: four stacks (base, app, data and web, all named precertly-<name>).

precertly-data depends on precertly-app (its VPC); precertly-app never refers to it.
precertly-web (the static site on S3 and CloudFront) stands alone.

The stacks are environment-agnostic: no account or region is set, so synthesis needs no
credentials and makes no lookups. Availability zones are fixed for the same reason.
"""

from typing import Any

import aws_cdk as cdk

from app_stack import IMAGE_TAG_CONTEXT_KEY, AppStack, validate_image_tag
from base_stack import BUDGET_EMAIL_CONTEXT_KEY, BaseStack
from data_stack import DataStack
from precertly_stack import AVAILABILITY_ZONES, PrecertlyStack
from spike_stack import SPIKE_CONTEXT_KEY, SpikeStack, spike_enabled
from web_stack import (
    WEB_CERTIFICATE_ARN_CONTEXT_KEY,
    WEB_DOMAIN_CONTEXT_KEY,
    WebStack,
    validate_certificate_arn,
    validate_web_domain,
)

__all__ = ["AVAILABILITY_ZONES", "STACK_NAMES", "PrecertlyStack", "build_app"]

STACK_NAMES = ("precertly-base", "precertly-app", "precertly-data", "precertly-web")
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
    # Likewise required for every stack. Never committed: scripts/deploy.sh reads them from the
    # environment or .env.local, and CI and the tests pass clearly fake placeholders.
    web_domain = validate_web_domain(app.node.try_get_context(WEB_DOMAIN_CONTEXT_KEY))
    web_certificate_arn = validate_certificate_arn(
        app.node.try_get_context(WEB_CERTIFICATE_ARN_CONTEXT_KEY)
    )
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
    WebStack(app, "precertly-web", domain=web_domain, certificate_arn=web_certificate_arn)
    # A throwaway spike (see spike_stack.py), built only with `-c spike=true`. STACK_NAMES is
    # the normal app and does not list it.
    if spike_enabled(app.node.try_get_context(SPIKE_CONTEXT_KEY)):
        SpikeStack(
            app,
            "precertly-spike",
            image_tag=image_tag,
            vpc=app_stack.vpc,
            public_subnets=app_stack.public_subnets,
        )
    return app


if __name__ == "__main__":
    build_app().synth()
