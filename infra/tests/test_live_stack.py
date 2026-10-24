import ast
import json
from pathlib import Path
from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template
from support import WEB_DOMAIN, context

import names
from app import build_app
from app_stack import (
    BEDROCK_PROFILE_ID,
    EMBEDDING_MODEL_ID,
    HEALTH_CHECK_COMMAND,
    bedrock_invoke_statement,
)
from data_stack import DB_NAME, SECRET_FIELD_TO_ENV
from live_stack import ACTOR_CLAIM, ACTOR_HEADER, BURST_LIMIT, RATE_LIMIT, REQUIRED_SCOPE

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / "src" / "precertly" / "settings.py"
LIVE_MODELS = ROOT / "src" / "precertly" / "live" / "models.py"
SITE = f"https://{WEB_DOMAIN}"


def _stack(name: str, extra: dict[str, Any] | None = None) -> cdk.Stack:
    app = build_app(extra if extra is not None else context())
    stack = next(c for c in app.node.children if c.node.id == name)
    assert isinstance(stack, cdk.Stack)
    return stack


def _template(extra: dict[str, Any] | None = None) -> Template:
    return Template.from_stack(_stack("precertly-live", extra))


def _resources(template: Template, resource_type: str) -> dict[str, dict[str, Any]]:
    return {k: dict(v) for k, v in template.find_resources(resource_type).items()}


def _only(template: Template, resource_type: str) -> dict[str, Any]:
    found = _resources(template, resource_type)
    assert len(found) == 1, f"expected one {resource_type}, found {list(found)}"
    return next(iter(found.values()))


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _arn_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if "Ref" in value:
        return "${" + value["Ref"] + "}"
    return "".join(_arn_text(part) for part in value["Fn::Join"][1])


def _output_name(reference: dict[str, Any]) -> tuple[str, str]:
    inner = reference["Fn::GetStackOutput"]
    return inner["StackName"], inner["OutputName"]


def _policy_statements(template: Template, role_prefix: str) -> list[dict[str, Any]]:
    for policy in _resources(template, "AWS::IAM::Policy").values():
        if any(r["Ref"].startswith(role_prefix) for r in policy["Properties"]["Roles"]):
            return list(policy["Properties"]["PolicyDocument"]["Statement"])
    raise AssertionError(f"no policy for {role_prefix}")


def _security_groups(template: Template) -> dict[str, dict[str, Any]]:
    return {
        r["Properties"]["GroupDescription"].split(":")[0]: r
        for r in _resources(template, "AWS::EC2::SecurityGroup").values()
    }


def _container() -> dict[str, Any]:
    properties = _only(_template(), "AWS::ECS::TaskDefinition")["Properties"]
    (container,) = properties["ContainerDefinitions"]
    return container


def _settings_fields() -> set[str]:
    return {
        node.target.id
        for node in ast.walk(ast.parse(SETTINGS.read_text()))
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }


def _routes(template: Template) -> dict[str, dict[str, Any]]:
    return {
        r["Properties"]["RouteKey"]: r
        for r in _resources(template, "AWS::ApiGatewayV2::Route").values()
    }


# --- security groups ------------------------------------------------------------------------


def test_two_security_groups_with_no_inline_rules_but_the_default_egress() -> None:
    groups = _security_groups(_template())
    assert set(groups) == {"Live VPC link", "Live task"}
    for group in groups.values():
        properties = group["Properties"]
        assert "SecurityGroupIngress" not in properties
        assert properties["SecurityGroupEgress"] == [
            {
                "CidrIp": "0.0.0.0/0",
                "Description": "Allow all outbound traffic by default",
                "IpProtocol": "-1",
            }
        ]


