import json
from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template
from support import AUTH_DOMAIN_PREFIX, WEB_DOMAIN, context

import names
from app import build_app
from auth_stack import AUTH_DOMAIN_PREFIX_CONTEXT_KEY, validate_auth_domain_prefix


def _template(extra: dict[str, Any] | None = None) -> Template:
    app = build_app(extra if extra is not None else context())
    stack = next(c for c in app.node.children if c.node.id == "precertly-auth")
    assert isinstance(stack, cdk.Stack)
    return Template.from_stack(stack)


def _resources(template: Template, resource_type: str) -> dict[str, dict[str, Any]]:
    return {k: dict(v) for k, v in template.find_resources(resource_type).items()}


def _only(template: Template, resource_type: str) -> dict[str, Any]:
    found = _resources(template, resource_type)
    assert len(found) == 1, f"expected one {resource_type}, found {list(found)}"
    return next(iter(found.values()))


def test_the_stack_is_a_pool_a_domain_and_a_client_and_nothing_else() -> None:
    types = sorted(r["Type"] for r in _template().to_json()["Resources"].values())
    assert types == [
        "AWS::Cognito::UserPool",
        "AWS::Cognito::UserPoolClient",
        "AWS::Cognito::UserPoolDomain",
    ]


def test_nobody_can_sign_themselves_up() -> None:
    properties = _only(_template(), "AWS::Cognito::UserPool")["Properties"]
    assert properties["AdminCreateUserConfig"] == {"AllowAdminCreateUserOnly": True}


def test_sign_in_is_by_email() -> None:
    properties = _only(_template(), "AWS::Cognito::UserPool")["Properties"]
    assert properties["UsernameAttributes"] == ["email"]
    assert "AliasAttributes" not in properties


def test_mfa_is_required_and_totp_is_the_only_second_factor() -> None:
    template = _template()
    properties = _only(template, "AWS::Cognito::UserPool")["Properties"]
    assert properties["MfaConfiguration"] == "ON"
    assert properties["EnabledMfas"] == ["SOFTWARE_TOKEN_MFA"]
    # No SMS: no role for it, no SNS configuration, no phone number as an attribute.
    assert "SmsConfiguration" not in properties
    assert _resources(template, "AWS::IAM::Role") == {}
    assert "phone_number" not in json.dumps(properties)


def test_password_policy_is_strong() -> None:
    policy = _only(_template(), "AWS::Cognito::UserPool")["Properties"]["Policies"][
        "PasswordPolicy"
    ]
    assert policy["MinimumLength"] >= 12
    for rule in ("RequireLowercase", "RequireUppercase", "RequireNumbers", "RequireSymbols"):
        assert policy[rule] is True
    assert policy["TemporaryPasswordValidityDays"] <= 7


def test_no_self_service_recovery() -> None:
    properties = _only(_template(), "AWS::Cognito::UserPool")["Properties"]
    assert properties["AccountRecoverySetting"] == {
        "RecoveryMechanisms": [{"Name": "admin_only", "Priority": 1}]
    }


def test_no_advanced_security_features() -> None:
    properties = _only(_template(), "AWS::Cognito::UserPool")["Properties"]
    assert properties["UserPoolTier"] == "ESSENTIALS"  # not PLUS, which has threat protection
    assert properties["UserPoolAddOns"] == {"AdvancedSecurityMode": "OFF"}


def test_pool_name_and_removal() -> None:
    pool = _only(_template(), "AWS::Cognito::UserPool")
    assert pool["Properties"]["UserPoolName"] == names.USER_POOL
    assert pool["Properties"]["DeletionProtection"] == "INACTIVE"
    assert pool["DeletionPolicy"] == "Delete"
    assert pool["UpdateReplacePolicy"] == "Delete"


def test_every_resource_is_removed_with_the_stack() -> None:
    for resource in _template().to_json()["Resources"].values():
        assert resource.get("DeletionPolicy", "Delete") == "Delete", resource["Type"]
        assert resource.get("UpdateReplacePolicy", "Delete") == "Delete", resource["Type"]


def test_hosted_ui_domain_comes_from_the_context_prefix() -> None:
    domain = _only(_template(), "AWS::Cognito::UserPoolDomain")["Properties"]
    assert domain["Domain"] == AUTH_DOMAIN_PREFIX
    other = _template(context(authDomainPrefix="another-prefix"))
    assert _only(other, "AWS::Cognito::UserPoolDomain")["Properties"]["Domain"] == (
        "another-prefix"
    )
    # A managed (custom) domain would need a certificate and DNS; this is the Cognito-owned one.
    assert "CustomDomainConfig" not in domain


