"""precertly-base: budget alarms, the container repository and GitHub's deploy access.

Everything here is account-level and independent of the app and data stacks. The deploy role
names resources those stacks will create (cluster, service, task roles) by their fixed names
from `names.py`; the ARNs are built from `Aws.ACCOUNT_ID` and `Aws.REGION`, so nothing is
hard-coded and synthesis needs no credentials.
"""

from typing import Any

import aws_cdk as cdk
from aws_cdk import Aws, aws_budgets, aws_ecr, aws_iam
from constructs import Construct

import names
from precertly_stack import PrecertlyStack

BUDGET_EMAIL_CONTEXT_KEY = "budgetEmail"
BUDGET_LIMIT_USD = 60
ACTUAL_THRESHOLDS_USD = (20, 40, 55)
FORECAST_THRESHOLD_PERCENT = 100

GITHUB_OIDC_HOST = "token.actions.githubusercontent.com"
GITHUB_OIDC_AUDIENCE = "sts.amazonaws.com"
ECS_TASKS_SERVICE = "ecs-tasks.amazonaws.com"

ECR_PUSH_ACTIONS = (
    "ecr:BatchCheckLayerAvailability",
    "ecr:BatchGetImage",
    "ecr:CompleteLayerUpload",
    "ecr:GetDownloadUrlForLayer",
    "ecr:InitiateLayerUpload",
    "ecr:PutImage",
    "ecr:UploadLayerPart",
)


