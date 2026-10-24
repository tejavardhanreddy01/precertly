#!/usr/bin/env bash
# Deploy from the owner's machine: build and push the API image, deploy one CDK stack, check
# what is running, and smoke-test the API at the cost of one short-lived task.
# GitHub Actions never deploys (the account's managed policy blocks OIDC providers).
# See docs/deployment.md.
set -euo pipefail

# Hard-coded defaults, overridable from the environment.
export AWS_PROFILE="${AWS_PROFILE:-precertly}"
export AWS_REGION="${AWS_REGION:-us-east-2}"
export AWS_DEFAULT_REGION="$AWS_REGION"
export AWS_PAGER=""
readonly REPO_NAME="precertly-api"
readonly STACKS=(precertly-base precertly-app precertly-data precertly-web precertly-auth precertly-live)
# Stacks whose deploy needs the image of HEAD in ECR; `up` pushes it first. precertly-data holds
# the tools task definition and precertly-live the live task definition, which pin the same tag.
readonly IMAGE_STACKS=(precertly-app precertly-data precertly-live)
readonly DATA_STACK="precertly-data"
readonly WEB_STACK="precertly-web"
readonly AUTH_STACK="precertly-auth"
readonly LIVE_STACK="precertly-live"
# What every stack other than precertly-web is given for webDomain and webCertificateArn: the CDK
# app requires both for every stack, but only the web stack uses them. They are also what CI and
# infra/tests/support.py use, and the web stack refuses to deploy with them (a test compares).
readonly PLACEHOLDER_WEB_DOMAIN="site.example.invalid"
readonly PLACEHOLDER_WEB_CERTIFICATE_ARN="arn:aws:acm:us-east-1:000000000000:certificate/00000000-0000-0000-0000-000000000000"
# The Cognito hosted-UI prefix every stack other than precertly-auth is given (the CDK app needs
# it for every stack). Also what CI and infra/tests/support.py use; precertly-auth refuses it.
readonly PLACEHOLDER_AUTH_DOMAIN_PREFIX="placeholder-auth-domain"
# Must match infra/names.py (tests/test_deploy_script.py compares them).
readonly CLUSTER="precertly"
readonly SERVICE="precertly-api"
readonly CONTAINER="api"
readonly TOOLS_FAMILY="precertly-tools"
readonly TOOLS_CONTAINER="tools"
readonly LIVE_SERVICE="precertly-api-live"
readonly LIVE_CONTAINER="api"
readonly API_PORT=8000
# Which ECS service the q_ functions below talk to: the API (default) or the live service.
ACTIVE_SERVICE="$SERVICE"
ACTIVE_CONTAINER="$CONTAINER"
# Polling for `smoke` and `down`; overridable so the tests do not sleep.
POLL_SECONDS="${DEPLOY_POLL_SECONDS:-10}"
WAIT_TIMEOUT="${DEPLOY_WAIT_TIMEOUT:-600}"

