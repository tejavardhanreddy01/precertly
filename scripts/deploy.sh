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
readonly STACKS=(precertly-base precertly-app precertly-data)
# Stacks whose deploy needs the image of HEAD in ECR; `up` pushes it first. (precertly-data has
# no service yet; add it here when it does.)
readonly IMAGE_STACKS=(precertly-app)
# Must match infra/names.py (tests/test_deploy_script.py compares them).
readonly CLUSTER="precertly"
readonly SERVICE="precertly-api"
readonly CONTAINER="api"
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

Commands:
  image        Build the API image for linux/arm64 and push it to ECR as sha-<12 hex of HEAD>.
               Refuses a dirty working tree or a HEAD that is not pushed. Tags are immutable,
               so a tag that already exists is skipped.
  stack <name> Deploy one CDK stack: precertly-base, precertly-app or precertly-data.
               Needs BUDGET_EMAIL (environment or .env.local; never printed).
               Passes -c imageTag=sha-<12 hex of HEAD>; precertly-app first checks that
               image is in ECR and refuses if it is not.
               --all is refused; the CDK approval prompt for IAM and security changes stays on.
  up <name>    The usual path: for precertly-app run `image` first, then show `cdk diff`
               (email redacted), ask "Deploy? [y/N]", then run `stack`. --yes only skips the
               image confirmation: it never skips the diff or the Deploy? question.
  status       Read-only. Stack status, service counts and rollout, task definition revision,
               image tag and health check, available NAT gateways, RDS instances and ECR images.
               A missing resource prints "not deployed".
  smoke        Refuses unless the service has no task. Scales to 1, waits (10 min), runs /health
               and /policies inside the task with ECS Exec, then ALWAYS scales back to 0 and
               waits until no task runs, even after a failure or Ctrl-C. Needs the AWS
               session-manager-plugin. --bedrock also runs scripts/smoke_llm.py (one real
               Bedrock call; asks first, and --yes does not skip that question).
  down         Set the service to 0 and wait until no task runs.

Options:
  --yes        Skip the image build confirmation (image, up).
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

# If BUDGET_EMAIL is unset, read it from the optional, gitignored .env.local at the repo root.
# The file is parsed, never sourced: blank lines, # comments and KEY=value lines only, no
# expansion. Only BUDGET_EMAIL is taken. Error messages name a line number, never its content.
load_env_local() {
  if [[ -n "${BUDGET_EMAIL:-}" ]]; then
    return 0
  fi
  local file line key value found="" n=0
  local assignment_re='^([A-Za-z_][A-Za-z0-9_]*)=(.*)$'
  local dquoted_re='^"(.*)"$' squoted_re="^'(.*)'\$"
  local email_re='^[^[:space:]@]+@[^[:space:]@]+$'
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
      key="${BASH_REMATCH[1]}"
      value="${BASH_REMATCH[2]}"
    else
      die ".env.local line ${n} is not KEY=value"
    fi
    if [[ "$value" =~ $dquoted_re || "$value" =~ $squoted_re ]]; then
      value="${BASH_REMATCH[1]}"
    fi
    if [[ "$key" == "BUDGET_EMAIL" ]]; then
      found="$value"
    fi
  done <"$file"
  if [[ -z "$found" ]]; then
    return 0
  fi
  [[ "$found" =~ $email_re ]] || die "BUDGET_EMAIL in .env.local is not an email address"
  BUDGET_EMAIL="$found"
  export BUDGET_EMAIL
}

require_budget_email() {
  [[ -n "${BUDGET_EMAIL:-}" ]] ||
    die "BUDGET_EMAIL is not set (the address for the budget alerts): export it or put BUDGET_EMAIL=<address> in .env.local"
}

# Print text with the budget address replaced, so a CDK diff can be shown and pasted.
redact() {
  local text="$1"
  if [[ -n "${BUDGET_EMAIL:-}" ]]; then
    text="${text//"$BUDGET_EMAIL"/REDACTED}"
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

  # The tag of HEAD is passed to every stack because one `cdk deploy <name>` runs the whole app,
  # which requires it. Only precertly-app uses it, so only that stack checks the image exists.
  local tag
  tag="$(image_tag)"
  local email_arg="budgetEmail=${BUDGET_EMAIL}"
  if $DRY_RUN; then
    echo "dry-run: no AWS call is made"
    email_arg="budgetEmail=REDACTED"
    if [[ "$name" == "precertly-app" ]]; then
      echo "dry-run: skipping the check that ${tag} is in ECR"
    fi
  elif [[ "$name" == "precertly-app" ]]; then
    ecr_has_tag "$tag" ||
      die "image ${tag} is not in ECR; push it first: scripts/deploy.sh image"
  fi
  cd "$(repo_root)/infra"
  # --require-approval is left at the CDK default on purpose.
  run npx --no-install cdk deploy "$name" -c "$email_arg" -c "imageTag=${tag}"
}

