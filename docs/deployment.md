# Deployment

Deploys run from the owner's Mac with `scripts/deploy.sh`. GitHub Actions never deploys: this AWS account's managed organization policy denies `iam:*Provider*`, so no OIDC provider can exist and a workflow cannot assume an AWS role. Long-lived AWS keys in GitHub are not an option. CI only synthesises and tests (`infra-synth`) and builds the image without pushing it (`docker-build`).

## Prerequisites

- AWS CLI v2 and a login for the `precertly` profile: `aws login --profile precertly`. The credentials are short-lived console-login credentials, so log in again if a command reports an expired token. The script defaults to that profile and to `us-east-2`; `AWS_PROFILE` and `AWS_REGION` override them.
- Docker with buildx, running. Images are built for `linux/arm64`.
- `BUDGET_EMAIL` for `stack` and `up`. Set it in the environment, or put one line `BUDGET_EMAIL=<address>` in `.env.local` at the repo root. That file is gitignored and is parsed, not sourced: only blank lines, `#` comments and `KEY=value` lines are accepted, nothing is expanded, and only `BUDGET_EMAIL`, `WEB_DOMAIN` and `WEB_CERTIFICATE_ARN` are used (the last two only for `precertly-web`; see The web stack). The environment wins if both are set. The address is passed to the CDK as `-c budgetEmail=...` and is never printed (the `cdk diff` that `up` shows is redacted). Do not put it in a committed file, a shell history you share or a CI variable.
- `WEB_DOMAIN` and `WEB_CERTIFICATE_ARN` for `stack precertly-web` and `up precertly-web` only, from the environment or `.env.local` like the budget address: the site's hostname (a plain lowercase name such as `site.example.com`) and the ARN of an ACM certificate for it in **us-east-1** (CloudFront accepts no other region). The script refuses to run, even with `--dry-run`, if either is missing, malformed or one of the placeholders. The ARN is never printed: dry runs and the diff show `REDACTED`. Every other stack is given the documented placeholders, so base, app and data deploys need neither.
- For stacks: `cd infra && uv sync --locked && npm ci` once, and the account bootstrapped for the CDK.
- For `web publish`: `uv` and Node with `npm` on the Mac (the script runs `uv sync`, then `npm ci` in `web/`; it never runs `npm install`, so `package-lock.json` is not rewritten). `web/package.json` pins Node 24.x in `engines`; the repository sets no `engine-strict`, so npm only warns on Node 22 (a user-level `.npmrc` that sets it would make it an error). It needs no `BUDGET_EMAIL`, `WEB_DOMAIN` or `WEB_CERTIFICATE_ARN`: it reads the bucket and distribution from the deployed stack's outputs.
- For `smoke`: the AWS `session-manager-plugin`, which ECS Exec needs on your machine.
- For `web smoke`: `curl` (the macOS one is enough) and network access to the site; nothing else.
- A clean working tree with `HEAD` pushed, for `image` (and so for `up precertly-app` and `up precertly-data`, which also check that the image is in ECR) and for `web publish`.

## Runbook

Every command takes `--dry-run`, which prints the commands with the account id and email redacted and does not touch AWS or Docker. Run it first.

1. **Check what exists:** `scripts/deploy.sh status`. Read-only. It prints the CloudFormation status of the four stacks, one line for the web stack (state, distribution id, bucket name; `not deployed` if the stack does not exist), the service's desired, running and pending counts and rollout, the task definition revision, its image tag and whether a container health check is set, the number of available NAT gateways, the number of RDS instances and the images in the ECR repository. A resource that does not exist prints "not deployed"; any other failure (expired login, no permission) stops with the AWS error instead of reading as "not deployed".
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

## The web stack

