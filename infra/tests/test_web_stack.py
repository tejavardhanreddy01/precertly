import json
import re
from pathlib import Path
from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template
from support import WEB_CERTIFICATE_ARN, WEB_DOMAIN, context

from app import build_app
from web_stack import CONTENT_SECURITY_POLICY, ERROR_PAGE_PATH

ARN = "arn:aws:acm:us-east-1:111122223333:certificate/12345678-abcd-4abc-8abc-123456789abc"
WEB_STACK_SOURCE = Path(__file__).resolve().parents[1] / "web_stack.py"


def _template(extra: dict[str, Any] | None = None) -> Template:
    app = build_app(extra if extra is not None else context())
    stack = next(c for c in app.node.children if c.node.id == "precertly-web")
    assert isinstance(stack, cdk.Stack)
    return Template.from_stack(stack)


def _resources(template: Template, resource_type: str) -> dict[str, dict[str, Any]]:
    return {k: dict(v) for k, v in template.find_resources(resource_type).items()}


def _only(template: Template, resource_type: str) -> dict[str, Any]:
    found = _resources(template, resource_type)
    assert len(found) == 1, f"expected one {resource_type}, found {list(found)}"
    return next(iter(found.values()))


def _props(resource_type: str) -> dict[str, Any]:
    return _only(_template(), resource_type)["Properties"]


def _distribution_config() -> dict[str, Any]:
    return _props("AWS::CloudFront::Distribution")["DistributionConfig"]


def _headers_config() -> dict[str, Any]:
    return _props("AWS::CloudFront::ResponseHeadersPolicy")["ResponseHeadersPolicyConfig"]


def test_the_stack_has_exactly_these_resources() -> None:
    types = sorted(r["Type"] for r in _template().to_json()["Resources"].values())
    assert types == [
        "AWS::CloudFront::CachePolicy",
        "AWS::CloudFront::Distribution",
        "AWS::CloudFront::OriginAccessControl",
        "AWS::CloudFront::ResponseHeadersPolicy",
        "AWS::S3::Bucket",
        "AWS::S3::BucketPolicy",
    ]


# ---------------------------------------------------------------------------------------------
# The bucket
# ---------------------------------------------------------------------------------------------


def test_bucket_blocks_all_public_access() -> None:
    assert _props("AWS::S3::Bucket")["PublicAccessBlockConfiguration"] == {
        "BlockPublicAcls": True,
        "BlockPublicPolicy": True,
        "IgnorePublicAcls": True,
        "RestrictPublicBuckets": True,
    }


def test_bucket_name_is_generated() -> None:
    assert "BucketName" not in _props("AWS::S3::Bucket")