usage() {
  cat <<'EOF'
Usage:
  scripts/deploy.sh image [--yes] [--dry-run]
  scripts/deploy.sh stack <name> [--dry-run]
  scripts/deploy.sh up <name> [--yes] [--dry-run]
  scripts/deploy.sh status [--dry-run]
  scripts/deploy.sh smoke [--bedrock] [--dry-run]
  scripts/deploy.sh down [--dry-run]
  scripts/deploy.sh tools migrate|load|check [--yes] [--dry-run]
  scripts/deploy.sh web publish [--yes] [--dry-run]
  scripts/deploy.sh web smoke [--dry-run]
  scripts/deploy.sh live start|stop|status [--dry-run]
  scripts/deploy.sh live smoke [--run CASE_ID] [--yes] [--dry-run]

Commands:
  image        Build the API image for linux/arm64 and push it to ECR as sha-<12 hex of HEAD>.
               Refuses a dirty working tree or a HEAD that is not pushed. Tags are immutable,
               so a tag that already exists is skipped.
  stack <name> Deploy one CDK stack: precertly-base, precertly-app, precertly-data,
               precertly-web, precertly-auth or precertly-live. Needs BUDGET_EMAIL (environment or
               .env.local; never printed). precertly-web also needs WEB_DOMAIN and
               WEB_CERTIFICATE_ARN (same sources), precertly-auth AUTH_DOMAIN_PREFIX and
               WEB_DOMAIN, precertly-live WEB_DOMAIN; each refuses if a value is missing or looks
               like the placeholder. Every other stack is given the placeholders, so base, app
               and data need no configuration. precertly-live also refuses unless precertly-app,
               precertly-data and precertly-auth are complete and the last two have the outputs
               it reads (deploy data from the same commit first).
               Passes --exclusively, so a stack's dependencies are never deployed with it
               (deploy precertly-app before precertly-data yourself).
               Passes -c imageTag=sha-<12 hex of HEAD>; precertly-app, precertly-data and
               precertly-live first check that image is in ECR and refuse if it is not.
               --all is refused; the CDK approval prompt for IAM and security changes stays on.
  up <name>    The usual path: for precertly-app, precertly-data and precertly-live run `image`
               first (precertly-web and precertly-auth have no image step), then show
               `cdk diff --exclusively` (email, certificate ARN and hosted-UI prefix redacted),
               ask "Deploy? [y/N]", then run `stack`. --yes only skips the image confirmation:
               it never skips the diff or the Deploy? question.
  status       Read-only. Stack status, service counts and rollout, task definition revision,
               image tag and health check, the Aurora cluster (status, engine version, minimum
               and maximum ACU; a paused instance still reports available), available NAT
               gateways, RDS instances and ECR images, and one line for the web stack (stack
               state, distribution id, bucket name) and the live service (desired, running).
               A missing resource prints "not deployed".
  smoke        Refuses unless the service has no task. Scales to 1, waits (10 min), waits for the
               ECS Exec agent (2 min), runs /health and /policies inside the task with ECS Exec
               (retrying only "not ready" errors), waits for the ECS health check to be HEALTHY
               (2 min), then ALWAYS scales back to 0 and waits until no task runs, even after
               a failure or Ctrl-C. Needs the AWS session-manager-plugin. --bedrock also runs
               scripts/smoke_llm.py (one real Bedrock call; asks first, and --yes does not
               skip that question).
  down         Set the service to 0 and wait until no task runs.
  tools <verb> Run one precertly-tools task (the API image with a command) in the public subnets
               with the tools security group, all read from the precertly-data stack outputs:
                 migrate  alembic upgrade head                       (10 min limit)
                 load     python -m precertly.ingest evals/bundles --embed   (30 min limit)
                          real Bedrock embedding calls and writes to the database: asks
                          "Continue? [y/N]" first, and --yes does NOT skip that question
                 check    python -m precertly.db.check, read-only    (5 min limit)
               Refuses if a precertly-tools task is already running. On any exit, failure,
               timeout or Ctrl-C it stops every precertly-tools task still running and waits
               until none does, then prints the container exit code and the log tail. The exit
               status is non-zero unless the container exited 0.
  web publish  Build the static site and publish it to the deployed precertly-web stack.
               Refuses a dirty working tree, a HEAD that is not pushed, and a stack that is
               not deployed and complete; reads BucketName, DistributionId and SiteUrl from the
               stack outputs. Builds (uv sync, then in web/: npm ci, npm run prepare-data,
               npm run build, node scripts/check-dist.mjs), and stops before any upload if a
               step fails. Shows what `aws s3 sync --dryrun` would change (read-only), then asks
               "Publish? [y/N]"; --yes does NOT skip that question. Uploads in this order, so a
               visitor never gets a page that points at missing files:
                 1. assets/ (content-hashed names), Cache-Control public,max-age=31536000,immutable,
                    never deleting anything there
                 2. everything else, public,max-age=300, deleting what the build no longer has
                    (assets/ and index.html excluded, so never deleted here)
                 3. index.html, public,max-age=60
               It then invalidates only /index.html /404.html /favicon.svg /data/* and waits
               (at most 5 min) for CloudFront; on timeout the publish is still done.
               To roll back, check out an older commit and run it again (docs/deployment.md).
  web smoke    Read-only check of the published site: 2 describe-stacks reads (the stack must be
               CREATE_COMPLETE or UPDATE_COMPLETE; SiteUrl must be https://<hostname>), then GET
               requests with curl (never --insecure, no cookies, redirects not followed): /, the
               security headers, the first assets/*.js and *.css named in /, four /data/ files,
               /favicon.svg, /nope (404 page) and http:// (redirect to https). One PASS or FAIL
               line per check, a summary, and a non-zero exit if any check failed; a request that
               cannot complete is a failed check, not an abort. Uploads, invalidates and changes
               nothing. Run it after every publish.

  live start   Scale precertly-api-live to 1, wait for the ECS health status HEALTHY, then for the
               gateway to answer GET /health with 200, and print the seconds it took. Refuses
               unless precertly-live is complete. Prints the cost warning: a running task costs
               about 0.36 USD a day until `live stop`. Does not stop the task if a wait times out.
  live stop    Set precertly-api-live to 0 and wait until no task runs.
  live status  Read-only: the auth and live stack states, the live service counts, the API URL,
               the HTTP status of the gateway's GET /health (nothing else of the response), and
               one line saying whether the environment is UP or DOWN.
  live smoke   Refuses unless the live service shows no task and precertly-live is complete;
               needs WEB_DOMAIN. Scales to 1 (a trap ALWAYS scales back to 0 and waits, even
               after a failure or Ctrl-C), measures the cold start to the first gateway 200, then
               checks through the gateway: unauthenticated GET /health is 200; unauthenticated
               POST /live/runs and GET /live/runs/anything are 401; GET /policies is 404; the CORS
               preflight from https://WEB_DOMAIN gets that origin and a different origin gets none.
               With --run CASE_ID it also needs the session-manager-plugin, asks "Continue? About
               0.07 USD of Bedrock and the database wakes up. [y/N]" first (--yes does NOT skip
               it), and makes ONE real run inside the task through ECS Exec (actor deploy-smoke);
               it prints only the final status, reason, timings, tokens and outcome.

Options:
  --yes        Skip the image build confirmation (image, up). tools load, web publish, web
               smoke and live smoke --run ignore it for their own questions; it is refused for
               the other live commands.
  --run CASE_ID  live smoke only: add one real run of that curated case.
  --bedrock    smoke only: add the real Bedrock call.
  --dry-run    Print the commands, with the email and account id redacted, and run nothing
               that touches AWS or Docker. The git working-tree and push checks are skipped.
  -h, --help   Show this help.

Environment:
  AWS_PROFILE  default: precertly (log in first: aws login --profile precertly)
  AWS_REGION   default: us-east-2
  BUDGET_EMAIL address for the budget alerts (stack, up). If unset, it is read from the
               gitignored file .env.local at the repo root: a BUDGET_EMAIL=address line,
               parsed and never sourced.
  WEB_DOMAIN   the site's hostname, for precertly-web, precertly-auth, precertly-live and
               live smoke (same sources as BUDGET_EMAIL).
  AUTH_DOMAIN_PREFIX
               the Cognito hosted-UI prefix, for precertly-auth only (same sources); redacted in
               the output.
  WEB_CERTIFICATE_ARN
               ARN of the ACM certificate in us-east-1, for precertly-web only; redacted in the
               output.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

DRY_RUN=false
ASSUME_YES=false
BEDROCK=false

# In a dry run, print the command instead of running it.
run() {
  if $DRY_RUN; then
    printf '+'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

# Ask a yes/no question on stderr; only "y" or "Y" is a yes (anything else, or no input, is no).
confirm() {
  local reply=""
  printf '%s [y/N] ' "$1" >&2
  read -r reply || reply=""
  [[ -t 0 ]] || printf '\n' >&2
  [[ "$reply" == "y" || "$reply" == "Y" ]]
}

repo_root() {
  git rev-parse --show-toplevel
}

image_tag() {
  printf 'sha-%s' "$(git rev-parse --short=12 HEAD)"
}

validate_stack() {
  local name="$1" s
  for s in "${STACKS[@]}"; do
    if [[ "$name" == "$s" ]]; then
      return 0
    fi
  done
  die "unknown stack '${name}'; expected one of: ${STACKS[*]}"
}

needs_image() {
  local name="$1" s
  for s in "${IMAGE_STACKS[@]}"; do
    if [[ "$name" == "$s" ]]; then
      return 0
    fi
  done
  return 1
}

# Settings the stacks need that are never committed. An unset one is read from the optional,
# gitignored .env.local at the repo root; the environment wins. The file is parsed, never
# sourced: blank lines, # comments and KEY=value lines only, no expansion, and only the keys
# asked for are taken. Error messages name a line number or a key, never a value.
readonly EMAIL_RE='^[^[:space:]@]+@[^[:space:]@]+$'
readonly DOMAIN_LABEL='[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?'
readonly DOMAIN_RE="^${DOMAIN_LABEL}(\\.${DOMAIN_LABEL})+\$"
readonly AUTH_PREFIX_RE='^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$'
readonly CERT_ARN_RE='^arn:aws:acm:us-east-1:[0-9]{12}:certificate/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'

# check_setting KEY VALUE [WHERE]: die if the value is not shaped like a KEY. WHERE (for example
# " in .env.local") is added to the message; the value never is.
check_setting() {
  local key="$1" value="$2" where="${3:-}"
  case "$key" in
    BUDGET_EMAIL) [[ "$value" =~ $EMAIL_RE ]] || die "BUDGET_EMAIL${where} is not an email address" ;;
    WEB_DOMAIN)
      [[ "$value" =~ $DOMAIN_RE && ${#value} -le 253 && ! "${value##*.}" =~ ^[0-9]+$ ]] ||
        die "WEB_DOMAIN${where} is not a plain lowercase hostname (no scheme, port, path or wildcard)"
      ;;
    WEB_CERTIFICATE_ARN)
      [[ "$value" =~ $CERT_ARN_RE ]] ||
        die "WEB_CERTIFICATE_ARN${where} is not an ACM certificate ARN in us-east-1"
      ;;
    AUTH_DOMAIN_PREFIX)
      [[ "$value" =~ $AUTH_PREFIX_RE && "$value" != *aws* && "$value" != *amazon* && "$value" != *cognito* ]] ||
        die "AUTH_DOMAIN_PREFIX${where} is not 3 to 63 lowercase letters, digits and hyphens (no hyphen at either end, none of aws, amazon, cognito)"
      ;;
  esac
}

# load_env_local KEY...: for each KEY that is unset in the environment, take it from .env.local.
load_env_local() {
  local wanted=() key
  for key in "$@"; do
    if [[ -z "${!key:-}" ]]; then
      wanted+=("$key")
    fi
  done
  if [[ ${#wanted[@]} -eq 0 ]]; then
    return 0
  fi
  local file line name value n=0 found
  local assignment_re='^([A-Za-z_][A-Za-z0-9_]*)=(.*)$'
  local dquoted_re='^"(.*)"$' squoted_re="^'(.*)'\$"
  file="$(repo_root)/.env.local"
  if [[ ! -f "$file" ]]; then
    return 0
  fi
  while IFS= read -r line || [[ -n "$line" ]]; do
    n=$((n + 1))
    line="${line%$'\r'}"
    if [[ -z "${line//[[:space:]]/}" || "$line" =~ ^[[:space:]]*# ]]; then
      continue
    fi
    if [[ "$line" =~ $assignment_re ]]; then
      name="${BASH_REMATCH[1]}"
      value="${BASH_REMATCH[2]}"
    else
      die ".env.local line ${n} is not KEY=value"
    fi
    if [[ "$value" =~ $dquoted_re || "$value" =~ $squoted_re ]]; then
      value="${BASH_REMATCH[1]}"
    fi
    # Last assignment wins. (No associative array: the script must run on macOS's bash 3.2.)
    for key in "${wanted[@]}"; do
      if [[ "$name" == "$key" ]]; then
        printf -v "found_${key}" '%s' "$value"
      fi
    done
  done <"$file"
  for key in "${wanted[@]}"; do
    name="found_${key}"
    found="${!name:-}"
    if [[ -z "$found" ]]; then
      continue
    fi
    check_setting "$key" "$found" " in .env.local"
    printf -v "$key" '%s' "$found"
    export "${key?}"
  done
}

require_budget_email() {
  [[ -n "${BUDGET_EMAIL:-}" ]] ||
    die "BUDGET_EMAIL is not set (the address for the budget alerts): export it or put BUDGET_EMAIL=<address> in .env.local"
}

# precertly-web needs a real domain and certificate: refuse when either is missing, malformed or
# one of the placeholders (an all-zero account or certificate id, or a .invalid hostname).
require_web_settings() {
  [[ -n "${WEB_DOMAIN:-}" ]] ||
    die "WEB_DOMAIN is not set (the site's hostname): export it or put WEB_DOMAIN=<hostname> in .env.local"
  [[ -n "${WEB_CERTIFICATE_ARN:-}" ]] ||
    die "WEB_CERTIFICATE_ARN is not set (the ACM certificate in us-east-1): export it or put WEB_CERTIFICATE_ARN=<arn> in .env.local"
  check_setting WEB_DOMAIN "$WEB_DOMAIN"
  check_setting WEB_CERTIFICATE_ARN "$WEB_CERTIFICATE_ARN"
  if [[ "$WEB_DOMAIN" == "$PLACEHOLDER_WEB_DOMAIN" || "$WEB_DOMAIN" == *.invalid ]]; then
    die "WEB_DOMAIN looks like a placeholder; set the real hostname"
  fi
  if [[ "$WEB_CERTIFICATE_ARN" == "$PLACEHOLDER_WEB_CERTIFICATE_ARN" || "$WEB_CERTIFICATE_ARN" == *:000000000000:* ]]; then
    die "WEB_CERTIFICATE_ARN looks like a placeholder; set the real certificate ARN"
  fi
}

# precertly-live needs the real site hostname (the gateway allows exactly that origin); a missing,
# malformed or placeholder value is refused. Also used by precertly-auth (its callback URLs).
require_real_web_domain() {
  [[ -n "${WEB_DOMAIN:-}" ]] ||
    die "WEB_DOMAIN is not set (the site's hostname): export it or put WEB_DOMAIN=<hostname> in .env.local"
  check_setting WEB_DOMAIN "$WEB_DOMAIN"
  if [[ "$WEB_DOMAIN" == "$PLACEHOLDER_WEB_DOMAIN" || "$WEB_DOMAIN" == *.invalid ]]; then
    die "WEB_DOMAIN looks like a placeholder; set the real hostname"
  fi
}

# precertly-auth also needs the real Cognito hosted-UI prefix (never committed, like the others).
require_auth_settings() {
  [[ -n "${AUTH_DOMAIN_PREFIX:-}" ]] ||
    die "AUTH_DOMAIN_PREFIX is not set (the Cognito hosted-UI prefix): export it or put AUTH_DOMAIN_PREFIX=<prefix> in .env.local"
  check_setting AUTH_DOMAIN_PREFIX "$AUTH_DOMAIN_PREFIX"
  if [[ "$AUTH_DOMAIN_PREFIX" == "$PLACEHOLDER_AUTH_DOMAIN_PREFIX" || "$AUTH_DOMAIN_PREFIX" == placeholder-* ]]; then
    die "AUTH_DOMAIN_PREFIX looks like a placeholder; set the real prefix"
  fi
  require_real_web_domain
}

# Set CDK_CONTEXT to the -c arguments every stack needs. Only precertly-web gets the real domain
# and certificate, precertly-auth the real hosted-UI prefix and site domain (its callback URLs),
# precertly-live the real site domain (the gateway's CORS origin); the others get the
# placeholders. With redact=true (a dry run) the address, the certificate ARN and the prefix are
# replaced, as they are in the diff.
build_context() {
  local name="$1" tag="$2" redact="$3" email domain arn prefix
  email="$BUDGET_EMAIL"
  domain="$PLACEHOLDER_WEB_DOMAIN"
  arn="$PLACEHOLDER_WEB_CERTIFICATE_ARN"
  prefix="$PLACEHOLDER_AUTH_DOMAIN_PREFIX"
  if [[ "$name" == "$WEB_STACK" ]]; then
    domain="$WEB_DOMAIN"
    arn="$WEB_CERTIFICATE_ARN"
    if [[ "$redact" == true ]]; then
      arn="REDACTED"
    fi
  elif [[ "$name" == "$AUTH_STACK" ]]; then
    domain="$WEB_DOMAIN"
    prefix="$AUTH_DOMAIN_PREFIX"
    if [[ "$redact" == true ]]; then
      prefix="REDACTED"
    fi
  elif [[ "$name" == "$LIVE_STACK" ]]; then
    domain="$WEB_DOMAIN"
  fi
  if [[ "$redact" == true ]]; then
    email="REDACTED"
  fi
  CDK_CONTEXT=(-c "budgetEmail=${email}" -c "imageTag=${tag}" -c "webDomain=${domain}" -c "webCertificateArn=${arn}" -c "authDomainPrefix=${prefix}")
}

# Print text with the budget address and the certificate ARN replaced, so a CDK diff can be
# shown and pasted.
redact() {
  local text="$1"
  if [[ -n "${BUDGET_EMAIL:-}" ]]; then
    text="${text//"$BUDGET_EMAIL"/REDACTED}"
  fi
  if [[ -n "${WEB_CERTIFICATE_ARN:-}" ]]; then
    text="${text//"$WEB_CERTIFICATE_ARN"/REDACTED}"
  fi
  if [[ -n "${AUTH_DOMAIN_PREFIX:-}" ]]; then
    text="${text//"$AUTH_DOMAIN_PREFIX"/REDACTED}"
  fi
  printf '%s\n' "$text"
}

require_clean_and_pushed() {
  if [[ -n "$(git status --porcelain)" ]]; then
    die "the working tree is not clean; commit or stash first so the tag maps to a real commit"
  fi
  git fetch --quiet origin
  if [[ -z "$(git branch --remotes --contains HEAD)" ]]; then
    die "HEAD is not pushed; push it first so the tag maps to a real commit"
  fi
}

# Exit status 0 if the image tag is in the repository, 1 if it is not. Any other failure
# (no credentials, no repository, no permission) stops the script rather than reading as "not there".
ecr_has_tag() {
  local tag="$1" err
  if err="$(aws ecr describe-images --repository-name "$REPO_NAME" \
    --image-ids "imageTag=${tag}" 2>&1 >/dev/null)"; then
    return 0
  fi
  [[ "$err" == *ImageNotFoundException* ]] || die "could not check for ${tag}: ${err}"
  return 1
}

ecr_login() {
  local host="$1"
  if $DRY_RUN; then
    printf '+ aws ecr get-login-password | docker login --username AWS --password-stdin %q\n' "$host"
  else
    aws ecr get-login-password | docker login --username AWS --password-stdin "$host"
  fi
}

cmd_image() {
  cd "$(repo_root)"
  local tag account registry_host registry
  tag="$(image_tag)"

  if $DRY_RUN; then
    echo "dry-run: skipping the git clean and pushed checks; no AWS or Docker call is made"
    account="ACCOUNT_ID"
  else
    require_clean_and_pushed
    account="$(aws sts get-caller-identity --query Account --output text)" ||
      die "could not read the AWS identity; run: aws login --profile ${AWS_PROFILE}"
    [[ "$account" =~ ^[0-9]{12}$ ]] || die "unexpected account id from STS"
  fi
  registry_host="${account}.dkr.ecr.${AWS_REGION}.amazonaws.com"
  registry="${registry_host}/${REPO_NAME}"

  if $DRY_RUN; then
    echo "tag: ${tag}"
    echo "target: account ACCOUNT_ID, region ${AWS_REGION}, repository ${REPO_NAME}"
    run aws sts get-caller-identity --query Account --output text
  else
    echo "tag: ${tag}"
    echo "target: account ending ${account: -4}, region ${AWS_REGION}, repository ${REPO_NAME}"
    if ! $ASSUME_YES; then
      confirm "Build and push this image?" || die "not confirmed; nothing pushed"
    fi
  fi

  # Tags are immutable: an existing tag is never rebuilt or pushed again.
  if $DRY_RUN; then
    run aws ecr describe-images --repository-name "$REPO_NAME" --image-ids "imageTag=${tag}"
  else
    if ecr_has_tag "$tag"; then
      echo "${tag} already pushed, skipping"
      return 0
    fi
  fi

  ecr_login "$registry_host"
  # Provenance and SBOM off: attestation manifests would show up as untagged images that the
  # lifecycle rule deletes after a day.
  run docker buildx build --platform linux/arm64 --provenance=false --sbom=false \
    -t "${registry}:${tag}" --push .
  run aws ecr describe-images --repository-name "$REPO_NAME" \
    --image-ids "imageTag=${tag}" --query 'imageDetails[0].imageDigest' --output text
}

# The real settings a stack needs on top of the budget address: precertly-web its domain and
# certificate, precertly-auth its hosted-UI prefix and the site domain, precertly-live the site
# domain. The other stacks need nothing (they get placeholders).
require_stack_settings() {
  case "$1" in
    "$WEB_STACK") require_web_settings ;;
    "$AUTH_STACK") require_auth_settings ;;
    "$LIVE_STACK") require_real_web_domain ;;
  esac
}

q_publish_output_count() {
  run aws cloudformation describe-stacks --stack-name "$1" \
    --query "length(Stacks[0].Outputs[?starts_with(OutputKey,'PublishOutput')])" --output text
}

# precertly-live refers to precertly-app, precertly-data and precertly-auth, so it refuses to
# deploy until all three are complete, and until the last two have the outputs it reads (the
# data stack gains them in the same release: deploy it first, from this commit).
LIVE_DEPS_CHECKED=false
require_live_dependencies() {
  local dep
  if $LIVE_DEPS_CHECKED; then
    return 0
  fi
  LIVE_DEPS_CHECKED=true
  if $DRY_RUN; then
    echo "dry-run: ${LIVE_STACK} refuses unless these are CREATE_COMPLETE or UPDATE_COMPLETE, and the last two have the outputs it reads:"
    for dep in precertly-app "$DATA_STACK" "$AUTH_STACK"; do q_stack_status "$dep"; done
    q_publish_output_count "$DATA_STACK"
    q_publish_output_count "$AUTH_STACK"
    return 0
  fi
  for dep in precertly-app "$DATA_STACK" "$AUTH_STACK"; do
    require_stack_complete "$dep" up "$LIVE_STACK"
  done
  for dep in "$DATA_STACK" "$AUTH_STACK"; do
    probe q_publish_output_count "$dep"
    [[ "$AWS_OUT" =~ ^[0-9]+$ && "$AWS_OUT" -ge 1 ]] ||
      die "${dep} has none of the outputs ${LIVE_STACK} reads; deploy it from this commit first: scripts/deploy.sh up ${dep}"
  done
}

cmd_stack() {
  local name="$1"
  validate_stack "$name"
  require_budget_email
  require_stack_settings "$name"

  # The tag of HEAD is passed to every stack because the CDK app is built whole, which requires
  # it. Only precertly-app and precertly-data use it, so only they check the image exists.
  local tag
  tag="$(image_tag)"
  build_context "$name" "$tag" "$DRY_RUN"
  if $DRY_RUN; then
    echo "dry-run: no AWS call is made"
    if needs_image "$name"; then
      echo "dry-run: skipping the check that ${tag} is in ECR"
    fi
  elif needs_image "$name"; then
    ecr_has_tag "$tag" ||
      die "image ${tag} is not in ECR; push it first: scripts/deploy.sh image"
  fi
  if [[ "$name" == "$LIVE_STACK" ]]; then
    require_live_dependencies
  fi
  cd "$(repo_root)/infra"
  # --require-approval is left at the CDK default on purpose. --exclusively: deploying one stack
  # must not also deploy the stacks it depends on (precertly-data depends on precertly-app).
  run npx --no-install cdk deploy --exclusively "$name" "${CDK_CONTEXT[@]}"
}

# image (where the stack needs it), then the diff, then a question that --yes never skips,
# then the same deploy as `stack`.
cmd_up() {
  local name="$1"
  validate_stack "$name"
  require_budget_email
  require_stack_settings "$name"
  local tag
  tag="$(image_tag)"

  # Before the image is built and pushed, so a missing dependency costs nothing.
  if [[ "$name" == "$LIVE_STACK" ]]; then
    require_live_dependencies
  fi
  if needs_image "$name"; then
    cmd_image
  fi

  build_context "$name" "$tag" "$DRY_RUN"
  cd "$(repo_root)/infra"
  if $DRY_RUN; then
    run npx --no-install cdk diff --exclusively "$name" "${CDK_CONTEXT[@]}"
    echo "dry-run: the diff above is always shown, then it asks 'Deploy? [y/N]' (--yes does not skip either)"
  else
    echo "--- cdk diff ${name} (email and certificate ARN redacted) ---"
    local out
    if ! out="$(npx --no-install cdk diff --exclusively "$name" "${CDK_CONTEXT[@]}" 2>&1)"; then
      redact "$out" >&2
      die "cdk diff failed; nothing deployed"
    fi
    redact "$out"
    echo "--- end of diff ---"
    confirm "Deploy?" || die "not confirmed; nothing deployed"
  fi
  cmd_stack "$name"
}

# ---------------------------------------------------------------------------------------------
# AWS queries. Each goes through `run`, so a dry run prints it and a real run executes it.
# The account id appears in none of them.
# ---------------------------------------------------------------------------------------------

q_stack_status() {
  run aws cloudformation describe-stacks --stack-name "$1" \
    --query 'Stacks[0].StackStatus' --output text
}

q_service() {
  run aws ecs describe-services --cluster "$CLUSTER" --services "$ACTIVE_SERVICE" \
    --query 'services[0].[status,desiredCount,runningCount,pendingCount,deployments[0].rolloutState,taskDefinition]' \
    --output text
}

q_task_definition() {
  run aws ecs describe-task-definition --task-definition "$1" \
    --query 'taskDefinition.[revision,containerDefinitions[0].image,containerDefinitions[0].healthCheck.command[0]]' \
    --output text
}

q_nat_gateways() {
  run aws ec2 describe-nat-gateways --filter Name=state,Values=available \
    --query 'length(NatGateways)' --output text
}

q_rds_instances() {
  run aws rds describe-db-instances --query 'length(DBInstances)' --output text
}

q_stack_output() {
  run aws cloudformation describe-stacks --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue | [0]" --output text
}

q_aurora_cluster() {
  run aws rds describe-db-clusters --db-cluster-identifier "$1" \
    --query 'DBClusters[0].[Status,EngineVersion,ServerlessV2ScalingConfiguration.MinCapacity,ServerlessV2ScalingConfiguration.MaxCapacity]' \
    --output text
}

q_ecr_image_count() {
  run aws ecr describe-images --repository-name "$REPO_NAME" \
    --query 'length(imageDetails)' --output text
}

q_ecr_image_tags() {
  run aws ecr describe-images --repository-name "$REPO_NAME" \
    --query 'imageDetails[].imageTags[]' --output text
}

q_set_desired() {
  run aws ecs update-service --cluster "$CLUSTER" --service "$ACTIVE_SERVICE" \
    --desired-count "$1" --query 'service.desiredCount' --output text
}

q_list_tasks() {
  run aws ecs list-tasks --cluster "$CLUSTER" --service-name "$ACTIVE_SERVICE" \
    --desired-status RUNNING --query 'taskArns[0]' --output text
}

q_describe_task() {
  run aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$1" \
    --query 'tasks[0].[lastStatus,healthStatus]' --output text
}

q_exec_agent_status() {
  run aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$1" \
    --query "tasks[0].containers[0].managedAgents[?name=='ExecuteCommandAgent'].lastStatus | [0]" \
    --output text
}

q_exec() {
  run aws ecs execute-command --cluster "$CLUSTER" --task "$1" --container "$ACTIVE_CONTAINER" \
    --interactive --command "$2"
}

# What a probe does when the aws call fails for a reason other than "not there": stop the script,
# or, while cleaning up after a smoke run, return 1 so the summary still prints.
PROBE_FATAL=true
AWS_OUT=""
AWS_ERR=""

# Run one of the q_ functions. Return 0 with stdout in AWS_OUT; return 3 if the resource does
# not exist; any other failure (no credentials, no permission) is an error, not "not deployed".
probe() {
  local err_file rc=0
  err_file="$(mktemp)"
  AWS_OUT="$("$@" 2>"$err_file")" || rc=$?
  AWS_ERR="$(cat "$err_file")"
  rm -f "$err_file"
  if [[ $rc -eq 0 ]]; then
    return 0
  fi
  case "$AWS_ERR" in
    *"does not exist"* | *NotFoundException* | *NotFoundFault* | *ServiceNotActiveException*) return 3 ;;
  esac
  if $PROBE_FATAL; then
    die "aws call failed (${1}): ${AWS_ERR}"
  fi
  return 1
}

SVC_STATUS="" SVC_DESIRED="" SVC_RUNNING="" SVC_PENDING="" SVC_ROLLOUT="" SVC_TASKDEF=""

# Read the service into SVC_*. Return 3 if the cluster or service is missing or not active.
fetch_service() {
  local rc=0
  probe q_service || rc=$?
  if [[ $rc -ne 0 ]]; then
    return "$rc"
  fi
  if [[ -z "$AWS_OUT" || "$AWS_OUT" == "None" ]]; then
    return 3
  fi
  IFS=$'\t' read -r SVC_STATUS SVC_DESIRED SVC_RUNNING SVC_PENDING SVC_ROLLOUT SVC_TASKDEF <<<"$AWS_OUT"
  [[ "$SVC_STATUS" == "ACTIVE" ]] || return 3
}

# Predicates for wait_for.
service_is_up() {
  fetch_service &&
    [[ "$SVC_RUNNING" == "1" && "$SVC_PENDING" == "0" ]] &&
    [[ "$SVC_ROLLOUT" == "COMPLETED" || "$SVC_ROLLOUT" == "None" ]]
}

service_is_down() {
  fetch_service && [[ "$SVC_RUNNING" == "0" && "$SVC_PENDING" == "0" ]]
}

# Poll a predicate until it holds or the given number of seconds pass.
wait_for_seconds() {
  local deadline=$((SECONDS + $1))
  shift
  while :; do
    if "$@"; then
      return 0
    fi
    if ((SECONDS >= deadline)); then
      return 1
    fi
    sleep "$POLL_SECONDS"
  done
}

wait_for() {
  wait_for_seconds "$WAIT_TIMEOUT" "$@"
}

# Set desired count 0 and wait until nothing runs. Retries the update: the console-login
# credentials are short-lived and this is the call that must not be lost.
scale_to_zero() {
  local attempt
  for attempt in 1 2 3; do
    if q_set_desired 0 >/dev/null; then
      break
    fi
    if ((attempt == 3)); then
      return 1
    fi
    sleep "$POLL_SECONDS"
  done
  wait_for service_is_down
}

cmd_status() {
  local s rc

  if $DRY_RUN; then
    echo "dry-run: read-only queries; none is run"
    for s in "${STACKS[@]}"; do q_stack_status "$s"; done
    q_service
    q_task_definition "TASK_DEFINITION"
    use_live_service
    q_service
    use_api_service
    q_stack_output "$DATA_STACK" ClusterIdentifier
    q_aurora_cluster "CLUSTER_ID"
    q_stack_output "$WEB_STACK" DistributionId
    q_stack_output "$WEB_STACK" BucketName
    q_nat_gateways
    q_rds_instances
    q_ecr_image_count
    q_ecr_image_tags
    return 0
  fi

  echo "precertly status (region ${AWS_REGION}, profile ${AWS_PROFILE})"
  echo "stacks"
  for s in "${STACKS[@]}"; do
    rc=0
    probe q_stack_status "$s" || rc=$?
    if [[ $rc -eq 0 ]]; then
      printf '  %-16s %s\n' "$s" "$AWS_OUT"
    else
      printf '  %-16s not deployed\n' "$s"
    fi
  done

  echo "service ${CLUSTER}/${SERVICE}"
  rc=0
  fetch_service || rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "  not deployed"
  else
    printf '  status %s, desired %s, running %s, pending %s, rollout %s\n' \
      "$SVC_STATUS" "$SVC_DESIRED" "$SVC_RUNNING" "$SVC_PENDING" "$SVC_ROLLOUT"
    local revision image health health_text="set"
    rc=0
    probe q_task_definition "$SVC_TASKDEF" || rc=$?
    if [[ $rc -eq 0 ]]; then
      IFS=$'\t' read -r revision image health <<<"$AWS_OUT"
      if [[ "$health" == "None" ]]; then
        health_text="not set"
      fi
      printf '  task definition revision %s, image tag %s, health check %s\n' \
        "$revision" "${image##*:}" "$health_text"
    else
      echo "  task definition: not found"
    fi
  fi

  echo "live service ${CLUSTER}/${LIVE_SERVICE}"
  use_live_service
  rc=0
  fetch_service || rc=$?
  use_api_service
  if [[ $rc -ne 0 ]]; then
    echo "  not deployed"
  else
    printf '  status %s, desired %s, running %s, pending %s, rollout %s\n' \
      "$SVC_STATUS" "$SVC_DESIRED" "$SVC_RUNNING" "$SVC_PENDING" "$SVC_ROLLOUT"
    if [[ "$SVC_RUNNING" != "0" ]]; then
      echo "  a live task is running and bills until: scripts/deploy.sh live stop"
    fi
  fi

  echo "aurora cluster"
  rc=0
  probe q_stack_output "$DATA_STACK" ClusterIdentifier || rc=$?
  local cluster_id="$AWS_OUT"
  if [[ $rc -ne 0 || -z "$cluster_id" || "$cluster_id" == "None" ]]; then
    echo "  not deployed"
  else
    rc=0
    probe q_aurora_cluster "$cluster_id" || rc=$?
    if [[ $rc -ne 0 ]]; then
      echo "  not deployed"
    else
      local db_status db_version db_min db_max
      IFS=$'\t' read -r db_status db_version db_min db_max <<<"$AWS_OUT"
      printf '  status %s, engine version %s, capacity %s to %s ACU\n' \
        "$db_status" "$db_version" "$db_min" "$db_max"
    fi
  fi

  echo "web site (${WEB_STACK})"
  rc=0
  probe q_stack_status "$WEB_STACK" || rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "  not deployed"
  else
    local web_state="$AWS_OUT" distribution_id bucket_name
    probe q_stack_output "$WEB_STACK" DistributionId || AWS_OUT="None"
    distribution_id="$AWS_OUT"
    probe q_stack_output "$WEB_STACK" BucketName || AWS_OUT="None"
    bucket_name="$AWS_OUT"
    printf '  stack %s, distribution %s, bucket %s\n' "$web_state" "$distribution_id" "$bucket_name"
  fi

  probe q_nat_gateways || AWS_OUT="unknown"
  echo "nat gateways available: ${AWS_OUT}"
  probe q_rds_instances || AWS_OUT="unknown"
  echo "rds instances: ${AWS_OUT}"

  rc=0
  probe q_ecr_image_count || rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "ecr ${REPO_NAME}: not deployed"
  else
    local count="$AWS_OUT"
    probe q_ecr_image_tags
    echo "ecr ${REPO_NAME}: ${count} image(s) ${AWS_OUT}"
  fi
}

cmd_down() {
  if $DRY_RUN; then
    echo "dry-run: no AWS call is made"
    q_set_desired 0
    q_service
    return 0
  fi
  local rc=0
  fetch_service || rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "${SERVICE} is not deployed; nothing to scale down"
    return 0
  fi
  echo "scaling ${SERVICE} to 0 (now desired ${SVC_DESIRED}, running ${SVC_RUNNING})"
  scale_to_zero || die "${SERVICE} did not reach 0 running tasks in ${WAIT_TIMEOUT}s; check: scripts/deploy.sh status"
  echo "desired ${SVC_DESIRED}, running ${SVC_RUNNING}"
}

# The command run inside the task: urllib only, because the image has no curl. A non-2xx
# status raises, so the marker line is missing and the check fails.
http_command() {
  local path="$1"
  printf '%s' "python -c \"import urllib.request as r; print('SMOKE_HTTP', '${path}', r.urlopen('http://127.0.0.1:${API_PORT}${path}', timeout=5).status)\""
}
readonly BEDROCK_COMMAND="python scripts/smoke_llm.py"

SMOKE_SUMMARY=""
SMOKE_FAILED=false
SMOKE_SCALED=false

record() {
  SMOKE_SUMMARY+="$(printf '  %-4s %s' "$1" "$2")"$'\n'
  if [[ "$1" == "FAIL" ]]; then
    SMOKE_FAILED=true
  fi
}

# The last lines of a failed exec, indented, so the cause is visible without a log hunt.
show_tail() {
  printf '%s\n' "$1" | tail -n 8 | sed 's/^/    | /' >&2
}

# Run one command in the task; pass if the output has the expected line. Only the errors that
# mean "ECS Exec is not ready yet" are retried (up to 3 attempts, a few seconds apart); any
# other failure, such as a non-200 or a Python error, fails at once. The output tail is shown
# for the final attempt only.
readonly EXEC_NOT_READY_RE="InvalidParameterException|TargetNotConnectedException|agent isn't running"
readonly EXEC_ATTEMPTS=3

smoke_exec_check() {
  local label="$1" arn="$2" command="$3" pattern="$4" out="" attempt
  local retry_seconds=$((POLL_SECONDS < 5 ? POLL_SECONDS : 5))
  for ((attempt = 1; attempt <= EXEC_ATTEMPTS; attempt++)); do
    out="$(q_exec "$arn" "$command" 2>&1)" || true
    if grep -Eq "$pattern" <<<"$out"; then
      record PASS "$label"
      return 0
    fi
    if ((attempt < EXEC_ATTEMPTS)) && grep -Eq "$EXEC_NOT_READY_RE" <<<"$out"; then
      echo "ECS Exec not ready (attempt ${attempt} of ${EXEC_ATTEMPTS}), retrying in ${retry_seconds}s"
      sleep "$retry_seconds"
      continue
    fi
    break
  done
  record FAIL "$label"
  show_tail "$out"
  return 1
}

# Predicates for the task being checked (SMOKE_TASK), polled with wait_for_seconds.
SMOKE_TASK=""
EXEC_AGENT_STATUS="unknown"
TASK_HEALTH="unknown"

exec_agent_is_running() {
  probe q_exec_agent_status "$SMOKE_TASK" || true
  EXEC_AGENT_STATUS="${AWS_OUT:-unknown}"
  [[ "$EXEC_AGENT_STATUS" == "RUNNING" ]]
}

task_is_healthy() {
  probe q_describe_task "$SMOKE_TASK" || true
  IFS=$'\t' read -r _ TASK_HEALTH <<<"$AWS_OUT"
  TASK_HEALTH="${TASK_HEALTH:-unknown}"
  [[ "$TASK_HEALTH" == "HEALTHY" ]]
}

smoke_steps() {
  local arn last health path wait_cap
  # The agent and health waits are capped at 120 s (less if WAIT_TIMEOUT is shorter).
  wait_cap=$((WAIT_TIMEOUT < 120 ? WAIT_TIMEOUT : 120))

  echo "scaling ${SERVICE} to 1"
  if ! q_set_desired 1 >/dev/null; then
    record FAIL "scale to 1"
    return 1
  fi
  if ! wait_for service_is_up; then
    record FAIL "service stable within ${WAIT_TIMEOUT}s (running ${SVC_RUNNING:-unknown}, rollout ${SVC_ROLLOUT:-unknown})"
    return 1
  fi
  record PASS "service stable (desired 1, running 1)"

  probe q_list_tasks || true
  arn="$AWS_OUT"
  if [[ -z "$arn" || "$arn" == "None" ]]; then
    record FAIL "no running task found"
    return 1
  fi
  SMOKE_TASK="$arn"
  probe q_describe_task "$arn" || true
  IFS=$'\t' read -r last health <<<"$AWS_OUT"
  echo "task ${arn##*/}: lastStatus ${last:-unknown}, healthStatus ${health:-unknown}"
  if [[ "${last:-}" != "RUNNING" ]]; then
    record FAIL "task lastStatus ${last:-unknown}, expected RUNNING"
    return 1
  fi
  record PASS "task RUNNING (healthStatus ${health:-unknown})"

  # A RUNNING task is not yet an ECS Exec-ready task: its agent starts a little later.
  if ! wait_for_seconds "$wait_cap" exec_agent_is_running; then
    record FAIL "ECS Exec agent not RUNNING within ${wait_cap}s (last status: ${EXEC_AGENT_STATUS})"
    return 1
  fi
  record PASS "ECS Exec agent running"

  for path in /health /policies; do
    smoke_exec_check "GET ${path} returned 200 (ECS Exec, urllib)" "$arn" \
      "$(http_command "$path")" "^SMOKE_HTTP ${path} 200[[:space:]]*\$" || return 1
  done

  if $BEDROCK; then
    smoke_exec_check "one real Bedrock call (scripts/smoke_llm.py)" "$arn" \
      "$BEDROCK_COMMAND" '^tokens in/out: [0-9]+/[0-9]+' || return 1
  fi

  # Last, so a slow health-check start period cannot hide an application failure above.
  if ! wait_for_seconds "$wait_cap" task_is_healthy; then
    record FAIL "ECS health check HEALTHY within ${wait_cap}s (last status: ${TASK_HEALTH})"
    return 1
  fi
  record PASS "ECS health check HEALTHY"
}

