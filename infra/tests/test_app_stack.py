import ast
import json
from pathlib import Path
from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template
from support import context

import names
from app import build_app

SETTINGS = Path(__file__).resolve().parents[2] / "src" / "precertly" / "settings.py"


def _template(extra: dict[str, Any] | None = None) -> Template:
    app = build_app(extra if extra is not None else context())
    stack = next(c for c in app.node.children if c.node.id == "precertly-app")
    assert isinstance(stack, cdk.Stack)
    return Template.from_stack(stack)


def _resources(template: Template, resource_type: str) -> dict[str, dict[str, Any]]:
    return {k: dict(v) for k, v in template.find_resources(resource_type).items()}


def _only(template: Template, resource_type: str) -> dict[str, Any]:
    found = _resources(template, resource_type)
    assert len(found) == 1, f"expected one {resource_type}, found {list(found)}"
    return next(iter(found.values()))["Properties"]


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _arn_text(value: Any) -> str:
    """Flatten an Fn::Join ARN to text, with each token as ${Name}."""
    if isinstance(value, str):
        return value
    if "Ref" in value:
        return "${" + value["Ref"] + "}"
    return "".join(_arn_text(part) for part in value["Fn::Join"][1])


def _policy_statements(template: Template, role_id_prefix: str) -> list[dict[str, Any]]:
    for policy in _resources(template, "AWS::IAM::Policy").values():
        roles = policy["Properties"]["Roles"]
        if any(r["Ref"].startswith(role_id_prefix) for r in roles):
            return list(policy["Properties"]["PolicyDocument"]["Statement"])
    raise AssertionError(f"no policy for {role_id_prefix}")


def test_network_has_no_nat_no_endpoints_no_load_balancer() -> None:
    template = _template()
    for resource_type in (
        "AWS::EC2::NatGateway",
        "AWS::EC2::EIP",
        "AWS::EC2::VPCEndpoint",
        "AWS::ElasticLoadBalancingV2::LoadBalancer",
        "AWS::ElasticLoadBalancingV2::TargetGroup",
        "AWS::ElasticLoadBalancing::LoadBalancer",
    ):
        assert _resources(template, resource_type) == {}, resource_type
    assert len(_resources(template, "AWS::EC2::InternetGateway")) == 1
    assert _only(template, "AWS::ECS::Service").get("LoadBalancers", []) == []


def test_two_public_subnets_in_the_fixed_zones_and_no_private_ones() -> None:
    template = _template()
    subnets = _resources(template, "AWS::EC2::Subnet")
    assert len(subnets) == 2
    zones = sorted(s["Properties"]["AvailabilityZone"] for s in subnets.values())
    assert zones == ["us-east-2a", "us-east-2b"]
    for subnet in subnets.values():
        assert subnet["Properties"]["MapPublicIpOnLaunch"] is True
    # Every route table sends 0.0.0.0/0 to the internet gateway, none to a NAT gateway.
    routes = _resources(template, "AWS::EC2::Route")
    assert len(routes) == 2
    for route in routes.values():
        assert route["Properties"]["DestinationCidrBlock"] == "0.0.0.0/0"
        assert "GatewayId" in route["Properties"]
        assert "NatGatewayId" not in route["Properties"]


def test_task_security_group_has_no_ingress() -> None:
    template = _template()
    assert _resources(template, "AWS::EC2::SecurityGroupIngress") == {}
    groups = _resources(template, "AWS::EC2::SecurityGroup")
    assert len(groups) == 1
    group = next(iter(groups.values()))["Properties"]
    assert "SecurityGroupIngress" not in group
    assert [r["CidrIp"] for r in group["SecurityGroupEgress"]] == ["0.0.0.0/0"]
    assert group["SecurityGroupEgress"][0]["IpProtocol"] == "-1"


def test_cluster_and_service() -> None:
    template = _template()
    assert _only(template, "AWS::ECS::Cluster")["ClusterName"] == names.CLUSTER
    service = _only(template, "AWS::ECS::Service")
    assert service["ServiceName"] == names.SERVICE
    assert service["LaunchType"] == "FARGATE"
    assert service["DesiredCount"] == 0
    assert service["EnableExecuteCommand"] is True
    assert service["NetworkConfiguration"]["AwsvpcConfiguration"]["AssignPublicIp"] == "ENABLED"
    deployment = service["DeploymentConfiguration"]
    assert deployment["MinimumHealthyPercent"] == 0
    assert deployment["MaximumPercent"] == 100
    assert deployment["DeploymentCircuitBreaker"] == {"Enable": True, "Rollback": True}