def test_the_task_group_admits_port_8000_from_the_vpc_link_group_only() -> None:
    template = _template()
    groups = _security_groups(template)
    link_id = next(
        k
        for k, v in _resources(template, "AWS::EC2::SecurityGroup").items()
        if v is groups["Live VPC link"] or v == groups["Live VPC link"]
    )
    task_id = next(
        k
        for k, v in _resources(template, "AWS::EC2::SecurityGroup").items()
        if v == groups["Live task"]
    )
    ingress = _resources(template, "AWS::EC2::SecurityGroupIngress")
    to_task = [
        r["Properties"]
        for r in ingress.values()
        if r["Properties"]["GroupId"] == {"Fn::GetAtt": [task_id, "GroupId"]}
    ]
    assert len(to_task) == 1  # exactly one source for the task group
    rule = to_task[0]
    assert rule["SourceSecurityGroupId"] == {"Fn::GetAtt": [link_id, "GroupId"]}
    assert (rule["IpProtocol"], rule["FromPort"], rule["ToPort"]) == ("tcp", 8000, 8000)


def test_no_ingress_rule_uses_an_address_range() -> None:
    text = json.dumps(_resources(_template(), "AWS::EC2::SecurityGroupIngress"))
    assert "CidrIp" not in text
    assert "CidrIpv6" not in text
    assert "SourcePrefixListId" not in text


def test_the_database_ingress_is_a_resource_of_the_live_stack() -> None:
    template = _template()
    task_id = next(
        k
        for k, v in _resources(template, "AWS::EC2::SecurityGroup").items()
        if v["Properties"]["GroupDescription"].startswith("Live task")
    )
    ingress = _resources(template, "AWS::EC2::SecurityGroupIngress")
    assert len(ingress) == 2  # the task port and the database port, nothing else
    (database,) = [r["Properties"] for r in ingress.values() if r["Properties"]["FromPort"] == 5432]
    assert database["IpProtocol"] == "tcp"
    assert database["ToPort"] == 5432
    assert database["SourceSecurityGroupId"] == {"Fn::GetAtt": [task_id, "GroupId"]}
    stack, output = _output_name(database["GroupId"])
    assert stack == "precertly-data"
    assert "DatabaseSecurityGroup" in output


# --- the task definition --------------------------------------------------------------------


def test_task_definition_shape() -> None:
    properties = _only(_template(), "AWS::ECS::TaskDefinition")["Properties"]
    assert properties["Family"] == names.API_LIVE_SERVICE == "precertly-api-live"
    assert properties["Cpu"] == "256"
    assert properties["Memory"] == "512"
    assert properties["NetworkMode"] == "awsvpc"
    assert properties["RequiresCompatibilities"] == ["FARGATE"]
    assert properties["RuntimePlatform"] == {
        "CpuArchitecture": "ARM64",
        "OperatingSystemFamily": "LINUX",
    }
    assert len(properties["ContainerDefinitions"]) == 1
    assert _container()["Name"] == names.LIVE_CONTAINER
    assert _container()["PortMappings"] == [{"ContainerPort": 8000, "Protocol": "tcp"}]
    assert "Command" not in _container()
    assert "EntryPoint" not in _container()


def test_image_comes_from_the_ecr_repo_at_the_context_tag() -> None:
    text = _arn_text(_container()["Image"])
    assert text.endswith("/precertly-api:sha-000000000000")
    other = json.dumps(
        _only(_template(context(imageTag="sha-abcdefabcdef")), "AWS::ECS::TaskDefinition")
    )
    assert "precertly-api:sha-abcdefabcdef" in other


def test_environment() -> None:
    environment = {e["Name"]: e["Value"] for e in _container()["Environment"]}
    assert environment == {
        "PRECERTLY_LIVE_ENABLED": "true",
        "PRECERTLY_AWS_REGION": {"Ref": "AWS::Region"},
        "PRECERTLY_AWS_PROFILE": "",
        "PRECERTLY_BEDROCK_MODEL_ID": BEDROCK_PROFILE_ID,
        "PRECERTLY_EMBEDDING_MODEL_ID": EMBEDDING_MODEL_ID,
        "PRECERTLY_DB_POOL": "null",
        "PRECERTLY_DB_CONNECT_TIMEOUT": "60",
    }


