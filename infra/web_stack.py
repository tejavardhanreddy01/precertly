"""precertly-web: the static recorded-mode site on S3 behind CloudFront.

A private bucket (no public access, TLS only, versioned) is the only origin; CloudFront reads it
through an origin access control, so the bucket policy allows one distribution and nothing else.
The distribution serves the Vite build in web/dist (hash routes, base './') on a custom domain
with a certificate that already exists in us-east-1; this stack takes its ARN and the domain from
CDK context and never creates DNS records or certificates. Publishing the files is not part of
this stack.

The bucket has removal policy DESTROY and no auto-delete (that needs a custom resource Lambda):
empty the bucket, including old versions, before destroying the stack, or the delete fails.

The stack has no cross-stack references and no environment, like the other stacks.

Live mode adds two optional CDK context values, both or neither: `liveApiOrigin` (the gateway of
precertly-live) and `liveAuthOrigin` (the Cognito hosted UI of precertly-auth). With neither, the
template is exactly what it was before they existed. With both, `connect-src` is 'self' plus
those two origins and nothing else, and the stack outputs them so `scripts/deploy.sh web publish`
can check that the JavaScript it publishes only calls hosts this policy allows. They are context,
not references to the other stacks, so this stack still stands alone. They are public identifiers
(they end up in the published JavaScript) but the repository never holds them.
"""

import re
from typing import Any

import aws_cdk as cdk
from aws_cdk import aws_certificatemanager, aws_cloudfront, aws_cloudfront_origins, aws_s3
from constructs import Construct

from precertly_stack import PrecertlyStack

WEB_DOMAIN_CONTEXT_KEY = "webDomain"
WEB_CERTIFICATE_ARN_CONTEXT_KEY = "webCertificateArn"
LIVE_API_ORIGIN_CONTEXT_KEY = "liveApiOrigin"
LIVE_AUTH_ORIGIN_CONTEXT_KEY = "liveAuthOrigin"
# CloudFront accepts only certificates from this region.
CERTIFICATE_REGION = "us-east-1"

# Served for a missing object (S3 answers 403 when the caller may not list the bucket, 404 when
# it may). The file itself is added by the web build, not by this stack.
ERROR_PAGE_PATH = "/404.html"
ERROR_TTL = cdk.Duration.seconds(10)

NONCURRENT_VERSION_DAYS = 30
ABORT_MULTIPART_DAYS = 1

CACHE_MIN_TTL = cdk.Duration.seconds(0)
CACHE_DEFAULT_TTL = cdk.Duration.seconds(300)
CACHE_MAX_TTL = cdk.Duration.days(365)

