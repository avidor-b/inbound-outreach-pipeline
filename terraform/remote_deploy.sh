#!/usr/bin/env bash
# Outreach — fast deploy via ECR. Build images locally, push, tell
# the EC2 to `docker compose pull && docker compose up -d`. No
# --build on the box — that's what was making deploys take 10 min
# (Next.js bundle + pip install on t3.medium). Now it's dominated by
# local build (re-uses BuildKit cache, a few seconds for src-only
# changes) plus ECR push (usually < 30s).
#
# Requires locally:
#   - docker (with buildkit)
#   - aws CLI v2
#
# Usage:
#   ./remote_deploy.sh                 # uses terraform outputs
#   ./remote_deploy.sh <instance-id>   # explicit instance id (i-xxxxxxxx)
#
# Env overrides:
#   SKIP_BUILD=1   Don't rebuild images locally; deploy whatever tag
#                  is already pushed as `latest`. Useful for env-only
#                  changes (SSM rotation, compose tweak).
#   OUTREACH_VERSION=<tag>
#                  Deploy a specific pre-pushed tag instead of minting
#                  a new one.
#   APP_URL=https://app.example.com
#                  Canonical public app URL for post-deploy checks.
#                  Defaults to Terraform output app_url.
#   SSM_ENVIRONMENT=v0
#                  SSM parameter environment segment for secrets.
#   SKIP_POST_DEPLOY_CHECKS=1
#                  Skip HTTP and optional browser smoke checks.
#   SKIP_AUTH_SMOKE=1
#                  Skip unauthenticated sensitive-route smoke checks.
#   SKIP_UI_SMOKE=0
#                  Run authenticated Playwright UI smoke after deploy.
#                  Requires UI_SMOKE_USER_ID and UI_SMOKE_USER_EMAIL.
#   UI_SMOKE_REQUIRE_DATA=1
#                  Enable data-dependent UI smoke assertions.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

command -v aws >/dev/null 2>&1 || { echo "x aws CLI not found on PATH" >&2; exit 2; }
command -v docker >/dev/null 2>&1 || { echo "x docker not found on PATH" >&2; exit 2; }
command -v curl >/dev/null 2>&1 || { echo "x curl not found on PATH" >&2; exit 2; }

# ---- Resolve terraform outputs ----------------------------------------------

resolve_output() {
    local key="$1"
    local value=""
    value=$(
        cd "$SCRIPT_DIR" &&
        ~/.local/bin/terraform output -raw "$key" 2>/dev/null ||
        terraform output -raw "$key" 2>/dev/null ||
        true
    )
    if [[ -n "$value" ]]; then
        printf '%s' "$value"
        return 0
    fi
    # During the backend migration window, Terraform refuses `output`
    # until init runs. Deploys can still safely read the existing local
    # state outputs without mutating backend configuration.
    if [[ -f "$SCRIPT_DIR/terraform.tfstate" ]] && command -v jq >/dev/null 2>&1; then
        jq -r --arg key "$key" '.outputs[$key].value // empty' "$SCRIPT_DIR/terraform.tfstate"
    fi
}

INSTANCE_ID="${1:-$(resolve_output instance_id)}"
DEPLOY_BUCKET="$(resolve_output deploy_bucket)"
ALB_DNS="$(resolve_output alb_dns_name 2>/dev/null || true)"
APP_URL="${APP_URL:-$(resolve_output app_url 2>/dev/null || true)}"
AWS_REGION="${AWS_REGION:-$(aws configure get region 2>/dev/null || echo us-east-1)}"
SSM_ENVIRONMENT="${SSM_ENVIRONMENT:-v0}"

[[ -z "$INSTANCE_ID" ]]   && { echo "x no instance_id (pass arg or run after terraform apply)" >&2; exit 2; }
[[ -z "$DEPLOY_BUCKET" ]] && { echo "x no deploy_bucket terraform output" >&2; exit 2; }
[[ -z "$APP_URL" ]]       && { echo "x no app_url terraform output; set APP_URL=https://your-app.example.com" >&2; exit 2; }

AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ECR_REGISTRY="${AWS_ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com"
TAG="${OUTREACH_VERSION:-$(date +%Y%m%d-%H%M%S)}"
SERVER_IMAGE="${ECR_REGISTRY}/outreach/server:${TAG}"
FRONTEND_IMAGE="${ECR_REGISTRY}/outreach/frontend:${TAG}"

echo "-> Deploy target:"
echo "   instance: $INSTANCE_ID"
echo "   tag:      $TAG"
echo "   app URL:  $APP_URL"
echo "   server:   $SERVER_IMAGE"
echo "   frontend: $FRONTEND_IMAGE"

# ---- Wait for SSM agent -----------------------------------------------------

echo "-> Waiting for SSM agent on $INSTANCE_ID to come online"
for attempt in {1..60}; do
    state=$(aws ssm describe-instance-information \
        --region "$AWS_REGION" \
        --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
        --query 'InstanceInformationList[0].PingStatus' \
        --output text 2>/dev/null || true)
    if [[ "$state" == "Online" ]]; then
        echo "   SSM agent Online"
        break
    fi
    if [[ $attempt -eq 60 ]]; then
        echo "x SSM agent didn't come online within 10 minutes" >&2
        exit 3
    fi
    sleep 10
done

ssm_run() {
    local label="$1"; shift
    local cmd="$*"
    echo "-> [$label] via SSM..."
    local cmd_id
    cmd_id=$(aws ssm send-command \
        --region "$AWS_REGION" \
        --document-name "AWS-RunShellScript" \
        --instance-ids "$INSTANCE_ID" \
        --comment "$label" \
        --parameters "commands=[\"$(printf '%s' "$cmd" | sed 's/"/\\"/g')\"]" \
        --timeout-seconds 3600 \
        --cloud-watch-output-config CloudWatchOutputEnabled=false \
        --query 'Command.CommandId' \
        --output text)
    # Poll every 3s — SSM get-command-invocation is cheap and 10s
    # was adding 20-30s of tail latency per deploy waiting for the
    # final "Success" state transition. Cap total wait at 3600s
    # to match the send-command timeout.
    local status=Pending waited=0
    while [[ "$status" == "Pending" || "$status" == "InProgress" || "$status" == "Delayed" ]]; do
        sleep 3
        waited=$((waited + 3))
        if [[ $waited -ge 3600 ]]; then
            echo "x [$label] exceeded 3600s, giving up" >&2
            exit 4
        fi
        status=$(aws ssm get-command-invocation \
            --region "$AWS_REGION" \
            --command-id "$cmd_id" \
            --instance-id "$INSTANCE_ID" \
            --query 'Status' --output text 2>/dev/null || echo "Pending")
    done
    local stdout stderr
    stdout=$(aws ssm get-command-invocation \
        --region "$AWS_REGION" --command-id "$cmd_id" \
        --instance-id "$INSTANCE_ID" --query 'StandardOutputContent' --output text)
    stderr=$(aws ssm get-command-invocation \
        --region "$AWS_REGION" --command-id "$cmd_id" \
        --instance-id "$INSTANCE_ID" --query 'StandardErrorContent' --output text)
    [[ -n "$stdout" && "$stdout" != "None" ]] && printf '%s\n' "$stdout" | sed 's/^/   | /'
    [[ -n "$stderr" && "$stderr" != "None" ]] && printf '%s\n' "$stderr" | sed 's/^/   x /' >&2
    if [[ "$status" != "Success" ]]; then
        echo "x [$label] failed (status=$status)" >&2
        exit 4
    fi
}

# ---- Cloud-init wait (first deploy only, but cheap to re-check) --------------

ssm_run "wait for cloud-init" \
    "for i in \$(seq 1 60); do s=\$(cloud-init status 2>/dev/null | grep -oE 'status: \\w+'); echo \"  \$s (attempt \$i)\"; if echo \"\$s\" | grep -qE 'status: (done|error)'; then exit 0; fi; sleep 10; done; exit 1"

# ---- Build + push images locally --------------------------------------------