def test_the_caps_are_left_at_their_code_defaults() -> None:
    # The code (src/precertly/settings.py) is the one place that says what they are.
    environment = {e["Name"] for e in _container()["Environment"]}
    secrets = {s["Name"] for s in _container()["Secrets"]}
    for name in environment | secrets:
        assert not name.startswith("PRECERTLY_LIVE_MAX"), name
        assert name != "PRECERTLY_LIVE_RUN_TIMEOUT_SECONDS"
    assert {"live_max_runs_per_day", "live_max_runs_per_user", "live_max_tokens_per_day"} <= (
        _settings_fields()
    )


def test_every_setting_name_exists_in_the_api_settings() -> None:
    fields = _settings_fields()
    names_set = {e["Name"] for e in _container()["Environment"]} | {
        s["Name"] for s in _container()["Secrets"]
    }
    for name in names_set:
        assert name.startswith("PRECERTLY_")
        assert name.removeprefix("PRECERTLY_").lower() in fields, name


def test_secrets_are_the_five_database_fields_of_the_data_stacks_secret() -> None:
    secrets = {s["Name"]: s["ValueFrom"] for s in _container()["Secrets"]}
    assert set(secrets) == set(SECRET_FIELD_TO_ENV.values())
    assert len(secrets) == 5
    for field, env in SECRET_FIELD_TO_ENV.items():
        join = secrets[env]["Fn::Join"][1]
        stack, output = _output_name(join[0])
        assert stack == "precertly-data"
        assert "DatabaseSecretAttachment" in output  # the attached secret: it has host and port
        assert join[1] == f":{field}::"
    assert SECRET_FIELD_TO_ENV["dbname"] == "PRECERTLY_DB_NAME"
    assert DB_NAME == "precertly"


def test_health_check_is_the_app_stacks() -> None:
    health = _container()["HealthCheck"]
    assert health["Command"] == ["CMD-SHELL", HEALTH_CHECK_COMMAND]
    assert "/health" in HEALTH_CHECK_COMMAND
    assert (health["Interval"], health["Timeout"], health["Retries"], health["StartPeriod"]) == (
        30,
        5,
        3,
        20,
    )
    app_container = _only(Template.from_stack(_stack("precertly-app")), "AWS::ECS::TaskDefinition")[
        "Properties"
    ]["ContainerDefinitions"][0]
    assert app_container["HealthCheck"] == health


def test_log_group() -> None:
    group = _only(_template(), "AWS::Logs::LogGroup")
    assert group["Properties"]["LogGroupName"] == names.LIVE_LOG_GROUP == "/precertly/live"
    assert group["Properties"]["RetentionInDays"] == 7
    options = _container()["LogConfiguration"]["Options"]
    assert options["awslogs-stream-prefix"] == "live"


# --- IAM ------------------------------------------------------------------------------------


def test_role_names_and_trust() -> None:
    roles = {
        r["Properties"]["RoleName"]: r["Properties"]
        for r in _resources(_template(), "AWS::IAM::Role").values()
    }
    assert set(roles) == {names.LIVE_TASK_ROLE, names.LIVE_EXECUTION_ROLE}
    for properties in roles.values():
        (statement,) = properties["AssumeRolePolicyDocument"]["Statement"]
        assert statement["Principal"] == {"Service": "ecs-tasks.amazonaws.com"}
        assert "ManagedPolicyArns" not in properties


def _normalised(statement: dict[str, Any]) -> dict[str, Any]:
    return {
        "Sid": statement.get("Sid"),
        "Effect": statement["Effect"],
        "Action": sorted(_as_list(statement["Action"])),
        "Resource": sorted(_arn_text(r) for r in _as_list(statement["Resource"])),
    }