# EXIT trap of `smoke` and `live smoke`. Runs after success, a failed check, a die or Ctrl-C/TERM,
# and always tries to leave the service at zero tasks before printing the summary.
SMOKE_TITLE="smoke"
SMOKE_DOWN_COMMAND="down"
GW_TMP=""
smoke_finish() {
  local rc=$?
  trap - EXIT
  trap '' INT TERM
  set +e
  PROBE_FATAL=false

  if $SMOKE_SCALED; then
    echo "scaling ${ACTIVE_SERVICE} back to 0"
    if scale_to_zero; then
      record PASS "scaled back to 0"
    else
      record FAIL "scale back to 0 not confirmed: run scripts/deploy.sh ${SMOKE_DOWN_COMMAND} NOW (a task bills while it runs)"
    fi
    fetch_service
  fi
  if [[ -n "$GW_TMP" ]]; then
    rm -rf "$GW_TMP"
  fi

  echo
  echo "${SMOKE_TITLE} summary"
  printf '%s' "$SMOKE_SUMMARY"
  if $SMOKE_SCALED; then
    echo "final counts: desired ${SVC_DESIRED:-unknown}, running ${SVC_RUNNING:-unknown}, pending ${SVC_PENDING:-unknown}"
  fi
  if [[ $rc -ne 0 || "$SMOKE_FAILED" == "true" ]]; then
    echo "result: FAIL"
    exit "$((rc != 0 ? rc : 1))"
  fi
  echo "result: PASS"
  exit 0
}