# The built site needs only its own origin: one external script and stylesheet, same-origin JSON.
# No `data:` in img-src or font-src: the build has none (web/scripts/check-dist.mjs fails on one).
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "font-src 'self'; connect-src 'self'; base-uri 'self'; form-action 'none'; "
    "frame-ancestors 'none'; object-src 'none'"
)
# One year, and neither includeSubDomains nor preload: the parent domain hosts other sites.
HSTS_MAX_AGE = cdk.Duration.seconds(31536000)
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=()"

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN_RE = re.compile(rf"^{_LABEL}(?:\.{_LABEL})+$")
# The same shapes web/scripts/live-config.mjs accepts for the build, without a path or slash.
_LIVE_API_ORIGIN_RE = re.compile(
    r"^https://[a-z0-9]{6,32}\.execute-api\.[a-z0-9-]{4,30}\.amazonaws\.com$"
)
_LIVE_AUTH_ORIGIN_RE = re.compile(
    r"^https://[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])\.auth\.[a-z0-9-]{4,30}\.amazoncognito\.com$"
)
_ARN_RE = re.compile(
    r"^arn:aws:acm:(?P<region>[a-z0-9-]+):(?P<account>\d{12}):certificate/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def validate_web_domain(domain: object) -> str:
    """A plain lowercase hostname with at least two labels: no scheme, port, path or wildcard."""
    if (
        not isinstance(domain, str)
        or len(domain) > 253
        or not _DOMAIN_RE.fullmatch(domain)
        or domain.rsplit(".", 1)[1].isdigit()
    ):
        raise ValueError(
            f"CDK context '{WEB_DOMAIN_CONTEXT_KEY}' is required and must be a plain lowercase "
            "hostname such as 'site.example.com' (no scheme, port, path, wildcard or trailing "
            f"dot): pass `-c {WEB_DOMAIN_CONTEXT_KEY}=<hostname>` (scripts/deploy.sh does this)."
        )
    return domain


def validate_certificate_arn(arn: object) -> str:
    """An ACM certificate ARN in us-east-1, the only region CloudFront accepts."""
    required = (
        f"CDK context '{WEB_CERTIFICATE_ARN_CONTEXT_KEY}' is required and must look like "
        f"'arn:aws:acm:{CERTIFICATE_REGION}:<12 digits>:certificate/<uuid>': pass "
        f"`-c {WEB_CERTIFICATE_ARN_CONTEXT_KEY}=<arn>` (scripts/deploy.sh does this)."
    )
    match = _ARN_RE.fullmatch(arn) if isinstance(arn, str) else None
    if match is None:
        raise ValueError(required)
    if match["region"] != CERTIFICATE_REGION:
        raise ValueError(
            f"CDK context '{WEB_CERTIFICATE_ARN_CONTEXT_KEY}' names a certificate in "
            f"'{match['region']}', but CloudFront only accepts certificates in "
            f"{CERTIFICATE_REGION}: request or import the certificate there."
        )
    assert isinstance(arn, str)
    return arn


def validate_live_origins(api: object, auth: object) -> tuple[str, str] | None:
    """Both live origins or neither. None means a recorded-only policy.

    Each is `https://` and a host of the exact shape (an execute-api host, a Cognito hosted-UI
    host): no path, port, credentials, query or wildcard. The messages name the context key and
    never repeat the value.
    """
    if api is None and auth is None:
        return None
    if api is None or auth is None:
        raise ValueError(
            f"CDK contexts '{LIVE_API_ORIGIN_CONTEXT_KEY}' and '{LIVE_AUTH_ORIGIN_CONTEXT_KEY}' "
            "go together: pass both or neither."
        )
    if not isinstance(api, str) or not _LIVE_API_ORIGIN_RE.fullmatch(api):
        raise ValueError(
            f"CDK context '{LIVE_API_ORIGIN_CONTEXT_KEY}' must be "
            "https://<id>.execute-api.<region>.amazonaws.com with no path, port or wildcard."
        )
    if not isinstance(auth, str) or not _LIVE_AUTH_ORIGIN_RE.fullmatch(auth):
        raise ValueError(
            f"CDK context '{LIVE_AUTH_ORIGIN_CONTEXT_KEY}' must be "
            "https://<prefix>.auth.<region>.amazoncognito.com with no path, port or wildcard."
        )
    return api, auth


def content_security_policy(live_origins: tuple[str, str] | None = None) -> str:
    """The policy for the site: its own origin only, plus the two live origins when given."""
    if live_origins is None:
        return CONTENT_SECURITY_POLICY
    api, auth = live_origins
    return CONTENT_SECURITY_POLICY.replace(
        "connect-src 'self';", f"connect-src 'self' {api} {auth};"
    )


class WebStack(PrecertlyStack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        domain: str,
        certificate_arn: str,
        live_api_origin: str | None = None,
        live_auth_origin: str | None = None,
        **kwargs: Any,
    ):
        super().__init__(scope, construct_id, **kwargs)
        self.domain = validate_web_domain(domain)
        certificate_arn = validate_certificate_arn(certificate_arn)
        self.live_origins = validate_live_origins(live_api_origin, live_auth_origin)

        self.bucket = aws_s3.Bucket(
            self,
            "SiteBucket",
            block_public_access=aws_s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            encryption=aws_s3.BucketEncryption.S3_MANAGED,
            versioned=True,
            lifecycle_rules=[
                aws_s3.LifecycleRule(
                    id="expire-old-versions",
                    noncurrent_version_expiration=cdk.Duration.days(NONCURRENT_VERSION_DAYS),
                    abort_incomplete_multipart_upload_after=cdk.Duration.days(ABORT_MULTIPART_DAYS),
                )
            ],
            # Deliberately no auto_delete_objects: it adds a custom resource Lambda.
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        cache_policy = aws_cloudfront.CachePolicy(
            self,
            "CachePolicy",
            comment="Honor the origin Cache-Control; 5 minutes when it sends none",
            min_ttl=CACHE_MIN_TTL,
            default_ttl=CACHE_DEFAULT_TTL,
            max_ttl=CACHE_MAX_TTL,
            enable_accept_encoding_gzip=True,
            enable_accept_encoding_brotli=True,
        )
        headers_policy = self._headers_policy()

        self.distribution = aws_cloudfront.Distribution(
            self,
            "Distribution",
            comment="precertly recorded-mode site",
            default_root_object="index.html",
            domain_names=[self.domain],
            certificate=aws_certificatemanager.Certificate.from_certificate_arn(
                self, "Certificate", certificate_arn
            ),
            minimum_protocol_version=aws_cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
            http_version=aws_cloudfront.HttpVersion.HTTP2_AND_3,
            price_class=aws_cloudfront.PriceClass.PRICE_CLASS_100,
            enable_logging=False,
            default_behavior=aws_cloudfront.BehaviorOptions(
                origin=aws_cloudfront_origins.S3BucketOrigin.with_origin_access_control(
                    self.bucket
                ),
                viewer_protocol_policy=aws_cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=aws_cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                cached_methods=aws_cloudfront.CachedMethods.CACHE_GET_HEAD,
                compress=True,
                cache_policy=cache_policy,
                response_headers_policy=headers_policy,
            ),
            error_responses=[
                aws_cloudfront.ErrorResponse(
                    http_status=status,
                    response_http_status=404,
                    response_page_path=ERROR_PAGE_PATH,
                    ttl=ERROR_TTL,
                )
                for status in (403, 404)
            ],
        )

        cdk.CfnOutput(self, "BucketName", value=self.bucket.bucket_name)
        cdk.CfnOutput(self, "DistributionId", value=self.distribution.distribution_id)
        cdk.CfnOutput(
            self, "DistributionDomainName", value=self.distribution.distribution_domain_name
        )
        cdk.CfnOutput(self, "SiteUrl", value=f"https://{self.domain}")
        if self.live_origins is not None:
            cdk.CfnOutput(self, "LiveApiOrigin", value=self.live_origins[0])
            cdk.CfnOutput(self, "LiveAuthOrigin", value=self.live_origins[1])

    def _headers_policy(self) -> aws_cloudfront.ResponseHeadersPolicy:
        return aws_cloudfront.ResponseHeadersPolicy(
            self,
            "HeadersPolicy",
            comment="Security headers for the static site; they replace any the origin sends",
            security_headers_behavior=aws_cloudfront.ResponseSecurityHeadersBehavior(
                content_security_policy=aws_cloudfront.ResponseHeadersContentSecurityPolicy(
                    content_security_policy=content_security_policy(self.live_origins),
                    override=True,
                ),
                strict_transport_security=aws_cloudfront.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=HSTS_MAX_AGE,
                    include_subdomains=False,
                    preload=False,
                    override=True,
                ),
                content_type_options=aws_cloudfront.ResponseHeadersContentTypeOptions(
                    override=True
                ),
                frame_options=aws_cloudfront.ResponseHeadersFrameOptions(
                    frame_option=aws_cloudfront.HeadersFrameOption.DENY, override=True
                ),
                referrer_policy=aws_cloudfront.ResponseHeadersReferrerPolicy(
                    referrer_policy=aws_cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
                    override=True,
                ),
            ),
            custom_headers_behavior=aws_cloudfront.ResponseCustomHeadersBehavior(
                custom_headers=[
                    aws_cloudfront.ResponseCustomHeader(
                        header="Permissions-Policy", value=PERMISSIONS_POLICY, override=True
                    )
                ]
            ),
        )
