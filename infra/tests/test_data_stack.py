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
from data_stack import (
    AURORA_POSTGRES_VERSION,
    DB_NAME,
    DB_USER,
    SECRET_FIELD_TO_ENV,
)

SETTINGS = Path(__file__).resolve().parents[2] / "src" / "precertly" / "settings.py"
DATA_STACK_SOURCE = Path(__file__).resolve().parents[1] / "data_stack.py"


def _template(extra: dict[str, Any] | None = None) -> Template:
    app = build_app(extra if extra is not None else context())
    stack = next(c for c in app.node.children if c.node.id == "precertly-data")
    assert isinstance(stack, cdk.Stack)
    return Template.from_stack(stack)


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
    if "Fn::GetAtt" in value:
        return "${" + ".".join(value["Fn::GetAtt"]) + "}"
    return "".join(_arn_text(part) for part in value["Fn::Join"][1])


def _security_groups(template: Template) -> dict[str, dict[str, Any]]:
    return {
        r["Properties"]["GroupDescription"].split(":")[0]: r
        for r in _resources(template, "AWS::EC2::SecurityGroup").values()
    }


def _task_definition(template: Template) -> dict[str, Any]:
    return _only(template, "AWS::ECS::TaskDefinition")["Properties"]


def _policy_statements(template: Template, role_prefix: str) -> list[dict[str, Any]]:
    for policy in _resources(template, "AWS::IAM::Policy").values():
        if any(r["Ref"].startswith(role_prefix) for r in policy["Properties"]["Roles"]):
            return list(policy["Properties"]["PolicyDocument"]["Statement"])
    raise AssertionError(f"no policy for {role_prefix}")


def _settings_fields() -> set[str]:
    return {
        node.target.id
        for node in ast.walk(ast.parse(SETTINGS.read_text()))
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }


# --- the database --------------------------------------------------------------------------


def test_cluster_properties() -> None:
    cluster = _only(_template(), "AWS::RDS::DBCluster")
    properties = cluster["Properties"]
    assert properties["Engine"] == "aurora-postgresql"
    assert properties["EngineVersion"] == "16.13"
    assert properties["ServerlessV2ScalingConfiguration"] == {
        "MinCapacity": 0,
        "MaxCapacity": 2,
        "SecondsUntilAutoPause": 300,
    }
    assert properties["StorageEncrypted"] is True
    assert properties["BackupRetentionPeriod"] == 1
    assert properties["DeletionProtection"] is False
    assert properties["DatabaseName"] == DB_NAME
    assert properties["EnableHttpEndpoint"] is False  # no Data API
    assert "DBClusterIdentifier" not in properties  # generated
    assert "SourceDBClusterIdentifier" not in properties
    assert "KmsKeyId" not in properties
    assert cluster["DeletionPolicy"] == "Delete"
    assert cluster["UpdateReplacePolicy"] == "Delete"


def test_engine_version_is_one_constant() -> None:
    assert AURORA_POSTGRES_VERSION.aurora_postgres_full_version == "16.13"
    # Said once: the only place the version is chosen.
    assert DATA_STACK_SOURCE.read_text().count("AuroraPostgresEngineVersion.") == 1


def test_one_serverless_writer_and_nothing_else() -> None:
    instance = _only(_template(), "AWS::RDS::DBInstance")
    properties = instance["Properties"]
    assert properties["DBInstanceClass"] == "db.serverless"
    assert properties["PubliclyAccessible"] is False
    assert instance["DeletionPolicy"] == "Delete"
    assert instance["UpdateReplacePolicy"] == "Delete"


def test_subnet_group_uses_only_the_isolated_subnets() -> None:
    group = _only(_template(), "AWS::RDS::DBSubnetGroup")["Properties"]
    ids = [s["Fn::GetStackOutput"]["OutputName"] for s in group["SubnetIds"]]
    assert len(ids) == 2
    for output_name in ids:
        assert "databaseSubnet" in output_name
        assert "publicSubnet" not in output_name


