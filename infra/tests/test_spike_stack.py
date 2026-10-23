"""The throwaway spike stack (see spike_stack.py): built only with context spike=true."""

from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template
from support import context

import names
from app import STACK_NAMES, build_app
from app_stack import API_PORT, HEALTH_CHECK_COMMAND
from spike_stack import LOG_GROUP, spike_enabled

SPIKE_CONTEXT = context(spike="true", imageTag="sha-8f97de763b08")


def _stack(app: cdk.App, name: str) -> cdk.Stack:
    stack = next(c for c in app.node.children if c.node.id == name)
    assert isinstance(stack, cdk.Stack)
    return stack


def _template(name: str = "precertly-spike", extra: dict[str, Any] | None = None) -> Template:
    return Template.from_stack(_stack(build_app(extra or SPIKE_CONTEXT), name))


def _resources(template: Template, resource_type: str) -> dict[str, dict[str, Any]]:
    return {k: dict(v) for k, v in template.find_resources(resource_type).items()}


def _only(template: Template, resource_type: str) -> dict[str, Any]:
    found = _resources(template, resource_type)
    assert len(found) == 1, f"expected one {resource_type}, found {list(found)}"
    return next(iter(found.values()))


def _security_group_id(template: Template, description_start: str) -> str:
    found = [
        logical_id
        for logical_id, r in _resources(template, "AWS::EC2::SecurityGroup").items()
        if r["Properties"]["GroupDescription"].startswith(description_start)
    ]
    assert len(found) == 1, found
    return found[0]


def test_synthesizes_with_the_spike_context() -> None:
    app = build_app(SPIKE_CONTEXT)
    names_found = [c.node.id for c in app.node.children if isinstance(c, cdk.Stack)]
    assert names_found == [*STACK_NAMES, "precertly-spike"]
    app.synth()


@pytest.mark.parametrize("value", [None, "false", "True", "1", "", True, False])
def test_only_the_string_true_turns_the_spike_on(value: object) -> None:
    assert not spike_enabled(value)
    ctx = context() if value is None else context(spike=value)
    app = build_app(ctx)
    assert tuple(c.node.id for c in app.node.children if isinstance(c, cdk.Stack)) == STACK_NAMES


def test_spike_enabled_accepts_only_true() -> None:
    assert spike_enabled("true")


def test_default_app_has_exactly_the_four_stacks() -> None:
    app = build_app(context())
    found = tuple(c.node.id for c in app.node.children if isinstance(c, cdk.Stack))
    assert found == STACK_NAMES
    assert STACK_NAMES == ("precertly-base", "precertly-app", "precertly-data", "precertly-web")


@pytest.mark.parametrize("name", STACK_NAMES)
def test_existing_templates_are_identical_with_and_without_the_spike(name: str) -> None:
    without = _template(name, context(imageTag="sha-8f97de763b08")).to_json()
    with_spike = _template(name, SPIKE_CONTEXT).to_json()
    assert with_spike == without


def test_app_template_has_no_spike_outputs_or_references() -> None:
    text = str(_template("precertly-app").to_json())
    assert "spike" not in text.lower()


def test_task_security_group_takes_only_the_vpc_link_group_on_the_api_port() -> None:
    template = _template()
    task_sg = _security_group_id(template, "Spike task")
    link_sg = _security_group_id(template, "Spike VPC link")
    task_group = _resources(template, "AWS::EC2::SecurityGroup")[task_sg]
    # No inline ingress at all, so no CIDR range anywhere.
    assert "SecurityGroupIngress" not in task_group["Properties"]
    ingress = [
        r["Properties"]
        for r in _resources(template, "AWS::EC2::SecurityGroupIngress").values()
        if r["Properties"]["GroupId"] == {"Fn::GetAtt": [task_sg, "GroupId"]}
    ]
    assert len(ingress) == 1
    rule = ingress[0]
    assert rule["SourceSecurityGroupId"] == {"Fn::GetAtt": [link_sg, "GroupId"]}
    assert (rule["IpProtocol"], rule["FromPort"], rule["ToPort"]) == ("tcp", API_PORT, API_PORT)
    assert not any(key.startswith("Cidr") for key in rule)


