#!/usr/bin/env bash
# Deploy from the owner's machine: build and push the API image, or deploy one CDK stack.
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

usage() {
  cat <<'EOF'
Usage:
  scripts/deploy.sh image [--yes] [--dry-run]
  scripts/deploy.sh stack <name> [--dry-run]

Commands:
  image        Build the API image for linux/arm64 and push it to ECR as sha-<12 hex of HEAD>.
               Refuses a dirty working tree or a HEAD that is not pushed. Tags are immutable,
               so a tag that already exists is skipped.
  stack <name> Deploy one CDK stack: precertly-base, precertly-app or precertly-data.
               Needs BUDGET_EMAIL in the environment (never written to a file or printed).
               --all is refused; the CDK approval prompt for IAM and security changes stays on.

Options:
  --yes        Skip the confirmation prompt (image).
  --dry-run    Print the commands, with the email and account id redacted, and run nothing
               that touches AWS or Docker. The git working-tree and push checks are skipped.
  -h, --help   Show this help.

Environment:
  AWS_PROFILE  default: precertly (log in first: aws login --profile precertly)
  AWS_REGION   default: us-east-2
  BUDGET_EMAIL address for the budget alerts (stack only)
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

DRY_RUN=false
ASSUME_YES=false

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

repo_root() {
  git rev-parse --show-toplevel
}

image_tag() {
  printf 'sha-%s' "$(git rev-parse --short=12 HEAD)"
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
      local reply=""
      read -r -p "Build and push this image? [y/N] " reply || reply=""
      [[ "$reply" == "y" || "$reply" == "Y" ]] || die "not confirmed; nothing pushed"
    fi
  fi

  # Tags are immutable: an existing tag is never rebuilt or pushed again.
  if $DRY_RUN; then
    run aws ecr describe-images --repository-name "$REPO_NAME" --image-ids "imageTag=${tag}"
  else
    local err
    if err="$(aws ecr describe-images --repository-name "$REPO_NAME" \
      --image-ids "imageTag=${tag}" 2>&1 >/dev/null)"; then
      echo "${tag} already pushed, skipping"
      return 0
    fi
    [[ "$err" == *ImageNotFoundException* ]] || die "could not check for ${tag}: ${err}"
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
  local known=false s
  for s in "${STACKS[@]}"; do
    [[ "$name" == "$s" ]] && known=true
  done
  $known || die "unknown stack '${name}'; expected one of: ${STACKS[*]}"
  [[ -n "${BUDGET_EMAIL:-}" ]] || die "BUDGET_EMAIL is not set (the address for the budget alerts)"

  local email_arg="budgetEmail=${BUDGET_EMAIL}"
  if $DRY_RUN; then
    echo "dry-run: no AWS call is made"
    email_arg="budgetEmail=REDACTED"
  fi
  cd "$(repo_root)/infra"
  # --require-approval is left at the CDK default on purpose.
  run npx --no-install cdk deploy "$name" -c "$email_arg"
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
      --all) die "--all is not allowed: deploy one stack at a time, by name" ;;
      -*) die "unknown option '${arg}' (see --help)" ;;
      *) positional+=("$arg") ;;
    esac
  done

  if [[ ${#positional[@]} -eq 0 ]]; then
    usage >&2
    exit 2
  fi

  case "${positional[0]}" in
    image)
      [[ ${#positional[@]} -eq 1 ]] || die "image takes no arguments"
      cmd_image
      ;;
    stack)
      [[ ${#positional[@]} -eq 2 ]] || die "usage: deploy.sh stack <name>"
      cmd_stack "${positional[1]}"
      ;;
    *) die "unknown command '${positional[0]}' (see --help)" ;;
  esac
}

main "$@"