def test_client_has_no_secret_and_only_the_code_grant() -> None:
    properties = _only(_template(), "AWS::Cognito::UserPoolClient")["Properties"]
    assert properties["GenerateSecret"] is False
    assert properties["AllowedOAuthFlows"] == ["code"]
    assert properties["AllowedOAuthFlowsUserPoolClient"] is True
    assert properties["SupportedIdentityProviders"] == ["COGNITO"]
    assert properties["ClientName"] == names.USER_POOL_CLIENT


def test_client_allows_no_password_srp_custom_or_admin_flows() -> None:
    flows = _only(_template(), "AWS::Cognito::UserPoolClient")["Properties"]["ExplicitAuthFlows"]
    # Refresh-token auth is how the SDK renews a session; it is not a way to sign in.
    assert flows == ["ALLOW_REFRESH_TOKEN_AUTH"]


def test_client_scopes_are_openid_and_email_only() -> None:
    properties = _only(_template(), "AWS::Cognito::UserPoolClient")["Properties"]
    assert properties["AllowedOAuthScopes"] == ["openid", "email"]


def test_callback_and_logout_urls_are_the_site_and_local_dev() -> None:
    properties = _only(_template(), "AWS::Cognito::UserPoolClient")["Properties"]
    expected = [f"https://{WEB_DOMAIN}/", "http://localhost:5173/"]
    assert properties["CallbackURLs"] == expected
    assert properties["LogoutURLs"] == expected
    other = _template(context(webDomain="precertly.example.org"))
    urls = _only(other, "AWS::Cognito::UserPoolClient")["Properties"]["CallbackURLs"]
    assert urls == ["https://precertly.example.org/", "http://localhost:5173/"]


def test_token_validity_is_short() -> None:
    properties = _only(_template(), "AWS::Cognito::UserPoolClient")["Properties"]
    units = properties["TokenValidityUnits"]
    assert units == {"AccessToken": "minutes", "IdToken": "minutes", "RefreshToken": "minutes"}
    assert properties["AccessTokenValidity"] <= 60
    assert properties["IdTokenValidity"] <= 60
    assert properties["RefreshTokenValidity"] <= 24 * 60


def test_user_existence_is_hidden_and_tokens_can_be_revoked() -> None:
    properties = _only(_template(), "AWS::Cognito::UserPoolClient")["Properties"]
    assert properties["PreventUserExistenceErrors"] == "ENABLED"
    assert properties["EnableTokenRevocation"] is True


def test_outputs() -> None:
    outputs = _template().to_json()["Outputs"]
    named = {k for k in outputs if not k.startswith("PublishOutput")}
    assert named == {"UserPoolId", "UserPoolClientId", "HostedUiBaseUrl", "IssuerUrl"}
    assert outputs["IssuerUrl"]["Value"]["Fn::Join"][1][0] == "https://"
    assert outputs["HostedUiBaseUrl"]["Value"]["Fn::Join"][1][0] == "https://"
    assert all("Export" not in o for o in outputs.values())


def test_the_stack_refers_to_no_other_stack() -> None:
    text = json.dumps(_template().to_json())
    assert "Fn::ImportValue" not in text
    assert "Fn::GetStackOutput" not in text
    for other in ("precertly-app", "precertly-data", "precertly-live", "precertly-web"):
        assert other not in text


# --- the domain prefix ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "prefix",
    ["abc", "a1b", "my-login-example", "x" * 63, "123", "a--b"],
)
def test_valid_prefixes(prefix: str) -> None:
    assert validate_auth_domain_prefix(prefix) == prefix


@pytest.mark.parametrize(
    "prefix",
    [
        None,
        123,
        "",
        "ab",
        "x" * 64,
        "-abc",
        "abc-",
        "ABC",
        "abc_def",
        "abc.def",
        "abc def",
        "my-aws-login",
        "amazon-login",
        "cognito-login",
        "mycognito",
        "https://abc",
    ],
)
def test_invalid_prefixes_are_refused(prefix: Any) -> None:
    with pytest.raises(ValueError, match=AUTH_DOMAIN_PREFIX_CONTEXT_KEY):
        validate_auth_domain_prefix(prefix)


def test_the_app_requires_the_prefix_for_every_stack() -> None:
    missing = context(authDomainPrefix=None)
    with pytest.raises(ValueError, match=AUTH_DOMAIN_PREFIX_CONTEXT_KEY):
        build_app(missing)
    with pytest.raises(ValueError, match=AUTH_DOMAIN_PREFIX_CONTEXT_KEY):
        build_app(context(authDomainPrefix="has-cognito-in-it"))


def test_the_placeholder_is_valid_so_synth_works_without_the_real_value() -> None:
    assert validate_auth_domain_prefix(AUTH_DOMAIN_PREFIX) == AUTH_DOMAIN_PREFIX