def test_no_security_group_in_the_stack_has_cidr_ingress() -> None:
    template = _template()
    for group in _resources(template, "AWS::EC2::SecurityGroup").values():
        assert "SecurityGroupIngress" not in group["Properties"]
    # Exactly one ingress rule in the whole stack.
    assert len(_resources(template, "AWS::EC2::SecurityGroupIngress")) == 1


def test_service_is_one_arm64_task_in_public_subnets_with_a_public_ip() -> None:
    template = _template()
    service = _only(template, "AWS::ECS::Service")["Properties"]
    assert service["DesiredCount"] == 1
    assert service["LaunchType"] == "FARGATE"
    assert service["EnableExecuteCommand"] is False
    vpc_config = service["NetworkConfiguration"]["AwsvpcConfiguration"]
    assert vpc_config["AssignPublicIp"] == "ENABLED"
    assert len(vpc_config["Subnets"]) == 2
    assert all("publicSubnet" in str(s) for s in vpc_config["Subnets"])
    assert service["DeploymentConfiguration"]["DeploymentCircuitBreaker"] == {
        "Enable": True,
        "Rollback": True,
    }
    task = _only(template, "AWS::ECS::TaskDefinition")["Properties"]
    assert task["RuntimePlatform"]["CpuArchitecture"] == "ARM64"
    assert (task["Cpu"], task["Memory"]) == ("256", "512")


def test_container_is_the_existing_image_with_the_same_health_check_and_nothing_else() -> None:
    task = _only(_template(), "AWS::ECS::TaskDefinition")["Properties"]
    (container,) = task["ContainerDefinitions"]
    assert container["PortMappings"] == [{"ContainerPort": API_PORT, "Protocol": "tcp"}]
    assert container["HealthCheck"]["Command"] == ["CMD-SHELL", HEALTH_CHECK_COMMAND]
    image = container["Image"]["Fn::Join"][1]
    assert image[-1] == f"/{names.ECR_REPO}:sha-8f97de763b08"
    # No environment, secrets or command: nothing for a database or Bedrock.
    for key in ("Environment", "Secrets", "Command", "EntryPoint"):
        assert key not in container


def test_task_role_has_no_policies() -> None:
    template = _template()
    roles = _resources(template, "AWS::IAM::Role")
    (task_role_id,) = [
        k for k, r in roles.items() if k.startswith("TaskRole") and "Properties" in r
    ]
    task_role = roles[task_role_id]["Properties"]
    assert "Policies" not in task_role
    assert "ManagedPolicyArns" not in task_role
    task_definition = _only(template, "AWS::ECS::TaskDefinition")["Properties"]
    assert task_definition["TaskRoleArn"] == {"Fn::GetAtt": [task_role_id, "Arn"]}
    for policy in _resources(template, "AWS::IAM::Policy").values():
        assert {"Ref": task_role_id} not in policy["Properties"]["Roles"]
    # Nothing the task role could be given: the only policy is the execution role's.
    (policy,) = _resources(template, "AWS::IAM::Policy").values()
    (role,) = policy["Properties"]["Roles"]
    assert role["Ref"].startswith("TaskDefinitionExecutionRole")


def test_execution_role_only_pulls_from_ecr_and_writes_logs() -> None:
    (policy,) = _resources(_template(), "AWS::IAM::Policy").values()
    actions = {
        a
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
        for a in (
            statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
        )
    }
    assert actions == {
        "ecr:BatchCheckLayerAvailability",
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer",
        "ecr:GetAuthorizationToken",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
    }


def test_log_group() -> None:
    group = _only(_template(), "AWS::Logs::LogGroup")
    assert group["Properties"]["LogGroupName"] == LOG_GROUP == "/precertly/spike"
    assert group["Properties"]["RetentionInDays"] == 1


