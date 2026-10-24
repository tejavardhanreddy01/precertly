# Live infrastructure (auth and live stacks)

Checked 2026-10-23, by synthesis and assertion tests only. **Nothing here is deployed**: no AWS call was made, no image with the live code has been pushed, and nothing has run against Cognito, API Gateway, Aurora or Bedrock. Do not call it live, deployed or running. How to deploy and operate it is in [deployment.md](deployment.md#the-live-environment-auth-and-live-stacks).

```
browser -> HTTP API (JWT authorizer, throttling, CORS) -> VPC link -> Cloud Map (SRV)
        -> one Fargate task in the app VPC's public subnets -> Aurora (precertly-data) and Bedrock
```

`precertly-auth` (`infra/auth_stack.py`) stands alone. `precertly-live` (`infra/live_stack.py`) refers to `precertly-app` (VPC, public subnets), `precertly-data` (database secret, database security group) and `precertly-auth` (issuer URL, client id) through weak cross-stack references (`Fn::GetStackOutput`, the `weak` setting in `infra/cdk.json`). None of those stacks refers to it, and the older four do not import its module (tests).

## New resources

| Stack | Resource types |
| --- | --- |
| `precertly-auth` | `AWS::Cognito::UserPool`, `AWS::Cognito::UserPoolDomain`, `AWS::Cognito::UserPoolClient` (3 resources, no IAM role, no Lambda) |
| `precertly-live` | `AWS::EC2::SecurityGroup` x2, `AWS::EC2::SecurityGroupIngress` x2, `AWS::Logs::LogGroup`, `AWS::IAM::Role` x2, `AWS::IAM::Policy` x2, `AWS::ECS::TaskDefinition`, `AWS::ECS::Service`, `AWS::ServiceDiscovery::PrivateDnsNamespace`, `AWS::ServiceDiscovery::Service`, `AWS::ApiGatewayV2::VpcLink`, `::Api`, `::Integration` x2, `::Route` x3, `::Authorizer`, `::Stage` (22 resources) |

No cluster, VPC, subnet, NAT gateway, load balancer, interface endpoint, WAF, Lambda, custom resource, KMS key, S3 bucket or secret is created (tests). Every resource is removed with its stack (`DeletionPolicy: Delete`; a test rejects `Retain` and `Snapshot`). The CDK validation report for the synthesized app has no violations.

## Template identity checks

Compared on 2026-10-23 with `cdk synth --no-lookups` of `main` (before) and of the branch (after), same placeholder context:

| Stack | Result |
| --- | --- |
| `precertly-base` | byte-identical |
| `precertly-app` | byte-identical (the Bedrock statement and the health check moved into functions in `infra/app_stack.py`; the output is the same) |
| `precertly-web` | byte-identical |
| `precertly-data` | resources, parameters and the five existing outputs unchanged; **two outputs added**: `PublishOutputFnGetAttDatabaseSecurityGroup7319C0F6GroupId3AE3795A` (the database security group id) and `PublishOutputRefDatabaseSecretAttachment9A63A3D4D3DD6154` (the attached secret's ARN). They are plain outputs with no export. The data template gets no ingress resource: the rule that admits the live task is a resource of the live stack. |

`infra/tests/test_template_identity.py` repeats this on every run by building the four original stacks alone and comparing.

## IAM, as synthesized

Task role `precertly-live-task` (trusted by `ecs-tasks.amazonaws.com`, no managed policies):

| Sid | Actions | Resources |
| --- | --- | --- |
| `BedrockInvoke` | `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream` | the inference profile `us.amazon.nova-2-lite-v1:0` in this account and region; the foundation model `amazon.nova-2-lite-v1:0` in any region (a `us.` profile routes across US regions); the foundation model `amazon.titan-embed-text-v2:0` in this region. The same function builds this statement for the app task role, and a test compares the two. |
| (ECS Exec) | `ssmmessages:CreateControlChannel`, `CreateDataChannel`, `OpenControlChannel`, `OpenDataChannel` | `*` (the actions have no resource-level scoping; added by enabling ECS Exec on the service) |

Execution role `precertly-live-execution`:

| Actions | Resources |
| --- | --- |
| `ecr:BatchCheckLayerAvailability`, `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer` | the repository `precertly-api` |
| `ecr:GetAuthorizationToken` | `*` (no resource-level scoping) |
| `logs:CreateLogStream`, `logs:PutLogEvents` | the log group `/precertly/live` |
| `secretsmanager:DescribeSecret`, `secretsmanager:GetSecretValue` | the data stack's attached database secret only |

The only wildcard resources are the four `ssmmessages` actions and `ecr:GetAuthorizationToken` (a test lists them). There is no S3, no other Secrets Manager access, no IAM provider and no `Condition`.

## Security groups, as synthesized

| Group | Inbound | Outbound |
| --- | --- | --- |
| Live VPC link | none | all |
| Live task | TCP 8000 from the Live VPC link group only (no CIDR range) | all |
| Database (`precertly-data`, unchanged) | TCP 5432 from the tools task group (as before) **and, from the live stack,** TCP 5432 from the Live task group (`AWS::EC2::SecurityGroupIngress` `DatabaseFromLiveTask`, a resource of `precertly-live`) | none |

The task has a public IP only because the VPC has no NAT or endpoints and the image pull needs a route out.

## Gateway routes

| Route | Authorizer | Notes |
| --- | --- | --- |
| `GET /health` | none | the web UI's up/down probe; no header mapping |
| `POST /live/runs` | JWT (Cognito access token, scope `openid`) | the header `x-precertly-actor` is overwritten with `$context.authorizer.claims.sub` |
| `GET /live/runs/{run_id}` | JWT (same) | same mapping |

There is no `$default` route and no catch-all, so any other path ends at the gateway with a 404 and never reaches the task. Stage `$default`: auto deploy, throttle 2 requests per second, burst 5. CORS: origin `https://<webDomain>` only, methods GET, POST, OPTIONS, headers `authorization` and `content-type`, max age 1 hour, no credentials.

Issuer: the authorizer's issuer, and the `IssuerUrl` output of `precertly-auth`, is `https://cognito-idp.<region>.amazonaws.com/<user pool id>`, built from `Aws.REGION` and the pool id. It is not built from the pool's `ProviderURL` attribute, which already starts with `https://`: the first deploy of `precertly-live` (2026-10-23) was rejected by API Gateway ("Issuer is not a valid URL for JWT Authorizer") and rolled back because the code had prefixed `https://` to that attribute. The live stack reads the pool id through a weak cross-stack reference (`PublishOutputRefUserPool...` on the auth stack); the `ProviderURL` publish output no longer exists.

Audience check: AWS documents that the HTTP API JWT authorizer matches the configured audience against `aud`, or against `client_id` when `aud` is absent (the page "Control access to HTTP APIs with JWT authorizers in API Gateway", read 2026-10-23). A Cognito access token has `client_id` and no `aud`, so the access token is the token to send. The same page notes that an ID token (whose `aud` is the client id) would also pass an audience check, and that nothing in a JWT marks it as an access token; the routes therefore also require the scope `openid`, which only an access token carries as a `scope` claim. The CDK construct's own documentation says only "aud"; it does not mention `client_id`.

## Decisions made in the code

- **Account recovery: none.** Users are created by the owner; a lost password is reset with `admin-set-user-password`. Email recovery would let control of a mailbox start a reset (TOTP would still be required afterwards). Change it in `infra/auth_stack.py` if you prefer self-service.
- **Cognito plan: Essentials**, with threat protection off ("advanced security" is a Plus feature).
- **Task size: 256 CPU, 512 MiB**, like the API task. Measured on 2026-10-23 on the Mac, not on Fargate: the API with live mode on and the 29 cases and 4 policies loaded uses about 90 MiB resident at start. A run's peak was not measured; an out-of-memory stop would show in `live smoke --run` as a failed run or a task restart. Raising it to 512/1024 costs about twice as much while running.
- **Caps** (10 runs a day, 3 per user, 2,000,000 tokens, 600 s) are left at the code defaults in `src/precertly/settings.py`; the task definition sets none of them.
- **CORS lists only the site**, not `http://localhost:5173/`, which the app client allows as a callback and logout URL for local development; a local web UI would be blocked by CORS until that is changed.
- **No access logs** on the stage (they would cost log storage and would hold request paths and caller addresses).

## Costs

Everything below is from memory of public pricing and is **unverified**; actual spend has not been checked.

| Item | Standing or per use |
| --- | --- |
| Running live task (0.25 vCPU, 0.5 GB ARM, plus a public IPv4 address) | about 0.36 USD a day while it runs: forgetting `live stop` costs that per day. At zero tasks: nothing. |
| Route 53 private hosted zone created by the Cloud Map namespace | about 0.50 USD a month, **the only standing cost**; unverified |
| Cloud Map | registered instances and queries; the instance exists only while the task runs; unverified |
| API Gateway HTTP API | per request (about 1 USD per million); the throttle caps it at 2 a second; unverified whether the VPC link has any charge |
| Cognito | per monthly active user above a free allowance (10,000 on Essentials); no SMS is used; unverified for this account |
| Log group `/precertly/live` | storage for 7 days; small |
| One live run | about 0.07 USD of Bedrock plus the Aurora wake-up (the owner's estimate; not measured) |

No NAT gateway, load balancer, interface endpoint, WAF or Cognito advanced security.

## Unverified

None of this has run. In particular:

1. **That the JWT authorizer accepts the access token.** The AWS documentation says it does (above); the spike had no authorizer. The `openid` scope requirement depends on the access token carrying that scope; if the web client asks for different scopes, every request is a 403.
2. **That the parameter mapping works with the Cloud Map integration**: that `overwrite:header.x-precertly-actor` = `$context.authorizer.claims.sub` is applied on a private integration to a service-discovery target, and that the app then sees the user's `sub` (a 36-character id the actor pattern accepts). The spike had no mapping.
3. **How the hosted UI behaves with TOTP required**: that the first sign-in asks for a new password and then for authenticator enrolment with a QR code, and which hosted UI (classic or managed login) a domain created this way gets. The client allows no password or SRP flow, so there is no command-line fallback for enrolment.
4. **CORS on gateway-generated errors**: whether a 401 from the authorizer, a 404 for an unknown path or a 429 from the throttle carries `access-control-allow-origin`. If not, a browser shows a CORS failure instead of the status. `live smoke` checks the preflight only.
5. **The Titan permission of the new task role.** It is the app task role's statement, and that role's Titan permission has also never been exercised; the tools task role's has (the 2026-10-20 load).
6. **That CloudFormation resolves `Fn::GetStackOutput` in the new places**: an authorizer's audience and issuer, the route and Cloud Map references, and the ECS secret references. It resolved the subnet and VPC references of the data stack in the 2026-10-20 deploy.
7. **The cold start with a database.** The spike measured 48 s from scale-up to the first 200 for `/health` with no database. A first run adds the Aurora resume and the run itself (40 to 60 s estimated, not measured).
8. **Teardown order.** The live stack must be destroyed before the data stack (its rule sits on the database security group) and before the auth stack (it reads the issuer); `cdk destroy` of the Cloud Map namespace after the service is the expected order but has not been run.
9. **Throttling and the gateway's default endpoint**: the stage throttle of 2 a second with burst 5 is applied to the default route settings; whether bursts from the web UI's polling stay under it is untested. The default `execute-api` endpoint is on (no custom domain), so `/health` is public.
10. **The issuer string.** The synthesized value was checked in tests (one `https://`, the regional Cognito host, the pool id reference) but it is only proven by a successful deploy of `precertly-live`: the previous bug passed synthesis and failed in API Gateway.
11. **All prices and costs** in the table above.

## Tests

`infra/tests/test_auth_stack.py`, `test_live_stack.py` and `test_template_identity.py` (assertion style, no AWS), `tests/test_live_smoke_run.py` (the in-task script against a local server) and the live sections of `tests/test_deploy_script.py` (stub `aws`, `curl`, `npx` and `docker`). Counts are in the Status section of `CLAUDE.md`.