def test_database_security_group_takes_only_the_tools_group_and_sends_nothing() -> None:
    template = _template()
    groups = _security_groups(template)
    database = groups["Database"]["Properties"]
    tools = groups["Tools task"]["Properties"]
    # Nothing is inline: the one ingress rule is its own resource.
    assert "SecurityGroupIngress" not in database
    assert "SecurityGroupIngress" not in tools
    # With outbound disabled the CDK writes one rule that matches no traffic at all.
    assert database["SecurityGroupEgress"] == [
        {
            "CidrIp": "255.255.255.255/32",
            "Description": "Disallow all traffic",
            "FromPort": 252,
            "IpProtocol": "icmp",
            "ToPort": 86,
        }
    ]
    assert [r["CidrIp"] for r in tools["SecurityGroupEgress"]] == ["0.0.0.0/0"]
    (rule,) = _resources(template, "AWS::EC2::SecurityGroupIngress").values()
    properties = rule["Properties"]
    assert (properties["IpProtocol"], properties["FromPort"], properties["ToPort"]) == (
        "tcp",
        5432,
        5432,
    )
    tools_id = next(
        k
        for k, v in _resources(template, "AWS::EC2::SecurityGroup").items()
        if v["Properties"]["GroupDescription"].startswith("Tools task")
    )
    assert properties["SourceSecurityGroupId"] == {"Fn::GetAtt": [tools_id, "GroupId"]}
    assert "CidrIp" not in properties and "CidrIpv6" not in properties
    assert len(_resources(template, "AWS::EC2::SecurityGroup")) == 2


def test_cluster_uses_the_database_group_alone() -> None:
    template = _template()
    cluster = _only(template, "AWS::RDS::DBCluster")["Properties"]
    (group,) = cluster["VpcSecurityGroupIds"]
    database_id = next(
        k
        for k, v in _resources(template, "AWS::EC2::SecurityGroup").items()
        if v["Properties"]["GroupDescription"].startswith("Database:")
    )
    assert group == {"Fn::GetAtt": [database_id, "GroupId"]}


# --- the secret -----------------------------------------------------------------------------


def test_secret_template_and_generated_password() -> None:
    secret = _only(_template(), "AWS::SecretsManager::Secret")
    generate = secret["Properties"]["GenerateSecretString"]
    assert json.loads(generate["SecretStringTemplate"]) == {
        "username": DB_USER,
        "dbname": DB_NAME,
    }
    assert generate["GenerateStringKey"] == "password"
    assert generate["PasswordLength"] >= 32
    # URL- and shell-safe: no punctuation at all, and no further exclusions to tune.
    assert generate["ExcludePunctuation"] is True
    assert "ExcludeCharacters" not in generate
    assert "Name" not in secret["Properties"]  # no fixed name
    assert secret["DeletionPolicy"] == "Delete"
    assert secret["UpdateReplacePolicy"] == "Delete"


def test_secret_is_attached_to_the_cluster_and_feeds_its_login() -> None:
    template = _template()
    attachment = _only(template, "AWS::SecretsManager::SecretTargetAttachment")["Properties"]
    assert attachment["TargetType"] == "AWS::RDS::DBCluster"
    cluster = _only(template, "AWS::RDS::DBCluster")["Properties"]
    assert ":SecretString:username::" in json.dumps(cluster["MasterUsername"])
    assert ":SecretString:password::" in json.dumps(cluster["MasterUserPassword"])


def test_ecs_secrets_map_the_json_keys_to_the_settings() -> None:
    (container,) = _task_definition(_template())["ContainerDefinitions"]
    mapped = {}
    for entry in container["Secrets"]:
        _, suffix = entry["ValueFrom"]["Fn::Join"][1]
        assert suffix.startswith(":") and suffix.endswith("::")
        mapped[suffix.strip(":")] = entry["Name"]
    assert mapped == {
        "host": "PRECERTLY_DB_HOST",
        "port": "PRECERTLY_DB_PORT",
        "username": "PRECERTLY_DB_USER",
        "password": "PRECERTLY_DB_PASSWORD",
        "dbname": "PRECERTLY_DB_NAME",
    }
    assert mapped == SECRET_FIELD_TO_ENV
    # The names are the ones settings.py reads (env_prefix PRECERTLY_ plus the field name).
    fields = _settings_fields()
    for env_name in mapped.values():
        assert env_name.removeprefix("PRECERTLY_").lower() in fields, env_name