cmd_smoke() {
  if $DRY_RUN; then
    echo "dry-run: no AWS call is made. Order of a real run:"
    q_service
    q_set_desired 1
    q_service
    q_list_tasks
    q_describe_task "TASK_ARN"
    q_exec_agent_status "TASK_ARN"
    q_exec "TASK_ARN" "$(http_command /health)"
    q_exec "TASK_ARN" "$(http_command /policies)"
    if $BEDROCK; then
      echo "dry-run: would ask 'One real Bedrock call, costs a fraction of a cent. Continue? [y/N]'"
      q_exec "TASK_ARN" "$BEDROCK_COMMAND"
    fi
    q_describe_task "TASK_ARN"
    echo "dry-run: on exit, interrupt or failure, always:"
    q_set_desired 0
    q_service
    return 0
  fi

  command -v session-manager-plugin >/dev/null 2>&1 ||
    die "session-manager-plugin is not installed (ECS Exec needs it): https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html"

  # Ask before anything starts, so no task bills while a question waits.
  if $BEDROCK; then
    confirm "One real Bedrock call, costs a fraction of a cent. Continue?" ||
      die "not confirmed; nothing started"
  fi

  local rc=0
  fetch_service || rc=$?
  if [[ $rc -ne 0 ]]; then
    die "${CLUSTER}/${SERVICE} is not deployed; deploy it first: scripts/deploy.sh up precertly-app"
  fi
  if [[ "$SVC_RUNNING" != "0" || "$SVC_PENDING" != "0" || "$SVC_DESIRED" != "0" ]]; then
    die "refusing: the service is not at zero (desired ${SVC_DESIRED}, running ${SVC_RUNNING}, pending ${SVC_PENDING}); something else may be using it. Check with status; to stop it: scripts/deploy.sh down"
  fi
  echo "service at zero; starting one task"

  # From here on every exit path scales back to 0 before the summary.
  trap smoke_finish EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  SMOKE_SCALED=true
  smoke_steps || true
}

# ---------------------------------------------------------------------------------------------
# tools: one precertly-tools task (the API image with a command), run and always cleaned up
# ---------------------------------------------------------------------------------------------

q_tools_running() {
  run aws ecs list-tasks --cluster "$CLUSTER" --family "$TOOLS_FAMILY" \
    --desired-status RUNNING --query 'taskArns[]' --output text
}

# $1 verb, $2 subnets (comma separated), $3 security group, $4 overrides JSON.
q_tools_run() {
  run aws ecs run-task --cluster "$CLUSTER" --task-definition "$TOOLS_FAMILY" \
    --launch-type FARGATE --count 1 --started-by "deploy.sh-tools-$1" \
    --network-configuration "awsvpcConfiguration={subnets=[$2],securityGroups=[$3],assignPublicIp=ENABLED}" \
    --overrides "$4" --query '[tasks[0].taskArn,failures[0].reason]' --output text
}

q_tools_describe() {
  run aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$1" \
    --query 'tasks[0].[lastStatus,containers[0].exitCode,stoppedReason]' --output text
}

q_tools_stop() {
  run aws ecs stop-task --cluster "$CLUSTER" --task "$1" \
    --reason "deploy.sh tools cleanup" --query 'task.lastStatus' --output text
}

q_tools_logs() {
  run aws logs get-log-events --log-group-name "$1" --log-stream-name "$2" --limit 40 \
    --query 'events[].message' --output text
}

TOOLS_COMMAND_JSON="" TOOLS_DEFAULT_TIMEOUT=0
TOOLS_STARTED=false TOOLS_TASK="" TOOLS_LOG_GROUP_NAME="" TOOLS_VERB=""
TOOLS_LAST="" TOOLS_EXIT="" TOOLS_REASON=""

# The command (a JSON array) and the time limit in seconds for each verb.
tools_settings() {
  case "$1" in
    migrate)
      TOOLS_COMMAND_JSON='["alembic","upgrade","head"]'
      TOOLS_DEFAULT_TIMEOUT=600
      ;;
    load)
      TOOLS_COMMAND_JSON='["python","-m","precertly.ingest","evals/bundles","--embed"]'
      TOOLS_DEFAULT_TIMEOUT=1800
      ;;
    check)
      TOOLS_COMMAND_JSON='["python","-m","precertly.db.check"]'
      TOOLS_DEFAULT_TIMEOUT=300
      ;;
    *) die "usage: deploy.sh tools migrate|load|check" ;;
  esac
}

tools_overrides() {
  printf '{"containerOverrides":[{"name":"%s","command":%s}]}' "$TOOLS_CONTAINER" "$TOOLS_COMMAND_JSON"
}

# Predicate: the task is STOPPED. Also reads its exit code and stop reason.
tools_task_stopped() {
  probe q_tools_describe "$TOOLS_TASK" || return 1
  IFS=$'\t' read -r TOOLS_LAST TOOLS_EXIT TOOLS_REASON <<<"$AWS_OUT"
  [[ "$TOOLS_LAST" == "STOPPED" ]]
}

# Predicate: no precertly-tools task is running.
tools_none_running() {
  probe q_tools_running || return 1
  [[ -z "$AWS_OUT" || "$AWS_OUT" == "None" ]]
}

# Stop every precertly-tools task that still runs and wait until none does. `tools` refuses to
# start while one runs, so any that runs now is ours, including one whose run-task call returned
# just as a signal arrived. If the listing fails, fall back to the task we know. Returns 1 if
# it could not be confirmed.
tools_stop_all() {
  local arns="" arn attempt
  local -a arn_list=()
  if probe q_tools_running; then
    arns="$AWS_OUT"
  else
    arns="$TOOLS_TASK"
  fi
  if [[ "$arns" != "None" && -n "$arns" ]]; then
    read -r -a arn_list <<<"$arns"
  fi
  # ${arr[@]+...}: bash 3.2 (macOS) treats an empty array as unset under set -u.
  for arn in ${arn_list[@]+"${arn_list[@]}"}; do
    for attempt in 1 2 3; do
      if probe q_tools_stop "$arn"; then
        break
      fi
      ((attempt == 3)) || sleep "$POLL_SECONDS"
    done
  done
  wait_for_seconds "$(tools_wait_cap)" tools_none_running
}

tools_wait_cap() {
  printf '%s' "$((WAIT_TIMEOUT < 120 ? WAIT_TIMEOUT : 120))"
}

# The last lines the task logged, to see why it failed or what a check found.
tools_log_tail() {
  local stream="tools/${TOOLS_CONTAINER}/${TOOLS_TASK##*/}" attempt text=""
  for attempt in 1 2; do
    if probe q_tools_logs "$TOOLS_LOG_GROUP_NAME" "$stream" && [[ -n "$AWS_OUT" && "$AWS_OUT" != "None" ]]; then
      text="$AWS_OUT"
      break
    fi
    # CloudWatch Logs can lag a few seconds behind the task.
    ((attempt == 2)) || sleep "$((POLL_SECONDS < 5 ? POLL_SECONDS : 5))"
  done
  if [[ -z "$text" ]]; then
    echo "  (no log output found in ${TOOLS_LOG_GROUP_NAME})"
  else
    printf '%s\n' "$text" | tr '\t' '\n' | tail -n 40 | sed 's/^/  | /'
  fi
}

# EXIT trap of `tools`: after success, a failed task, a timeout, a die or Ctrl-C/TERM, it stops
# whatever still runs, waits for it, then prints the summary, the exit code and the log tail.
tools_finish() {
  local rc=$?
  trap - EXIT
  trap '' INT TERM
  set +e
  PROBE_FATAL=false

  if $TOOLS_STARTED; then
    echo "stopping any running ${TOOLS_FAMILY} task"
    if tools_stop_all; then
      record PASS "no ${TOOLS_FAMILY} task running"
    else
      record FAIL "stop not confirmed: run aws ecs list-tasks --cluster ${CLUSTER} --family ${TOOLS_FAMILY} and stop it NOW (a task bills while it runs)"
    fi
    if [[ -n "$TOOLS_TASK" ]]; then
      tools_task_stopped
    fi
  fi

  echo
  echo "tools ${TOOLS_VERB} summary"
  printf '%s' "$SMOKE_SUMMARY"
  if [[ -n "$TOOLS_TASK" ]]; then
    echo "task ${TOOLS_TASK##*/}: ${TOOLS_LAST:-unknown}, container exit code ${TOOLS_EXIT:-unknown}"
    [[ "$TOOLS_REASON" != "None" && -n "$TOOLS_REASON" ]] && echo "stopped reason: ${TOOLS_REASON}"
    echo "--- log tail (${TOOLS_LOG_GROUP_NAME}) ---"
    tools_log_tail
    echo "--- end of log tail ---"
  fi
  if [[ -z "$TOOLS_TASK" || "$TOOLS_LAST" != "STOPPED" || "$TOOLS_EXIT" != "0" ]]; then
    SMOKE_FAILED=true
  fi
  if [[ $rc -ne 0 || "$SMOKE_FAILED" == "true" ]]; then
    echo "result: FAIL"
    exit "$((rc != 0 ? rc : 1))"
  fi
  echo "result: PASS"
  exit 0
}

tools_steps() {
  local timeout="$1" overrides="$2" subnets="$3" group="$4" arn failure

  # Before run-task returns, a signal would leave nothing to stop; the cleanup lists the family
  # anyway, so it is armed first.
  TOOLS_STARTED=true
  probe q_tools_run "$TOOLS_VERB" "$subnets" "$group" "$overrides" || true
  IFS=$'\t' read -r arn failure <<<"$AWS_OUT"
  if [[ -z "$arn" || "$arn" == "None" ]]; then
    record FAIL "run-task started nothing (${failure:-no reason given})"
    return 1
  fi
  TOOLS_TASK="$arn"
  echo "started ${TOOLS_FAMILY} task ${arn##*/}: ${TOOLS_VERB}"

  if ! wait_for_seconds "$timeout" tools_task_stopped; then
    record FAIL "task still ${TOOLS_LAST:-unknown} after ${timeout}s (limit for ${TOOLS_VERB}); stopping it"
    return 1
  fi
  if [[ "$TOOLS_EXIT" == "0" ]]; then
    record PASS "${TOOLS_VERB}: container exited 0"
  else
    record FAIL "${TOOLS_VERB}: container exit code ${TOOLS_EXIT:-unknown}"
    return 1
  fi
}