def test_bucket_is_encrypted_with_s3_managed_keys_and_versioned() -> None:
    props = _props("AWS::S3::Bucket")
    assert props["BucketEncryption"] == {
        "ServerSideEncryptionConfiguration": [
            {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
        ]
    }
    assert props["VersioningConfiguration"] == {"Status": "Enabled"}


def test_bucket_lifecycle_expires_old_versions_and_aborts_multipart_uploads() -> None:
    (rule,) = _props("AWS::S3::Bucket")["LifecycleConfiguration"]["Rules"]
    assert rule["Status"] == "Enabled"
    assert rule["NoncurrentVersionExpiration"] == {"NoncurrentDays": 30}
    assert rule["AbortIncompleteMultipartUpload"] == {"DaysAfterInitiation": 1}
    assert set(rule) == {
        "Id",
        "Status",
        "NoncurrentVersionExpiration",
        "AbortIncompleteMultipartUpload",
    }


def test_bucket_is_destroyed_with_the_stack_without_a_custom_resource() -> None:
    template = _template()
    bucket = _only(template, "AWS::S3::Bucket")
    assert bucket["DeletionPolicy"] == "Delete"
    assert bucket["UpdateReplacePolicy"] == "Delete"
    # auto_delete_objects would add a Lambda-backed custom resource and a tag on the bucket.
    assert _resources(template, "Custom::S3AutoDeleteObjects") == {}
    assert _resources(template, "AWS::Lambda::Function") == {}
    assert _resources(template, "AWS::CloudFormation::CustomResource") == {}


def test_bucket_policy_enforces_tls() -> None:
    statements = _only(_template(), "AWS::S3::BucketPolicy")["Properties"]["PolicyDocument"][
        "Statement"
    ]
    (deny,) = [s for s in statements if s["Effect"] == "Deny"]
    assert deny["Action"] == "s3:*"
    assert deny["Principal"] == {"AWS": "*"}
    assert deny["Condition"] == {"Bool": {"aws:SecureTransport": "false"}}


def test_bucket_policy_allows_only_the_one_distribution_to_read_objects() -> None:
    policy = _only(_template(), "AWS::S3::BucketPolicy")["Properties"]["PolicyDocument"]
    allows = [s for s in policy["Statement"] if s["Effect"] == "Allow"]
    assert len(allows) == 1
    (allow,) = allows
    assert allow["Principal"] == {"Service": "cloudfront.amazonaws.com"}
    assert allow["Action"] == "s3:GetObject"
    # Objects only: no ListBucket, no bucket-level resource.
    assert allow["Resource"]["Fn::Join"][1][-1] == "/*"
    assert set(allow["Condition"]) == {"StringEquals"}
    source = allow["Condition"]["StringEquals"]
    assert list(source) == ["AWS:SourceArn"]
    parts = source["AWS:SourceArn"]["Fn::Join"][1]
    assert ":cloudfront::" in parts
    distribution_ids = [p["Ref"] for p in parts if isinstance(p, dict)]
    assert distribution_ids[-1] == next(
        iter(_resources(_template(), "AWS::CloudFront::Distribution"))
    )
    # No wildcard principal or action: the only "*" is the object path.
    assert "*" not in json.dumps({k: v for k, v in allow.items() if k != "Resource"})


# ---------------------------------------------------------------------------------------------
# The distribution
# ---------------------------------------------------------------------------------------------


def test_origin_uses_an_origin_access_control_and_no_identity() -> None:
    template = _template()
    assert _resources(template, "AWS::CloudFront::CloudFrontOriginAccessIdentity") == {}
    oac = _only(template, "AWS::CloudFront::OriginAccessControl")["Properties"]
    assert oac["OriginAccessControlConfig"]["OriginAccessControlOriginType"] == "s3"
    assert oac["OriginAccessControlConfig"]["SigningBehavior"] == "always"
    assert oac["OriginAccessControlConfig"]["SigningProtocol"] == "sigv4"
    (origin,) = _distribution_config()["Origins"]
    assert origin["S3OriginConfig"] == {"OriginAccessIdentity": ""}
    assert "OriginAccessControlId" in origin
    assert "CustomOriginConfig" not in origin


def test_the_origin_is_the_bucket_through_its_regional_domain() -> None:
    template = _template()
    bucket = next(iter(_resources(template, "AWS::S3::Bucket")))
    (origin,) = _distribution_config()["Origins"]
    assert origin["DomainName"] == {"Fn::GetAtt": [bucket, "RegionalDomainName"]}


def test_domain_and_certificate_are_wired() -> None:
    config = _distribution_config()
    assert config["Aliases"] == [WEB_DOMAIN]
    assert config["ViewerCertificate"] == {
        "AcmCertificateArn": WEB_CERTIFICATE_ARN,
        "MinimumProtocolVersion": "TLSv1.2_2021",
        "SslSupportMethod": "sni-only",
    }


def test_the_domain_and_certificate_come_from_context() -> None:
    template = _template(context(webDomain="www.example.org", webCertificateArn=ARN))
    config = _only(template, "AWS::CloudFront::Distribution")["Properties"]["DistributionConfig"]
    assert config["Aliases"] == ["www.example.org"]
    assert config["ViewerCertificate"]["AcmCertificateArn"] == ARN
    assert WEB_DOMAIN not in json.dumps(template.to_json())


def test_viewers_are_redirected_to_https_with_modern_protocols() -> None:
    config = _distribution_config()
    behavior = config["DefaultCacheBehavior"]
    assert behavior["ViewerProtocolPolicy"] == "redirect-to-https"
    assert config["HttpVersion"] == "http2and3"
    assert config["ViewerCertificate"]["MinimumProtocolVersion"] == "TLSv1.2_2021"


def test_only_get_and_head_are_allowed_and_responses_are_compressed() -> None:
    behavior = _distribution_config()["DefaultCacheBehavior"]
    assert behavior["AllowedMethods"] == ["GET", "HEAD"]
    assert behavior["CachedMethods"] == ["GET", "HEAD"]
    assert behavior["Compress"] is True
    assert "CacheBehaviors" not in _distribution_config()


def test_root_object_price_class_and_no_access_logging() -> None:
    config = _distribution_config()
    assert config["DefaultRootObject"] == "index.html"
    assert config["PriceClass"] == "PriceClass_100"
    assert config["Enabled"] is True
    assert "Logging" not in config
    # A log bucket would be a second S3 bucket.
    assert len(_resources(_template(), "AWS::S3::Bucket")) == 1


def test_cache_policy_honors_the_origin_cache_control() -> None:
    template = _template()
    config = _only(template, "AWS::CloudFront::CachePolicy")["Properties"]["CachePolicyConfig"]
    assert (config["MinTTL"], config["DefaultTTL"], config["MaxTTL"]) == (0, 300, 31536000)
    key = config["ParametersInCacheKeyAndForwardedToOrigin"]
    assert key["EnableAcceptEncodingGzip"] is True
    assert key["EnableAcceptEncodingBrotli"] is True
    assert key["CookiesConfig"] == {"CookieBehavior": "none"}
    assert key["HeadersConfig"] == {"HeaderBehavior": "none"}
    assert key["QueryStringsConfig"] == {"QueryStringBehavior": "none"}
    policy_id = _distribution_config()["DefaultCacheBehavior"]["CachePolicyId"]
    assert policy_id == {"Ref": next(iter(_resources(template, "AWS::CloudFront::CachePolicy")))}


def test_missing_objects_show_the_error_page_with_a_404_for_ten_seconds() -> None:
    assert ERROR_PAGE_PATH == "/404.html"
    assert _distribution_config()["CustomErrorResponses"] == [
        {
            "ErrorCode": code,
            "ResponseCode": 404,
            "ResponsePagePath": "/404.html",
            "ErrorCachingMinTTL": 10,
        }
        for code in (403, 404)
    ]


# ---------------------------------------------------------------------------------------------
# The response headers
# ---------------------------------------------------------------------------------------------


def test_headers_policy_is_attached_to_the_default_behavior() -> None:
    template = _template()
    policy = next(iter(_resources(template, "AWS::CloudFront::ResponseHeadersPolicy")))
    behavior = _distribution_config()["DefaultCacheBehavior"]
    assert behavior["ResponseHeadersPolicyId"] == {"Ref": policy}


def test_security_headers_have_exactly_these_values_and_override_the_origin() -> None:
    config = _headers_config()
    assert config["SecurityHeadersConfig"] == {
        "ContentSecurityPolicy": {
            "ContentSecurityPolicy": (
                "default-src 'none'; script-src 'self'; style-src 'self'; "
                "img-src 'self'; font-src 'self'; connect-src 'self'; "
                "base-uri 'self'; form-action 'none'; frame-ancestors 'none'; "
                "object-src 'none'"
            ),
            "Override": True,
        },
        "StrictTransportSecurity": {
            "AccessControlMaxAgeSec": 31536000,
            "IncludeSubdomains": False,
            "Preload": False,
            "Override": True,
        },
        "ContentTypeOptions": {"Override": True},
        "FrameOptions": {"FrameOption": "DENY", "Override": True},
        "ReferrerPolicy": {
            "ReferrerPolicy": "strict-origin-when-cross-origin",
            "Override": True,
        },
    }
    assert config["CustomHeadersConfig"] == {
        "Items": [
            {
                "Header": "Permissions-Policy",
                "Value": "camera=(), microphone=(), geolocation=()",
                "Override": True,
            }
        ]
    }
    assert set(config) == {
        "Comment",
        "Name",
        "SecurityHeadersConfig",
        "CustomHeadersConfig",
    }


def test_the_content_security_policy_allows_nothing_remote_or_inline() -> None:
    csp = CONTENT_SECURITY_POLICY
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    assert "http" not in csp
    assert "*" not in csp
    directives = {d.split(" ", 1)[0]: d.split(" ", 1)[1] for d in csp.split("; ")}
    assert directives["default-src"] == "'none'"
    assert directives["frame-ancestors"] == "'none'"
    assert directives["script-src"] == "'self'"
    assert directives["style-src"] == "'self'"


# ---------------------------------------------------------------------------------------------
# Outputs, environment and what the stack leaves out
# ---------------------------------------------------------------------------------------------


def test_outputs() -> None:
    outputs = _template().to_json()["Outputs"]
    template = _template()
    bucket = next(iter(_resources(template, "AWS::S3::Bucket")))
    distribution = next(iter(_resources(template, "AWS::CloudFront::Distribution")))
    assert outputs == {
        "BucketName": {"Value": {"Ref": bucket}},
        "DistributionId": {"Value": {"Ref": distribution}},
        "DistributionDomainName": {"Value": {"Fn::GetAtt": [distribution, "DomainName"]}},
        "SiteUrl": {"Value": f"https://{WEB_DOMAIN}"},
    }
    assert all("Export" not in o for o in outputs.values())


def test_stack_has_no_cross_stack_references_and_is_environment_agnostic() -> None:
    text = json.dumps(_template().to_json())
    assert "Fn::ImportValue" not in text
    assert "Fn::GetStackOutput" not in text
    assert not re.search(r"\b\d{12}\b", text.replace("000000000000", ""))
    assert "us-east-2" not in text
    assert "us-east-1" in text  # only the certificate ARN


def test_no_account_arn_or_email_in_the_source() -> None:
    source = WEB_STACK_SOURCE.read_text()
    assert not re.search(r"\b\d{12}\b", source)
    assert "@" not in source


# ---------------------------------------------------------------------------------------------
# Validation of the context
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "domain",
    [
        None,
        "",
        "  ",
        "localhost",
        "Site.Example.com",
        "https://site.example.com",
        "site.example.com/",
        "site.example.com:443",
        "site.example.com.",
        "*.example.com",
        "-site.example.com",
        "site-.example.com",
        "site..example.com",
        "site example.com",
        "site_name.example.com",
        "1.2.3.4",
        "a." + "b" * 64 + ".com",
        12,
    ],
)
def test_a_bad_domain_is_refused(domain: Any) -> None:
    with pytest.raises(ValueError, match="webDomain"):
        build_app(context(webDomain=domain))