`precertly-web` (`infra/web_stack.py`) was **deployed by the owner on 2026-10-21** (owner-confirmed from their CLI output, us-east-2, not checked by Claude). **Nothing has been published to its bucket, and the domain has no DNS record, so the site is not hosted and has no URL.** It holds a private S3 bucket (all public access blocked, TLS enforced, S3-managed encryption, versioned; old versions expire after 30 days and incomplete multipart uploads after 1 day), a CloudFront distribution that reads it through an origin access control (the bucket policy lets that one distribution call `s3:GetObject` and nothing else), a cache policy, and a response headers policy. It has no cross-stack references and does not depend on the other stacks.

- **Inputs, never committed:** CDK context `webDomain` (a plain lowercase hostname) and `webCertificateArn` (an ACM certificate in us-east-1; the stack rejects any other region). The CDK app requires both for every stack, like `imageTag`, because one `cdk deploy <name>` still builds the whole app; CI and the tests pass the placeholders `site.example.invalid` and a certificate ARN made of zeros, and `scripts/deploy.sh` passes the same ones to the other stacks. The certificate already exists (the owner created it with the CLI); this stack creates no certificate and no DNS record, so the owner points the domain at the `DistributionDomainName` output by hand; that has not been done.
- **Deploy:** `scripts/deploy.sh up precertly-web` (redacted diff, "Deploy? [y/N]", `--exclusively`, no image step); `stack precertly-web` is the same without the diff. `WEB_DOMAIN` and `WEB_CERTIFICATE_ARN` go in the environment or `.env.local`. Publishing is `web publish` and checking the result is `web smoke` (both below).
- **Outputs:** `BucketName`, `DistributionId`, `DistributionDomainName`, `SiteUrl`.
- **Errors:** a 403 or 404 from S3 (S3 answers 403 for a missing key when the caller cannot list the bucket) is returned as a 404 with `/404.html`, cached for 10 seconds. The file `web/public/404.html` exists in the web build; it is the path the stack names (`ERROR_PAGE_PATH`).
- **Caching:** a custom cache policy honors the origin's `Cache-Control` (minimum 0, default 300 seconds when the origin sends none, maximum one year), with gzip and brotli. GET and HEAD only, compressed, price class 100, no access logging.
- **Headers (override the origin):** `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; connect-src 'self'; base-uri 'self'; form-action 'none'; frame-ancestors 'none'; object-src 'none'`; `Strict-Transport-Security: max-age=31536000` with neither `includeSubDomains` nor `preload` (the parent domain hosts other sites); `X-Content-Type-Options: nosniff`; `X-Frame-Options: DENY`; `Referrer-Policy: strict-origin-when-cross-origin`; `Permissions-Policy: camera=(), microphone=(), geolocation=()`. The policy has not been tested in a browser against a real response, because nothing has been published. `img-src` is `'self'` only, which the build satisfies: it has no `data:` URI, and `web/scripts/check-dist.mjs` fails the build on one in CSS.
- **Destroying it:** the bucket has removal policy DESTROY **without** auto-delete (that needs a custom resource Lambda, which this stack avoids). Empty the bucket first, including old versions and delete markers, or `cdk destroy precertly-web` fails on the non-empty bucket.
- **Names:** the bucket name is generated. The cache policy name includes the region and the response headers policy name does not; both must be unique in the account, so a second copy of the stack in one account would clash.

### Publishing the site: `scripts/deploy.sh web publish [--yes] [--dry-run]`

Builds the static site from a commit and copies it to the stack's bucket. Nothing has been published with it yet: the command is tested with stubs and `--dry-run` only and has not been run against real AWS. Run `--dry-run` first: it prints every command and runs none of them (no AWS, `uv` or `npm` call, and no git check).