# Read one output of a stack. Dies, with a pointer, if the stack or the output is missing or the
# value does not look like what it should.
stack_output() {
  local stack="$1" key="$2" pattern="$3" rc=0
  probe q_stack_output "$stack" "$key" || rc=$?
  if [[ $rc -ne 0 || -z "$AWS_OUT" || "$AWS_OUT" == "None" ]]; then
    die "${stack} is not deployed (no output ${key}); deploy it first: scripts/deploy.sh up ${stack}"
  fi
  [[ "$AWS_OUT" =~ $pattern ]] || die "output ${key} of ${stack} has an unexpected value"
  printf '%s' "$AWS_OUT"
}

data_output() {
  stack_output "$DATA_STACK" "$1" "$2"
}

cmd_tools() {
  TOOLS_VERB="$1"
  tools_settings "$TOOLS_VERB"
  local timeout="${DEPLOY_TOOLS_TIMEOUT:-$TOOLS_DEFAULT_TIMEOUT}"
  local overrides
  overrides="$(tools_overrides)"

  if $DRY_RUN; then
    echo "dry-run: no AWS call is made. Order of a real run:"
    if [[ "$TOOLS_VERB" == "load" ]]; then
      echo "dry-run: would ask 'Continue? [y/N]' first (--yes does not skip it)"
    fi
    q_stack_output "$DATA_STACK" ToolsSubnetIds
    q_stack_output "$DATA_STACK" ToolsSecurityGroupId
    q_stack_output "$DATA_STACK" ToolsLogGroupName
    q_tools_running
    echo "dry-run: refuses here if the line above lists a task"
    q_tools_run "$TOOLS_VERB" "SUBNET_IDS" "SECURITY_GROUP_ID" "$overrides"
    echo "dry-run: waits up to ${timeout}s for STOPPED, polling:"
    q_tools_describe "TASK_ARN"
    echo "dry-run: on exit, interrupt, timeout or failure, always:"
    q_tools_running
    q_tools_stop "TASK_ARN"
    q_tools_running
    q_tools_logs "LOG_GROUP" "tools/${TOOLS_CONTAINER}/TASK_ID"
    return 0
  fi

  # Ask before anything starts, so nothing bills while a question waits.
  if [[ "$TOOLS_VERB" == "load" ]]; then
    confirm "This makes real Bedrock embedding calls and writes to the database. Continue?" ||
      die "not confirmed; nothing started"
  fi

  local subnets group
  subnets="$(data_output ToolsSubnetIds '^subnet-[0-9a-f]+(,subnet-[0-9a-f]+)*$')"
  group="$(data_output ToolsSecurityGroupId '^sg-[0-9a-f]+$')"
  TOOLS_LOG_GROUP_NAME="$(data_output ToolsLogGroupName '^/[A-Za-z0-9/_.-]+$')"

  probe q_tools_running
  if [[ -n "$AWS_OUT" && "$AWS_OUT" != "None" ]]; then
    die "refusing: a ${TOOLS_FAMILY} task is already running (${AWS_OUT##*/}); it may be another run. Stop it: aws ecs stop-task --cluster ${CLUSTER} --task <task arn>"
  fi

  trap tools_finish EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  tools_steps "$timeout" "$overrides" "$subnets" "$group" || true
}

# ---------------------------------------------------------------------------------------------
# web publish: build web/dist and copy it to the precertly-web bucket.
# ---------------------------------------------------------------------------------------------

# Relative to the repo root, where cmd_web_publish runs.
readonly WEB_DIST="web/dist"
# Hashed assets never change under their name; the rest is short-lived; index.html is the entry
# point that names the hashed assets, so it is the shortest (CloudFront also honors these: the
# cache policy follows the origin's Cache-Control).
readonly ASSETS_CACHE_CONTROL="public,max-age=31536000,immutable"
readonly PAGES_CACHE_CONTROL="public,max-age=300"
readonly INDEX_CACHE_CONTROL="public,max-age=60"
# Only the files whose names are not content-hashed. /index.html is also the default root object.
readonly INVALIDATION_PATHS=(/index.html /404.html /favicon.svg "/data/*")

# The wait for CloudFront is capped at 5 minutes (less if WAIT_TIMEOUT is shorter).
invalidation_wait() {
  printf '%s' "$((WAIT_TIMEOUT < 300 ? WAIT_TIMEOUT : 300))"
}

# Like `run`, from a subdirectory of the repo.
run_in() {
  local dir="$1"
  shift
  if $DRY_RUN; then
    printf '+ (cd %q &&' "$dir"
    printf ' %q' "$@"
    printf ')\n'
  else
    (cd "$dir" && "$@")
  fi
}

# build_step LABEL COMMAND...: stop the whole publish, before any upload, if a step fails.
build_step() {
  local label="$1"
  shift
  echo "==> ${label}"
  "$@" || die "build step failed: ${label}; nothing was uploaded"
}

# The sync and copy commands. $1 is the bucket; $2, if given, is --dryrun. The same functions
# serve the preview and the upload, so the preview is the upload minus the writes.
s3_sync_assets() {
  run aws s3 sync "${WEB_DIST}/assets" "s3://$1/assets" --cache-control "$ASSETS_CACHE_CONTROL" ${2:+"$2"}
}

# Never --delete under assets/: a page cached by a browser may still name an older hashed file.
# index.html is excluded so it is neither uploaded here nor deleted.
s3_sync_rest() {
  run aws s3 sync "$WEB_DIST" "s3://$1" --exclude "assets/*" --exclude "index.html" --delete \
    --cache-control "$PAGES_CACHE_CONTROL" ${2:+"$2"}
}

s3_copy_index() {
  run aws s3 cp "${WEB_DIST}/index.html" "s3://$1/index.html" --cache-control "$INDEX_CACHE_CONTROL"
}

q_invalidate() {
  run aws cloudfront create-invalidation --distribution-id "$1" --paths "${INVALIDATION_PATHS[@]}" \
    --query Invalidation.Id --output text
}

q_invalidation_status() {
  run aws cloudfront get-invalidation --distribution-id "$1" --id "$2" \
    --query Invalidation.Status --output text
}

q_content_type() {
  run aws s3api head-object --bucket "$1" --key "$2" --query ContentType --output text
}

# count_lines PREFIX TEXT: how many lines of TEXT start with PREFIX.
count_lines() {
  local n=0 line
  while IFS= read -r line; do
    if [[ "$line" == "$1"* ]]; then
      n=$((n + 1))
    fi
  done <<<"$2"
  printf '%s' "$n"
}

# The first file under web/dist/DIR with extension EXT, or nothing.
first_dist_file() {
  find "${WEB_DIST}/$1" -type f -name "*.$2" 2>/dev/null | LC_ALL=C sort | sed -n '1p'
}

# check_content_type BUCKET KEY EXPECTED_REGEX: the type S3 holds for KEY must match. The AWS CLI
# picks a content type from the file extension with Python's mimetypes (the system's mime.types
# can override the built-in table), so this reads it back instead of assuming.
check_content_type() {
  local bucket="$1" key="$2" expected="$3" rc=0 actual
  probe q_content_type "$bucket" "$key" || rc=$?
  actual="$AWS_OUT"
  [[ $rc -eq 0 ]] || die "could not read the content type of ${key}: ${AWS_ERR:-not found}"
  [[ "$actual" =~ $expected ]] ||
    die "${key} was uploaded with content type '${actual}', expected ${expected}; the AWS CLI guesses it from the extension (Python mimetypes, which reads the system's mime.types). Fix that and run web publish again. CloudFront was not invalidated."
}

# After the uploads: one file of each kind the site serves.
verify_content_types() {
  local bucket="$1" key
  check_content_type "$bucket" index.html '^text/html'
  check_content_type "$bucket" 404.html '^text/html'
  check_content_type "$bucket" favicon.svg '^image/svg\+xml'
  key="$(first_dist_file assets js)"
  check_content_type "$bucket" "${key#"${WEB_DIST}"/}" '^(text|application)/javascript'
  key="$(first_dist_file assets css)"
  if [[ -n "$key" ]]; then
    check_content_type "$bucket" "${key#"${WEB_DIST}"/}" '^text/css'
  fi
  key="$(first_dist_file data json)"
  check_content_type "$bucket" "${key#"${WEB_DIST}"/}" '^application/json'
}

# True once CloudFront reports the invalidation Completed. A failed query is "not yet": the
# publish is already done, so the wait must not turn into a fatal error.
invalidation_is_done() {
  local fatal="$PROBE_FATAL" rc=0
  PROBE_FATAL=false
  probe q_invalidation_status "$1" "$2" || rc=$?
  PROBE_FATAL="$fatal"
  [[ $rc -eq 0 && "$AWS_OUT" == "Completed" ]]
}

# The build, in order. Any failure stops before the first aws call that writes.
web_build() {
  build_step "uv sync" run uv sync
  build_step "npm ci" run_in web npm ci
  build_step "npm run prepare-data" run_in web npm run prepare-data
  build_step "npm run build" run_in web npm run build
  build_step "check-dist" run_in web node scripts/check-dist.mjs
}

# The build must have produced a site, and must not have touched tracked files (what is published
# has to match the commit that was checked at the start).
web_check_build() {
  local f
  for f in index.html 404.html favicon.svg; do
    [[ -f "${WEB_DIST}/${f}" ]] || die "the build did not produce ${WEB_DIST}/${f}; nothing was uploaded"
  done
  [[ -n "$(first_dist_file assets js)" && -n "$(first_dist_file data json)" ]] ||
    die "${WEB_DIST} has no assets/*.js or data/*.json; nothing was uploaded"
  [[ -z "$(git status --porcelain)" ]] ||
    die "the build changed tracked files; the site would not match the commit. Nothing was uploaded."
}

# Show what the two syncs would do, read-only, and set WEB_FILE_COUNT.
web_preview() {
  local bucket="$1" assets rest n_assets n_rest_up n_rest_del
  assets="$(s3_sync_assets "$bucket" --dryrun)" || die "aws s3 sync --dryrun failed (assets/); nothing was uploaded"
  rest="$(s3_sync_rest "$bucket" --dryrun)" || die "aws s3 sync --dryrun failed (rest of the site); nothing was uploaded"
  n_assets="$(count_lines "(dryrun) upload:" "$assets")"
  n_rest_up="$(count_lines "(dryrun) upload:" "$rest")"
  n_rest_del="$(count_lines "(dryrun) delete:" "$rest")"
  echo "--- what would change (aws s3 sync --dryrun; a rebuild rewrites every file time, so unchanged files count as uploads) ---"
  echo "assets/        ${n_assets} to upload, nothing is ever deleted here"
  echo "everything else ${n_rest_up} to upload, ${n_rest_del} to delete, then index.html last"
  if [[ "$n_rest_del" -gt 0 ]]; then
    echo "to delete:"
    printf '%s\n' "$rest" | sed -n 's/^(dryrun) delete: /  /p' | sed -n '1,50p'
  fi
  echo "--- end ---"
}

# Die unless the stack $1 exists and is CREATE_COMPLETE or UPDATE_COMPLETE. The rest names the
# command to run again, for the message.
require_stack_complete() {
  local stack="$1" rc=0 state
  shift
  probe q_stack_status "$stack" || rc=$?
  state="$AWS_OUT"
  [[ $rc -ne 3 ]] || die "${stack} is not deployed; deploy it first: scripts/deploy.sh up ${stack}"
  [[ "$state" == "CREATE_COMPLETE" || "$state" == "UPDATE_COMPLETE" ]] ||
    die "${stack} is ${state}, not CREATE_COMPLETE or UPDATE_COMPLETE; wait for it or fix it, then run $* again"
}

require_web_stack_complete() {
  require_stack_complete "$WEB_STACK" "$@"
}

cmd_web_publish() {
  cd "$(repo_root)"
  local sha bucket dist_id site_url
  sha="$(git rev-parse --short=12 HEAD)"

  if $DRY_RUN; then
    echo "dry-run: skipping the git clean and pushed checks; no AWS, npm or uv call is made"
    echo "commit: ${sha}"
    echo "dry-run: refuses unless the stack is CREATE_COMPLETE or UPDATE_COMPLETE; reads the outputs:"
    q_stack_status "$WEB_STACK"
    q_stack_output "$WEB_STACK" BucketName
    q_stack_output "$WEB_STACK" DistributionId
    q_stack_output "$WEB_STACK" SiteUrl
    bucket="BUCKET_NAME"
    dist_id="DISTRIBUTION_ID"
    echo "dry-run: build; any failure stops before the first upload:"
    web_build
    echo "dry-run: preview (read-only), then it asks 'Publish? [y/N]' (--yes does not skip it):"
    s3_sync_assets "$bucket" --dryrun
    s3_sync_rest "$bucket" --dryrun
    echo "dry-run: upload, in this order:"
    s3_sync_assets "$bucket"
    s3_sync_rest "$bucket"
    s3_copy_index "$bucket"
    echo "dry-run: read back the content type of one file of each kind:"
    q_content_type "$bucket" "KEY"
    echo "dry-run: invalidate, then wait up to $(invalidation_wait)s for it to complete:"
    q_invalidate "$dist_id"
    q_invalidation_status "$dist_id" "INVALIDATION_ID"
    return 0
  fi

  require_clean_and_pushed
  echo "commit: ${sha}"

  require_web_stack_complete web publish
  bucket="$(stack_output "$WEB_STACK" BucketName '^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$')"
  dist_id="$(stack_output "$WEB_STACK" DistributionId '^[A-Z0-9]+$')"
  site_url="$(stack_output "$WEB_STACK" SiteUrl '^https://[a-z0-9.-]+$')"

  web_build
  web_check_build
  local file_count
  file_count="$(find "$WEB_DIST" -type f | wc -l | tr -d '[:space:]')"
  web_preview "$bucket"
  confirm "Publish?" || die "not confirmed; nothing uploaded"

  s3_sync_assets "$bucket" ||
    die "uploading assets/ failed; index.html was not replaced, so visitors still get the previous page. Run web publish again."
  s3_sync_rest "$bucket" ||
    die "uploading the rest of the site failed; index.html was not replaced. Run web publish again."
  s3_copy_index "$bucket" ||
    die "uploading index.html failed; the rest of the site is already uploaded. Run web publish again."
  verify_content_types "$bucket"

  local inv_id
  inv_id="$(q_invalidate "$dist_id")" ||
    die "the files are uploaded, but the CloudFront invalidation could not be created; visitors may see the old pages for up to 5 minutes (index.html: 1 minute). Run web publish again to retry it."
  echo "invalidation ${inv_id} created for ${INVALIDATION_PATHS[*]}"
  if ! wait_for_seconds "$(invalidation_wait)" invalidation_is_done "$dist_id" "$inv_id"; then
    echo "warning: invalidation ${inv_id} had not completed after $(invalidation_wait)s (last status: ${AWS_OUT:-unknown}). The publish is done; CloudFront finishes it on its own. Check: aws cloudfront get-invalidation --distribution-id ${dist_id} --id ${inv_id}" >&2
  fi

  echo "published commit ${sha}"
  echo "files in the build: ${file_count}"
  echo "site: ${site_url}"
}

