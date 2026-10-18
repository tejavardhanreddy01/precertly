"""CDK app for Precertly: three empty stacks and no resources yet.

The stacks are environment-agnostic: no account or region is set, so synthesis needs no
credentials and makes no lookups. Availability zones are fixed here for the same reason.
"""

import aws_cdk as cdk

STACK_NAMES = ("precertly-base", "precertly-app", "precertly-data")
AVAILABILITY_ZONES = ("us-east-2a", "us-east-2b")
# The CDK's built-in template validation rejects a stack with no resources. That is the
# intended state of this scaffold, so the rule is acknowledged on each stack; delete this
# (and the acknowledgement below) when the first stack gets a resource.
EMPTY_STACK_RULE = cdk.Acknowledgment(
    id="CloudFormation-Validate::F0001",
    reason="Scaffold: the stacks define no resources yet.",
)


class PrecertlyStack(cdk.Stack):
    @property
    def availability_zones(self) -> list[str]:
        # An environment-agnostic stack would otherwise return a deploy-time Fn::GetAZs token.
        return list(AVAILABILITY_ZONES)


def build_app() -> cdk.App:
    # No version-reporting metadata, so the templates carry nothing but the bootstrap check.
    app = cdk.App(analytics_reporting=False)
    for name in STACK_NAMES:
        stack = PrecertlyStack(app, name)
        cdk.Validations.of(stack).acknowledge(EMPTY_STACK_RULE)
    return app


if __name__ == "__main__":
    build_app().synth()
