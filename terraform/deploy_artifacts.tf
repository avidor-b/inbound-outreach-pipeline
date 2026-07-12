# S3 bucket for deploy artifacts. remote_deploy.sh tars the working
# tree, uploads here, then calls `aws ssm send-command` to fetch +
# extract on the EC2. This avoids any SSH/session-manager-plugin
# dependency locally — the deploy path is pure AWS API.
#
# Bucket is private, encrypted, with a short lifecycle (7 days) so
# old tarballs don't accumulate. Instance role has read-only access.

resource "aws_s3_bucket" "deploy" {
  bucket_prefix = "${local.name_prefix}-deploy-"
  force_destroy = true

  tags = local.common_tags
}

resource "aws_s3_bucket_server_side_encryption_configuration" "deploy" {
  bucket = aws_s3_bucket.deploy.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "deploy" {
  bucket                  = aws_s3_bucket.deploy.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "deploy" {
  bucket = aws_s3_bucket.deploy.id
  rule {
    id     = "expire-7-days"
    status = "Enabled"
    filter {}
    expiration {
      days = 7
    }
    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }
}

# Read-only grant for the EC2 instance role so ssm send-command can
# `aws s3 cp s3://.../outreach.tar.gz -` on the box.
resource "aws_iam_role_policy" "deploy_bucket_read" {
  name_prefix = "${local.name_prefix}-deploy-read-"
  role        = aws_iam_role.outreach.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "s3:GetObject",
        "s3:ListBucket",
      ]
      Resource = [
        aws_s3_bucket.deploy.arn,
        "${aws_s3_bucket.deploy.arn}/*",
      ]
    }]
  })
}

output "deploy_bucket" {
  description = "S3 bucket used by remote_deploy.sh to ship code to the EC2."
  value       = aws_s3_bucket.deploy.id
}
