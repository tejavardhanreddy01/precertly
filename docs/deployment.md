# Deployment

Deploys run from the owner's Mac with `scripts/deploy.sh`. GitHub Actions never deploys: this AWS account's managed organization policy denies `iam:*Provider*`, so no OIDC provider can exist and a workflow cannot assume an AWS role. Long-lived AWS keys in GitHub are not an option. CI only synthesises and tests (`infra-synth`) and builds the image without pushing it (`docker-build`).

## Prerequisites

- AWS CLI v2 and a login for the `precertly` profile: `aws login --profile precertly`. The credentials are short-lived console-login credentials, so log in again if a command reports an expired token. The script defaults to that profile and to `us-east-2`; `AWS_PROFILE` and `AWS_REGION` override them.
- Docker with buildx, running. Images are built for `linux/arm64`.
- `BUDGET_EMAIL` for `stack` and `up`. Set it in the environment, or put one line `BUDGET_EMAIL=<address>` in `.env.local` at the repo root. That file is gitignored and is parsed, not sourced: only blank lines, `#` comments and `KEY=value` lines are accepted, nothing is expanded, and only `BUDGET_EMAIL` is used. The environment wins if both are set. The address is passed to the CDK as `-c budgetEmail=...` and is never printed (the `cdk diff` that `up` shows is redacted). Do not put it in a committed file, a shell history you share or a CI variable.
- For stacks: `cd infra && uv sync --locked && npm ci` once, and the account bootstrapped for the CDK.
- For `smoke`: the AWS `session-manager-plugin`, which ECS Exec needs on your machine.
- A clean working tree with `HEAD` pushed, for `image` (and so for `up precertly-app` and `up precertly-data`, which also check that the image is in ECR).

## Runbook

Every command takes `--dry-run`, which prints the commands with the account id and email redacted and does not touch AWS or Docker. Run it first.

1. **Check what exists:** `scripts/deploy.sh status`. Read-only. It prints the CloudFormation status of the three stacks, the service's desired, running and pending counts and rollout, the task definition revision, its image tag and whether a container health check is set, the number of available NAT gateways, the number of RDS instances and the images in the ECR repository. A resource that does not exist prints "not deployed"; any other failure (expired login, no permission) stops with the AWS error instead of reading as "not deployed".
2. **Deploy a stack:** `scripts/deploy.sh up <name>`, one stack at a time, in dependency order: `precertly-base`, then `precertly-app`, then `precertly-data`. Deploy `precertly-app` before `precertly-data` even if it was deployed before: it gains the two isolated `database` subnets, which the data stack's cluster needs. Every deploy and diff passes `--exclusively`, so one stack is never deployed together with the stacks it depends on. For `precertly-app` and `precertly-data` it first runs the image step (build for `linux/arm64`, push `sha-<12 hex of HEAD>`, skipped if that tag exists). It then shows `cdk diff` with the budget address redacted, asks `Deploy? [y/N]`, and only on `y` runs the same deploy as `scripts/deploy.sh stack <name>`. `--yes` only skips the image build confirmation: it never skips the diff or the Deploy question, and the CDK's own approval prompt for IAM and security-group changes stays on. `--all` is refused. `precertly-app` deploys the service at desired count 0, so nothing starts.
3. **Smoke test:** `scripts/deploy.sh smoke [--bedrock]`. It refuses unless the service exists and has desired, running and pending counts of 0, so it never touches a task someone else started. With `--bedrock` it first asks `One real Bedrock call, costs a fraction of a cent. Continue? [y/N]` (before anything starts, so no task bills while the question waits; `--yes` is not accepted). Then it scales to 1 and runs these checks in order, stopping at the first failure:
   1. **Service stable:** desired 1, running 1, pending 0, rollout completed, within 10 minutes (`DEPLOY_WAIT_TIMEOUT`).
   2. **Task RUNNING:** the task's `lastStatus`; its `healthStatus` is printed for information.
   3. **ECS Exec agent running:** a RUNNING task is not yet ready for ECS Exec, because the `ExecuteCommandAgent` inside it starts a little later. Polls its managed-agent `lastStatus` until it is RUNNING, for at most 120 seconds. On timeout the FAIL line gives the last status seen (for example PENDING).
   4. **`GET /health` and `GET /policies` return 200:** run inside the task through ECS Exec with Python `urllib` (the image has no curl). Only the errors that mean Exec is not ready (`InvalidParameterException`, `TargetNotConnectedException`, "agent isn't running") are retried, up to 3 attempts a few seconds apart. Any other failure, such as a non-200 status or a Python error, fails at once with no retry. The output tail is shown for the final attempt only. With `--bedrock`, `python scripts/smoke_llm.py` runs the same way (one real Bedrock call).
   5. **ECS health check HEALTHY:** polls the task's `healthStatus` for at most 120 seconds and fails with the last status (UNKNOWN or UNHEALTHY) if it does not become HEALTHY. It runs after the application checks on purpose: the container health check has a start period, and waiting for it first would hide an application failure behind a slow start. UNKNOWN is also what a task shows if its task definition has no container health check, so `status` tells you whether one is set.

   On any exit, whether the checks passed, one failed (including the health check), the script died or you pressed Ctrl-C, a trap sets the desired count to 0 and waits until no task runs, retrying the update, and prints a one-screen summary: PASS or FAIL per check and the final counts. If the scale-down cannot be confirmed it says so, loudly: run `scripts/deploy.sh down` at once, because a running task bills by the hour.