if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
    echo "-> ECR login (local)"
    aws ecr get-login-password --region "$AWS_REGION" \
        | docker login --username AWS --password-stdin "$ECR_REGISTRY" >/dev/null

    echo "-> Building server image locally"
    DOCKER_BUILDKIT=1 docker build \
        -t "$SERVER_IMAGE" \
        -f "$REPO_ROOT/Dockerfile" \
        "$REPO_ROOT"

    echo "-> Building frontend image locally"
    DOCKER_BUILDKIT=1 docker build \
        -t "$FRONTEND_IMAGE" \
        --build-arg NEXT_PUBLIC_BACKEND_URL= \
        -f "$REPO_ROOT/frontend/Dockerfile" \
        "$REPO_ROOT/frontend"

    echo "-> Pushing to ECR"
    docker push "$SERVER_IMAGE"
    docker push "$FRONTEND_IMAGE"
else
    echo "-> SKIP_BUILD=1 — reusing tag $TAG (must already be in ECR)"
fi

# ---- Push a minimal tarball (compose.yml + migrations) ----------------------
# Even with ECR-based deploys we still need docker-compose.yml and
# the migrations directory on the EC2 so `docker compose up -d` can
# start the stack. Shipping JUST those files (a few KB) is fast.

TAR=$(mktemp --suffix=.tar.gz)
trap 'rm -f "$TAR"' EXIT
(cd "$REPO_ROOT" && tar -czf "$TAR" \
    docker-compose.yml \
    src/outreach_pipeline/db/migrations)

STAMP=$(date +%Y%m%d-%H%M%S)
S3_KEY="releases/outreach-meta-$STAMP.tar.gz"
aws s3 cp --no-progress --region "$AWS_REGION" "$TAR" "s3://$DEPLOY_BUCKET/$S3_KEY"

ssm_run "fetch compose + migrations" \
    "set -e; install -d -o ec2-user -g ec2-user /opt/outreach; cd /opt/outreach; aws s3 cp --region $AWS_REGION s3://$DEPLOY_BUCKET/$S3_KEY /tmp/outreach-meta.tar.gz; tar -xzf /tmp/outreach-meta.tar.gz -C /opt/outreach; chown -R ec2-user:ec2-user /opt/outreach/docker-compose.yml /opt/outreach/src; rm -f /tmp/outreach-meta.tar.gz"

# ---- Write the image tags + refresh SSM secrets -----------------------------

# Strip any prior release lines, append the new ones, and re-assert
# ownership. Kept as one inline bash command (no leading `#` comments
# inside the string — line continuations in the outer bash string
# collapse into a single-line script, so a `#` anywhere in the body
# would comment out the rest of the command).
ssm_run "write release tags to .env" \
    "set -e; /opt/outreach/refresh-env.sh; ENV=/opt/outreach/.env; grep -v -E '^(SERVER_IMAGE|FRONTEND_IMAGE|OUTREACH_VERSION|ECR_REGISTRY)=' \"\$ENV\" > \"\$ENV.new\" || true; mv \"\$ENV.new\" \"\$ENV\"; echo 'SERVER_IMAGE=$SERVER_IMAGE' >> \"\$ENV\"; echo 'FRONTEND_IMAGE=$FRONTEND_IMAGE' >> \"\$ENV\"; echo 'OUTREACH_VERSION=$TAG' >> \"\$ENV\"; echo 'ECR_REGISTRY=$ECR_REGISTRY' >> \"\$ENV\"; chmod 600 \"\$ENV\"; chown ec2-user:ec2-user \"\$ENV\"; grep -E '^(SERVER_IMAGE|FRONTEND_IMAGE|OUTREACH_VERSION)=' \"\$ENV\""

# ---- Pull + up --------------------------------------------------------------

# ECR login on the box (the instance role has ecr:GetAuthorizationToken),
# pull just the services we re-tagged, then restart via compose. Same
# one-line rule as above — no `#` inside the SSM command body.
#
# `docker login` emits "Error saving credentials: ... not implemented"
# on hosts without a credential helper but still authenticates the
# current daemon session, so the subsequent `pull` works fine. We
# tolerate a non-zero exit from login with `|| true` rather than aborting.
ssm_run "docker compose pull + up" \
    "set -e; cd /opt/outreach; aws ecr get-login-password --region $AWS_REGION | sudo -u ec2-user docker login --username AWS --password-stdin $ECR_REGISTRY || true; sudo -u ec2-user docker compose pull server frontend; sudo -u ec2-user docker compose up -d 2>&1 | tail -15"

