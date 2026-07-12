#!/usr/bin/env bash
# Create the S3 bucket + DynamoDB table used by Terraform's remote
# backend, then write backend.hcl for this checkout.
#
# This script is intentionally small and idempotent so a new AWS
# account can bootstrap state before `terraform init`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

AWS_REGION="${AWS_REGION:-$(aws configure get region 2>/dev/null || echo us-east-1)}"
PROJECT="${PROJECT:-outreach}"
ENVIRONMENT="${ENVIRONMENT:-v0}"
STATE_BUCKET="${STATE_BUCKET:-}"
LOCK_TABLE="${LOCK_TABLE:-${PROJECT}-${ENVIRONMENT}-terraform-locks}"
STATE_KEY="${STATE_KEY:-outreach-pipeline/${ENVIRONMENT}/terraform.tfstate}"

command -v aws >/dev/null 2>&1 || { echo "x aws CLI not found on PATH" >&2; exit 2; }

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
if [[ -z "$STATE_BUCKET" ]]; then
    STATE_BUCKET="${PROJECT}-${ENVIRONMENT}-terraform-state-${ACCOUNT_ID}-${AWS_REGION}"
fi

echo "-> Terraform backend bootstrap"
echo "   account: $ACCOUNT_ID"
echo "   region:  $AWS_REGION"
echo "   bucket:  $STATE_BUCKET"
echo "   table:   $LOCK_TABLE"
echo "   key:     $STATE_KEY"

if aws s3api head-bucket --bucket "$STATE_BUCKET" >/dev/null 2>&1; then
    echo "   S3 bucket exists"
else
    echo "-> Creating S3 bucket"
    if [[ "$AWS_REGION" == "us-east-1" ]]; then
        aws s3api create-bucket --bucket "$STATE_BUCKET" >/dev/null
    else
        aws s3api create-bucket \
            --bucket "$STATE_BUCKET" \
            --create-bucket-configuration "LocationConstraint=$AWS_REGION" \
            >/dev/null
    fi
fi

aws s3api put-public-access-block \
    --bucket "$STATE_BUCKET" \
    --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true \
    >/dev/null

aws s3api put-bucket-versioning \
    --bucket "$STATE_BUCKET" \
    --versioning-configuration Status=Enabled \
    >/dev/null

aws s3api put-bucket-encryption \
    --bucket "$STATE_BUCKET" \
    --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' \
    >/dev/null

if aws dynamodb describe-table --region "$AWS_REGION" --table-name "$LOCK_TABLE" >/dev/null 2>&1; then
    echo "   DynamoDB lock table exists"
else
    echo "-> Creating DynamoDB lock table"
    aws dynamodb create-table \
        --region "$AWS_REGION" \
        --table-name "$LOCK_TABLE" \
        --attribute-definitions AttributeName=LockID,AttributeType=S \
        --key-schema AttributeName=LockID,KeyType=HASH \
        --billing-mode PAY_PER_REQUEST \
        >/dev/null
    aws dynamodb wait table-exists --region "$AWS_REGION" --table-name "$LOCK_TABLE"
fi

cat > "$SCRIPT_DIR/backend.hcl" <<EOF
bucket         = "$STATE_BUCKET"
key            = "$STATE_KEY"
region         = "$AWS_REGION"
encrypt        = true
dynamodb_table = "$LOCK_TABLE"
EOF
chmod 600 "$SCRIPT_DIR/backend.hcl"

echo "-> Wrote $SCRIPT_DIR/backend.hcl"
echo "   Next: cd terraform && terraform init -backend-config=backend.hcl"
