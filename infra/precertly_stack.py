"""Base class for the Precertly stacks."""

import aws_cdk as cdk

AVAILABILITY_ZONES = ("us-east-2a", "us-east-2b")


class PrecertlyStack(cdk.Stack):
    @property
    def availability_zones(self) -> list[str]:
        # An environment-agnostic stack would otherwise return a deploy-time Fn::GetAZs token.
        return list(AVAILABILITY_ZONES)