@pytest.mark.parametrize(
    "domain", ["a.co", "site.example.com", "precertly.tejaavuthu.com", "a-b.c1.io"]
)
def test_plain_hostnames_are_accepted(domain: str) -> None:
    assert build_app(context(webDomain=domain)) is not None


@pytest.mark.parametrize(
    "arn",
    [
        None,
        "",
        "not an arn",
        "arn:aws:acm:us-east-1:12345:certificate/12345678-abcd-4abc-8abc-123456789abc",
        "arn:aws:acm:us-east-1:111122223333:certificate/not-a-uuid",
        "arn:aws:acm:us-east-1:111122223333:certificate/12345678-ABCD-4ABC-8ABC-123456789ABC",
        "arn:aws:acm:us-east-1:111122223333:certificate/12345678-abcd-4abc-8abc-123456789abc/x",
        "arn:aws:iam::111122223333:role/12345678-abcd-4abc-8abc-123456789abc",
        "arn:aws-cn:acm:us-east-1:111122223333:certificate/12345678-abcd-4abc-8abc-123456789abc",
        " " + ARN,
        ARN + "\n",
        7,
    ],
)
def test_a_bad_certificate_arn_is_refused(arn: Any) -> None:
    with pytest.raises(ValueError, match="webCertificateArn"):
        build_app(context(webCertificateArn=arn))