# --- the tools task -------------------------------------------------------------------------


def test_tools_task_shape() -> None:
    task = _task_definition(_template())
    assert task["Family"] == names.TOOLS_FAMILY == "precertly-tools"
    assert task["RequiresCompatibilities"] == ["FARGATE"]
    assert task["RuntimePlatform"] == {"CpuArchitecture": "ARM64", "OperatingSystemFamily": "LINUX"}
    assert (task["Cpu"], task["Memory"]) == ("512", "1024")
    (container,) = task["ContainerDefinitions"]
    assert container["Name"] == names.TOOLS_CONTAINER
    for key in ("Command", "EntryPoint", "PortMappings", "HealthCheck"):
        assert key not in container, key
    assert container["LogConfiguration"]["Options"]["awslogs-stream-prefix"] == "tools"


def test_tools_have_no_service_and_no_exec() -> None:
    template = _template()
    assert _resources(template, "AWS::ECS::Service") == {}
    assert _resources(template, "AWS::ECS::Cluster") == {}
    text = json.dumps(template.to_json())
    assert "ssmmessages" not in text
    assert "EnableExecuteCommand" not in text


def test_image_comes_from_the_ecr_repo_at_the_context_tag() -> None:
    (container,) = _task_definition(_template())["ContainerDefinitions"]
    assert _arn_text(container["Image"]).endswith(f"/{names.ECR_REPO}:sha-000000000000")
    other = _template(context(imageTag="sha-abcdef012345"))
    (container,) = _task_definition(other)["ContainerDefinitions"]
    assert _arn_text(container["Image"]).endswith(f"/{names.ECR_REPO}:sha-abcdef012345")


def test_plain_environment() -> None:
    (container,) = _task_definition(_template())["ContainerDefinitions"]
    env = {e["Name"]: e["Value"] for e in container["Environment"]}
    assert env["PRECERTLY_DB_POOL"] == "null"
    assert env["PRECERTLY_DB_CONNECT_TIMEOUT"] == "60"
    assert env["PRECERTLY_AWS_PROFILE"] == ""
    assert env["PRECERTLY_AWS_REGION"] == {"Ref": "AWS::Region"}
    assert set(env) == {
        "PRECERTLY_AWS_REGION",
        "PRECERTLY_AWS_PROFILE",
        "PRECERTLY_BEDROCK_MODEL_ID",
        "PRECERTLY_EMBEDDING_MODEL_ID",
        "PRECERTLY_DB_POOL",
        "PRECERTLY_DB_CONNECT_TIMEOUT",
    }
    # No password or URL in plain text: those come only through the secrets.
    for name in env:
        assert name not in {"PRECERTLY_DB_PASSWORD", "PRECERTLY_DATABASE_URL"}
    assert {"db_pool", "db_connect_timeout"} <= _settings_fields()


def test_role_names_are_separate_from_the_api_roles() -> None:
    template = _template()
    roles = {
        r["Properties"]["RoleName"]: r["Properties"]
        for r in _resources(template, "AWS::IAM::Role").values()
    }
    assert set(roles) == {names.TOOLS_TASK_ROLE, names.TOOLS_EXECUTION_ROLE}
    assert not {names.TASK_ROLE, names.EXECUTION_ROLE} & set(roles)
    for role in roles.values():
        (statement,) = role["AssumeRolePolicyDocument"]["Statement"]
        assert statement["Principal"] == {"Service": "ecs-tasks.amazonaws.com"}
    task = _task_definition(template)
    assert "TaskRoleArn" in task and "ExecutionRoleArn" in task
    assert task["TaskRoleArn"] != task["ExecutionRoleArn"]


def test_task_role_may_only_embed() -> None:
    (statement,) = _policy_statements(_template(), "ToolsTaskRole")
    assert statement["Action"] == "bedrock:InvokeModel"
    assert statement["Effect"] == "Allow"
    assert "Condition" not in statement
    assert _arn_text(statement["Resource"]) == (
        "arn:${AWS::Partition}:bedrock:${AWS::Region}::foundation-model/amazon.titan-embed-text-v2:0"
    )


