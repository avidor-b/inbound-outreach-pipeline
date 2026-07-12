# Outreach Terraform

AWS deployment example for the outreach-pipeline stack. One EC2 box in a
private subnet runs the server + frontend + ChromaDB containers behind an ALB.
An RDS Postgres 16 instance replaces the dockerized Postgres. Secrets live in
SSM Parameter Store; images in ECR; deploys land via SSM send-command + S3.
No public SSH or public EC2 IP is required.

No live account configuration is committed. `terraform.tfvars` and
`backend.hcl` are ignored because they contain organization-specific resource
identifiers and may expose operational metadata. Start from the checked-in
examples or let `bootstrap_backend.sh` generate the backend configuration.

See `docs/cloud-architecture.md` for the target topology.

## What this module creates

| Layer | Resources |
|---|---|
| Network | VPC 10.0.0.0/16, public-a/b + private-a/b subnets, IGW, NAT gateway, VPC flow logs |
| Compute | `t3.medium` Amazon Linux 2023 in private-a, 40 GB gp3 encrypted root, IMDSv2-only |
| Ingress | ALB in public subnets, HTTPS-only, canonical app host routing, TLS 1.2+ policy |
| Data | RDS Postgres 16 `db.t3.micro`, encrypted at rest, private subnets, 7-day backups |
| Secrets | SSM SecureString for every app key; instance role grants read-only access |
| Images | ECR repos `outreach-server` + `outreach-frontend`, scan-on-push, AES256 |
| Deploys | S3 bucket for release tarballs (7-day lifecycle), SSM send-command executes on-box |
| Observability | CloudWatch agent for host metrics, log groups for cwagent + docker, 4 metric alarms, SNS topic |
| Audit | CloudTrail → private encrypted S3 bucket with log-file validation, 90-day lifecycle |

## Configure an AWS account

```bash
cd terraform/

cp terraform.tfvars.example terraform.tfvars
# Edit alert_email, domain_root, and any optional sizing values.

# Authenticate to AWS. Any credential source works. If you use the
# AWS CLI v2 `aws login` cache, export first — Terraform's provider
# doesn't read the cache:
eval "$(aws configure export-credentials --format env)"

./bootstrap_backend.sh
terraform init -reconfigure -backend-config=backend.hcl
terraform plan
terraform apply
```

Apply duration depends on the target account, selected instance sizes, and
whether AWS needs to create networking and database resources from scratch.

Before `terraform apply`, the `domain_root` must have a public Route 53
hosted zone in the target AWS account. If the domain is registered
elsewhere, create the hosted zone in Route 53 and delegate the domain or
subdomain to the Route 53 nameservers first.

## Terraform state

The repository never commits `backend.hcl` or Terraform state. Team operation
should use a private S3 backend and DynamoDB locking in the same AWS account as
the environment.

The recommended path is:

```bash
cd terraform/
./bootstrap_backend.sh
terraform init -backend-config=backend.hcl
```

`bootstrap_backend.sh` creates:
- a private, encrypted, versioned S3 state bucket
- a pay-per-request DynamoDB lock table
- `backend.hcl` pointing Terraform at those resources

Override names if your organization requires a naming convention:

```bash
STATE_BUCKET=my-company-prod-tfstate \
LOCK_TABLE=my-company-prod-tflocks \
STATE_KEY=outreach-pipeline/prod/terraform.tfstate \
./bootstrap_backend.sh
```

Manual equivalent:

```bash
cd terraform/
cp backend.example.hcl backend.hcl
# edit backend.hcl: bucket, dynamodb_table, region, key
terraform init -backend-config=backend.hcl
```

Do not point one environment at another account's state bucket. If you
change `backend.hcl`, review it like production
configuration and migrate/reinitialize Terraform state deliberately.

## Deploy the app

```bash
./remote_deploy.sh
```

