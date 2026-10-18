import json
from pathlib import Path
from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

import names
from app import build_app

EMAIL = "ci@example.invalid"
OIDC_HOST = "token.actions.githubusercontent.com"
# The only actions allowed on Resource "*"; AWS offers no resource-level scoping for them.
WILDCARD_RESOURCE_ACTIONS = {
    "ecr:GetAuthorizationToken",
    "ecs:RegisterTaskDefinition",
    "ecs:DescribeTaskDefinition",
}


def _template() -> Template:
    app = build_app({"budgetEmail": EMAIL})
    stack = next(c for c in app.node.children if c.node.id == "precertly-base")
    assert isinstance(stack, cdk.Stack)
    return Template.from_stack(stack)


def _only(template: Template, resource_type: str) -> dict[str, Any]:
    found = template.find_resources(resource_type)
    assert len(found) == 1, f"expected one {resource_type}, found {list(found)}"
    return dict(next(iter(found.values())))


def _statements(template: Template) -> list[dict[str, Any]]:
    policy = _only(template, "AWS::IAM::Policy")
    return list(policy["Properties"]["PolicyDocument"]["Statement"])


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _arn_text(value: Any) -> str:
    """Flatten an Fn::Join ARN to text, with each token as ${Name}."""
    if isinstance(value, str):
        return value
    if "Ref" in value:
        return "${" + value["Ref"] + "}"
    return "".join(_arn_text(part) for part in value["Fn::Join"][1])


def test_base_stack_resources() -> None:
    template = _template().to_json()
    types = sorted(r["Type"] for r in template["Resources"].values())
    assert types == [
        "AWS::Budgets::Budget",
        "AWS::ECR::Repository",
        "AWS::IAM::OIDCProvider",
        "AWS::IAM::Policy",
        "AWS::IAM::Role",
    ]
    # Native OIDC provider and native auto-empty: no custom-resource Lambda anywhere.
    assert "AWS::Lambda::Function" not in types


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


def test_oidc_provider() -> None:
    template = _template()
    template.has_resource_properties(
        "AWS::IAM::OIDCProvider",
        {"Url": f"https://{OIDC_HOST}", "ClientIdList": ["sts.amazonaws.com"]},
    )
    assert "ThumbprintList" not in _only(template, "AWS::IAM::OIDCProvider")["Properties"]


def test_deploy_role_trust_policy_is_exact() -> None:
    template = _template()
    role = _only(template, "AWS::IAM::Role")["Properties"]
    assert role["RoleName"] == names.DEPLOY_ROLE
    assert role["MaxSessionDuration"] == 3600
    provider_id = next(iter(template.find_resources("AWS::IAM::OIDCProvider")))
    assert role["AssumeRolePolicyDocument"]["Statement"] == [
        {
            "Effect": "Allow",
            "Action": "sts:AssumeRoleWithWebIdentity",
            "Principal": {"Federated": {"Ref": provider_id}},
            "Condition": {
                "StringEquals": {
                    f"{OIDC_HOST}:aud": "sts.amazonaws.com",
                    f"{OIDC_HOST}:sub": "repo:tejavardhanreddy01/precertly:environment:production",
                }
            },
        }
    ]


def test_deploy_role_policy_has_no_stray_wildcards() -> None:
    statements = _statements(_template())
    assert statements
    for statement in statements:
        assert statement["Effect"] == "Allow"
        actions = _as_list(statement["Action"])
        for action in actions:
            assert "*" not in action, action
        if "*" in _as_list(statement["Resource"]):
            assert set(actions) <= WILDCARD_RESOURCE_ACTIONS, actions
    wildcard_actions = {
        action
        for statement in statements
        if "*" in _as_list(statement["Resource"])
        for action in _as_list(statement["Action"])
    }
    assert wildcard_actions == WILDCARD_RESOURCE_ACTIONS
    iam_actions = {a for s in statements for a in _as_list(s["Action"]) if a.startswith("iam:")}
    assert iam_actions == {"iam:PassRole"}


def test_deploy_role_pass_role_names_the_two_roles() -> None:
    (statement,) = [s for s in _statements(_template()) if s["Action"] == "iam:PassRole"]
    assert sorted(_arn_text(r) for r in statement["Resource"]) == [
        f"arn:${{AWS::Partition}}:iam::${{AWS::AccountId}}:role/{names.EXECUTION_ROLE}",
        f"arn:${{AWS::Partition}}:iam::${{AWS::AccountId}}:role/{names.TASK_ROLE}",
    ]
    assert statement["Condition"] == {
        "StringEquals": {"iam:PassedToService": "ecs-tasks.amazonaws.com"}
    }


def test_deploy_role_ecs_scoping() -> None:
    by_action: dict[str, dict[str, Any]] = {}
    for statement in _statements(_template()):
        for action in _as_list(statement["Action"]):
            by_action[action] = statement
    prefix = "arn:${AWS::Partition}:ecs:${AWS::Region}:${AWS::AccountId}:"
    cluster = prefix + f"cluster/{names.CLUSTER}"

    for action in ("ecs:UpdateService", "ecs:DescribeServices"):
        assert _arn_text(by_action[action]["Resource"]) == (
            prefix + f"service/{names.CLUSTER}/{names.SERVICE}"
        )
        assert "Condition" not in by_action[action]
    run = by_action["ecs:RunTask"]
    assert _arn_text(run["Resource"]) == prefix + f"task-definition/{names.TOOLS_FAMILY}:*"
    for action in ("ecs:RunTask", "ecs:DescribeTasks", "ecs:StopTask", "ecs:ListTasks"):
        condition = by_action[action]["Condition"]
        assert set(condition) == {"ArnEquals"}
        assert _arn_text(condition["ArnEquals"]["ecs:cluster"]) == cluster
    assert _arn_text(by_action["ecs:ListTasks"]["Resource"]) == cluster
    for action in ("ecs:DescribeTasks", "ecs:StopTask"):
        assert _arn_text(by_action[action]["Resource"]) == prefix + f"task/{names.CLUSTER}/*"


def test_deploy_role_ecr_push_is_scoped_to_the_repository() -> None:
    template = _template()
    repo_id = next(iter(template.find_resources("AWS::ECR::Repository")))
    (push,) = [s for s in _statements(template) if s["Sid"] == "EcrPush"]
    assert push["Resource"] == {"Fn::GetAtt": [repo_id, "Arn"]}
    assert set(push["Action"]) == {
        "ecr:BatchCheckLayerAvailability",
        "ecr:BatchGetImage",
        "ecr:CompleteLayerUpload",
        "ecr:GetDownloadUrlForLayer",
        "ecr:InitiateLayerUpload",
        "ecr:PutImage",
        "ecr:UploadLayerPart",
    }


def test_template_has_no_hard_coded_account_or_region() -> None:
    text = json.dumps(_template().to_json())
    assert "us-east" not in text
    assert "AWS::AccountId" in text
    assert "AWS::Region" in text


def test_outputs() -> None:
    template = _template()
    template.has_output("RepositoryUri", {"Value": Match.any_value()})
    template.has_output("DeployRoleArn", {"Value": Match.any_value()})
