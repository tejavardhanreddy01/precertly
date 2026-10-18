import json
from pathlib import Path
from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

import names
from app import build_app

EMAIL = "ci@example.invalid"


def _template() -> Template:
    app = build_app({"budgetEmail": EMAIL})
    stack = next(c for c in app.node.children if c.node.id == "precertly-base")
    assert isinstance(stack, cdk.Stack)
    return Template.from_stack(stack)


def _only(template: Template, resource_type: str) -> dict[str, Any]:
    found = template.find_resources(resource_type)
    assert len(found) == 1, f"expected one {resource_type}, found {list(found)}"
    return dict(next(iter(found.values())))


def test_base_stack_resources() -> None:
    template = _template().to_json()
    types = sorted(r["Type"] for r in template["Resources"].values())
    assert types == [
        "AWS::Budgets::Budget",
        "AWS::ECR::Repository",
    ]


def test_base_stack_has_no_iam_resources() -> None:
    # The account's managed organization policy denies iam:*Provider*, so a GitHub OIDC
    # provider cannot be created and a deploy would fail. Deploys run from the owner's machine
    # with their own credentials, so the stack needs no IAM at all. Native auto-empty on the
    # repository also means no custom-resource Lambda, and so no Lambda role either.
    template = _template()
    for resource_type in ("AWS::IAM::OIDCProvider", "AWS::IAM::Role", "AWS::IAM::Policy"):
        assert template.find_resources(resource_type) == {}, resource_type
    assert template.find_resources("AWS::Lambda::Function") == {}


def test_ecr_repository() -> None:
    template = _template()
    template.has_resource(
        "AWS::ECR::Repository",
        {
            "Properties": {
                "RepositoryName": names.ECR_REPO,
                "ImageTagMutability": "IMMUTABLE",
                "ImageScanningConfiguration": {"ScanOnPush": True},
                "EncryptionConfiguration": {"EncryptionType": "AES256"},
                "EmptyOnDelete": True,
            },
            "DeletionPolicy": "Delete",
            "UpdateReplacePolicy": "Delete",
        },
    )


def test_ecr_lifecycle_rules() -> None:
    repo = _only(_template(), "AWS::ECR::Repository")
    policy = json.loads(repo["Properties"]["LifecyclePolicy"]["LifecyclePolicyText"])
    rules = sorted(policy["rules"], key=lambda r: r["rulePriority"])
    assert [(r["selection"], r["action"]) for r in rules] == [
        (
            {
                "tagStatus": "tagged",
                "tagPrefixList": ["sha-"],
                "countType": "imageCountMoreThan",
                "countNumber": 5,
            },
            {"type": "expire"},
        ),
        (
            {
                "tagStatus": "untagged",
                "countType": "sinceImagePushed",
                "countNumber": 1,
                "countUnit": "days",
            },
            {"type": "expire"},
        ),
    ]


def test_budget_type_period_limit_and_no_filters() -> None:
    budget = _only(_template(), "AWS::Budgets::Budget")["Properties"]["Budget"]
    assert budget["BudgetType"] == "COST"
    assert budget["TimeUnit"] == "ANNUALLY"
    assert budget["BudgetLimit"] == {"Amount": 60, "Unit": "USD"}
    assert budget["CostTypes"] == {"IncludeCredit": False, "IncludeRefund": False}
    # Account-wide: no cost filters and no planned limits.
    assert set(budget) == {"BudgetType", "TimeUnit", "BudgetLimit", "CostTypes"}


def test_budget_notifications() -> None:
    props = _only(_template(), "AWS::Budgets::Budget")["Properties"]
    got = [
        (
            n["Notification"]["NotificationType"],
            n["Notification"]["ThresholdType"],
            n["Notification"]["Threshold"],
            n["Notification"]["ComparisonOperator"],
            n["Subscribers"],
        )
        for n in props["NotificationsWithSubscribers"]
    ]
    email = [{"SubscriptionType": "EMAIL", "Address": EMAIL}]
    assert got == [
        ("ACTUAL", "ABSOLUTE_VALUE", 20, "GREATER_THAN", email),
        ("ACTUAL", "ABSOLUTE_VALUE", 40, "GREATER_THAN", email),
        ("ACTUAL", "ABSOLUTE_VALUE", 55, "GREATER_THAN", email),
        ("FORECASTED", "PERCENTAGE", 100, "GREATER_THAN", email),
    ]


def test_budget_email_comes_from_context() -> None:
    app = build_app({"budgetEmail": "someone@example.invalid"})
    stack = next(c for c in app.node.children if c.node.id == "precertly-base")
    assert isinstance(stack, cdk.Stack)
    text = json.dumps(Template.from_stack(stack).to_json())
    assert "someone@example.invalid" in text
    assert EMAIL not in text


@pytest.mark.parametrize("context", [None, {}, {"budgetEmail": ""}, {"budgetEmail": "  "}])
def test_app_fails_clearly_without_budget_email(context: dict[str, Any] | None) -> None:
    with pytest.raises(ValueError, match="budgetEmail"):
        build_app(context)


def test_budget_email_is_not_committed() -> None:
    root = Path(__file__).resolve().parent.parent
    for name in ("app.py", "base_stack.py", "names.py"):
        assert "@" not in (root / name).read_text(), name


def test_template_has_no_hard_coded_account_or_region() -> None:
    text = json.dumps(_template().to_json())
    assert "us-east" not in text


def test_outputs() -> None:
    template = _template()
    template.has_output("RepositoryUri", {"Value": Match.any_value()})
    assert set(template.to_json()["Outputs"]) == {"RepositoryUri"}