1. **Guards:** refuses a dirty working tree (untracked files count) and a `HEAD` that is not pushed, the same check as `image`, so what is published matches a commit; prints the short sha. Then refuses unless `precertly-web` exists and is `CREATE_COMPLETE` or `UPDATE_COMPLETE`, and reads `BucketName`, `DistributionId` and `SiteUrl` from its outputs (no bucket, id or account is written down anywhere).
2. **Build**, stopping before any upload if a step fails: `uv sync`, then in `web/`: `npm ci`, `npm run prepare-data`, `npm run build`, `node scripts/check-dist.mjs`. Afterwards it checks that `web/dist` has `index.html`, `404.html`, `favicon.svg`, some `assets/*.js` and some `data/*.json`, and that the build left the working tree clean.
3. **Preview and question:** `aws s3 sync --dryrun` (read-only) for the two syncs, with the counts to upload and delete and the keys that would be deleted, then `Publish? [y/N]`. `--yes` is accepted and ignored: it never skips this question. A rebuild rewrites every file's modification time, so `sync` treats every built file as changed; the upload count is therefore roughly the size of the build, and the number worth reading is the delete list.
4. **Upload**, in this order, so a visitor never gets an `index.html` that names files that are not there yet:
   1. `assets/` (content-hashed names) with `Cache-Control: public,max-age=31536000,immutable` and **no `--delete`**: older hashed files stay, because a page cached by a browser may still name them.
   2. Everything else (`data/*.json`, `404.html`, `favicon.svg`) with `public,max-age=300`, and `--delete` with `--exclude "assets/*" --exclude index.html`, so a stale file outside `assets/` is removed and nothing under `assets/` or `index.html` ever is.
   3. `index.html` last, with `public,max-age=60`.