def test_task_role_bedrock_statement_is_the_shared_helper_and_the_app_stacks() -> None:
    live = _stack("precertly-live")
    helper = live.resolve(bedrock_invoke_statement(live).to_statement_json())
    live_statements = _policy_statements(Template.from_stack(live), "TaskRole")
    (live_bedrock,) = [s for s in live_statements if s.get("Sid") == "BedrockInvoke"]
    assert _normalised(live_bedrock) == _normalised(helper)

    app_stack = _stack("precertly-app")
    app_statements = _policy_statements(Template.from_stack(app_stack), "TaskRole")
    (app_bedrock,) = [s for s in app_statements if s.get("Sid") == "BedrockInvoke"]
    assert _normalised(live_bedrock) == _normalised(app_bedrock)
    # In words: the profile in this account and region, its foundation model, Titan in-region.
    assert _normalised(live_bedrock)["Resource"] == sorted(
        [
            "arn:${AWS::Partition}:bedrock:${AWS::Region}::foundation-model/amazon.titan-embed-text-v2:0",
            "arn:${AWS::Partition}:bedrock:${AWS::Region}:${AWS::AccountId}:inference-profile/us.amazon.nova-2-lite-v1:0",
            "arn:${AWS::Partition}:bedrock:*::foundation-model/amazon.nova-2-lite-v1:0",
        ]
    )


def test_task_role_has_the_bedrock_statement_and_ecs_exec_and_nothing_else() -> None:
    statements = _policy_statements(_template(), "TaskRole")
    assert len(statements) == 2
    actions = {a for s in statements for a in _as_list(s["Action"])}
    assert actions == {
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
        "ssmmessages:CreateControlChannel",
        "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel",
        "ssmmessages:OpenDataChannel",
    }
    for statement in statements:
        assert "Condition" not in statement


def test_execution_role_pulls_from_the_repo_writes_its_log_group_and_reads_one_secret() -> None:
    statements = _policy_statements(_template(), "ExecutionRole")
    by_actions = {tuple(sorted(_as_list(s["Action"]))): s["Resource"] for s in statements}
    assert set(by_actions) == {
        (
            "ecr:BatchCheckLayerAvailability",
            "ecr:BatchGetImage",
            "ecr:GetDownloadUrlForLayer",
        ),
        ("ecr:GetAuthorizationToken",),
        ("logs:CreateLogStream", "logs:PutLogEvents"),
        ("secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue"),
    }
    repo = by_actions[
        ("ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer")
    ]
    assert _arn_text(repo).endswith(":repository/precertly-api")
    stack, output = _output_name(
        by_actions[("secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue")]
    )
    assert (stack, "DatabaseSecretAttachment" in output) == ("precertly-data", True)


def test_only_ecs_exec_and_the_ecr_token_use_a_wildcard_resource() -> None:
    wildcard = set()
    for policy in _resources(_template(), "AWS::IAM::Policy").values():
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


def test_no_s3_no_other_secrets_access_and_no_provider() -> None:
    template = _template()
    text = json.dumps(template.to_json())
    assert '"s3:' not in text
    assert "AWS::S3::" not in text
    for resource_type in (
        "AWS::IAM::OIDCProvider",
        "AWS::IAM::SAMLProvider",
        "AWS::IAM::ManagedPolicy",
        "AWS::SecretsManager::Secret",
    ):
        assert _resources(template, resource_type) == {}, resource_type
    # The task role never sees Secrets Manager: ECS reads the secret with the execution role.
    task_actions = {
        a for s in _policy_statements(template, "TaskRole") for a in _as_list(s["Action"])
    }
    assert not {a for a in task_actions if a.startswith("secretsmanager:")}


# --- the service ----------------------------------------------------------------------------


def test_service_is_at_zero_with_exec_and_a_public_ip_in_the_public_subnets() -> None:
    properties = _only(_template(), "AWS::ECS::Service")["Properties"]
    assert properties["ServiceName"] == names.API_LIVE_SERVICE
    assert properties["Cluster"] == names.CLUSTER == "precertly"
    assert properties["DesiredCount"] == 0
    assert properties["EnableExecuteCommand"] is True
    assert properties["LaunchType"] == "FARGATE"
    assert properties["LoadBalancers"] == []
    assert properties["DeploymentConfiguration"] == {
        "DeploymentCircuitBreaker": {"Enable": True, "Rollback": True},
        "MaximumPercent": 100,
        "MinimumHealthyPercent": 0,
    }
    network = properties["NetworkConfiguration"]["AwsvpcConfiguration"]
    assert network["AssignPublicIp"] == "ENABLED"
    assert len(network["Subnets"]) == 2
    for subnet in network["Subnets"]:
        stack, output = _output_name(subnet)
        assert stack == "precertly-app"
        assert "publicSubnet" in output


