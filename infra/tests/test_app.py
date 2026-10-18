import json

import aws_cdk as cdk
from aws_cdk.assertions import Template

from app import AVAILABILITY_ZONES, STACK_NAMES, PrecertlyStack, build_app

CONTEXT = {"budgetEmail": "ci@example.invalid"}


def _stacks(app: cdk.App) -> dict[str, PrecertlyStack]:
    found = {c.node.id: c for c in app.node.children if isinstance(c, cdk.Stack)}
    return {name: stack for name, stack in found.items() if isinstance(stack, PrecertlyStack)}


def test_three_stacks_exist() -> None:
    assert tuple(_stacks(build_app(CONTEXT))) == STACK_NAMES
    assert STACK_NAMES == ("precertly-base", "precertly-app", "precertly-data")


def test_stacks_have_no_hard_coded_account_or_region() -> None:
    app = build_app(CONTEXT)
    for stack in _stacks(app).values():
        assert cdk.Token.is_unresolved(stack.account)
        assert cdk.Token.is_unresolved(stack.region)
    for artifact in app.synth().artifacts:
        env = getattr(artifact, "environment", None)
        if env is not None:
            assert env.account == "unknown-account"
            assert env.region == "unknown-region"


def test_availability_zones_are_fixed_without_a_lookup() -> None:
    for stack in _stacks(build_app(CONTEXT)).values():
        assert stack.availability_zones == ["us-east-2a", "us-east-2b"]
    assert AVAILABILITY_ZONES == ("us-east-2a", "us-east-2b")


def test_app_and_data_stacks_define_no_resources() -> None:
    for name, stack in _stacks(build_app(CONTEXT)).items():
        if name == "precertly-base":
            continue
        template = Template.from_stack(stack).to_json()
        assert not template.get("Resources")
        # Nothing in an empty template should pin a region or an account either.
        assert "us-east-2" not in json.dumps(template)
