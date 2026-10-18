# Deployment

Deploys run from the owner's Mac with `scripts/deploy.sh`. GitHub Actions never deploys: this AWS account's managed organization policy denies `iam:*Provider*`, so no OIDC provider can exist and a workflow cannot assume an AWS role. Long-lived AWS keys in GitHub are not an option. CI only synthesises and tests (`infra-synth`) and builds the image without pushing it (`docker-build`).

## Prerequisites

- AWS CLI v2 and a login for the `precertly` profile: `aws login --profile precertly`. The credentials are short-lived console-login credentials, so log in again if a command reports an expired token. The script defaults to that profile and to `us-east-2`; `AWS_PROFILE` and `AWS_REGION` override them.
- Docker with buildx, running. Images are built for `linux/arm64`.
- `BUDGET_EMAIL` for `stack` and `up`. Set it in the environment, or put one line `BUDGET_EMAIL=<address>` in `.env.local` at the repo root. That file is gitignored and is parsed, not sourced: only blank lines, `#` comments and `KEY=value` lines are accepted, nothing is expanded, and only `BUDGET_EMAIL` is used. The environment wins if both are set. The address is passed to the CDK as `-c budgetEmail=...` and is never printed (the `cdk diff` that `up` shows is redacted). Do not put it in a committed file, a shell history you share or a CI variable.
- For stacks: `cd infra && uv sync --locked && npm ci` once, and the account bootstrapped for the CDK.
- For `smoke`: the AWS `session-manager-plugin`, which ECS Exec needs on your machine.
- A clean working tree with `HEAD` pushed, for `image` (and so for `up precertly-app`).

## Runbook

Every command takes `--dry-run`, which prints the commands with the account id and email redacted and does not touch AWS or Docker. Run it first.

1. **Check what exists:** `scripts/deploy.sh status`. Read-only. It prints the CloudFormation status of the three stacks, the service's desired, running and pending counts and rollout, the task definition revision, its image tag and whether a container health check is set, the number of available NAT gateways, the number of RDS instances and the images in the ECR repository. A resource that does not exist prints "not deployed"; any other failure (expired login, no permission) stops with the AWS error instead of reading as "not deployed".
2. **Deploy a stack:** `scripts/deploy.sh up <name>`, one stack at a time, in dependency order: `precertly-base`, then `precertly-data`, then `precertly-app`, once the middle one is defined. For `precertly-app` it first runs the image step (build for `linux/arm64`, push `sha-<12 hex of HEAD>`, skipped if that tag exists). It then shows `cdk diff` with the budget address redacted, asks `Deploy? [y/N]`, and only on `y` runs the same deploy as `scripts/deploy.sh stack <name>`. `--yes` only skips the image build confirmation: it never skips the diff or the Deploy question, and the CDK's own approval prompt for IAM and security-group changes stays on. `--all` is refused. `precertly-app` deploys the service at desired count 0, so nothing starts.
3. **Smoke test:** `scripts/deploy.sh smoke [--bedrock]`. It refuses unless the service exists and has desired, running and pending counts of 0, so it never touches a task someone else started. Then it scales to 1 and waits up to 10 minutes for a stable service, picks the task and prints its `lastStatus` and `healthStatus`, runs `GET /health` and `GET /policies` inside the task through ECS Exec (Python `urllib`; the image has no curl) and fails on any status other than 200. With `--bedrock` it first asks `One real Bedrock call, costs a fraction of a cent. Continue? [y/N]` (before anything starts, so no task bills while the question waits; `--yes` is not accepted) and then runs `python scripts/smoke_llm.py` in the task. On any exit, whether the checks passed, one failed, the script died or you pressed Ctrl-C, a trap sets the desired count to 0 and waits until no task runs, retrying the update, and prints a one-screen summary: PASS or FAIL per check and the final counts. If the scale-down cannot be confirmed it says so, loudly: run `scripts/deploy.sh down` at once, because a running task bills by the hour.
4. **Stop:** `scripts/deploy.sh down` sets the desired count to 0 and confirms that no task runs. Use it after any manual start or if `smoke` was killed hard (`kill -9` cannot be trapped).

`DEPLOY_WAIT_TIMEOUT` (default 600 seconds) and `DEPLOY_POLL_SECONDS` (default 10) change the waits; the tests set them to nothing.

