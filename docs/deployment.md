# Deployment

Deploys run from the owner's Mac with `scripts/deploy.sh`. GitHub Actions never deploys: this AWS account's managed organization policy denies `iam:*Provider*`, so no OIDC provider can exist and a workflow cannot assume an AWS role. Long-lived AWS keys in GitHub are not an option. CI only synthesises and tests (`infra-synth`) and builds the image without pushing it (`docker-build`).

## Prerequisites

- AWS CLI v2 and a login for the `precertly` profile: `aws login --profile precertly`. The credentials are short-lived console-login credentials. The script defaults to that profile and to `us-east-2`; `AWS_PROFILE` and `AWS_REGION` override them.
- Docker with buildx, running. Images are built for `linux/arm64`.
- `BUDGET_EMAIL` in the environment for any `stack` command. It is passed to the CDK as `-c budgetEmail=...`, never printed by the script and never written to a file. Do not put it in a committed file, a shell history you share or a CI variable.
- For stacks: `cd infra && uv sync --locked && npm ci` once, and the account bootstrapped for the CDK.
- A clean working tree with `HEAD` pushed, for `image`.

## Order

1. `scripts/deploy.sh image`: build and push the API image, tagged `sha-<12 hex of HEAD>`.
2. `scripts/deploy.sh stack <name>` for one stack at a time, in dependency order: `precertly-base` (budget, ECR repository), then `precertly-data`, then `precertly-app`, once the last two define resources. Never `--all`; the script refuses it. The CDK's approval prompt for IAM and security-group changes stays on.

Both subcommands take `--dry-run`, which prints the commands with the account id and email redacted and does not touch AWS or Docker. Run it first.

## Why tags are immutable

The ECR repository `precertly-api` has immutable tags. A `sha-` tag names one commit and one image, so what runs is what was reviewed, a rollback is a task definition pointing at an older tag, and nothing can overwrite a tag after the fact. The consequence is that `image` never re-pushes: if the tag exists it prints "already pushed, skipping" and exits 0. To change an image, commit and push, which gives a new tag.

The repository keeps the last 5 `sha-` images and expires untagged images after one day. The script builds with `--provenance=false --sbom=false` so BuildKit adds no attestation manifests, which would appear as untagged images and be deleted.

## What to call it

Until the service is running and a smoke check passes, say **deployable**, not **deployed**. A pushed image is "pushed", a created stack is "created", and "deployed" is for a service that is serving and has answered `/health` and `/policies` after the release. README and the Status section in CLAUDE.md follow the same rule, with the date checked.