def test_task_definition_shape() -> None:
    task = _only(_template(), "AWS::ECS::TaskDefinition")
    assert task["Family"] == names.SERVICE
    assert task["RequiresCompatibilities"] == ["FARGATE"]
    assert task["RuntimePlatform"]["CpuArchitecture"] == "ARM64"
    assert task["RuntimePlatform"]["OperatingSystemFamily"] == "LINUX"
    assert task["Cpu"] == "256"
    assert task["Memory"] == "512"
    (container,) = task["ContainerDefinitions"]
    assert container["PortMappings"] == [{"ContainerPort": 8000, "Protocol": "tcp"}]
    # The image's own CMD applies; ECS ignores the Dockerfile HEALTHCHECK, so the health check
    # is set on the container (see test_health_check_targets_health).
    for key in ("Command", "EntryPoint"):
        assert key not in container


def test_health_check_targets_health() -> None:
    (container,) = _only(_template(), "AWS::ECS::TaskDefinition")["ContainerDefinitions"]
    check = container["HealthCheck"]
    assert check["Command"][0] == "CMD-SHELL"
    assert len(check["Command"]) == 2
    command = check["Command"][1]
    # Python, not curl: the image has no curl.
    assert command.startswith("python -c ")
    assert "curl" not in command
    assert "urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" in command
    assert (check["Interval"], check["Timeout"], check["Retries"], check["StartPeriod"]) == (
        30,
        5,
        3,
        20,
    )
    # The probe is the same Python as the Dockerfile's, so the two cannot drift apart.
    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text()
    assert "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health'" in (
        dockerfile
    )


def test_image_comes_from_the_ecr_repo_at_the_context_tag() -> None:
    (container,) = _only(_template(), "AWS::ECS::TaskDefinition")["ContainerDefinitions"]
    image = _arn_text(container["Image"])
    assert image.endswith(f"/{names.ECR_REPO}:sha-000000000000")
    other = _template(context(imageTag="sha-abcdef012345"))
    (container,) = _only(other, "AWS::ECS::TaskDefinition")["ContainerDefinitions"]
    assert _arn_text(container["Image"]).endswith(f"/{names.ECR_REPO}:sha-abcdef012345")


def test_task_has_no_secrets_and_no_database_settings() -> None:
    task = _only(_template(), "AWS::ECS::TaskDefinition")
    (container,) = task["ContainerDefinitions"]
    assert "Secrets" not in container
    assert "RepositoryCredentials" not in container
    env_names = [e["Name"] for e in container["Environment"]]
    assert env_names, "expected the Bedrock settings"
    for name in env_names:
        assert not name.startswith("PRECERTLY_DB"), name
        assert name != "PRECERTLY_DATABASE_URL"
        assert name != "DATABASE_URL"
    text = json.dumps(task)
    assert "secretsmanager" not in text.lower()
    assert "ssm:" not in text


def test_environment_matches_the_api_settings() -> None:
    defaults: dict[str, Any] = {}
    for node in ast.walk(ast.parse(SETTINGS.read_text())):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Constant)
        ):
            defaults[node.target.id] = node.value.value
    (container,) = _only(_template(), "AWS::ECS::TaskDefinition")["ContainerDefinitions"]
    env = {e["Name"]: e["Value"] for e in container["Environment"]}
    assert env["PRECERTLY_BEDROCK_MODEL_ID"] == defaults["bedrock_model_id"]
    assert env["PRECERTLY_EMBEDDING_MODEL_ID"] == defaults["embedding_model_id"]
    # Empty: the settings default is a local named profile that a task does not have.
    assert env["PRECERTLY_AWS_PROFILE"] == ""
    assert env["PRECERTLY_AWS_REGION"] == {"Ref": "AWS::Region"}
    assert set(env) == {
        "PRECERTLY_AWS_REGION",
        "PRECERTLY_AWS_PROFILE",
        "PRECERTLY_BEDROCK_MODEL_ID",
        "PRECERTLY_EMBEDDING_MODEL_ID",
    }