The script tars the working tree (minus build artifacts), uploads to
the deploy S3 bucket, and issues an SSM send-command that extracts the
minimal deploy metadata, refreshes `/opt/outreach/.env` from SSM, pulls
immutable ECR image tags, and runs `docker compose up -d`. No SSH, no
session-manager-plugin locally — just the AWS CLI.

Deploy duration depends on image cache state, network throughput, and whether
the frontend and backend images need to be rebuilt.

Options:
- `SKIP_BUILD=1` — skip the docker build, deploy an already-pushed tag
- `OUTREACH_VERSION=<tag>` — deploy or roll back to a specific immutable ECR tag
- `SKIP_POST_DEPLOY_CHECKS=1` — skip HTTP/auth smoke checks
- `SKIP_AUTH_SMOKE=1` — skip unauthenticated sensitive-route checks
- `SKIP_UI_SMOKE=0` — run authenticated UI smoke when user env vars are provided

Rollback example:

```bash
OUTREACH_VERSION=<previous-image-tag> SKIP_BUILD=1 ./remote_deploy.sh
```

## Smoke test

```bash
./smoke_test.sh
```

Checks infra, containers, endpoints, and unauthenticated route guards.
Exit code 0 = all green. It uses AWS SSM send-command for
internal/container checks, so it needs AWS CLI permissions for SSM but
does not require SSH or the Session Manager plugin locally.

You can also smoke-test the public endpoints manually:

```bash
APP_URL="$(terraform output -raw app_url)"
curl -i "$APP_URL/"
curl -i "$APP_URL/auth/me"   # expected 401 without a session
curl -i "$APP_URL/health"    # expected 404; public health is not routed
```

## Shell on the instance

Via SSM Session Manager — no SSH exposed on the internet:

```bash
aws ssm start-session --target "$(terraform output -raw instance_id)"
```

Requires the session-manager-plugin.

## Rotating a secret

SSM parameters are written once by Terraform (value = placeholder)
with `lifecycle.ignore_changes = [value]`, so you can rotate keys
out-of-band without Terraform trying to revert them:

```bash
read -rsp "New value: " SECRET_VALUE && echo
aws ssm put-parameter --overwrite --type SecureString \
    --name /outreach/v0/OPENAI_API_KEY --value "$SECRET_VALUE"
unset SECRET_VALUE
```

The next `./remote_deploy.sh` (or calling `/opt/outreach/refresh-env.sh`
directly via SSM) picks up the new value and restarts the containers.

## Local development

`terraform/` is purely for cloud deploys. For local dev, use
`docker-compose.yml` with the `dev` profile (spins up a local Postgres
so you don't need RDS access):

```bash
docker compose --profile dev up --build
```

## Security posture

- No public SSH, no public EC2 IP. Ops shell = SSM Session Manager.
- ALB is HTTPS-only (no HTTP listener, port 80 closed at the SG).
- Listener rules require the canonical app hostname. Raw ALB DNS
  requests fall through to the default `404`.
- Public `/health` is not routed. The ALB target group still probes
  backend `/health` internally.
- IMDSv2 required.
- Every secret is SSM SecureString (KMS-backed); the instance role
  allows `GetParametersByPath` on `/outreach/<env>/*` only.
- RDS is in private subnets, encrypted at rest, and accepts ingress
  only from the EC2 SG.
- CloudTrail logs all management-plane calls to a private encrypted
  S3 bucket with log-file validation (tamper detection).
- VPC flow logs capture REJECT traffic for forensics.
- Cookies carry Secure (`COOKIE_SECURE=true` is baked into the
  refresh-env.sh — HTTPS-only deploy).
- Frontend responses include baseline browser security headers and
  omit `X-Powered-By`.

## Teardown

```bash
terraform destroy
```

Tears down everything this module created: EC2, RDS, ALB, ECR repos,
S3 buckets (all have `force_destroy = true` so they clear even if
non-empty), IAM roles, SSM parameters, CloudTrail trail.