def test_cloud_map_registration_is_an_srv_record_on_the_container_port() -> None:
    template = _template()
    namespace = _only(template, "AWS::ServiceDiscovery::PrivateDnsNamespace")["Properties"]
    assert namespace["Name"].endswith(".local")
    discovery = _only(template, "AWS::ServiceDiscovery::Service")["Properties"]
    assert [r["Type"] for r in discovery["DnsConfig"]["DnsRecords"]] == ["SRV"]
    service = _only(template, "AWS::ECS::Service")["Properties"]
    (registry,) = service["ServiceRegistries"]
    assert registry["ContainerName"] == "api"
    assert registry["ContainerPort"] == API_PORT


def test_gateway_has_one_unauthorized_get_health_route_through_the_vpc_link() -> None:
    template = _template()
    template.resource_count_is("AWS::ApiGatewayV2::Api", 1)
    template.resource_count_is("AWS::ApiGatewayV2::Route", 1)
    template.resource_count_is("AWS::ApiGatewayV2::Authorizer", 0)
    template.resource_count_is("AWS::ApiGatewayV2::DomainName", 0)
    template.resource_count_is("AWS::ApiGatewayV2::ApiMapping", 0)
    api = _only(template, "AWS::ApiGatewayV2::Api")["Properties"]
    assert api["ProtocolType"] == "HTTP"
    route = _only(template, "AWS::ApiGatewayV2::Route")["Properties"]
    assert route["RouteKey"] == "GET /health"
    assert route["AuthorizationType"] == "NONE"
    integration = _only(template, "AWS::ApiGatewayV2::Integration")["Properties"]
    assert integration["ConnectionType"] == "VPC_LINK"
    assert integration["IntegrationType"] == "HTTP_PROXY"
    assert "ServiceCloudmapService" in str(integration["IntegrationUri"])


def test_vpc_link_is_in_the_public_subnets_with_its_own_group() -> None:
    template = _template()
    link = _only(template, "AWS::ApiGatewayV2::VpcLink")["Properties"]
    assert len(link["SubnetIds"]) == 2
    assert all("publicSubnet" in str(s) for s in link["SubnetIds"])
    link_sg = _security_group_id(template, "Spike VPC link")
    assert link["SecurityGroupIds"] == [{"Fn::GetAtt": [link_sg, "GroupId"]}]


def test_default_stage_auto_deploys_with_low_throttling() -> None:
    stage = _only(_template(), "AWS::ApiGatewayV2::Stage")["Properties"]
    assert stage["StageName"] == "$default"
    assert stage["AutoDeploy"] is True
    assert stage["DefaultRouteSettings"] == {"ThrottlingRateLimit": 1, "ThrottlingBurstLimit": 2}


def test_the_invoke_url_is_an_output() -> None:
    outputs = _template().to_json()["Outputs"]
    assert "ApiUrl" in outputs
    assert "execute-api" in str(outputs["ApiUrl"])


def test_every_resource_is_removed_with_the_stack() -> None:
    resources = _template().to_json()["Resources"]
    assert resources
    for logical_id, resource in resources.items():
        assert resource["DeletionPolicy"] == "Delete", logical_id
        assert resource["UpdateReplacePolicy"] == "Delete", logical_id


def test_nothing_clashes_with_the_other_stacks() -> None:
    app = build_app(SPIKE_CONTEXT)
    spike = Template.from_stack(_stack(app, "precertly-spike")).to_json()["Resources"]
    fixed_names = {
        names.CLUSTER,
        names.SERVICE,
        names.TOOLS_FAMILY,
        names.TASK_ROLE,
        names.EXECUTION_ROLE,
        names.LOG_GROUP,
        names.TOOLS_TASK_ROLE,
        names.TOOLS_EXECUTION_ROLE,
        names.TOOLS_LOG_GROUP,
    }
    text = str(spike)
    for name in fixed_names:
        assert f"'{name}'" not in text, name
