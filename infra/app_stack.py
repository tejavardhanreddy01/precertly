"""precertly-app: the network and the API service, at zero tasks.

Two public subnets and an internet gateway, and nothing else on the network: no NAT gateway,
no interface endpoints, no load balancer. The task has no inbound rules at all; it is
reached with ECS Exec, not over the network. The service has desired count 0, so deploying
this stack starts nothing and costs nothing beyond the log group.

The API task gets no database and no secrets; the data stack is empty.
"""

from typing import Any

import aws_cdk as cdk
from aws_cdk import Aws, aws_ec2, aws_ecr, aws_ecs, aws_iam, aws_logs
from constructs import Construct

import names
from precertly_stack import PrecertlyStack

IMAGE_TAG_CONTEXT_KEY = "imageTag"
IMAGE_TAG_PREFIX = "sha-"

# The values of src/precertly/settings.py (bedrock_model_id, embedding_model_id); a test
# compares them with the settings source so they cannot drift.
BEDROCK_PROFILE_ID = "us.amazon.nova-2-lite-v1:0"
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
API_PORT = 8000
# The same Python as the Dockerfile HEALTHCHECK (the image has no curl), run through the shell.
HEALTH_CHECK_COMMAND = (
    'python -c "import urllib.request; '
    f"urllib.request.urlopen('http://127.0.0.1:{API_PORT}/health', timeout=3)\""
)


def validate_image_tag(tag: object) -> str:
    if not isinstance(tag, str) or not tag.startswith(IMAGE_TAG_PREFIX) or tag == IMAGE_TAG_PREFIX:
        raise ValueError(
            f"CDK context '{IMAGE_TAG_CONTEXT_KEY}' is required and must look like "
            f"'{IMAGE_TAG_PREFIX}<12 hex>': pass `-c {IMAGE_TAG_CONTEXT_KEY}=<tag>` "
            "(scripts/deploy.sh does this)."
        )
    return tag