# ---------------------------------------------------------------------------------------------
# web smoke: read-only checks of the published site, over HTTPS, with curl.
# The only AWS calls are two describe-stacks reads; nothing is uploaded, invalidated or changed,
# and no request carries a cache-busting query string.
# ---------------------------------------------------------------------------------------------

readonly SMOKE_CURL_TIMEOUT=20
# What a dry run prints for the hostname, which it does not read.
readonly DRY_RUN_HOST="SITE_HOST"
# What infra/web_stack.py (the response headers policy) and web/public/404.html promise.
readonly HSTS_MAX_AGE_SECONDS=31536000
readonly NOT_FOUND_TEXT="This page does not exist"
# CloudFront answers a plain-HTTP GET with a 301 (viewer protocol policy redirect-to-https); the
# others are accepted because any of them is a redirect that keeps the method.
readonly REDIRECT_STATUSES=(301 302 307 308)

WEB_SMOKE_HOST=""
WEB_SMOKE_TMP=""
WEB_SMOKE_COUNT=0
WEB_SMOKE_TLS_DONE=false
WEB_SMOKE_PASSED=0
WEB_SMOKE_FAILED=0
WEB_SMOKE_FAILURES=""
ROOT_BODY=""
# The last response, set by fetch.
FETCH_STATUS=""
FETCH_VERIFY=""
FETCH_HEADERS=""
FETCH_BODY=""
FETCH_SHOWN=true

web_smoke_cleanup() {
  if [[ -n "$WEB_SMOKE_TMP" ]]; then
    rm -rf "$WEB_SMOKE_TMP"
  fi
}

# Text from the network made safe to print: control characters, then anything that looks like an
# account id, a distribution id or a bucket name, and the settings redact() knows, are replaced.
printable() {
  local text
  text="$(printf '%s' "$1" | LC_ALL=C tr -c '[:print:]' ' ')"
  redact "$text" | sed -E \
    -e 's/[0-9]{12}/REDACTED/g' \
    -e 's/E[A-Z0-9]{12,13}/REDACTED/g' \
    -e 's/precertly-web-[a-z0-9-]+/REDACTED/g'
}

lowercase() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

# PASS WHAT [DETAIL]
smoke_pass() {
  WEB_SMOKE_PASSED=$((WEB_SMOKE_PASSED + 1))
  if [[ -n "${2:-}" ]]; then
    printf 'PASS %s (%s)\n' "$1" "$(printable "${2:0:100}")"
  else
    printf 'PASS %s\n' "$1"
  fi
}

# FAIL WHAT EXPECTED GOT. After the first failure on a response, its first 200 characters follow
# (once per response); a body is never printed otherwise.
smoke_fail() {
  local line
  line="$1: expected $2, got $(printable "${3:0:200}")"
  WEB_SMOKE_FAILED=$((WEB_SMOKE_FAILED + 1))
  WEB_SMOKE_FAILURES+="  ${line}"$'\n'
  printf 'FAIL %s\n' "$line"
  if ! $FETCH_SHOWN; then
    FETCH_SHOWN=true
    if [[ -s "$FETCH_BODY" ]]; then
      printf '     response body starts: %s\n' "$(printable "$(head -c 200 "$FETCH_BODY")")"
    else
      printf '     response body: empty\n'
    fi
  fi
}

# fetch WHAT URL ASSERTS: one GET. Returns 0 with the response in FETCH_*. A dry run prints the
# request and what it asserts and returns 1; so does a request curl could not complete, after
# recording it as a failed check: a network error never aborts the run.
fetch() {
  local what="$1" url="$2" asserts="$3" scheme="${2%%://*}" rc=0 out="" err=""
  if $DRY_RUN; then
    printf 'GET %s\n    asserts: %s\n' "$url" "$asserts"
    return 1
  fi
  WEB_SMOKE_COUNT=$((WEB_SMOKE_COUNT + 1))
  FETCH_HEADERS="${WEB_SMOKE_TMP}/headers.${WEB_SMOKE_COUNT}"
  FETCH_BODY="${WEB_SMOKE_TMP}/body.${WEB_SMOKE_COUNT}"
  : >"$FETCH_HEADERS"
  : >"$FETCH_BODY"
  FETCH_STATUS=""
  FETCH_VERIFY=""
  FETCH_SHOWN=true
  # --disable (first) ignores ~/.curlrc; no --insecure, no cookies, no --location.
  out="$(curl --disable --silent --show-error --max-time "$SMOKE_CURL_TIMEOUT" --proto "=${scheme}" \
    --dump-header "$FETCH_HEADERS" --output "$FETCH_BODY" \
    --write-out '%{http_code} %{ssl_verify_result}' "$url" 2>"${WEB_SMOKE_TMP}/stderr")" || rc=$?
  err="$(head -c 200 "${WEB_SMOKE_TMP}/stderr")"
  read -r FETCH_STATUS FETCH_VERIFY <<<"$out" || true

  if [[ "$scheme" == "https" ]] && ! $WEB_SMOKE_TLS_DONE; then
    WEB_SMOKE_TLS_DONE=true
    if [[ $rc -eq 0 && "$FETCH_VERIFY" == "0" ]]; then
      smoke_pass "TLS certificate for ${WEB_SMOKE_HOST} verified by curl's own trust store, no --insecure" \
        "ssl_verify_result 0"
    else
      smoke_fail "TLS certificate for ${WEB_SMOKE_HOST}" "ssl_verify_result 0 without --insecure" \
        "curl exit ${rc}, ssl_verify_result ${FETCH_VERIFY:-none}: ${err}"
    fi
  fi
  if [[ $rc -ne 0 ]]; then
    smoke_fail "$what" "a response" "curl exit ${rc}: ${err:-no message}"
    return 1
  fi
  FETCH_SHOWN=false
  return 0
}

# The value of the first response header called $1 (lower case), trimmed; empty if there is none.
header_value() {
  awk -v want="$1" '
    { sub(/\r$/, "") }
    index($0, ":") > 0 {
      if (tolower(substr($0, 1, index($0, ":") - 1)) == want) {
        value = substr($0, index($0, ":") + 1)
        sub(/^[ \t]+/, "", value)
        sub(/[ \t]+$/, "", value)
        print value
        exit
      }
    }' "$FETCH_HEADERS"
}

# expect_status WHAT CODE...: the status is one of the codes given.
expect_status() {
  local what="$1" code expected
  shift
  expected="$1"
  if [[ $# -gt 1 ]]; then
    expected="one of $*"
  fi
  for code in "$@"; do
    if [[ "$FETCH_STATUS" == "$code" ]]; then
      smoke_pass "${what} status" "$FETCH_STATUS"
      return 0
    fi
  done
  smoke_fail "${what} status" "$expected" "${FETCH_STATUS:-none}"
}

# expect_header WHAT HEADER REGEX EXPECTED: the header is present and its lower-cased value
# matches REGEX. expect_header_lacks is the same with the match reversed (a missing header still
# fails, so it cannot pass for the wrong reason). A PASS line names the sub-check by EXPECTED; a FAIL
# line already does, in "expected EXPECTED".
expect_header() {
  header_check "$1" "$2" "$3" "$4" true
}

expect_header_lacks() {
  header_check "$1" "$2" "$3" "$4" false
}

header_check() {
  local what="$1" name="$2" regex="$3" expected="$4" want_match="$5" value lower matched=false
  value="$(header_value "$name")"
  lower="$(lowercase "$value")"
  if [[ -n "$value" ]] && [[ "$lower" =~ $regex ]]; then
    matched=true
  fi
  if [[ -z "$value" ]]; then
    smoke_fail "${what} ${name}" "$expected" "no ${name} header"
  elif [[ "$matched" == "$want_match" ]]; then
    smoke_pass "${what} ${name}: ${expected}" "$value"
  else
    smoke_fail "${what} ${name}" "$expected" "$value"
  fi
}

# expect_header_present WHAT HEADER
expect_header_present() {
  expect_header "$1" "$2" '.' "the header to be present"
}

# A comma-separated Cache-Control or a semicolon-separated HSTS value, token by token.
readonly CACHE_MAX_AGE_60_RE='(^|,)[[:space:]]*max-age=60[[:space:]]*(,|$)'
readonly CACHE_IMMUTABLE_RE='(^|,)[[:space:]]*immutable[[:space:]]*(,|$)'
readonly HSTS_RE="(^|;)[[:space:]]*max-age=${HSTS_MAX_AGE_SECONDS}[[:space:]]*(;|\$)"
readonly HSTS_EXTRAS_RE='includesubdomains|preload'

# expect_body_contains WHAT TEXT
expect_body_contains() {
  if grep -Fq -- "$2" "$FETCH_BODY"; then
    smoke_pass "$1 body contains \"$2\""
  else
    smoke_fail "$1 body" "text containing \"$2\"" "a body without it"
  fi
}

smoke_root() {
  local base="https://${WEB_SMOKE_HOST}" what="GET /"
  ROOT_BODY=""
  fetch "$what" "${base}/" "200; content-type text/html; cache-control max-age=60; content-security-policy with default-src 'none' and frame-ancestors 'none'; strict-transport-security max-age=${HSTS_MAX_AGE_SECONDS} without includeSubDomains or preload; x-content-type-options nosniff; x-frame-options DENY; referrer-policy and permissions-policy present" ||
    return 0
  ROOT_BODY="$FETCH_BODY"
  expect_status "$what" 200
  expect_header "$what" content-type '^text/html' "text/html"
  expect_header "$what" cache-control "$CACHE_MAX_AGE_60_RE" "max-age=60"
  expect_header_present "$what" content-security-policy
  expect_header "$what" content-security-policy "default-src 'none'" "default-src 'none'"
  expect_header "$what" content-security-policy "frame-ancestors 'none'" "frame-ancestors 'none'"
  expect_header "$what" strict-transport-security "$HSTS_RE" "max-age=${HSTS_MAX_AGE_SECONDS}"
  expect_header_lacks "$what" strict-transport-security "$HSTS_EXTRAS_RE" "no includeSubDomains and no preload"
  expect_header "$what" x-content-type-options '^nosniff$' "nosniff"
  expect_header "$what" x-frame-options '^deny$' "DENY"
  expect_header_present "$what" referrer-policy
  expect_header_present "$what" permissions-policy
}

# smoke_asset EXT TYPE_REGEX TYPE_NAME: the first assets/*.EXT file named in the body of /.
smoke_asset() {
  local ext="$1" name=""
  local asserts="200; content-type ${3}; cache-control immutable"
  if $DRY_RUN; then
    fetch "GET /assets/*.${ext}" "https://${WEB_SMOKE_HOST}/assets/<first .${ext} file named in the body of />" "$asserts" || true
    return 0
  fi
  if [[ -n "$ROOT_BODY" ]]; then
    name="$(grep -Eo 'assets/[A-Za-z0-9._-]+\.'"$ext"'([^A-Za-z0-9._-]|$)' "$ROOT_BODY" |
      sed -n '1p' | sed 's/[^A-Za-z0-9._-]$//' || true)"
  fi
  if [[ -z "$name" ]]; then
    smoke_fail "first .${ext} file under assets/ named in the body of /" "a name like assets/<file>.${ext}" \
      "$([[ -n "$ROOT_BODY" ]] && echo "none in the body" || echo "no body of / to read")"
    return 0
  fi
  fetch "GET /${name}" "https://${WEB_SMOKE_HOST}/${name}" "$asserts" || return 0
  expect_status "GET /${name}" 200
  expect_header "GET /${name}" content-type "$2" "$3"
  expect_header "GET /${name}" cache-control "$CACHE_IMMUTABLE_RE" "immutable"
}

# smoke_json PATH: a data file, 200 and a JSON content type.
smoke_json() {
  fetch "GET $1" "https://${WEB_SMOKE_HOST}$1" "200; content-type application/json" || return 0
  expect_status "GET $1" 200
  expect_header "GET $1" content-type '^application/json' "application/json"
}

smoke_favicon() {
  fetch "GET /favicon.svg" "https://${WEB_SMOKE_HOST}/favicon.svg" "200; content-type image/svg+xml" || return 0
  expect_status "GET /favicon.svg" 200
  expect_header "GET /favicon.svg" content-type '^image/svg\+xml' "image/svg+xml"
}

# A path that is not in the bucket: CloudFront turns S3's 403 or 404 into a 404 with /404.html.
smoke_not_found() {
  local what="GET /nope"
  fetch "$what" "https://${WEB_SMOKE_HOST}/nope" "404 (not 200); content-type text/html; body contains \"${NOT_FOUND_TEXT}\"" ||
    return 0
  expect_status "$what" 404
  expect_header "$what" content-type '^text/html' "text/html"
  expect_body_contains "$what" "$NOT_FOUND_TEXT"
}

# The distribution's viewer protocol policy is redirect-to-https (infra/web_stack.py), which
# CloudFront answers with a 301 for GET. Not followed: the first response is the one checked.
smoke_http_redirect() {
  local what="GET http://${WEB_SMOKE_HOST}/" location lower
  fetch "$what" "http://${WEB_SMOKE_HOST}/" "one of ${REDIRECT_STATUSES[*]}; location starting with https://${WEB_SMOKE_HOST}/ (not followed)" ||
    return 0
  expect_status "$what" "${REDIRECT_STATUSES[@]}"
  location="$(header_value location)"
  lower="$(lowercase "$location")"
  if [[ -z "$location" ]]; then
    smoke_fail "$what location" "https://${WEB_SMOKE_HOST}/..." "no location header"
  elif [[ "$lower" == "https://${WEB_SMOKE_HOST}/"* ]]; then
    smoke_pass "$what location" "$location"
  else
    smoke_fail "$what location" "https://${WEB_SMOKE_HOST}/..." "$location"
  fi
}

web_smoke_checks() {
  smoke_root
  smoke_asset js '^(text|application)/javascript' "text/javascript or application/javascript"
  smoke_asset css '^text/css' "text/css"
  smoke_json /data/index.json
  smoke_json /data/schema.json
  smoke_json /data/policies/facet-joint-interventions.json
  smoke_json /data/cases/facet-01.json
  smoke_favicon
  smoke_not_found
  smoke_http_redirect
}

cmd_web_smoke() {
  local site_url=""
  if $DRY_RUN; then
    echo "dry-run: no AWS call and no request is made. Read-only: nothing is uploaded, invalidated or changed."
    echo "dry-run: refuses unless the stack is CREATE_COMPLETE or UPDATE_COMPLETE; reads the site URL (the only AWS calls):"
    q_stack_status "$WEB_STACK"
    q_stack_output "$WEB_STACK" SiteUrl
    echo "dry-run: refuses unless it is https://<hostname>. Then these requests, in this order (curl --disable --silent --show-error --max-time ${SMOKE_CURL_TIMEOUT}, --proto =https or =http, never --insecure, no cookies, redirects not followed):"
    WEB_SMOKE_HOST="$DRY_RUN_HOST"
    web_smoke_checks
    return 0
  fi

  require_web_stack_complete web smoke
  site_url="$(stack_output "$WEB_STACK" SiteUrl '.*')"
  [[ "$site_url" =~ ^https://[a-z0-9.-]+$ ]] ||
    die "the SiteUrl output of ${WEB_STACK} is '$(printable "$site_url")', not https://<hostname>; web smoke sends nothing to a site that is not https"
  WEB_SMOKE_HOST="${site_url#https://}"
  WEB_SMOKE_TMP="$(mktemp -d)"
  trap web_smoke_cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  echo "web smoke: ${site_url} (read-only; the AWS calls were two describe-stacks reads)"
  web_smoke_checks

  echo
  echo "web smoke summary: ${site_url}"
  echo "${WEB_SMOKE_PASSED} passed, ${WEB_SMOKE_FAILED} failed"
  if [[ "$WEB_SMOKE_FAILED" -gt 0 ]]; then
    echo "failed checks:"
    printf '%s' "$WEB_SMOKE_FAILURES"
    echo "result: FAIL"
    exit 1
  fi
  echo "result: PASS"
}

cmd_web() {
  case "$1" in
    publish) cmd_web_publish ;;
    smoke) cmd_web_smoke ;;
    *) die "usage: deploy.sh web publish|smoke" ;;
  esac
}