@pytest.mark.parametrize("region", ["us-east-2", "eu-west-1", "us-west-2"])
def test_a_certificate_outside_us_east_1_is_refused_with_a_clear_error(region: str) -> None:
    with pytest.raises(ValueError, match=f"'{region}'.*only accepts certificates in us-east-1"):
        build_app(context(webCertificateArn=ARN.replace("us-east-1", region)))


@pytest.mark.parametrize("key", ["webDomain", "webCertificateArn", "imageTag", "budgetEmail"])
def test_every_context_key_is_required_for_every_stack(key: str) -> None:
    with pytest.raises(ValueError, match=key):
        build_app(context(**{key: None}))


# ---------------------------------------------------------------------------------------------
# Live mode: two optional origins in connect-src
# ---------------------------------------------------------------------------------------------

GOLDEN = Path(__file__).resolve().parent / "golden" / "precertly-web.template.json"
LIVE_API = "https://abcdefghij.execute-api.us-east-2.amazonaws.com"
LIVE_AUTH = "https://placeholder-prefix.auth.us-east-2.amazoncognito.com"


def _live_context(**extra: Any) -> dict[str, Any]:
    return context(**{"liveApiOrigin": LIVE_API, "liveAuthOrigin": LIVE_AUTH, **extra})


def _csp(template: Template) -> str:
    config = _only(template, "AWS::CloudFront::ResponseHeadersPolicy")["Properties"]
    return config["ResponseHeadersPolicyConfig"]["SecurityHeadersConfig"]["ContentSecurityPolicy"][
        "ContentSecurityPolicy"
    ]


