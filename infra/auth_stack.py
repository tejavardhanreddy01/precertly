"""precertly-auth: who may start a live run.

A Cognito user pool with no self sign-up (the owner creates each user with the CLI), email as
the user name, and TOTP required for every sign-in (no SMS, so no SMS role, SNS spend or phone
numbers). One public app client for the web site: authorization code grant only, scopes openid
and email, short-lived tokens. A hosted-UI domain from a prefix that is never committed.

The stack stands alone: it refers to no other stack, and nothing here is needed by the app or
data stacks. precertly-live reads the issuer URL and the client id from its outputs.

Nothing here has a standing cost beyond what Cognito bills per monthly active user (the first
10,000 on the Essentials plan are free; unverified for this account). No advanced security
features. Everything is removed with the stack: no deletion protection, users included.
"""

import re
from typing import Any

import aws_cdk as cdk
from aws_cdk import Aws, aws_cognito
from constructs import Construct

import names
from precertly_stack import PrecertlyStack
from web_stack import validate_web_domain

AUTH_DOMAIN_PREFIX_CONTEXT_KEY = "authDomainPrefix"

# Cognito's own rules for a domain prefix: lowercase letters, digits and hyphens, and it may not
# contain these words. Checked here so a bad prefix fails at synth, not halfway through a deploy.
FORBIDDEN_PREFIX_WORDS = ("aws", "amazon", "cognito")
_PREFIX_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$")

LOCAL_ORIGIN = "http://localhost:5173/"
# The shortest the service allows is 60 minutes for the refresh token, 5 for the others.
ACCESS_TOKEN_VALIDITY = cdk.Duration.hours(1)
ID_TOKEN_VALIDITY = cdk.Duration.hours(1)
REFRESH_TOKEN_VALIDITY = cdk.Duration.days(1)
PASSWORD_MIN_LENGTH = 14
TEMPORARY_PASSWORD_VALIDITY = cdk.Duration.days(3)
OAUTH_SCOPES = (aws_cognito.OAuthScope.OPENID, aws_cognito.OAuthScope.EMAIL)


def validate_auth_domain_prefix(prefix: object) -> str:
    """3 to 63 lowercase letters, digits and hyphens, no hyphen at either end, and none of the
    words Cognito reserves."""
    required = (
        f"CDK context '{AUTH_DOMAIN_PREFIX_CONTEXT_KEY}' is required and must be 3 to 63 lowercase "
        "letters, digits and hyphens, with no leading or trailing hyphen and none of the words "
        f"{', '.join(FORBIDDEN_PREFIX_WORDS)}: pass `-c {AUTH_DOMAIN_PREFIX_CONTEXT_KEY}=<prefix>` "
        "(scripts/deploy.sh does this)."
    )
    if (
        not isinstance(prefix, str)
        or not 3 <= len(prefix) <= 63
        or not _PREFIX_RE.fullmatch(prefix)
    ):
        raise ValueError(required)
    if any(word in prefix for word in FORBIDDEN_PREFIX_WORDS):
        raise ValueError(required)
    return prefix


class AuthStack(PrecertlyStack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        domain_prefix: str,
        web_domain: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        domain_prefix = validate_auth_domain_prefix(domain_prefix)
        web_domain = validate_web_domain(web_domain)
        self.user_pool = self._user_pool()
        self.domain = self.user_pool.add_domain(
            "HostedUiDomain",
            cognito_domain=aws_cognito.CognitoDomainOptions(domain_prefix=domain_prefix),
        )
        self.client = self._client(web_domain)
        # The `iss` claim of every token: https://cognito-idp.<region>.amazonaws.com/<pool id>.
        # Built from its parts on purpose: the pool's ProviderURL attribute already starts with
        # "https://" (a deploy of precertly-live failed on "https://https://...").
        self.issuer_url = cdk.Fn.join(
            "",
            ["https://cognito-idp.", Aws.REGION, ".amazonaws.com/", self.user_pool.user_pool_id],
        )
        cdk.CfnOutput(self, "UserPoolId", value=self.user_pool.user_pool_id)
        cdk.CfnOutput(self, "UserPoolClientId", value=self.client.user_pool_client_id)
        cdk.CfnOutput(self, "HostedUiBaseUrl", value=self.domain.base_url())
        cdk.CfnOutput(self, "IssuerUrl", value=self.issuer_url)
        # Applies to every resource that has a removal policy; the user pool defaults to RETAIN.
        cdk.RemovalPolicies.of(self).destroy()

    def _user_pool(self) -> aws_cognito.UserPool:
        return aws_cognito.UserPool(
            self,
            "UserPool",
            user_pool_name=names.USER_POOL,
            # Users are created by the owner (admin-create-user); nobody can register.
            self_sign_up_enabled=False,
            sign_in_aliases=aws_cognito.SignInAliases(email=True, username=False),
            auto_verify=aws_cognito.AutoVerifiedAttrs(email=True),
            # TOTP only: no SMS (no SMS role, no SNS, no phone numbers) and no email code.
            mfa=aws_cognito.Mfa.REQUIRED,
            mfa_second_factor=aws_cognito.MfaSecondFactor(otp=True, sms=False, email=False),
            password_policy=aws_cognito.PasswordPolicy(
                min_length=PASSWORD_MIN_LENGTH,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
                temp_password_validity=TEMPORARY_PASSWORD_VALIDITY,
            ),
            # No self-service recovery: with a handful of invited users the owner resets a
            # password with admin-set-user-password, and a mailbox takeover alone cannot reset
            # one. (TOTP is still required after a reset.)
            account_recovery=aws_cognito.AccountRecovery.NONE,
            # Essentials, the plan new pools get: no threat protection (that is Plus).
            feature_plan=aws_cognito.FeaturePlan.ESSENTIALS,
            standard_threat_protection_mode=(
                aws_cognito.StandardThreatProtectionMode.NO_ENFORCEMENT
            ),
            deletion_protection=False,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

    def _client(self, web_domain: str) -> aws_cognito.UserPoolClient:
        site = f"https://{web_domain}/"
        return self.user_pool.add_client(
            "WebClient",
            user_pool_client_name=names.USER_POOL_CLIENT,
            # A browser app cannot keep a secret; the code exchange uses PKCE.
            generate_secret=False,
            # Nothing but the hosted UI's code flow: no password, SRP, custom or admin flows.
            # (Refresh-token auth is always on and is not a way to sign in.)
            auth_flows=aws_cognito.AuthFlow(
                user_password=False,
                user_srp=False,
                custom=False,
                admin_user_password=False,
            ),
            o_auth=aws_cognito.OAuthSettings(
                flows=aws_cognito.OAuthFlows(
                    authorization_code_grant=True,
                    implicit_code_grant=False,
                    client_credentials=False,
                ),
                scopes=list(OAUTH_SCOPES),
                callback_urls=[site, LOCAL_ORIGIN],
                logout_urls=[site, LOCAL_ORIGIN],
            ),
            supported_identity_providers=[aws_cognito.UserPoolClientIdentityProvider.COGNITO],
            access_token_validity=ACCESS_TOKEN_VALIDITY,
            id_token_validity=ID_TOKEN_VALIDITY,
            refresh_token_validity=REFRESH_TOKEN_VALIDITY,
            prevent_user_existence_errors=True,
            enable_token_revocation=True,
        )