# ---- Post-deploy verification ----------------------------------------------

http_code() {
    local url="$1"
    local insecure="${2:-0}"
    if [[ "$insecure" == "1" ]]; then
        curl -k -sS -o /dev/null -w '%{http_code}' "$url"
    else
        curl -sS -o /dev/null -w '%{http_code}' "$url"
    fi
}

if [[ "${SKIP_POST_DEPLOY_CHECKS:-0}" != "1" ]]; then
    echo "-> Post-deploy HTTP checks"
    code=$(http_code "$APP_URL/")
    [[ "$code" == "200" ]] || { echo "x $APP_URL/ returned $code" >&2; exit 5; }
    echo "   canonical frontend root: $code"

    code=$(http_code "$APP_URL/auth/me")
    [[ "$code" == "401" ]] || { echo "x $APP_URL/auth/me returned $code, expected 401" >&2; exit 5; }
    echo "   canonical backend auth challenge: $code"

    if [[ -n "$ALB_DNS" ]]; then
        raw_code=$(http_code "https://$ALB_DNS/health" 1)
        [[ "$raw_code" == "404" ]] || {
            echo "x raw ALB DNS /health returned $raw_code, expected 404" >&2
            exit 5
        }
        echo "   raw ALB DNS denied: $raw_code"
    fi

    if [[ "${SKIP_AUTH_SMOKE:-0}" != "1" ]]; then
        BASE_URL="$APP_URL" "$REPO_ROOT/scripts/prod_auth_smoke.sh"
    fi

    if [[ "${SKIP_UI_SMOKE:-1}" != "1" ]]; then
        [[ -n "${UI_SMOKE_USER_ID:-}" ]] || { echo "x UI_SMOKE_USER_ID required when SKIP_UI_SMOKE=0" >&2; exit 5; }
        [[ -n "${UI_SMOKE_USER_EMAIL:-}" ]] || { echo "x UI_SMOKE_USER_EMAIL required when SKIP_UI_SMOKE=0" >&2; exit 5; }
        command -v node >/dev/null 2>&1 || { echo "x node not found on PATH for UI smoke" >&2; exit 5; }

        echo "-> Authenticated UI smoke"
        JWT_SECRET=$(aws ssm get-parameter \
            --region "$AWS_REGION" \
            --name "/outreach/$SSM_ENVIRONMENT/JWT_SECRET" \
            --with-decryption \
            --query Parameter.Value \
            --output text)
        JWT=$(
            JWT_SECRET="$JWT_SECRET" \
            UI_SMOKE_USER_ID="$UI_SMOKE_USER_ID" \
            UI_SMOKE_USER_EMAIL="$UI_SMOKE_USER_EMAIL" \
            ./venv/bin/python - <<'PY'
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt

print(jwt.encode({
    "sub": os.environ["UI_SMOKE_USER_ID"],
    "email": os.environ["UI_SMOKE_USER_EMAIL"],
    "role": os.environ.get("UI_SMOKE_USER_ROLE", "user"),
    "jti": secrets.token_urlsafe(16),
    "exp": datetime.now(timezone.utc) + timedelta(hours=1),
}, os.environ["JWT_SECRET"], algorithm="HS256"))
PY
        )
        (cd "$REPO_ROOT/tests/ui" && \
            BASE_URL="$APP_URL" JWT="$JWT" REQUIRE_DATA="${UI_SMOKE_REQUIRE_DATA:-0}" \
            node ui_regression.mjs)
    else
        echo "   UI smoke skipped (set SKIP_UI_SMOKE=0 with UI_SMOKE_USER_ID/UI_SMOKE_USER_EMAIL to run)"
    fi
fi

echo
echo "v Deployed tag $TAG."
echo "  App URL:  $APP_URL"
echo "  Shell:    aws ssm start-session --region $AWS_REGION --target $INSTANCE_ID"