def test_without_the_live_context_the_template_is_byte_identical_to_the_one_on_main() -> None:
    # tests/golden/precertly-web.template.json was synthesized from main's web_stack.py (before
    # the live origins existed) with this test context. A change that touches the recorded-only
    # template has to change this file on purpose.
    template = _template().to_json()
    assert json.dumps(template, indent=2, sort_keys=True) + "\n" == GOLDEN.read_text()
    assert "LiveApiOrigin" not in template.get("Outputs", {})
    assert "LiveAuthOrigin" not in template.get("Outputs", {})


def test_with_the_live_context_connect_src_is_self_plus_exactly_the_two_origins() -> None:
    csp = _csp(_template(_live_context()))
    assert csp == (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
        f"font-src 'self'; connect-src 'self' {LIVE_API} {LIVE_AUTH}; base-uri 'self'; "
        "form-action 'none'; frame-ancestors 'none'; object-src 'none'"
    )


def test_with_the_live_context_nothing_else_in_the_policy_loosens() -> None:
    directives = dict(d.strip().split(" ", 1) for d in _csp(_template(_live_context())).split(";"))
    assert directives["default-src"] == "'none'"
    assert directives["form-action"] == "'none'"
    assert directives["frame-ancestors"] == "'none'"
    assert directives["object-src"] == "'none'"
    assert directives["script-src"] == "'self'"
    assert directives["style-src"] == "'self'"
    assert directives["connect-src"].split() == ["'self'", LIVE_API, LIVE_AUTH]
    text = _csp(_template(_live_context()))
    for banned in ("*", "unsafe-inline", "unsafe-eval", "data:", "http:"):
        assert banned not in text