def test_role_names_and_trust() -> None:
    template = _template()
    roles = {
        r["Properties"]["RoleName"]: r["Properties"]
        for r in _resources(template, "AWS::IAM::Role").values()
    }
    assert set(roles) == {names.TASK_ROLE, names.EXECUTION_ROLE}
    for role in roles.values():
        (statement,) = role["AssumeRolePolicyDocument"]["Statement"]
        assert statement["Principal"] == {"Service": "ecs-tasks.amazonaws.com"}
        assert statement["Action"] == "sts:AssumeRole"
    task = _only(template, "AWS::ECS::TaskDefinition")
    assert "TaskRoleArn" in task
    assert "ExecutionRoleArn" in task


def test_no_iam_provider_and_no_lambda() -> None:
    template = _template()
    for resource_type in (
        "AWS::IAM::OIDCProvider",
        "AWS::IAM::SAMLProvider",
        "AWS::Lambda::Function",
        "AWS::CloudFormation::CustomResource",
    ):
        assert _resources(template, resource_type) == {}, resource_type
    assert not [
        t
        for t in (r["Type"] for r in template.to_json()["Resources"].values())
        if t.startswith("Custom::")
    ]


def test_bedrock_actions_are_scoped_to_specific_arns() -> None:
    statements = _policy_statements(_template(), "TaskRole")
    (bedrock,) = [
        s for s in statements if any(a.startswith("bedrock:") for a in _as_list(s["Action"]))
    ]
    assert sorted(_as_list(bedrock["Action"])) == [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
    ]
    assert sorted(_arn_text(r) for r in _as_list(bedrock["Resource"])) == sorted(
        [
            "arn:${AWS::Partition}:bedrock:${AWS::Region}::foundation-model/amazon.titan-embed-text-v2:0",
            "arn:${AWS::Partition}:bedrock:${AWS::Region}:${AWS::AccountId}:inference-profile/us.amazon.nova-2-lite-v1:0",
            "arn:${AWS::Partition}:bedrock:*::foundation-model/amazon.nova-2-lite-v1:0",
        ]
    )
    assert "Condition" not in bedrock


def test_only_ecs_exec_and_ecr_token_use_a_wildcard_resource() -> None:
    template = _template()
    wildcard = set()
    for policy in _resources(template, "AWS::IAM::Policy").values():
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            actions = _as_list(statement["Action"])
            assert all("*" not in a for a in actions), actions
            if "*" in _as_list(statement["Resource"]):
                wildcard.update(actions)
    assert wildcard == {
        "ssmmessages:CreateControlChannel",
        "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel",
        "ssmmessages:OpenDataChannel",
        "ecr:GetAuthorizationToken",
    }


def test_execution_role_pulls_only_from_the_repo_and_writes_only_its_log_group() -> None:
    statements = _policy_statements(_template(), "ExecutionRole")
    by_actions = {tuple(sorted(_as_list(s["Action"]))): s["Resource"] for s in statements}
    assert _arn_text(
        by_actions[
            (
                "ecr:BatchCheckLayerAvailability",
                "ecr:BatchGetImage",
                "ecr:GetDownloadUrlForLayer",
            )
        ]
    ).endswith(f":repository/{names.ECR_REPO}")
    assert by_actions[("logs:CreateLogStream", "logs:PutLogEvents")]["Fn::GetAtt"][1] == "Arn"
    assert len(statements) == 3


def test_log_group() -> None:
    props = _only(_template(), "AWS::Logs::LogGroup")
    assert props["LogGroupName"] == names.LOG_GROUP
    assert props["RetentionInDays"] == 7
    template = _template()
    template.has_resource("AWS::Logs::LogGroup", {"DeletionPolicy": "Delete"})


def test_outputs() -> None:
    outputs = _template().to_json()["Outputs"]
    assert set(outputs) == {"ClusterName", "ServiceName", "LogGroupName"}


@pytest.mark.parametrize(
    "tag", [None, "", "sha-", "latest", "abc123", "SHA-000000000000", 12, "  sha-000000000000"]
)
def test_image_tag_is_validated(tag: Any) -> None:
    with pytest.raises(ValueError, match="imageTag"):
        build_app(context(imageTag=tag))


def test_stack_is_environment_agnostic() -> None:
    text = json.dumps(_template().to_json())
    assert "AWS::AccountId" in text
    # Only the fixed availability zones name a region.
    assert text.count("us-east-2") == text.count("us-east-2a") + text.count("us-east-2b")