def test_service_uses_the_task_group_and_registers_an_srv_record_on_port_8000() -> None:
    template = _template()
    properties = _only(template, "AWS::ECS::Service")["Properties"]
    (group,) = properties["NetworkConfiguration"]["AwsvpcConfiguration"]["SecurityGroups"]
    (task_id,) = [
        k
        for k, v in _resources(template, "AWS::EC2::SecurityGroup").items()
        if v["Properties"]["GroupDescription"].startswith("Live task")
    ]
    assert group == {"Fn::GetAtt": [task_id, "GroupId"]}
    (registry,) = properties["ServiceRegistries"]
    assert registry["ContainerName"] == names.LIVE_CONTAINER
    assert registry["ContainerPort"] == 8000
    service = _only(template, "AWS::ServiceDiscovery::Service")["Properties"]
    assert service["Name"] == names.LIVE_CLOUD_MAP_SERVICE
    assert [r["Type"] for r in service["DnsConfig"]["DnsRecords"]] == ["SRV"]


def test_the_namespace_is_private_dns_in_the_app_vpc() -> None:
    namespace = _only(_template(), "AWS::ServiceDiscovery::PrivateDnsNamespace")["Properties"]
    assert namespace["Name"] == names.LIVE_NAMESPACE
    stack, output = _output_name(namespace["Vpc"])
    assert stack == "precertly-app"
    assert output.startswith("PublishOutputRefVpc")


def test_the_stack_owns_no_cluster_vpc_or_costly_network_piece() -> None:
    template = _template()
    for resource_type in (
        "AWS::ECS::Cluster",
        "AWS::EC2::VPC",
        "AWS::EC2::Subnet",
        "AWS::EC2::NatGateway",
        "AWS::EC2::EIP",
        "AWS::EC2::VPCEndpoint",
        "AWS::ElasticLoadBalancingV2::LoadBalancer",
        "AWS::ElasticLoadBalancingV2::TargetGroup",
        "AWS::WAFv2::WebACL",
        "AWS::Lambda::Function",
        "AWS::CloudFormation::CustomResource",
        "AWS::KMS::Key",
    ):
        assert _resources(template, resource_type) == {}, resource_type
    assert not [
        r["Type"]
        for r in template.to_json()["Resources"].values()
        if r["Type"].startswith("Custom::")
    ]


# --- the gateway ----------------------------------------------------------------------------


def test_exactly_three_routes_and_no_catch_all() -> None:
    routes = _routes(_template())
    assert set(routes) == {"GET /health", "POST /live/runs", "GET /live/runs/{run_id}"}
    for key in routes:
        assert key != "$default"
        assert "ANY" not in key
        assert "{proxy+}" not in key


def test_health_has_no_authorizer_and_the_live_routes_have_the_jwt_authorizer() -> None:
    template = _template()
    routes = _routes(template)
    health = routes["GET /health"]["Properties"]
    assert health.get("AuthorizationType", "NONE") == "NONE"
    assert "AuthorizerId" not in health
    assert "AuthorizationScopes" not in health
    authorizer_id = next(iter(_resources(template, "AWS::ApiGatewayV2::Authorizer")))
    for key in ("POST /live/runs", "GET /live/runs/{run_id}"):
        properties = routes[key]["Properties"]
        assert properties["AuthorizationType"] == "JWT"
        assert properties["AuthorizerId"] == {"Ref": authorizer_id}
        # Only an access token has a scope claim; an ID token would pass the audience check.
        assert properties["AuthorizationScopes"] == [REQUIRED_SCOPE] == ["openid"]