4. **Stop:** `scripts/deploy.sh down` sets the desired count to 0 and confirms that no task runs. Use it after any manual start or if `smoke` was killed hard (`kill -9` cannot be trapped).

`DEPLOY_WAIT_TIMEOUT` (default 600 seconds) and `DEPLOY_POLL_SECONDS` (default 10) change the waits (the agent and health waits are capped at 120 seconds, or at `DEPLOY_WAIT_TIMEOUT` if that is shorter); the tests set them to nothing.

`image` and `stack <name>` still exist as the two halves of `up`: `scripts/deploy.sh image [--yes]` builds and pushes, and `scripts/deploy.sh stack <name>` deploys without a diff or question. `precertly-app` needs the image from `image`: the script passes `-c imageTag=<tag of HEAD>` and refuses if that tag is not in ECR.

## Database and tools

`precertly-data` was deployed by the owner on 2026-10-20 (owner-confirmed from their CLI output, not checked by Claude): `precertly-app` first (additions only), then `precertly-data`; the Aurora cluster scales to zero and the service stays at zero tasks. `scripts/deploy.sh tools` works on the database:

- `tools migrate` runs `alembic upgrade head`; `tools load` runs `python -m precertly.ingest evals/bundles --embed`, which makes real Bedrock embedding calls and writes to the database, so it asks `Continue? [y/N]` first and `--yes` does not skip that question; `tools check` runs `python -m precertly.db.check`, read-only SELECTs that print the pgvector version, each table's row count and whether the connection uses TLS, and no host, user, database name or password.
- Each reads the subnets, security group and log group from the `precertly-data` stack outputs, refuses if a `precertly-tools` task already runs, starts one task with `aws ecs run-task` in the public subnets with a public IP, and waits for it to stop, for at most 10 minutes (`migrate`), 30 (`load`) or 5 (`check`; `DEPLOY_TOOLS_TIMEOUT` overrides, for tests). On any exit, failure, timeout or Ctrl-C it stops every `precertly-tools` task still running and waits until none does, then prints the container exit code and the log tail. The exit status is non-zero unless the container exited 0. If the stop cannot be confirmed it says so loudly: stop the task by hand, because it bills while it runs.
- Idle, the database costs Aurora storage plus about 0.40 USD a month for the secret (the storage rate is approximate and unchecked; actual spend has not been checked). A paused instance still reports `available`; the pause was observed in the CloudWatch metric (below).

**Results of the first deploy (2026-10-20, owner-confirmed from their AWS CLI and script output, us-east-2; not checked by Claude):**