`image` and `stack <name>` still exist as the two halves of `up`: `scripts/deploy.sh image [--yes]` builds and pushes, and `scripts/deploy.sh stack <name>` deploys without a diff or question. `precertly-app` needs the image from `image`: the script passes `-c imageTag=<tag of HEAD>` and refuses if that tag is not in ECR.

## What is verified

`up`, `status`, `smoke` and `down` are tested with stub `aws`, `npx` and `docker` programs and with `--dry-run` (`tests/test_deploy_script.py`), and the script is shellchecked. They have not been run against real AWS. The first real run is the check of three things the stubs cannot show: the `--query` expressions against the real API output, the exact quoting of the ECS Exec command string, and that a Ctrl-C during an Exec session reaches the trap. Until the owner has run them, do not call them verified.

## Manual fallback

If the script is not available or a step fails in a way you want to inspect, these are the commands it runs (cluster `precertly`, service `precertly-api`, container `api`, region `us-east-2`; names are in `infra/names.py`):

```bash
# status
aws cloudformation describe-stacks --stack-name precertly-app --query 'Stacks[0].StackStatus' --output text
aws ecs describe-services --cluster precertly --services precertly-api \
  --query 'services[0].[status,desiredCount,runningCount,pendingCount,deployments[0].rolloutState,taskDefinition]' --output text
aws ec2 describe-nat-gateways --filter Name=state,Values=available --query 'length(NatGateways)' --output text
aws rds describe-db-instances --query 'length(DBInstances)' --output text
aws ecr describe-images --repository-name precertly-api --query 'length(imageDetails)' --output text

# smoke test
aws ecs update-service --cluster precertly --service precertly-api --desired-count 1
aws ecs wait services-stable --cluster precertly --services precertly-api
TASK=$(aws ecs list-tasks --cluster precertly --service-name precertly-api --desired-status RUNNING --query 'taskArns[0]' --output text)
aws ecs describe-tasks --cluster precertly --tasks "$TASK" --query 'tasks[0].[lastStatus,healthStatus]' --output text
aws ecs execute-command --cluster precertly --task "$TASK" --container api --interactive \
  --command "python -c \"import urllib.request as r; print(r.urlopen('http://127.0.0.1:8000/health', timeout=5).status)\""
aws ecs execute-command --cluster precertly --task "$TASK" --container api --interactive \
  --command "python scripts/smoke_llm.py"     # one real Bedrock call; costs a fraction of a cent

# always finish with
aws ecs update-service --cluster precertly --service precertly-api --desired-count 0
aws ecs wait services-stable --cluster precertly --services precertly-api

# image and stack
scripts/deploy.sh image
cd infra && npx --no-install cdk diff precertly-app -c budgetEmail=<address> -c imageTag=sha-<12 hex>
cd infra && npx --no-install cdk deploy precertly-app -c budgetEmail=<address> -c imageTag=sha-<12 hex>
```

## Why tags are immutable

The ECR repository `precertly-api` has immutable tags. A `sha-` tag names one commit and one image, so what runs is what was reviewed, a rollback is a task definition pointing at an older tag, and nothing can overwrite a tag after the fact. The consequence is that `image` never re-pushes: if the tag exists it prints "already pushed, skipping" and exits 0. To change an image, commit and push, which gives a new tag.

The repository keeps the last 5 `sha-` images and expires untagged images after one day. The script builds with `--provenance=false --sbom=false` so BuildKit adds no attestation manifests, which would appear as untagged images and be deleted.

## Cost

- The VPC has no NAT gateway, no interface endpoints and no load balancer, which are the parts of a network that bill by the hour, so the network costs nothing.
- The service is at desired count 0 and costs nothing. A running task costs Fargate vCPU and memory time plus the hourly charge for its public IPv4 address. `smoke` always scales back to 0 and `down` confirms it; `status` shows the running count.
- The log group keeps logs for 7 days.

## What to call it

Until the service is running and a smoke check passes, say **deployable**, not **deployed**. A pushed image is "pushed", a created stack is "created", and "deployed" is for a service that is serving and has answered `/health` and `/policies` after the release. README and the Status section in CLAUDE.md follow the same rule, with the date checked.