def test_authorizer_checks_the_cognito_issuer_and_client() -> None:
    authorizer = _only(_template(), "AWS::ApiGatewayV2::Authorizer")["Properties"]
    assert authorizer["AuthorizerType"] == "JWT"
    assert authorizer["IdentitySource"] == ["$request.header.Authorization"]
    (audience,) = authorizer["JwtConfiguration"]["Audience"]
    stack, output = _output_name(audience)
    assert stack == "precertly-auth"
    assert "UserPoolWebClient" in output
    issuer = _flat(authorizer["JwtConfiguration"]["Issuer"])
    # One scheme, the regional Cognito host and the pool id (a reference to the auth stack).
    assert issuer.startswith("https://cognito-idp.{AWS::Region}.amazonaws.com/{precertly-auth:")
    assert "UserPool" in issuer
    assert issuer.count("https://") == 1


def _flat(value: Any) -> str:
    """A template value as text: literals as they are, a reference to another stack as
    {stack:output} and a pseudo parameter as {name}."""
    if isinstance(value, str):
        return value
    if "Ref" in value:
        return "{" + value["Ref"] + "}"
    if "Fn::GetStackOutput" in value:
        inner = value["Fn::GetStackOutput"]
        return "{" + inner["StackName"] + ":" + inner["OutputName"] + "}"
    return "".join(_flat(part) for part in value["Fn::Join"][1])


def test_the_authorizer_issuer_has_one_scheme_and_no_provider_url() -> None:
    template = _template()
    authorizer = _only(template, "AWS::ApiGatewayV2::Authorizer")["Properties"]
    assert "https://https://" not in _flat(authorizer["JwtConfiguration"]["Issuer"])
    assert "https://https://" not in json.dumps(template.to_json())
    # ProviderURL already contains the scheme; the issuer must not be built from it.
    assert "ProviderURL" not in json.dumps(template.to_json())


def _integrations(template: Template) -> dict[str, dict[str, Any]]:
    return {
        k: v["Properties"]
        for k, v in _resources(template, "AWS::ApiGatewayV2::Integration").items()
    }


def test_both_authorized_routes_overwrite_the_actor_header_with_the_verified_subject() -> None:
    template = _template()
    routes = _routes(template)
    integrations = _integrations(template)
    for key in ("POST /live/runs", "GET /live/runs/{run_id}"):
        target = routes[key]["Properties"]["Target"]["Fn::Join"][1][1]["Ref"]
        mapping = integrations[target]["RequestParameters"]
        assert mapping == {f"overwrite:header.{ACTOR_HEADER}": "$context.authorizer.claims.sub"}
        assert ACTOR_CLAIM == "$context.authorizer.claims.sub"
    # Never an append (which would leave a client's value in place beside ours).
    assert "append:" not in json.dumps(integrations)


def test_health_has_no_actor_mapping() -> None:
    template = _template()
    target = _routes(template)["GET /health"]["Properties"]["Target"]["Fn::Join"][1][1]["Ref"]
    assert "RequestParameters" not in _integrations(template)[target]


def test_integrations_are_private_through_the_vpc_link_to_cloud_map() -> None:
    template = _template()
    link_id = next(iter(_resources(template, "AWS::ApiGatewayV2::VpcLink")))
    service_id = next(iter(_resources(template, "AWS::ServiceDiscovery::Service")))
    integrations = _integrations(template)
    assert len(integrations) == 2
    for properties in integrations.values():
        assert properties["ConnectionType"] == "VPC_LINK"
        assert properties["ConnectionId"] == {"Ref": link_id}
        assert properties["IntegrationType"] == "HTTP_PROXY"
        assert properties["IntegrationUri"] == {"Fn::GetAtt": [service_id, "Arn"]}


def test_the_actor_header_is_the_one_the_application_reads() -> None:
    tree = ast.parse(LIVE_MODELS.read_text())
    values = {
        node.targets[0].id: node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
    }
    assert values["ACTOR_HEADER"] == ACTOR_HEADER == "x-precertly-actor"


