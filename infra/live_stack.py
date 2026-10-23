"""precertly-live: the live run API on the internet, behind a gateway, at zero tasks.

    browser -> HTTP API (JWT authorizer, throttling, CORS) -> VPC link -> Cloud Map (SRV)
        -> one Fargate task in the app VPC's public subnets
        -> Aurora (precertly-data) and Bedrock

The pieces and why:
- The task has a public IP only because the VPC has no NAT or endpoints and the image pull needs
  a route out. Its security group admits port 8000 from the VPC link's security group and
  nothing else, so a direct connection to the public IP finds nothing (spike result,
  2026-10-23: the connection timed out).
- The gateway is the only way in. GET /health has no authorizer (the web UI uses it to tell
  whether the environment is up). POST /live/runs and GET /live/runs/{run_id} need a Cognito
  access token, and the gateway overwrites the x-precertly-actor header with the token's `sub`
  before the request reaches the task, so a header sent by a client never survives. No other
  route exists, so any other path ends at the gateway with a 404.
- The service is created at desired count 0 and started by scripts/deploy.sh (`live start`).
  Behind a service with no task the gateway answers 500.
- The existing cluster, VPC and subnets come from precertly-app; the database secret and the
  database security group from precertly-data; the issuer and the client id from
  precertly-auth. All are weak cross-stack references (cdk.json), so none of those stacks
  refers to this one. The one thing this stack adds to the data stack's network is a rule on
  the database security group; it lives here, as its own resource, so the data template only
  gains outputs.

Costs: a running task bills by the second (about 0.36 USD a day with its public IPv4 address,
unverified). The standing cost is the private hosted zone that the Cloud Map namespace creates
(about 0.50 USD a month, unverified). No NAT, load balancer, interface endpoint or WAF.
Everything has removal policy DESTROY.
"""

from typing import Any

import aws_cdk as cdk
from aws_cdk import (
    Aws,
    aws_apigatewayv2,
    aws_apigatewayv2_authorizers,
    aws_apigatewayv2_integrations,
    aws_ec2,
    aws_ecr,
    aws_ecs,
    aws_iam,
    aws_logs,
    aws_secretsmanager,
    aws_servicediscovery,
)
from constructs import Construct

import names
from app_stack import (
    API_PORT,
    BEDROCK_PROFILE_ID,
    EMBEDDING_MODEL_ID,
    api_health_check,
    bedrock_invoke_statement,
)
from data_stack import DB_CONNECT_TIMEOUT_SECONDS, DB_PORT, SECRET_FIELD_TO_ENV
from precertly_stack import PrecertlyStack
from web_stack import validate_web_domain

LIVE_CPU = 256
LIVE_MEMORY_MIB = 512

# The header the application trusts (src/precertly/live/models.py ACTOR_HEADER; a test compares).
ACTOR_HEADER = "x-precertly-actor"
# The claim the gateway copies into it: the Cognito user's id.
ACTOR_CLAIM = "$context.authorizer.claims.sub"
# Only an access token carries a `scope` claim (an ID token has none), so requiring this scope
# keeps ID tokens out; the audience check alone would accept either (the authorizer matches
# `client_id` when `aud` is absent, which is the case for Cognito access tokens).
REQUIRED_SCOPE = "openid"

DEFAULT_STAGE_NAME = "$default"
# Per second, for the whole stage. A run takes minutes and one runs at a time, so a client that
# polls every few seconds is far inside it.
RATE_LIMIT = 2
BURST_LIMIT = 5
CORS_MAX_AGE = cdk.Duration.hours(1)
CORS_ALLOWED_HEADERS = ("authorization", "content-type")

HEALTH_PATH = "/health"
START_PATH = "/live/runs"
POLL_PATH = "/live/runs/{run_id}"