1. `status`: base, app and data stacks `CREATE_COMPLETE` or `UPDATE_COMPLETE`; service desired 0, running 0, pending 0; Aurora available, engine 16.13, capacity 0.0 to 2.0 ACU; 0 NAT gateways; 4 images in ECR. `precertly-app` had been updated first (additions only; task definition revision 3, image `sha-30bc9a4e4f79`, container health check set).
2. `tools migrate`: run by the owner, output not pasted. The later check shows all 8 tables and pgvector 0.8.1, so the task resolved the secret's five JSON keys, reached the database and the master user ran `CREATE EXTENSION vector`.
3. `tools check`: before the migration, server version 16.13, pgvector not installed, `tls: yes`, all 8 tables missing, exit 1 (expected); after the load, pgvector 0.8.1, `tls: yes`, result ok. TLS was measured with `pg_stat_ssl`; the server certificate is not verified (the asyncpg default). This was also the first real run of `tools` against AWS: `run-task`, the log tail, the stop and the exit code all worked.
4. `tools load`: all 29 bundles, exit 0, 237 embedding calls, 29,463 tokens, 0 retries. This is the first real Titan call: the tools task role may call Titan. The API task role's Titan permission is still unverified. The 237 is more than the offline estimate of 210 unique texts because the vector cache is the chunks currently stored and each bundle replaces the one case's chunks (see README, The data stack). After the load the tables hold `cases` 1 and `chart_chunks` 175, the last bundle only; the other tables are empty.
5. Pause: `ServerlessDatabaseCapacity` in 5-minute buckets (local time) read 16:05 0.0, 16:10 1.0, 16:15 0.5, 16:20 0.5, 16:25 0.0, 16:30 0.0, 16:35 0.0, so the cluster paused about 5 minutes after the load ended. A later `tools check` (about 16:40) connected to the paused cluster and passed within the 60 s connect timeout. Resume: **the resume time was not measured**, and the `aws rds describe-events` output was not reported.
6. Cross-stack references (`Fn::GetStackOutput`, the `weak` setting in `infra/cdk.json`): the `precertly-data` deploy succeeded, so CloudFormation resolved them. The secret's JSON keys after the cluster attachment were not inspected directly with `aws secretsmanager get-secret-value`; that the task connected shows `host`, `port`, `username`, `password` and `dbname` were all present for ECS, which is the evidence for it.

**Still open:** the resume time from a paused database (read the first-connect time in the `tools check` log after a pause of more than 5 minutes, and `aws rds describe-events --source-type db-instance`); the API task role's Titan permission; actual spend (Cost Explorer lags 8 to 24 hours); the ECR scan of `sha-30bc9a4e4f79`; and what the service does when it runs in front of the database (the API task has no database and no secrets today).

To remove the database: `cd infra && npx --no-install cdk destroy precertly-data -c budgetEmail=<address> -c imageTag=sha-<12 hex>` (not part of `deploy.sh`). Everything in the stack is deleted with it, without a snapshot.

## What is verified

`up`, `status`, `smoke`, `down` and `tools` are tested with stub `aws`, `npx` and `docker` programs and with `--dry-run` (`tests/test_deploy_script.py`), and CI shellchecks the script (it was not run locally for the `tools` change: shellcheck was not installed). On 2026-10-20 the owner ran `status`, `tools check`, `tools migrate` and `tools load` against real AWS and they worked (results above). `smoke` passed against real AWS on 2026-10-18, on task definition revision 2, after the fix for the first failed run (PR 43), and `smoke --bedrock` also passed once that day (owner-confirmed from their CLI output): service stable, Exec agent running, `/health` 200, `/policies` 200 through ECS Exec, ECS health HEALTHY, scaled back to 0. That passing run shows the `--query` expressions and the quoting of the ECS Exec command worked against real AWS. `smoke` has not been re-run on revision 3. The one thing still unconfirmed is that a Ctrl-C during an Exec session reaches the trap.

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
cd infra && npx --no-install cdk diff --exclusively precertly-app -c budgetEmail=<address> -c imageTag=sha-<12 hex>
cd infra && npx --no-install cdk deploy --exclusively precertly-app -c budgetEmail=<address> -c imageTag=sha-<12 hex>
```

## Why tags are immutable

The ECR repository `precertly-api` has immutable tags. A `sha-` tag names one commit and one image, so what runs is what was reviewed, a rollback is a task definition pointing at an older tag, and nothing can overwrite a tag after the fact. The consequence is that `image` never re-pushes: if the tag exists it prints "already pushed, skipping" and exits 0. To change an image, commit and push, which gives a new tag.

The repository keeps the last 5 `sha-` images and expires untagged images after one day. The script builds with `--provenance=false --sbom=false` so BuildKit adds no attestation manifests, which would appear as untagged images and be deleted.

## Cost

- The VPC has no NAT gateway, no interface endpoints and no load balancer, which are the parts of a network that bill by the hour, so the network costs nothing.
- The service is at desired count 0 and costs nothing. A running task costs Fargate vCPU and memory time plus the hourly charge for its public IPv4 address. `smoke` always scales back to 0 and `down` confirms it; `status` shows the running count.
- The log group keeps logs for 7 days.
- The data stack (deployed 2026-10-20) adds Aurora storage and the secret while idle; compute is zero while the cluster is paused. Actual spend has not been checked; see Database and tools.

## What to call it

Until the service is running and a smoke check passes, say **deployable**, not **deployed**. A pushed image is "pushed", a created stack is "created", and "deployed" is for a service that is serving and has answered `/health` and `/policies` after the release. As of 2026-10-20 the database is deployed (the data stack exists and was used through the tools task) and the service is at zero tasks: nothing is live. README and the Status section in CLAUDE.md follow the same rule, with the date checked.