# image (where the stack needs it), then the diff, then a question that --yes never skips,
# then the same deploy as `stack`.
cmd_up() {
  local name="$1"
  validate_stack "$name"
  require_budget_email
  local tag
  tag="$(image_tag)"

  if needs_image "$name"; then
    cmd_image
  fi

  local email_arg="budgetEmail=${BUDGET_EMAIL}"
  if $DRY_RUN; then
    email_arg="budgetEmail=REDACTED"
  fi
  cd "$(repo_root)/infra"
  if $DRY_RUN; then
    run npx --no-install cdk diff "$name" -c "$email_arg" -c "imageTag=${tag}"
    echo "dry-run: the diff above is always shown, then it asks 'Deploy? [y/N]' (--yes does not skip either)"
  else
    echo "--- cdk diff ${name} (email redacted) ---"
    local out
    if ! out="$(npx --no-install cdk diff "$name" -c "$email_arg" -c "imageTag=${tag}" 2>&1)"; then
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
    *"does not exist"* | *NotFoundException* | *ServiceNotActiveException*) return 3 ;;
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

# Poll a predicate until it holds or WAIT_TIMEOUT seconds pass.
wait_for() {
  local deadline=$((SECONDS + WAIT_TIMEOUT))
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

# Run one command in the task; pass if the output has the expected line.
smoke_exec_check() {
  local label="$1" arn="$2" command="$3" pattern="$4" out=""
  out="$(q_exec "$arn" "$command" 2>&1)" || true
  if grep -Eq "$pattern" <<<"$out"; then
    record PASS "$label"
    return 0
  fi
  record FAIL "$label"
  show_tail "$out"
  return 1
}

smoke_steps() {
  local arn out last health path

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
  probe q_describe_task "$arn" || true
  IFS=$'\t' read -r last health <<<"$AWS_OUT"
  echo "task ${arn##*/}: lastStatus ${last:-unknown}, healthStatus ${health:-unknown}"
  if [[ "${last:-}" != "RUNNING" ]]; then
    record FAIL "task lastStatus ${last:-unknown}, expected RUNNING"
    return 1
  fi
  record PASS "task RUNNING (healthStatus ${health:-unknown})"

  for path in /health /policies; do
    smoke_exec_check "GET ${path} returned 200 (ECS Exec, urllib)" "$arn" \
      "$(http_command "$path")" "^SMOKE_HTTP ${path} 200[[:space:]]*\$" || return 1
  done

  if $BEDROCK; then
    smoke_exec_check "one real Bedrock call (scripts/smoke_llm.py)" "$arn" \
      "$BEDROCK_COMMAND" '^tokens in/out: [0-9]+/[0-9]+' || return 1
  fi
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
    q_exec "TASK_ARN" "$(http_command /health)"
    q_exec "TASK_ARN" "$(http_command /policies)"
    if $BEDROCK; then
      echo "dry-run: would ask 'One real Bedrock call, costs a fraction of a cent. Continue? [y/N]'"
      q_exec "TASK_ARN" "$BEDROCK_COMMAND"
    fi
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
  [[ "$POLL_SECONDS" =~ ^[0-9]+$ && "$WAIT_TIMEOUT" =~ ^[0-9]+$ ]] ||
    die "DEPLOY_POLL_SECONDS and DEPLOY_WAIT_TIMEOUT must be whole numbers"

  case "$command" in
    image)
      [[ ${#positional[@]} -eq 1 ]] || die "image takes no arguments"
      cmd_image
      ;;
    stack)
      [[ ${#positional[@]} -eq 2 ]] || die "usage: deploy.sh stack <name>"
      load_env_local
      cmd_stack "${positional[1]}"
      ;;
    up)
      [[ ${#positional[@]} -eq 2 ]] || die "usage: deploy.sh up <name>"
      load_env_local
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
    *) die "unknown command '${command}' (see --help)" ;;
  esac
}

main "$@"