# ---------------------------------------------------------------------------------------------
# live: start, stop, status and smoke for the live service (precertly-live)
#
# The live service is one Fargate task behind an HTTP API (gateway), at desired count 0 until
# started. A running task bills by the second; `live stop` and the trap in `live smoke` set it
# back to 0.
# ---------------------------------------------------------------------------------------------

readonly LIVE_API_URL_RE='^https://[a-z0-9]+\.execute-api\.[a-z0-9-]+\.amazonaws\.com/?$'
readonly LIVE_COST_WARNING="a running live task costs about 0.36 USD a day (Fargate time plus its public IPv4 address; unverified) until it is stopped: scripts/deploy.sh live stop"
readonly LIVE_RUN_PROMPT="Continue? About 0.07 USD of Bedrock and the database wakes up."
# An origin that is not the site's, for the CORS check (.invalid never resolves).
readonly LIVE_OTHER_ORIGIN="https://other.example.invalid"
readonly CASE_ID_RE='^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$'
# The script in the image that makes the one run (scripts/live_smoke_run.py).
readonly LIVE_RUN_SCRIPT="scripts/live_smoke_run.py"

LIVE_RUN_REQUESTED=false
LIVE_RUN_CASE=""
GW_URL=""
GW_STATUS=""
GW_COUNT=0

use_live_service() {
  ACTIVE_SERVICE="$LIVE_SERVICE"
  ACTIVE_CONTAINER="$LIVE_CONTAINER"
}

use_api_service() {
  ACTIVE_SERVICE="$SERVICE"
  ACTIVE_CONTAINER="$CONTAINER"
}

gw_cleanup() {
  if [[ -n "$GW_TMP" ]]; then
    rm -rf "$GW_TMP"
  fi
}

# The gateway URL without a trailing slash, from the ApiUrl output. Dies if it is missing or
# does not look like an API Gateway URL (nothing is sent anywhere else).
live_api_url() {
  local url
  url="$(stack_output "$LIVE_STACK" ApiUrl '.*')"
  [[ "$url" =~ $LIVE_API_URL_RE ]] ||
    die "the ApiUrl output of ${LIVE_STACK} is '$(printable "$url")', not https://<id>.execute-api.<region>.amazonaws.com"
  printf '%s' "${url%/}"
}

# gw_print METHOD PATH [curl option...]: what a dry run shows for one gateway request.
gw_print() {
  local method="$1" path="$2"
  shift 2
  printf '+ curl --disable --silent --show-error --max-time %q --proto %q --request %q' \
    "$SMOKE_CURL_TIMEOUT" "=https" "$method"
  if [[ $# -gt 0 ]]; then
    printf ' %q' "$@"
  fi
  printf ' %q\n' "${GW_URL}${path}"
}

# gw_request METHOD PATH [curl option...]: one request to the gateway, over https only, never
# --insecure, no cookies, redirects not followed. Sets GW_STATUS (000 if curl could not complete)
# and the FETCH_HEADERS and FETCH_BODY files that header_value reads. Returns curl's exit code.
gw_request() {
  local method="$1" path="$2" rc=0 out=""
  shift 2
  GW_COUNT=$((GW_COUNT + 1))
  FETCH_HEADERS="${GW_TMP}/headers.${GW_COUNT}"
  FETCH_BODY="${GW_TMP}/body.${GW_COUNT}"
  : >"$FETCH_HEADERS"
  : >"$FETCH_BODY"
  out="$(curl --disable --silent --show-error --max-time "$SMOKE_CURL_TIMEOUT" --proto '=https' \
    --request "$method" --dump-header "$FETCH_HEADERS" --output "$FETCH_BODY" \
    --write-out '%{http_code}' "$@" "${GW_URL}${path}" 2>"${GW_TMP}/stderr")" || rc=$?
  GW_STATUS="${out:-000}"
  return "$rc"
}

# Predicates for wait_for.
gateway_health_ok() {
  gw_request GET /health || return 1
  [[ "$GW_STATUS" == "200" ]]
}

# The first running task of the active service is HEALTHY (the container health check).
live_task_is_healthy() {
  probe q_list_tasks || true
  SMOKE_TASK="$AWS_OUT"
  [[ -n "$SMOKE_TASK" && "$SMOKE_TASK" != "None" ]] || return 1
  task_is_healthy
}

live_health_wait() {
  printf '%s' "$((WAIT_TIMEOUT < 300 ? WAIT_TIMEOUT : 300))"
}

cmd_live_start() {
  use_live_service
  if $DRY_RUN; then
    echo "dry-run: no AWS call and no request is made. Order of a real run:"
    q_stack_status "$LIVE_STACK"
    q_stack_output "$LIVE_STACK" ApiUrl
    q_service
    echo "dry-run: prints the warning: ${LIVE_COST_WARNING}"
    q_set_desired 1
    q_service
    echo "dry-run: waits up to ${WAIT_TIMEOUT}s for 1 running task, then up to $(live_health_wait)s for the ECS health status HEALTHY:"
    q_list_tasks
    q_describe_task "TASK_ARN"
    echo "dry-run: then polls the gateway until /health returns 200 (up to ${WAIT_TIMEOUT}s) and prints the elapsed seconds:"
    GW_URL="API_URL"
    gw_print GET /health
    return 0
  fi

  require_stack_complete "$LIVE_STACK" live start
  GW_URL="$(live_api_url)"
  local rc=0
  fetch_service || rc=$?
  if [[ $rc -ne 0 ]]; then
    die "${CLUSTER}/${LIVE_SERVICE} is not deployed; deploy it first: scripts/deploy.sh up ${LIVE_STACK}"
  fi
  echo "warning: ${LIVE_COST_WARNING}"
  GW_TMP="$(mktemp -d)"
  trap gw_cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  local started=$SECONDS
  echo "scaling ${LIVE_SERVICE} to 1 (now desired ${SVC_DESIRED}, running ${SVC_RUNNING})"
  q_set_desired 1 >/dev/null || die "could not set the desired count of ${LIVE_SERVICE} to 1"
  wait_for service_is_up ||
    die "${LIVE_SERVICE} did not reach 1 running task in ${WAIT_TIMEOUT}s (running ${SVC_RUNNING:-unknown}, rollout ${SVC_ROLLOUT:-unknown}). It is NOT stopped: look at scripts/deploy.sh live status, and run scripts/deploy.sh live stop when you are done"
  wait_for_seconds "$(live_health_wait)" live_task_is_healthy ||
    die "the task did not become HEALTHY within $(live_health_wait)s (last status: ${TASK_HEALTH}). It is NOT stopped: look at the log group /precertly/live, and run scripts/deploy.sh live stop when you are done"
  wait_for gateway_health_ok ||
    die "the gateway did not answer GET /health with 200 within ${WAIT_TIMEOUT}s (last status: ${GW_STATUS:-none}). The task is NOT stopped: run scripts/deploy.sh live stop when you are done"
  echo "live environment is up: gateway GET /health returned 200 after $((SECONDS - started))s"
  echo "stop it when you are done: scripts/deploy.sh live stop"
}

cmd_live_stop() {
  use_live_service
  if $DRY_RUN; then
    echo "dry-run: no AWS call is made"
    q_set_desired 0
    q_service
    return 0
  fi
  local rc=0
  fetch_service || rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "${LIVE_SERVICE} is not deployed; nothing to stop"
    return 0
  fi
  echo "scaling ${LIVE_SERVICE} to 0 (now desired ${SVC_DESIRED}, running ${SVC_RUNNING})"
  scale_to_zero || die "${LIVE_SERVICE} did not reach 0 running tasks in ${WAIT_TIMEOUT}s; check: scripts/deploy.sh live status"
  echo "desired ${SVC_DESIRED}, running ${SVC_RUNNING}: the live task is stopped"
}

# One line saying whether the environment is up, from the counts and the gateway's /health.
live_environment_line() {
  local desired="$1" running="$2" gateway="$3"
  if [[ "$running" == "0" && "$desired" == "0" ]]; then
    if [[ "$gateway" == "200" ]]; then
      echo "environment: DOWN? the service is at zero tasks but the gateway answered 200; check again in a minute"
    else
      echo "environment: DOWN (service at zero tasks; gateway /health ${gateway})"
    fi
  elif [[ "$running" != "0" && "$gateway" == "200" ]]; then
    echo "environment: UP (desired ${desired}, running ${running}, gateway /health 200); it bills until: scripts/deploy.sh live stop"
  else
    echo "environment: NOT READY (desired ${desired}, running ${running}, gateway /health ${gateway}); a running task bills until: scripts/deploy.sh live stop"
  fi
}

cmd_live_status() {
  local s rc
  if $DRY_RUN; then
    echo "dry-run: read-only queries; none is run"
    q_stack_status "$AUTH_STACK"
    q_stack_status "$LIVE_STACK"
    use_live_service
    q_service
    use_api_service
    q_stack_output "$LIVE_STACK" ApiUrl
    GW_URL="API_URL"
    gw_print GET /health
    return 0
  fi

  echo "live environment (region ${AWS_REGION}, profile ${AWS_PROFILE})"
  echo "stacks"
  for s in "$AUTH_STACK" "$LIVE_STACK"; do
    rc=0
    probe q_stack_status "$s" || rc=$?
    if [[ $rc -eq 0 ]]; then
      printf '  %-16s %s\n' "$s" "$AWS_OUT"
    else
      printf '  %-16s not deployed\n' "$s"
    fi
  done

  echo "service ${CLUSTER}/${LIVE_SERVICE}"
  use_live_service
  rc=0
  fetch_service || rc=$?
  use_api_service
  local desired="" running=""
  if [[ $rc -ne 0 ]]; then
    echo "  not deployed"
  else
    desired="$SVC_DESIRED"
    running="$SVC_RUNNING"
    printf '  status %s, desired %s, running %s, pending %s, rollout %s\n' \
      "$SVC_STATUS" "$SVC_DESIRED" "$SVC_RUNNING" "$SVC_PENDING" "$SVC_ROLLOUT"
  fi

  local url="" gateway="no response"
  rc=0
  probe q_stack_output "$LIVE_STACK" ApiUrl || rc=$?
  if [[ $rc -eq 0 && "$AWS_OUT" =~ $LIVE_API_URL_RE ]]; then
    url="${AWS_OUT%/}"
    echo "api url: ${url}"
    GW_URL="$url"
    GW_TMP="$(mktemp -d)"
    rc=0
    gw_request GET /health || rc=$?
    if [[ $rc -eq 0 ]]; then
      gateway="$GW_STATUS"
    else
      gateway="no response (curl exit ${rc})"
    fi
    gw_cleanup
    GW_TMP=""
    echo "gateway GET /health: ${gateway}"
  else
    echo "api url: not available"
  fi

  if [[ -z "$desired" ]]; then
    echo "environment: DOWN (${LIVE_SERVICE} is not deployed)"
  else
    live_environment_line "$desired" "$running" "$gateway"
  fi
}

# --- live smoke ---------------------------------------------------------------------------

# gw_expect LABEL EXPECTED_STATUS METHOD PATH [curl option...]: one request, one named check.
gw_expect() {
  local label="$1" expected="$2" method="$3" path="$4" rc=0
  shift 4
  gw_request "$method" "$path" "$@" || rc=$?
  if [[ $rc -ne 0 ]]; then
    record FAIL "${label} (no response: curl exit ${rc})"
  elif [[ "$GW_STATUS" == "$expected" ]]; then
    record PASS "$label"
  else
    record FAIL "${label} (expected ${expected}, got ${GW_STATUS})"
  fi
}

CORS_PREFLIGHT_OPTIONS=(--header "Access-Control-Request-Method: POST" --header "Access-Control-Request-Headers: authorization,content-type")

# The preflight from the site origin must be answered with that origin (and no other, and no
# wildcard), the methods and the two headers; one from another origin must get no allow-origin.
live_cors_checks() {
  local origin="$1" rc=0 value
  local label="CORS preflight OPTIONS /live/runs from the site origin"
  gw_request OPTIONS /live/runs --header "Origin: ${origin}" "${CORS_PREFLIGHT_OPTIONS[@]}" || rc=$?
  if [[ $rc -ne 0 ]]; then
    record FAIL "${label} (no response: curl exit ${rc})"
  else
    if [[ "$GW_STATUS" == "200" || "$GW_STATUS" == "204" ]]; then
      record PASS "${label} is ${GW_STATUS}"
    else
      record FAIL "${label} (expected 200 or 204, got ${GW_STATUS})"
    fi
    value="$(header_value access-control-allow-origin)"
    if [[ "$value" == "$origin" ]]; then
      record PASS "${label} allows exactly the site origin"
    else
      record FAIL "${label} allows exactly the site origin (got '$(printable "${value:-no header}")')"
    fi
    value="$(lowercase "$(header_value access-control-allow-methods)")"
    if [[ "$value" =~ (^|[^a-z])post([^a-z]|$) ]]; then
      record PASS "${label} allows POST"
    else
      record FAIL "${label} allows POST (got '$(printable "${value:-no header}")')"
    fi
    value="$(lowercase "$(header_value access-control-allow-headers)")"
    if [[ "$value" == *authorization* && "$value" == *content-type* ]]; then
      record PASS "${label} allows the authorization and content-type headers"
    else
      record FAIL "${label} allows the authorization and content-type headers (got '$(printable "${value:-no header}")')"
    fi
  fi

  rc=0
  label="CORS preflight OPTIONS /live/runs from a different origin"
  gw_request OPTIONS /live/runs --header "Origin: ${LIVE_OTHER_ORIGIN}" "${CORS_PREFLIGHT_OPTIONS[@]}" || rc=$?
  if [[ $rc -ne 0 ]]; then
    record FAIL "${label} (no response: curl exit ${rc})"
  else
    value="$(header_value access-control-allow-origin)"
    if [[ -z "$value" ]]; then
      record PASS "${label} gets no access-control-allow-origin header"
    else
      record FAIL "${label} gets no access-control-allow-origin header (got '$(printable "$value")')"
    fi
  fi
}

# What runs inside the task: one run of CASE, one summary line (scripts/live_smoke_run.py).
live_run_command() {
  printf 'python %s %s' "$LIVE_RUN_SCRIPT" "$1"
}

# One real run through ECS Exec. The script prints a single "LIVE_RUN status=... reason=...
# overall=... tokens=... timings=..." line and never the result; only the errors that mean
# "ECS Exec is not ready" are retried, like smoke_exec_check.
live_run_check() {
  local arn="$1" case_id="$2" out="" line="" attempt status reason
  local retry_seconds=$((POLL_SECONDS < 5 ? POLL_SECONDS : 5))
  local label="one real live run of ${case_id}"
  for ((attempt = 1; attempt <= EXEC_ATTEMPTS; attempt++)); do
    out="$(q_exec "$arn" "$(live_run_command "$case_id")" 2>&1)" || true
    line="$(printf '%s\n' "$out" | tr -d '\r' | grep -E '^LIVE_RUN ' | tail -n 1 || true)"
    if [[ -n "$line" ]]; then
      break
    fi
    if ((attempt < EXEC_ATTEMPTS)) && grep -Eq "$EXEC_NOT_READY_RE" <<<"$out"; then
      echo "ECS Exec not ready (attempt ${attempt} of ${EXEC_ATTEMPTS}), retrying in ${retry_seconds}s"
      sleep "$retry_seconds"
      continue
    fi
    break
  done
  if [[ -z "$line" ]]; then
    record FAIL "${label}: no result line from ${LIVE_RUN_SCRIPT}"
    show_tail "$out"
    return 1
  fi
  status="${line#*status=}"
  status="${status%% *}"
  reason="${line#*reason=}"
  reason="${reason%% *}"
  line="$(printable "${line#LIVE_RUN }")"
  if [[ "$status" == "done" ]]; then
    record PASS "${label}: ${line}"
  else
    record FAIL "${label}: status ${status}, reason ${reason} (${line})"
    return 1
  fi
}

live_run_steps() {
  local arn wait_cap
  wait_cap=$((WAIT_TIMEOUT < 120 ? WAIT_TIMEOUT : 120))
  probe q_list_tasks || true
  arn="$AWS_OUT"
  if [[ -z "$arn" || "$arn" == "None" ]]; then
    record FAIL "live run: no running task found"
    return 1
  fi
  SMOKE_TASK="$arn"
  if ! wait_for_seconds "$wait_cap" exec_agent_is_running; then
    record FAIL "live run: ECS Exec agent not RUNNING within ${wait_cap}s (last status: ${EXEC_AGENT_STATUS})"
    return 1
  fi
  record PASS "ECS Exec agent running"
  echo "running ${LIVE_RUN_CASE}: one real run (waits up to 300 s)"
  live_run_check "$arn" "$LIVE_RUN_CASE"
}

live_smoke_steps() {
  local origin="https://${WEB_DOMAIN}" started=$SECONDS
  echo "scaling ${LIVE_SERVICE} to 1"
  if ! q_set_desired 1 >/dev/null; then
    record FAIL "scale to 1"
    return 1
  fi
  if ! wait_for service_is_up; then
    record FAIL "service stable within ${WAIT_TIMEOUT}s (running ${SVC_RUNNING:-unknown}, rollout ${SVC_ROLLOUT:-unknown})"
    return 1
  fi
  record PASS "service stable (desired 1, running 1)"

  echo "waiting for the first gateway 200 on /health"
  if wait_for gateway_health_ok; then
    record PASS "cold start: scale-up to the first gateway 200 on /health took $((SECONDS - started))s"
  else
    record FAIL "cold start: the gateway returned 200 on /health within ${WAIT_TIMEOUT}s (last status: ${GW_STATUS:-none})"
    return 1
  fi

  gw_expect "unauthenticated GET /health through the gateway is 200" 200 GET /health
  gw_expect "unauthenticated POST /live/runs is 401" 401 POST /live/runs \
    --header "content-type: application/json" --data '{}'
  gw_expect "unauthenticated GET /live/runs/anything is 401" 401 GET /live/runs/anything
  gw_expect "GET /policies has no route and is 404 at the gateway" 404 GET /policies
  live_cors_checks "$origin"

  if [[ -n "$LIVE_RUN_CASE" ]]; then
    live_run_steps || return 1
  fi
}

cmd_live_smoke() {
  use_live_service
  require_real_web_domain
  if $DRY_RUN; then
    echo "dry-run: no AWS call and no request is made. Order of a real run:"
    if [[ -n "$LIVE_RUN_CASE" ]]; then
      echo "dry-run: would ask '${LIVE_RUN_PROMPT} [y/N]' first (--yes does not skip it)"
    fi
    q_stack_status "$LIVE_STACK"
    q_stack_output "$LIVE_STACK" ApiUrl
    q_service
    echo "dry-run: refuses unless the service shows desired 0, running 0 and pending 0; then:"
    q_set_desired 1
    q_service
    GW_URL="API_URL"
    echo "dry-run: polls the gateway until /health returns 200 (up to ${WAIT_TIMEOUT}s; that time is the cold start), then these requests:"
    gw_print GET /health
    gw_print POST /live/runs --header "content-type: application/json" --data '{}'
    gw_print GET /live/runs/anything
    gw_print GET /policies
    gw_print OPTIONS /live/runs --header "Origin: https://${WEB_DOMAIN}" "${CORS_PREFLIGHT_OPTIONS[@]}"
    gw_print OPTIONS /live/runs --header "Origin: ${LIVE_OTHER_ORIGIN}" "${CORS_PREFLIGHT_OPTIONS[@]}"
    if [[ -n "$LIVE_RUN_CASE" ]]; then
      echo "dry-run: then one real run inside the task (ECS Exec):"
      q_list_tasks
      q_exec_agent_status "TASK_ARN"
      q_exec "TASK_ARN" "$(live_run_command "$LIVE_RUN_CASE")"
    fi
    echo "dry-run: on exit, interrupt or failure, always:"
    q_set_desired 0
    q_service
    return 0
  fi

  if [[ -n "$LIVE_RUN_CASE" ]]; then
    command -v session-manager-plugin >/dev/null 2>&1 ||
      die "session-manager-plugin is not installed (ECS Exec needs it for --run): https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html"
    # Ask before anything starts, so no task bills while a question waits.
    confirm "$LIVE_RUN_PROMPT" || die "not confirmed; nothing started"
  fi

  require_stack_complete "$LIVE_STACK" live smoke
  GW_URL="$(live_api_url)"
  local rc=0
  fetch_service || rc=$?
  if [[ $rc -ne 0 ]]; then
    die "${CLUSTER}/${LIVE_SERVICE} is not deployed; deploy it first: scripts/deploy.sh up ${LIVE_STACK}"
  fi
  if [[ "$SVC_RUNNING" != "0" || "$SVC_PENDING" != "0" || "$SVC_DESIRED" != "0" ]]; then
    die "refusing: ${LIVE_SERVICE} is not at zero (desired ${SVC_DESIRED}, running ${SVC_RUNNING}, pending ${SVC_PENDING}); something else may be using it. Check with live status; to stop it: scripts/deploy.sh live stop"
  fi
  echo "service at zero; starting one task"

  GW_TMP="$(mktemp -d)"
  SMOKE_TITLE="live smoke"
  SMOKE_DOWN_COMMAND="live stop"
  # From here on every exit path scales back to 0 before the summary.
  trap smoke_finish EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  SMOKE_SCALED=true
  live_smoke_steps || true
}

cmd_live() {
  case "$1" in
    start) cmd_live_start ;;
    stop) cmd_live_stop ;;
    status) cmd_live_status ;;
    smoke) cmd_live_smoke ;;
    *) die "usage: deploy.sh live start|stop|status|smoke [--run CASE_ID]" ;;
  esac
}

