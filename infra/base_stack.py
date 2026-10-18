"""precertly-base: the budget alarms and the container repository.

Account-level and independent of the app and data stacks. There is deliberately no IAM here:
the account's managed organization policy denies `iam:*Provider*`, so a GitHub OIDC provider
and deploy role cannot exist. Deploys run from the owner's machine instead.
"""

from typing import Any

import aws_cdk as cdk
from aws_cdk import aws_budgets, aws_ecr
from constructs import Construct

import names
from precertly_stack import PrecertlyStack

BUDGET_EMAIL_CONTEXT_KEY = "budgetEmail"
BUDGET_LIMIT_USD = 60
ACTUAL_THRESHOLDS_USD = (20, 40, 55)
FORECAST_THRESHOLD_PERCENT = 100


class BaseStack(PrecertlyStack):
    def __init__(
        self, scope: Construct, construct_id: str, *, budget_email: str, **kwargs: Any
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.budget = self._budget(budget_email)
        self.repository = self._repository()
        cdk.CfnOutput(self, "RepositoryUri", value=self.repository.repository_uri)

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
