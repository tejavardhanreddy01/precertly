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
readonly STACKS=(precertly-base precertly-app precertly-data precertly-web)
# Stacks whose deploy needs the image of HEAD in ECR; `up` pushes it first. precertly-data holds
# the tools task definition, which pins the same image tag.
readonly IMAGE_STACKS=(precertly-app precertly-data)
readonly DATA_STACK="precertly-data"
readonly WEB_STACK="precertly-web"
# What every stack other than precertly-web is given for webDomain and webCertificateArn: the CDK
# app requires both for every stack, but only the web stack uses them. They are also what CI and
# infra/tests/support.py use, and the web stack refuses to deploy with them (a test compares).
readonly PLACEHOLDER_WEB_DOMAIN="site.example.invalid"
readonly PLACEHOLDER_WEB_CERTIFICATE_ARN="arn:aws:acm:us-east-1:000000000000:certificate/00000000-0000-0000-0000-000000000000"
# Must match infra/names.py (tests/test_deploy_script.py compares them).
readonly CLUSTER="precertly"
readonly SERVICE="precertly-api"
readonly CONTAINER="api"
readonly TOOLS_FAMILY="precertly-tools"
readonly TOOLS_CONTAINER="tools"
readonly API_PORT=8000
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

Commands:
  image        Build the API image for linux/arm64 and push it to ECR as sha-<12 hex of HEAD>.
               Refuses a dirty working tree or a HEAD that is not pushed. Tags are immutable,
               so a tag that already exists is skipped.
  stack <name> Deploy one CDK stack: precertly-base, precertly-app, precertly-data or
               precertly-web. Needs BUDGET_EMAIL (environment or .env.local; never printed).
               precertly-web also needs WEB_DOMAIN and WEB_CERTIFICATE_ARN (same sources) and
               refuses if they are missing or look like the placeholders; every other stack is
               given the placeholders, so they need no configuration for base, app or data.
               Passes --exclusively, so a stack's dependencies are never deployed with it
               (deploy precertly-app before precertly-data yourself).
               Passes -c imageTag=sha-<12 hex of HEAD>; precertly-app and precertly-data first
               check that image is in ECR and refuse if it is not.
               --all is refused; the CDK approval prompt for IAM and security changes stays on.
  up <name>    The usual path: for precertly-app and precertly-data run `image` first
               (precertly-web has no image step), then show `cdk diff --exclusively` (email and
               certificate ARN redacted), ask "Deploy? [y/N]", then run `stack`. --yes only
               skips the image confirmation: it never skips the diff or the Deploy? question.
  status       Read-only. Stack status, service counts and rollout, task definition revision,
               image tag and health check, the Aurora cluster (status, engine version, minimum
               and maximum ACU; a paused instance still reports available), available NAT
               gateways, RDS instances and ECR images, and one line for the web stack (stack
               state, distribution id, bucket name).
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

Options:
  --yes        Skip the image build confirmation (image, up). tools load ignores it.
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
  WEB_DOMAIN   the site's hostname, for precertly-web only (same sources as BUDGET_EMAIL).
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

# Set CDK_CONTEXT to the -c arguments every stack needs. Only precertly-web gets the real domain
# and certificate; the others get the placeholders. With redact=true (a dry run) the address and
# the certificate ARN are replaced, as they are in the diff.
build_context() {
  local name="$1" tag="$2" redact="$3" email domain arn
  email="$BUDGET_EMAIL"
  domain="$PLACEHOLDER_WEB_DOMAIN"
  arn="$PLACEHOLDER_WEB_CERTIFICATE_ARN"
  if [[ "$name" == "$WEB_STACK" ]]; then
    domain="$WEB_DOMAIN"
    arn="$WEB_CERTIFICATE_ARN"
    if [[ "$redact" == true ]]; then
      arn="REDACTED"
    fi
  fi
  if [[ "$redact" == true ]]; then
    email="REDACTED"
  fi
  CDK_CONTEXT=(-c "budgetEmail=${email}" -c "imageTag=${tag}" -c "webDomain=${domain}" -c "webCertificateArn=${arn}")
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

cmd_stack() {
  local name="$1"
  validate_stack "$name"
  require_budget_email
  if [[ "$name" == "$WEB_STACK" ]]; then
    require_web_settings
  fi

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
  if [[ "$name" == "$WEB_STACK" ]]; then
    require_web_settings
  fi
  local tag
  tag="$(image_tag)"

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
  run aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" \
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
  run aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" \
    --desired-count "$1" --query 'service.desiredCount' --output text
}

q_list_tasks() {
  run aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SERVICE" \
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
  run aws ecs execute-command --cluster "$CLUSTER" --task "$1" --container "$CONTAINER" \
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

# EXIT trap of `smoke`. Runs after success, a failed check, a die or Ctrl-C/TERM, and always
# tries to leave the service at zero tasks before printing the summary.
smoke_finish() {
  local rc=$?
  trap - EXIT
  trap '' INT TERM
  set +e
  PROBE_FATAL=false

  if $SMOKE_SCALED; then
    echo "scaling ${SERVICE} back to 0"
    if scale_to_zero; then
      record PASS "scaled back to 0"
    else
      record FAIL "scale back to 0 not confirmed: run scripts/deploy.sh down NOW (a task bills while it runs)"
    fi
    fetch_service
  fi

  echo
  echo "smoke summary"
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

# Read one output of the data stack. Dies, with a pointer, if the stack or the output is missing
# or the value does not look like what it should.
data_output() {
  local key="$1" pattern="$2" rc=0
  probe q_stack_output "$DATA_STACK" "$key" || rc=$?
  if [[ $rc -ne 0 || -z "$AWS_OUT" || "$AWS_OUT" == "None" ]]; then
    die "${DATA_STACK} is not deployed (no output ${key}); deploy it first: scripts/deploy.sh up ${DATA_STACK}"
  fi
  [[ "$AWS_OUT" =~ $pattern ]] || die "output ${key} of ${DATA_STACK} has an unexpected value"
  printf '%s' "$AWS_OUT"
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

# The settings a stack needs: the budget address always, the web domain and certificate only for
# precertly-web (the other stacks get placeholders, so a bad web line in .env.local cannot block
# them).
load_settings() {
  if [[ "$1" == "$WEB_STACK" ]]; then
    load_env_local BUDGET_EMAIL WEB_DOMAIN WEB_CERTIFICATE_ARN
  else
    load_env_local BUDGET_EMAIL
  fi
}

main() {
  local positional=()
  local arg
  for arg in "$@"; do
    case "$arg" in
      -h | --help)
        usage
        exit 0
        ;;
      --dry-run) DRY_RUN=true ;;
      --yes) ASSUME_YES=true ;;
      --bedrock) BEDROCK=true ;;
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
    *) die "unknown command '${command}' (see --help)" ;;
  esac
}

main "$@"