# The settings a stack needs: the budget address always; the web domain and certificate only for
# precertly-web; the hosted-UI prefix and the web domain for precertly-auth; the web domain for
# precertly-live. The other stacks get placeholders, so a bad line for one of these in .env.local
# cannot block them.
load_settings() {
  case "$1" in
    "$WEB_STACK") load_env_local BUDGET_EMAIL WEB_DOMAIN WEB_CERTIFICATE_ARN ;;
    "$AUTH_STACK") load_env_local BUDGET_EMAIL AUTH_DOMAIN_PREFIX WEB_DOMAIN ;;
    "$LIVE_STACK") load_env_local BUDGET_EMAIL WEB_DOMAIN ;;
    *) load_env_local BUDGET_EMAIL ;;
  esac
}

main() {
  local positional=()
  local arg
  while [[ $# -gt 0 ]]; do
    arg="$1"
    shift
    case "$arg" in
      -h | --help)
        usage
        exit 0
        ;;
      --dry-run) DRY_RUN=true ;;
      --yes) ASSUME_YES=true ;;
      --bedrock) BEDROCK=true ;;
      --run)
        [[ $# -gt 0 ]] || die "--run needs a case id: live smoke --run CASE_ID"
        LIVE_RUN_REQUESTED=true
        LIVE_RUN_CASE="$1"
        shift
        ;;
      --run=*)
        LIVE_RUN_REQUESTED=true
        LIVE_RUN_CASE="${arg#--run=}"
        ;;
      --all) die "--all is not allowed: deploy one stack at a time, by name" ;;
      -*) die "unknown option '${arg}' (see --help)" ;;
      *) positional+=("$arg") ;;
    esac
  done

  if [[ ${#positional[@]} -eq 0 ]]; then
    usage >&2
    exit 2
  fi

  local command="${positional[0]}"
  if $BEDROCK && [[ "$command" != "smoke" ]]; then
    die "--bedrock is only for smoke"
  fi
  if $ASSUME_YES && [[ "$command" == "status" || "$command" == "smoke" || "$command" == "down" ]]; then
    die "--yes does not apply to ${command}"
  fi
  if $LIVE_RUN_REQUESTED; then
    [[ "$command" == "live" && "${positional[1]:-}" == "smoke" ]] || die "--run is only for live smoke"
    [[ "$LIVE_RUN_CASE" =~ $CASE_ID_RE ]] ||
      die "--run needs a case id of letters, digits, '.', '_' and '-' (for example facet-01)"
  fi
  # live smoke --run asks a question that --yes never skips; every other live command has none.
  if $ASSUME_YES && [[ "$command" == "live" ]] && ! { [[ "${positional[1]:-}" == "smoke" ]] && $LIVE_RUN_REQUESTED; }; then
    die "--yes does not apply to live ${positional[1]:-}"
  fi
  if $ASSUME_YES && [[ "$command" == "tools" && "${positional[1]:-}" != "load" ]]; then
    die "--yes does not apply to tools ${positional[1]:-}"
  fi
  [[ "$POLL_SECONDS" =~ ^[0-9]+$ && "$WAIT_TIMEOUT" =~ ^[0-9]+$ ]] ||
    die "DEPLOY_POLL_SECONDS and DEPLOY_WAIT_TIMEOUT must be whole numbers"

  case "$command" in
    image)
      [[ ${#positional[@]} -eq 1 ]] || die "image takes no arguments"
      cmd_image
      ;;
    stack)
      [[ ${#positional[@]} -eq 2 ]] || die "usage: deploy.sh stack <name>"
      load_settings "${positional[1]}"
      cmd_stack "${positional[1]}"
      ;;
    up)
      [[ ${#positional[@]} -eq 2 ]] || die "usage: deploy.sh up <name>"
      load_settings "${positional[1]}"
      cmd_up "${positional[1]}"
      ;;
    status)
      [[ ${#positional[@]} -eq 1 ]] || die "status takes no arguments"
      cmd_status
      ;;
    smoke)
      [[ ${#positional[@]} -eq 1 ]] || die "smoke takes no arguments"
      cmd_smoke
      ;;
    down)
      [[ ${#positional[@]} -eq 1 ]] || die "down takes no arguments"
      cmd_down
      ;;
    tools)
      [[ ${#positional[@]} -eq 2 ]] || die "usage: deploy.sh tools migrate|load|check"
      cmd_tools "${positional[1]}"
      ;;
    web)
      [[ ${#positional[@]} -eq 2 ]] || die "usage: deploy.sh web publish|smoke"
      cmd_web "${positional[1]}"
      ;;
    live)
      [[ ${#positional[@]} -eq 2 ]] || die "usage: deploy.sh live start|stop|status|smoke [--run CASE_ID]"
      if [[ "${positional[1]}" == "smoke" ]]; then
        load_env_local WEB_DOMAIN
      fi
      cmd_live "${positional[1]}"
      ;;
    *) die "unknown command '${command}' (see --help)" ;;
  esac
}

main "$@"
