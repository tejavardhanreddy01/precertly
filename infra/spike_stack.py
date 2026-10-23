"""precertly-spike: a THROWAWAY infrastructure spike (2026-10-23). Deleted or replaced by the
real live stack; never part of a normal synth or deploy.

Question: does this work with no load balancer, no NAT and no interface endpoints?

    API Gateway HTTP API -> VPC link -> Cloud Map (SRV) -> Fargate task in a PUBLIC subnet

AWS's own example uses private subnets and endpoints, so this combination is unverified. It is
built only when the CDK context `spike` is the string "true"; without it the app has exactly the
four normal stacks. Nothing here has been deployed.

Like precertly-data, it takes the app's VPC and subnets by reference (weak cross-stack
references), so precertly-app must not change. The task runs the existing API image with no
secrets, no database settings and no permissions at all; the only way in is the VPC link's
security group on port 8000, and the only route is `GET /health`.
"""

from typing import Any

import aws_cdk as cdk
from aws_cdk import (
    aws_apigatewayv2,
    aws_apigatewayv2_integrations,
    aws_ec2,
    aws_ecr,
    aws_ecs,
    aws_iam,
    aws_logs,
    aws_servicediscovery,
)
from constructs import Construct

import names
from app_stack import API_PORT, HEALTH_CHECK_COMMAND
from precertly_stack import PrecertlyStack

SPIKE_CONTEXT_KEY = "spike"
NAMESPACE_NAME = "precertly-spike.local"
CLOUD_MAP_SERVICE_NAME = "api"
LOG_GROUP = "/precertly/spike"
# The throttle on the stage: this is a probe, not a service.
DEFAULT_STAGE_NAME = "$default"
RATE_LIMIT = 1
BURST_LIMIT = 2


def spike_enabled(context_value: object) -> bool:
    """Only the string "true" (what `-c spike=true` passes) turns the spike on."""
    return context_value == "true"


class SpikeStack(PrecertlyStack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        image_tag: str,
        vpc: aws_ec2.IVpc,
        public_subnets: aws_ec2.SubnetSelection,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.vpc_link_security_group = aws_ec2.SecurityGroup(
            self,
            "VpcLinkSecurityGroup",
            vpc=vpc,
            description="Spike VPC link: no inbound rules, all outbound",
            allow_all_outbound=True,
        )
        self.task_security_group = aws_ec2.SecurityGroup(
            self,
            "TaskSecurityGroup",
            vpc=vpc,
            description="Spike task: port 8000 from the VPC link only, all outbound",
            allow_all_outbound=True,
        )
        # The only inbound rule: the VPC link's security group, never a CIDR range.
        self.task_security_group.add_ingress_rule(
            self.vpc_link_security_group,
            aws_ec2.Port.tcp(API_PORT),
            "API port from the VPC link",
        )
        self.log_group = aws_logs.LogGroup(
            self,
            "LogGroup",
            log_group_name=LOG_GROUP,
            retention=aws_logs.RetentionDays.ONE_DAY,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        self.cluster = aws_ecs.Cluster(self, "Cluster", vpc=vpc)
        self.task_definition = self._task_definition(image_tag)
        self.service = aws_ecs.FargateService(
            self,
            "Service",
            cluster=self.cluster,
            task_definition=self.task_definition,
            desired_count=1,
            # No NAT and no endpoints: a public IP is what lets the task pull from ECR.
            assign_public_ip=True,
            vpc_subnets=public_subnets,
            security_groups=[self.task_security_group],
            enable_execute_command=False,
            min_healthy_percent=0,
            max_healthy_percent=100,
            circuit_breaker=aws_ecs.DeploymentCircuitBreaker(rollback=True),
        )
        self.namespace = aws_servicediscovery.PrivateDnsNamespace(
            self, "Namespace", name=NAMESPACE_NAME, vpc=vpc
        )
        # With awsvpc networking an SRV record needs the container and its port.
        self.service.enable_cloud_map(
            cloud_map_namespace=self.namespace,
            name=CLOUD_MAP_SERVICE_NAME,
            dns_record_type=aws_servicediscovery.DnsRecordType.SRV,
            container=self.task_definition.default_container,
            container_port=API_PORT,
        )
        cloud_map_service = self.service.cloud_map_service
        assert cloud_map_service is not None
        self.vpc_link = aws_apigatewayv2.VpcLink(
            self,
            "VpcLink",
            vpc=vpc,
            subnets=public_subnets,
            security_groups=[self.vpc_link_security_group],
        )
        self.api = aws_apigatewayv2.HttpApi(
            self,
            "HttpApi",
            api_name="precertly-spike",
            description="Throwaway spike: one GET /health route, no authorizer",
            create_default_stage=False,
        )
        self.api.add_routes(
            path="/health",
            methods=[aws_apigatewayv2.HttpMethod.GET],
            integration=aws_apigatewayv2_integrations.HttpServiceDiscoveryIntegration(
                "HealthIntegration",
                cloud_map_service,
                vpc_link=self.vpc_link,
            ),
        )
        # The default stage, made by hand because the one HttpApi creates takes no throttle.
        self.stage = aws_apigatewayv2.HttpStage(
            self,
            "DefaultStage",
            http_api=self.api,
            stage_name=DEFAULT_STAGE_NAME,
            auto_deploy=True,
            throttle=aws_apigatewayv2.ThrottleSettings(
                rate_limit=RATE_LIMIT, burst_limit=BURST_LIMIT
            ),
        )
        cdk.CfnOutput(self, "ApiUrl", value=self.stage.url)
        cdk.CfnOutput(self, "ClusterName", value=self.cluster.cluster_name)
        cdk.CfnOutput(self, "ServiceName", value=self.service.service_name)
        cdk.CfnOutput(self, "LogGroupName", value=self.log_group.log_group_name)
        # Everything goes with the stack. Applies to every resource that has a removal policy.
        cdk.RemovalPolicies.of(self).destroy()

    def _task_definition(self, image_tag: str) -> aws_ecs.FargateTaskDefinition:
        repository = aws_ecr.Repository.from_repository_name(self, "Repository", names.ECR_REPO)
        # A task role with no policies; the execution role is created by the task definition
        # (image pull from the repository and log writes only).
        task_role = aws_iam.Role(
            self,
            "TaskRole",
            assumed_by=aws_iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        task_definition = aws_ecs.FargateTaskDefinition(
            self,
            "TaskDefinition",
            cpu=256,
            memory_limit_mib=512,
            runtime_platform=aws_ecs.RuntimePlatform(
                cpu_architecture=aws_ecs.CpuArchitecture.ARM64,
                operating_system_family=aws_ecs.OperatingSystemFamily.LINUX,
            ),
            task_role=task_role,
        )
        # No command, secrets, database settings or Bedrock settings: /health touches nothing.
        # The timings are the app stack's (they are inline there); the command is imported.
        task_definition.add_container(
            "api",
            image=aws_ecs.ContainerImage.from_ecr_repository(repository, image_tag),
            port_mappings=[aws_ecs.PortMapping(container_port=API_PORT)],
            logging=aws_ecs.LogDrivers.aws_logs(stream_prefix="spike", log_group=self.log_group),
            health_check=aws_ecs.HealthCheck(
                command=["CMD-SHELL", HEALTH_CHECK_COMMAND],
                interval=cdk.Duration.seconds(30),
                timeout=cdk.Duration.seconds(5),
                retries=3,
                start_period=cdk.Duration.seconds(20),
            ),
        )
        return task_definition