class BaseStack(PrecertlyStack):
    def __init__(
        self, scope: Construct, construct_id: str, *, budget_email: str, **kwargs: Any
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.budget = self._budget(budget_email)
        self.repository = self._repository()
        self.oidc_provider = aws_iam.OidcProviderNative(
            self,
            "GithubOidcProvider",
            url=f"https://{GITHUB_OIDC_HOST}",
            client_ids=[GITHUB_OIDC_AUDIENCE],
        )
        self.deploy_role = self._deploy_role()
        cdk.CfnOutput(self, "RepositoryUri", value=self.repository.repository_uri)
        cdk.CfnOutput(self, "DeployRoleArn", value=self.deploy_role.role_arn)

    def _budget(self, email: str) -> aws_budgets.CfnBudget:
        subscribers = [
            aws_budgets.CfnBudget.SubscriberProperty(subscription_type="EMAIL", address=email)
        ]
        actual = [
            aws_budgets.CfnBudget.NotificationWithSubscribersProperty(
                notification=aws_budgets.CfnBudget.NotificationProperty(
                    notification_type="ACTUAL",
                    comparison_operator="GREATER_THAN",
                    threshold=usd,
                    threshold_type="ABSOLUTE_VALUE",
                ),
                subscribers=subscribers,
            )
            for usd in ACTUAL_THRESHOLDS_USD
        ]
        forecast = aws_budgets.CfnBudget.NotificationWithSubscribersProperty(
            notification=aws_budgets.CfnBudget.NotificationProperty(
                notification_type="FORECASTED",
                comparison_operator="GREATER_THAN",
                threshold=FORECAST_THRESHOLD_PERCENT,
                threshold_type="PERCENTAGE",
            ),
            subscribers=subscribers,
        )
        # No cost filters: the budget covers the whole account.
        return aws_budgets.CfnBudget(
            self,
            "AnnualBudget",
            budget=aws_budgets.CfnBudget.BudgetDataProperty(
                budget_type="COST",
                time_unit="ANNUALLY",
                budget_limit=aws_budgets.CfnBudget.SpendProperty(
                    amount=BUDGET_LIMIT_USD, unit="USD"
                ),
                cost_types=aws_budgets.CfnBudget.CostTypesProperty(
                    include_credit=False, include_refund=False
                ),
            ),
            notifications_with_subscribers=[*actual, forecast],
        )

    def _repository(self) -> aws_ecr.Repository:
        repository = aws_ecr.Repository(
            self,
            "ApiRepository",
            repository_name=names.ECR_REPO,
            image_scan_on_push=True,
            image_tag_mutability=aws_ecr.TagMutability.IMMUTABLE,
            encryption=aws_ecr.RepositoryEncryption.AES_256,
            removal_policy=cdk.RemovalPolicy.DESTROY,
            empty_on_delete=True,
            lifecycle_rules=[
                aws_ecr.LifecycleRule(
                    description="Keep the last 5 sha- images",
                    tag_status=aws_ecr.TagStatus.TAGGED,
                    tag_prefix_list=["sha-"],
                    max_image_count=5,
                ),
                aws_ecr.LifecycleRule(
                    description="Expire untagged images a day after push",
                    tag_status=aws_ecr.TagStatus.UNTAGGED,
                    max_image_age=cdk.Duration.days(1),
                ),
            ],
        )
        # AES256 is the service default, so the L2 construct leaves it out of the template;
        # state it so the encryption is visible in what is deployed.
        cfn_repository = repository.node.default_child
        assert isinstance(cfn_repository, aws_ecr.CfnRepository)
        cfn_repository.add_property_override("EncryptionConfiguration.EncryptionType", "AES256")
        return repository

    def _arn(self, service: str, resource: str) -> str:
        return self.format_arn(
            service=service,
            resource=resource,
            account=Aws.ACCOUNT_ID,
            region="" if service == "iam" else Aws.REGION,
        )

    def _deploy_role(self) -> aws_iam.Role:
        role = aws_iam.Role(
            self,
            "DeployRole",
            role_name=names.DEPLOY_ROLE,
            max_session_duration=cdk.Duration.hours(1),
            # pyright rejects jsii's `_statement` parameter name against the IPrincipal protocol.
            assumed_by=aws_iam.WebIdentityPrincipal(  # pyright: ignore[reportArgumentType]
                self.oidc_provider.oidc_provider_arn,
                conditions={
                    "StringEquals": {
                        f"{GITHUB_OIDC_HOST}:aud": GITHUB_OIDC_AUDIENCE,
                        f"{GITHUB_OIDC_HOST}:sub": (
                            f"repo:{names.GITHUB_REPO}:environment:{names.GITHUB_ENVIRONMENT}"
                        ),
                    }
                },
            ),
        )
        cluster_arn = self._arn("ecs", f"cluster/{names.CLUSTER}")
        in_cluster = {"ArnEquals": {"ecs:cluster": cluster_arn}}

        # Explicit statements rather than grant helpers, so the rendered policy is exactly this.
        role.add_to_policy(
            aws_iam.PolicyStatement(
                sid="EcrPush",
                actions=list(ECR_PUSH_ACTIONS),
                resources=[self.repository.repository_arn],
            )
        )
        role.add_to_policy(
            aws_iam.PolicyStatement(
                sid="EcrAuthorizationToken",
                actions=["ecr:GetAuthorizationToken"],
                resources=["*"],
            )
        )
        role.add_to_policy(
            aws_iam.PolicyStatement(
                sid="EcsTaskDefinitions",
                actions=["ecs:RegisterTaskDefinition", "ecs:DescribeTaskDefinition"],
                resources=["*"],
            )
        )
        role.add_to_policy(
            aws_iam.PolicyStatement(
                sid="EcsService",
                actions=["ecs:UpdateService", "ecs:DescribeServices"],
                resources=[self._arn("ecs", f"service/{names.CLUSTER}/{names.SERVICE}")],
            )
        )
        role.add_to_policy(
            aws_iam.PolicyStatement(
                sid="EcsRunToolsTask",
                actions=["ecs:RunTask"],
                resources=[self._arn("ecs", f"task-definition/{names.TOOLS_FAMILY}:*")],
                conditions=in_cluster,
            )
        )
        role.add_to_policy(
            aws_iam.PolicyStatement(
                sid="EcsTasksInCluster",
                actions=["ecs:DescribeTasks", "ecs:StopTask"],
                resources=[self._arn("ecs", f"task/{names.CLUSTER}/*")],
                conditions=in_cluster,
            )
        )
        role.add_to_policy(
            aws_iam.PolicyStatement(
                sid="EcsListTasksInCluster",
                actions=["ecs:ListTasks"],
                resources=[cluster_arn],
                conditions=in_cluster,
            )
        )
        role.add_to_policy(
            aws_iam.PolicyStatement(
                sid="PassEcsRoles",
                actions=["iam:PassRole"],
                resources=[
                    self._arn("iam", f"role/{names.TASK_ROLE}"),
                    self._arn("iam", f"role/{names.EXECUTION_ROLE}"),
                ],
                conditions={"StringEquals": {"iam:PassedToService": ECS_TASKS_SERVICE}},
            )
        )
        return role