def test_vpc_link_is_in_the_public_subnets_with_its_own_group() -> None:
    template = _template()
    link = _only(template, "AWS::ApiGatewayV2::VpcLink")["Properties"]
    assert len(link["SubnetIds"]) == 2
    for subnet in link["SubnetIds"]:
        assert "publicSubnet" in _output_name(subnet)[1]
    (group,) = link["SecurityGroupIds"]
    (link_id,) = [
        k
        for k, v in _resources(template, "AWS::EC2::SecurityGroup").items()
        if v["Properties"]["GroupDescription"].startswith("Live VPC link")
    ]
    assert group == {"Fn::GetAtt": [link_id, "GroupId"]}


def test_cors_allows_the_site_origin_only() -> None:
    cors = _only(_template(), "AWS::ApiGatewayV2::Api")["Properties"]["CorsConfiguration"]
    assert cors == {
        "AllowCredentials": False,
        "AllowHeaders": ["authorization", "content-type"],
        "AllowMethods": ["GET", "POST", "OPTIONS"],
        "AllowOrigins": [SITE],
        "MaxAge": 3600,
    }
    other = _template(context(webDomain="precertly.example.org"))
    api = _only(other, "AWS::ApiGatewayV2::Api")["Properties"]
    assert api["CorsConfiguration"]["AllowOrigins"] == ["https://precertly.example.org"]
    assert "*" not in json.dumps(cors)


def test_one_default_stage_with_auto_deploy_and_throttling() -> None:
    stage = _only(_template(), "AWS::ApiGatewayV2::Stage")["Properties"]
    assert stage["StageName"] == "$default"
    assert stage["AutoDeploy"] is True
    assert stage["DefaultRouteSettings"] == {
        "ThrottlingBurstLimit": BURST_LIMIT,
        "ThrottlingRateLimit": RATE_LIMIT,
    }
    assert (RATE_LIMIT, BURST_LIMIT) == (2, 5)
    assert "AccessLogSettings" not in stage


def test_api_has_no_quick_create_target_and_one_api() -> None:
    api = _only(_template(), "AWS::ApiGatewayV2::Api")["Properties"]
    assert api["ProtocolType"] == "HTTP"
    assert api["Name"] == names.API_NAME
    assert "Target" not in api
    assert "RouteKey" not in api


# --- references, outputs and removal --------------------------------------------------------


def test_references_to_other_stacks_are_weak_and_only_to_app_data_and_auth() -> None:
    template = _template().to_json()
    text = json.dumps(template)
    assert "Fn::ImportValue" not in text
    assert all("Export" not in o for o in template["Outputs"].values())
    referenced = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if "Fn::GetStackOutput" in value:
                referenced.add(value["Fn::GetStackOutput"]["StackName"])
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(template)
    assert referenced == {"precertly-app", "precertly-data", "precertly-auth"}


def test_outputs() -> None:
    outputs = _template().to_json()["Outputs"]
    assert set(outputs) == {"ApiUrl", "ClusterName", "ServiceName", "LogGroupName"}
    assert outputs["ClusterName"]["Value"] == names.CLUSTER
    url = outputs["ApiUrl"]["Value"]["Fn::Join"][1]
    assert url[0] == "https://"
    assert ".execute-api." in url


def test_every_resource_is_removed_with_the_stack() -> None:
    for resource in _template().to_json()["Resources"].values():
        assert resource.get("DeletionPolicy", "Delete") == "Delete", resource["Type"]
        assert resource.get("UpdateReplacePolicy", "Delete") == "Delete", resource["Type"]
    text = json.dumps(_template().to_json())
    assert "Retain" not in text
    assert "Snapshot" not in text


def test_the_placeholder_site_domain_is_accepted_at_synth() -> None:
    # deploy.sh refuses the placeholder for this stack; the stack itself must not, or CI could
    # not synthesize it.
    assert SITE.endswith(".invalid")
    assert _only(_template(), "AWS::ApiGatewayV2::Api")


@pytest.mark.parametrize("domain", ["https://site.example.com", "Site.Example.com", "a b.com", ""])
def test_a_bad_site_domain_is_refused(domain: str) -> None:
    with pytest.raises(ValueError, match="webDomain"):
        build_app(context(webDomain=domain))