class LiveStack(PrecertlyStack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        image_tag: str,
        web_domain: str,
        vpc: aws_ec2.IVpc,
        public_subnets: aws_ec2.SubnetSelection,
        database_secret: aws_secretsmanager.ISecret,
        database_security_group: aws_ec2.ISecurityGroup,
        issuer_url: str,
        client_id: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.web_domain = validate_web_domain(web_domain)
        self.vpc_link_security_group = aws_ec2.SecurityGroup(
            self,
            "VpcLinkSecurityGroup",
            vpc=vpc,
            description="Live VPC link: no inbound rules, all outbound",
            allow_all_outbound=True,
        )
        self.task_security_group = aws_ec2.SecurityGroup(
            self,
            "TaskSecurityGroup",
            vpc=vpc,
            description="Live task: port 8000 from the VPC link only, all outbound",
            allow_all_outbound=True,
        )
        # The only inbound rule: the VPC link's security group, never a CIDR range.
        self.task_security_group.add_ingress_rule(
            self.vpc_link_security_group,
            aws_ec2.Port.tcp(API_PORT),
            "API port from the VPC link",
        )
        # The database group (precertly-data) admits the tools task only. This rule is a
        # resource of this stack on purpose: add_ingress_rule across stacks may put it in the
        # other one, and the data template must not change.
        aws_ec2.CfnSecurityGroupIngress(
            self,
            "DatabaseFromLiveTask",
            group_id=database_security_group.security_group_id,
            source_security_group_id=self.task_security_group.security_group_id,
            ip_protocol="tcp",
            from_port=DB_PORT,
            to_port=DB_PORT,
            description="PostgreSQL from the live task",
        )

        self.log_group = aws_logs.LogGroup(
            self,
            "LogGroup",
            log_group_name=names.LIVE_LOG_GROUP,
            retention=aws_logs.RetentionDays.ONE_WEEK,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        self.task_role = aws_iam.Role(
            self,
            "TaskRole",
            role_name=names.LIVE_TASK_ROLE,
            assumed_by=aws_iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        # The same statement as the app task role. ECS Exec adds its four ssmmessages actions
        # (resource "*", which has no resource-level scoping) when the service is created.
        self.task_role.add_to_policy(bedrock_invoke_statement(self))
        self.execution_role = aws_iam.Role(
            self,
            "ExecutionRole",
            role_name=names.LIVE_EXECUTION_ROLE,
            assumed_by=aws_iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        repository = aws_ecr.Repository.from_repository_name(self, "Repository", names.ECR_REPO)
        self.task_definition = self._task_definition(repository, image_tag, database_secret)

        # The existing cluster, by name: this stack does not own it.
        self.cluster = aws_ecs.Cluster.from_cluster_attributes(
            self, "Cluster", cluster_name=names.CLUSTER, vpc=vpc, security_groups=[]
        )
        self.service = aws_ecs.FargateService(
            self,
            "LiveService",
            service_name=names.API_LIVE_SERVICE,
            cluster=self.cluster,
            task_definition=self.task_definition,
            desired_count=0,
            enable_execute_command=True,
            # A public IP is what lets the task pull from ECR with no NAT or endpoints.
            assign_public_ip=True,
            vpc_subnets=public_subnets,
            security_groups=[self.task_security_group],
            min_healthy_percent=0,
            max_healthy_percent=100,
            circuit_breaker=aws_ecs.DeploymentCircuitBreaker(rollback=True),
        )
        self.namespace = aws_servicediscovery.PrivateDnsNamespace(
            self, "Namespace", name=names.LIVE_NAMESPACE, vpc=vpc
        )
        # With awsvpc networking an SRV record needs the container and its port.
        self.service.enable_cloud_map(
            cloud_map_namespace=self.namespace,
            name=names.LIVE_CLOUD_MAP_SERVICE,
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
        self.api = self._api()
        self._routes(cloud_map_service, issuer_url, client_id)
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

    def _task_definition(
        self,
        repository: aws_ecr.IRepository,
        image_tag: str,
        database_secret: aws_secretsmanager.ISecret,
    ) -> aws_ecs.FargateTaskDefinition:
        task_definition = aws_ecs.FargateTaskDefinition(
            self,
            "LiveTaskDefinition",
            family=names.API_LIVE_SERVICE,
            cpu=LIVE_CPU,
            memory_limit_mib=LIVE_MEMORY_MIB,
            runtime_platform=aws_ecs.RuntimePlatform(
                cpu_architecture=aws_ecs.CpuArchitecture.ARM64,
                operating_system_family=aws_ecs.OperatingSystemFamily.LINUX,
            ),
            task_role=self.task_role,
            execution_role=self.execution_role,
        )
        # No command or entrypoint: the image's own CMD applies. The three daily caps are left at
        # their defaults in src/precertly/settings.py on purpose, so the code is the one place
        # that says what they are.
        task_definition.add_container(
            names.LIVE_CONTAINER,
            image=aws_ecs.ContainerImage.from_ecr_repository(repository, image_tag),
            port_mappings=[aws_ecs.PortMapping(container_port=API_PORT)],
            environment={
                "PRECERTLY_LIVE_ENABLED": "true",
                "PRECERTLY_AWS_REGION": Aws.REGION,
                # Empty: use the task role, not the local named profile the setting defaults to.
                "PRECERTLY_AWS_PROFILE": "",
                "PRECERTLY_BEDROCK_MODEL_ID": BEDROCK_PROFILE_ID,
                "PRECERTLY_EMBEDDING_MODEL_ID": EMBEDDING_MODEL_ID,
                # No idle connection may keep the database awake; a resume can take a while.
                "PRECERTLY_DB_POOL": "null",
                "PRECERTLY_DB_CONNECT_TIMEOUT": str(DB_CONNECT_TIMEOUT_SECONDS),
            },
            secrets={
                env: aws_ecs.Secret.from_secrets_manager(database_secret, field)
                for field, env in SECRET_FIELD_TO_ENV.items()
            },
            logging=aws_ecs.LogDrivers.aws_logs(stream_prefix="live", log_group=self.log_group),
            health_check=api_health_check(),
        )
        return task_definition

    def _api(self) -> aws_apigatewayv2.HttpApi:
        return aws_apigatewayv2.HttpApi(
            self,
            "HttpApi",
            api_name=names.API_NAME,
            description="Precertly live runs: GET /health, POST /live/runs, GET /live/runs/{id}",
            # The default stage is made by hand below, because the one HttpApi creates takes no
            # throttle.
            create_default_stage=False,
            cors_preflight=aws_apigatewayv2.CorsPreflightOptions(
                allow_origins=[f"https://{self.web_domain}"],
                allow_methods=[
                    aws_apigatewayv2.CorsHttpMethod.GET,
                    aws_apigatewayv2.CorsHttpMethod.POST,
                    aws_apigatewayv2.CorsHttpMethod.OPTIONS,
                ],
                allow_headers=list(CORS_ALLOWED_HEADERS),
                max_age=CORS_MAX_AGE,
                allow_credentials=False,
            ),
        )

    def _routes(
        self,
        cloud_map_service: aws_servicediscovery.IService,
        issuer_url: str,
        client_id: str,
    ) -> None:
        authorizer = aws_apigatewayv2_authorizers.HttpJwtAuthorizer(
            "Authorizer",
            issuer_url,
            jwt_audience=[client_id],
            authorizer_name="cognito-access-token",
            identity_source=["$request.header.Authorization"],
        )
        open_integration = aws_apigatewayv2_integrations.HttpServiceDiscoveryIntegration(
            "HealthIntegration", cloud_map_service, vpc_link=self.vpc_link
        )
        # OVERWRITE, not append: whatever header a client sends is replaced by the verified
        # claim. Only the authorized routes get this integration.
        actor_mapping = aws_apigatewayv2.ParameterMapping().overwrite_header(
            ACTOR_HEADER, aws_apigatewayv2.MappingValue.custom(ACTOR_CLAIM)
        )
        actor_integration = aws_apigatewayv2_integrations.HttpServiceDiscoveryIntegration(
            "LiveIntegration",
            cloud_map_service,
            vpc_link=self.vpc_link,
            parameter_mapping=actor_mapping,
        )
        self.api.add_routes(
            path=HEALTH_PATH,
            methods=[aws_apigatewayv2.HttpMethod.GET],
            integration=open_integration,
        )
        # No catch-all and no $default route: any other path is a 404 at the gateway.
        self.api.add_routes(
            path=START_PATH,
            methods=[aws_apigatewayv2.HttpMethod.POST],
            integration=actor_integration,
            authorizer=authorizer,
            authorization_scopes=[REQUIRED_SCOPE],
        )
        self.api.add_routes(
            path=POLL_PATH,
            methods=[aws_apigatewayv2.HttpMethod.GET],
            integration=actor_integration,
            authorizer=authorizer,
            authorization_scopes=[REQUIRED_SCOPE],
        )