def test_execution_role_scopes() -> None:
    template = _template()
    statements = _policy_statements(template, "ToolsExecutionRole")
    by_actions = {tuple(sorted(_as_list(s["Action"]))): s["Resource"] for s in statements}
    assert len(statements) == 4
    assert _arn_text(
        by_actions[
            (
                "ecr:BatchCheckLayerAvailability",
                "ecr:BatchGetImage",
                "ecr:GetDownloadUrlForLayer",
            )
        ]
    ).endswith(f":repository/{names.ECR_REPO}")
    assert by_actions[("ecr:GetAuthorizationToken",)] == "*"
    log_group_id = next(iter(_resources(template, "AWS::Logs::LogGroup")))
    assert by_actions[("logs:CreateLogStream", "logs:PutLogEvents")] == {
        "Fn::GetAtt": [log_group_id, "Arn"]
    }
    # The CDK grants read on the secret: both actions, the one secret, nothing else.
    secret_resource = by_actions[("secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue")]
    attachment_id = next(iter(_resources(template, "AWS::SecretsManager::SecretTargetAttachment")))
    assert secret_resource == {"Ref": attachment_id}


def test_only_the_ecr_token_uses_a_wildcard_resource() -> None:
    wildcard = set()
    for policy in _resources(_template(), "AWS::IAM::Policy").values():
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            actions = _as_list(statement["Action"])
            assert all("*" not in a for a in actions), actions
            if "*" in _as_list(statement["Resource"]):
                wildcard.update(actions)
    assert wildcard == {"ecr:GetAuthorizationToken"}


def test_log_group() -> None:
    template = _template()
    group = _only(template, "AWS::Logs::LogGroup")
    assert group["Properties"]["LogGroupName"] == names.TOOLS_LOG_GROUP == "/precertly/tools"
    assert group["Properties"]["RetentionInDays"] == 7
    assert group["DeletionPolicy"] == "Delete"


def test_outputs_are_what_deploy_sh_reads() -> None:
    outputs = _template().to_json()["Outputs"]
    assert set(outputs) == {
        "ClusterIdentifier",
        "SecretArn",
        "ToolsSubnetIds",
        "ToolsSecurityGroupId",
        "ToolsLogGroupName",
    }
    subnet_ids = outputs["ToolsSubnetIds"]["Value"]["Fn::Join"]
    assert subnet_ids[0] == ","
    assert len(subnet_ids[1]) == 2
    for reference in subnet_ids[1]:
        assert "publicSubnet" in reference["Fn::GetStackOutput"]["OutputName"]


# --- what the stack must not contain ---------------------------------------------------------


def test_no_lambda_no_custom_resource_no_iam_provider() -> None:
    template = _template()
    for resource_type in (
        "AWS::IAM::OIDCProvider",
        "AWS::IAM::SAMLProvider",
        "AWS::Lambda::Function",
        "AWS::CloudFormation::CustomResource",
        "AWS::RDS::DBProxy",
        "AWS::EC2::NatGateway",
        "AWS::EC2::VPCEndpoint",
    ):
        assert _resources(template, resource_type) == {}, resource_type
    types = [r["Type"] for r in template.to_json()["Resources"].values()]
    assert not [t for t in types if t.startswith("Custom::")]


def test_nothing_is_retained_or_snapshotted() -> None:
    for resource in _template().to_json()["Resources"].values():
        assert resource.get("DeletionPolicy", "Delete") == "Delete", resource["Type"]
        assert resource.get("UpdateReplacePolicy", "Delete") == "Delete", resource["Type"]


def test_stack_is_environment_agnostic() -> None:
    text = json.dumps(_template().to_json())
    assert "us-east-2" not in text
    assert "AWS::AccountId" in text


@pytest.mark.parametrize("tag", [None, "latest", "sha-"])
def test_image_tag_is_still_required(tag: Any) -> None:
    with pytest.raises(ValueError, match="imageTag"):
        build_app(context(imageTag=tag))