5. **Content types:** the AWS CLI picks a content type from the file extension with Python's `mimetypes`, which also reads the system's `mime.types` and so can differ between machines. The script does not assume: after the uploads it reads back (`aws s3api head-object`) the type of `index.html`, `404.html`, `favicon.svg` and one `.js`, `.css` and `.json` file, and stops, before the invalidation, if one is wrong (`text/html`, `image/svg+xml`, `text/javascript` or `application/javascript`, `text/css`, `application/json`). Stubs cannot show what the real CLI does, so this check is what covers it.
6. **Invalidation:** only the files whose names are not content-hashed: `/index.html /404.html /favicon.svg /data/*` (`/index.html` is also the distribution's default root object). Then it waits for CloudFront to report `Completed`, for at most 5 minutes (`DEPLOY_WAIT_TIMEOUT` if shorter; `DEPLOY_POLL_SECONDS` sets the interval). On a timeout it prints a warning with the `get-invalidation` command and still exits 0: the publish is done and CloudFront finishes the invalidation by itself. If the invalidation cannot be created at all, it exits non-zero and says the files are uploaded.
7. **Summary:** the commit published, the number of files in the build, and the `SiteUrl` output.

A failure during the uploads stops there and says which step failed. Because of the order, the page visitors get is never left pointing at missing files: if `assets/` or the rest fails, `index.html` was not replaced; if only `index.html` fails, everything else is already up. Run the command again.

**Rolling back.** The bucket is versioned and every publish comes from a commit, so a rollback is a publish of an older commit: `git checkout <older commit>`, then `scripts/deploy.sh web publish` (the clean tree and pushed `HEAD` checks still apply, so the commit must have been pushed), then `git checkout main`. The hashed files of newer builds stay in `assets/` and are harmless. Bucket versions (`aws s3api list-object-versions`) are a last resort for a single file; they expire 30 days after being replaced.

### Checking the published site: `scripts/deploy.sh web smoke [--dry-run]`

A read-only check of the site `web publish` uploaded, so the same checks can be repeated after every publish and during a launch window. **Added 2026-10-22; tested with stub `aws` and `curl` programs and with `--dry-run` only, and not yet run against the real site.** It changes nothing anywhere: no AWS write, no upload, no invalidation, and no cache-busting query string. It takes no confirmation, and `--yes` has no effect.

- **AWS:** two `describe-stacks` reads of `precertly-web` and nothing else. It refuses, before any request, unless the stack is `CREATE_COMPLETE` or `UPDATE_COMPLETE` (the same guard as `web publish`) and `SiteUrl` is `https://<hostname>` with no path.
- **Requests:** `curl --disable --silent --show-error --max-time 20 --proto =https` (`=http` for the one plain-HTTP request). `--disable` ignores `~/.curlrc`; it never uses `--insecure`, cookies, credentials or `--location`. A request that cannot complete (timeout, DNS, TLS) is one failed check, not an abort, and the summary still prints.
- **Checks**, one `PASS` or `FAIL <what>: expected X, got Y` line each:
  - `GET /`: status 200; `content-type` `text/html`; `cache-control` has `max-age=60` (the `index.html` setting; `max-age=600` fails); `content-security-policy` is present and has `default-src 'none'` and `frame-ancestors 'none'`; `strict-transport-security` has `max-age=31536000` and neither `includeSubDomains` nor `preload`; `x-content-type-options: nosniff`; `x-frame-options: DENY`; `referrer-policy` and `permissions-policy` are present. Header names are case-insensitive.
  - The first `assets/*.js` and first `assets/*.css` named in the body of `/`: status 200, a JavaScript or CSS `content-type`, `cache-control` has `immutable`. If the body names none, that is a failed check.
  - `/data/index.json`, `/data/schema.json`, `/data/policies/facet-joint-interventions.json` and `/data/cases/facet-01.json`: status 200 and `application/json`.
  - `/favicon.svg`: 200 and `image/svg+xml`.
  - `/nope`: 404 (not 200), `text/html`, and a body containing `This page does not exist` (the CloudFront error response serving `/404.html`).
  - `http://<host>/`: one of 301, 302, 307, 308, with a `Location` starting `https://<host>/`, not followed. The stack's viewer protocol policy is `REDIRECT_TO_HTTPS`, which CloudFront answers with a 301 for GET.
  - TLS: every HTTPS request must verify the certificate without `-k`; the result (`ssl_verify_result`) is printed once, on the first request.
- **Output:** a body is never printed except, after the first failed check on a response, its first 200 characters (control characters removed; anything shaped like an account id, a distribution id or a bucket name, and the settings `redact()` knows, replaced). The stack is read for `SiteUrl` only, so the bucket name and distribution id are never read. The summary is `N passed, M failed`, the failed lines again, and `result: PASS` or `result: FAIL` (exit 1).
- **When:** after every `web publish`, once the invalidation has completed (the script waits for it); and on a schedule during a launch window. A failure on `/` right after a publish may only mean the old `index.html` is still cached (`max-age=60`); run it again before assuming a problem.
- **`--dry-run`:** prints the two AWS reads and each request (method, URL with `SITE_HOST` for the hostname, what it asserts) and makes no call.

## Before the next up

**Not done as of this PR (2026-10-21).** The images pushed so far (`sha-951a31b14a9a` and `sha-30bc9a4e4f79` among them) predate two changes on `main`: the certifi 2026.10.19 lockfile bump (PR #47, which fixed the pip-audit advisory GHSA-6q48-6c7w-9gw2) and the uv 0.13.4 base image bump (PR #51). Before the next `up`:

1. Build and push an image from current `main` (`scripts/deploy.sh image`), which gives a new `sha-` tag.
2. Scan it on push and record the scan result here with the date.
3. Re-run `smoke` after the task definition changes: it has only passed on revision 2 and has not been run on revision 3 or later.

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

`up`, `status`, `smoke`, `down`, `tools`, `web publish` and `web smoke` (the last also with a stub `curl`; never run against the real site as of 2026-10-22) are tested with stub `aws`, `npx` and `docker` programs and with `--dry-run` (`tests/test_deploy_script.py`), and CI shellchecks the script (it was not run locally for the `tools` change: shellcheck was not installed). On 2026-10-20 the owner ran `status`, `tools check`, `tools migrate` and `tools load` against real AWS and they worked (results above). `smoke` passed against real AWS on 2026-10-18, on task definition revision 2, after the fix for the first failed run (PR 43), and `smoke --bedrock` also passed once that day (owner-confirmed from their CLI output): service stable, Exec agent running, `/health` 200, `/policies` 200 through ECS Exec, ECS health HEALTHY, scaled back to 0. That passing run shows the `--query` expressions and the quoting of the ECS Exec command worked against real AWS. `smoke` has not been re-run on revision 3. The one thing still unconfirmed is that a Ctrl-C during an Exec session reaches the trap. `web publish` has only run against stubs and with `--dry-run`: no real `aws s3`, `aws s3api` or `aws cloudfront` call has been made by it, so the `--query` expressions and the content types the real CLI picks are unconfirmed until the owner's first run.

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

# the web stack (needs a us-east-1 certificate; deployed 2026-10-21, owner-confirmed)
cd infra && npx --no-install cdk diff --exclusively precertly-web -c budgetEmail=<address> -c imageTag=sha-<12 hex> \
  -c webDomain=<hostname> -c webCertificateArn=<arn>

# publishing the site (what `web publish` runs, after the build in web/dist)
aws s3 sync web/dist/assets s3://<bucket>/assets --cache-control "public,max-age=31536000,immutable"
aws s3 sync web/dist s3://<bucket> --exclude "assets/*" --exclude index.html --delete --cache-control "public,max-age=300"
aws s3 cp web/dist/index.html s3://<bucket>/index.html --cache-control "public,max-age=60"
aws cloudfront create-invalidation --distribution-id <id> --paths /index.html /404.html /favicon.svg "/data/*"

# checking the site (what `web smoke` runs for the first request; the rest are in its dry run)
curl --disable --silent --show-error --max-time 20 --proto '=https' --dump-header - --output /dev/null https://<hostname>/
```

## Why tags are immutable

The ECR repository `precertly-api` has immutable tags. A `sha-` tag names one commit and one image, so what runs is what was reviewed, a rollback is a task definition pointing at an older tag, and nothing can overwrite a tag after the fact. The consequence is that `image` never re-pushes: if the tag exists it prints "already pushed, skipping" and exits 0. To change an image, commit and push, which gives a new tag.

The repository keeps the last 5 `sha-` images and expires untagged images after one day. The script builds with `--provenance=false --sbom=false` so BuildKit adds no attestation manifests, which would appear as untagged images and be deleted.

## Cost

- The VPC has no NAT gateway, no interface endpoints and no load balancer, which are the parts of a network that bill by the hour, so the network costs nothing.
- The service is at desired count 0 and costs nothing. A running task costs Fargate vCPU and memory time plus the hourly charge for its public IPv4 address. `smoke` always scales back to 0 and `down` confirms it; `status` shows the running count.
- The log group keeps logs for 7 days.
- The web stack (deployed 2026-10-21) bills per use: S3 storage and requests, and CloudFront requests and transfer at price class 100; the stack itself has no hourly charge. Nothing has been published, so there is no traffic and the cost is expected to be near zero; actual spend has not been checked.
- The data stack (deployed 2026-10-20) adds Aurora storage and the secret while idle; compute is zero while the cluster is paused. Actual spend has not been checked; see Database and tools.

## What to call it

Until the service is running and a smoke check passes, say **deployable**, not **deployed**. A pushed image is "pushed", a created stack is "created", and "deployed" is for a service that is serving and has answered `/health` and `/policies` after the release. As of 2026-10-20 the database is deployed (the data stack exists and was used through the tools task) and the service is at zero tasks: nothing is live. The web stack follows the same rule: as of 2026-10-21 it is deployed (owner-confirmed), nothing has been published to it and the domain has no DNS record, so the site is not hosted and must not be called live. README and the Status section in CLAUDE.md follow the same rule, with the date checked.