def test_the_live_context_changes_only_the_policy_and_adds_two_outputs() -> None:
    plain = json.loads(GOLDEN.read_text())
    live = _template(_live_context()).to_json()
    assert live["Outputs"]["LiveApiOrigin"]["Value"] == LIVE_API
    assert live["Outputs"]["LiveAuthOrigin"]["Value"] == LIVE_AUTH
    del live["Outputs"]["LiveApiOrigin"], live["Outputs"]["LiveAuthOrigin"]
    for template in (plain, live):
        for resource in template["Resources"].values():
            if resource["Type"] == "AWS::CloudFront::ResponseHeadersPolicy":
                config = resource["Properties"]["ResponseHeadersPolicyConfig"]
                csp_config = config["SecurityHeadersConfig"]["ContentSecurityPolicy"]
                csp_config["ContentSecurityPolicy"] = ""
    assert live == plain


@pytest.mark.parametrize("missing", ["liveApiOrigin", "liveAuthOrigin"])
def test_half_a_live_context_is_refused_for_every_stack(missing: str) -> None:
    ctx = _live_context()
    del ctx[missing]
    with pytest.raises(ValueError, match="both or neither"):
        build_app(ctx)


@pytest.mark.parametrize(
    "value",
    [
        "http://abcdefghij.execute-api.us-east-2.amazonaws.com",
        "https://abcdefghij.execute-api.us-east-2.amazonaws.com/",
        "https://abcdefghij.execute-api.us-east-2.amazonaws.com/prod",
        "https://abcdefghij.execute-api.us-east-2.amazonaws.com:443",
        "https://*.execute-api.us-east-2.amazonaws.com",
        "https://abcdefghij.execute-api.us-east-2.amazonaws.com.evil.example",
        "https://user@abcdefghij.execute-api.us-east-2.amazonaws.com",
        "https://abcdefghij.execute-api.us-east-2.amazonaws.com?x=1",
        "https://ABCDEFGHIJ.execute-api.us-east-2.amazonaws.com",
        "https://abcdefghij.execute-api.us-east-2.amazonaws.com; script-src *",
        "abcdefghij.execute-api.us-east-2.amazonaws.com",
        "",
        5,
    ],
)
def test_a_malformed_api_origin_is_refused_without_repeating_it(value: Any) -> None:
    with pytest.raises(ValueError, match="liveApiOrigin") as error:
        build_app(_live_context(liveApiOrigin=value))
    if isinstance(value, str) and value:
        assert value not in str(error.value)


@pytest.mark.parametrize(
    "value",
    [
        "http://placeholder-prefix.auth.us-east-2.amazoncognito.com",
        "https://placeholder-prefix.auth.us-east-2.amazoncognito.com/",
        "https://placeholder-prefix.auth.us-east-2.amazoncognito.com/login",
        "https://placeholder-prefix.auth.us-east-2.amazoncognito.com:8443",
        "https://*.auth.us-east-2.amazoncognito.com",
        "https://placeholder-prefix.auth.us-east-2.amazoncognito.com.evil.example",
        "https://placeholder-prefix.us-east-2.amazoncognito.com",
        "https://-bad.auth.us-east-2.amazoncognito.com",
        "https://placeholder-prefix.auth.us-east-2.amazoncognito.com; connect-src *",
        "",
        5,
    ],
)
def test_a_malformed_auth_origin_is_refused_without_repeating_it(value: Any) -> None:
    with pytest.raises(ValueError, match="liveAuthOrigin") as error:
        build_app(_live_context(liveAuthOrigin=value))
    if isinstance(value, str) and value:
        assert value not in str(error.value)


def test_the_live_origins_are_context_not_references_to_other_stacks() -> None:
    template = _template(_live_context()).to_json()
    text = json.dumps(template)
    assert "Fn::ImportValue" not in text
    assert "Fn::GetStackOutput" not in text


def test_the_source_never_holds_a_real_live_host() -> None:
    # Only the clearly fake placeholders of the tests may appear in the repository.
    source = WEB_STACK_SOURCE.read_text()
    assert "execute-api." in source  # the pattern
    assert not re.search(r"https://[a-z0-9]+\.execute-api\.[a-z0-9-]+\.amazonaws\.com", source)