class AppStack(PrecertlyStack):
    def __init__(self, scope: Construct, construct_id: str, *, image_tag: str, **kwargs: Any):
        super().__init__(scope, construct_id, **kwargs)
        self.vpc = self._vpc()
        self.security_group = aws_ec2.SecurityGroup(
            self,
            "TaskSecurityGroup",
            vpc=self.vpc,
            description="API task: no inbound rules, all outbound",
            allow_all_outbound=True,
        )
        self.log_group = aws_logs.LogGroup(
            self,
            "ApiLogGroup",
            log_group_name=names.LOG_GROUP,
            retention=aws_logs.RetentionDays.ONE_WEEK,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        repository = aws_ecr.Repository.from_repository_name(self, "Repository", names.ECR_REPO)
        self.task_role = self._task_role()
        self.execution_role = aws_iam.Role(
            self,
            "ExecutionRole",
            role_name=names.EXECUTION_ROLE,
            assumed_by=aws_iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        self.task_definition = self._task_definition(repository, image_tag)
        self.cluster = aws_ecs.Cluster(self, "Cluster", cluster_name=names.CLUSTER, vpc=self.vpc)
        self.service = aws_ecs.FargateService(
            self,
            "ApiService",
            service_name=names.SERVICE,
            cluster=self.cluster,
            task_definition=self.task_definition,
            desired_count=0,
            enable_execute_command=True,
            assign_public_ip=True,
            vpc_subnets=aws_ec2.SubnetSelection(subnet_type=aws_ec2.SubnetType.PUBLIC),
            security_groups=[self.security_group],
            min_healthy_percent=0,
            max_healthy_percent=100,
            circuit_breaker=aws_ecs.DeploymentCircuitBreaker(rollback=True),
        )
        cdk.CfnOutput(self, "ClusterName", value=self.cluster.cluster_name)
        cdk.CfnOutput(self, "ServiceName", value=self.service.service_name)
        cdk.CfnOutput(self, "LogGroupName", value=self.log_group.log_group_name)

    def _vpc(self) -> aws_ec2.Vpc:
        return aws_ec2.Vpc(
            self,
            "Vpc",
            availability_zones=self.availability_zones,
            nat_gateways=0,
            subnet_configuration=[
                aws_ec2.SubnetConfiguration(
                    name="public", subnet_type=aws_ec2.SubnetType.PUBLIC, cidr_mask=24
                )
            ],
            # The default would add a custom-resource Lambda and its role to empty the VPC's
            # default security group. Nothing uses that group; the task has its own.
            restrict_default_security_group=False,
        )

    def _bedrock_arn(self, resource: str, *, region: str, account: str) -> str:
        return self.format_arn(service="bedrock", resource=resource, region=region, account=account)

    def _task_role(self) -> aws_iam.Role:
        role = aws_iam.Role(
            self,
            "TaskRole",
            role_name=names.TASK_ROLE,
            assumed_by=aws_iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        # ECS Exec: `enable_execute_command` on the service adds the four ssmmessages actions to
        # this role. They have no resource-level scoping, so that statement's resource is "*";
        # it is the only wildcard resource on the role.
        # Unverified until a real call: a "us." profile routes across US regions, so the
        # underlying foundation model is allowed in any region (wildcard region, this model id
        # only), alongside the profile in this account and region. Titan is called in-region.
        foundation_model = BEDROCK_PROFILE_ID.removeprefix("us.")
        role.add_to_policy(
            aws_iam.PolicyStatement(
                sid="BedrockInvoke",
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=[
                    self._bedrock_arn(
                        f"inference-profile/{BEDROCK_PROFILE_ID}",
                        region=Aws.REGION,
                        account=Aws.ACCOUNT_ID,
                    ),
                    self._bedrock_arn(
                        f"foundation-model/{foundation_model}", region="*", account=""
                    ),
                    self._bedrock_arn(
                        f"foundation-model/{EMBEDDING_MODEL_ID}", region=Aws.REGION, account=""
                    ),
                ],
            )
        )
        return role

    def _task_definition(
        self, repository: aws_ecr.IRepository, image_tag: str
    ) -> aws_ecs.FargateTaskDefinition:
        task_definition = aws_ecs.FargateTaskDefinition(
            self,
            "ApiTaskDefinition",
            family=names.SERVICE,
            cpu=256,
            memory_limit_mib=512,
            runtime_platform=aws_ecs.RuntimePlatform(
                cpu_architecture=aws_ecs.CpuArchitecture.ARM64,
                operating_system_family=aws_ecs.OperatingSystemFamily.LINUX,
            ),
            task_role=self.task_role,
            execution_role=self.execution_role,
        )
        # No command or entrypoint: the image's own CMD applies. ECS ignores the Dockerfile
        # HEALTHCHECK, so the container health check is set below. No secrets and no database
        # settings.
        task_definition.add_container(
            "api",
            image=aws_ecs.ContainerImage.from_ecr_repository(repository, image_tag),
            port_mappings=[aws_ecs.PortMapping(container_port=API_PORT)],
            environment={
                "PRECERTLY_AWS_REGION": Aws.REGION,
                # Empty: use the task role, not the local named profile the setting defaults to.
                "PRECERTLY_AWS_PROFILE": "",
                "PRECERTLY_BEDROCK_MODEL_ID": BEDROCK_PROFILE_ID,
                "PRECERTLY_EMBEDDING_MODEL_ID": EMBEDDING_MODEL_ID,
            },
            logging=aws_ecs.LogDrivers.aws_logs(stream_prefix="api", log_group=self.log_group),
            health_check=aws_ecs.HealthCheck(
                command=["CMD-SHELL", HEALTH_CHECK_COMMAND],
                interval=cdk.Duration.seconds(30),
                timeout=cdk.Duration.seconds(5),
                retries=3,
                start_period=cdk.Duration.seconds(20),
            ),
        )
        return task_definition
