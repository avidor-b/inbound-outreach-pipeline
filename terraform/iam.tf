# IAM role + instance profile for the EC2.
#
# Grants only what the app needs at runtime:
#   - SSM:GetParameter / GetParametersByPath   — pull secrets at boot
#   - KMS:Decrypt                              — decrypt SSM SecureStrings
#   - ECR read access                          — docker pull images
#   - CloudWatch Logs write                    — container log shipping
#   - CloudWatch metric PutMetricData          — CloudWatch agent
#
# All further permissions must be added explicitly; there's no broad
# AdministratorAccess escape hatch.

resource "aws_iam_role" "outreach" {
  name_prefix = "${local.name_prefix}-ec2-"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_instance_profile" "outreach" {
  name_prefix = "${local.name_prefix}-ec2-"
  role        = aws_iam_role.outreach.name
  tags        = local.common_tags
}

# --- SSM Parameter Store: read + decrypt ---------------------------------

data "aws_caller_identity" "current" {}

resource "aws_iam_policy" "outreach_ssm_read" {
  name_prefix = "${local.name_prefix}-ssm-"
  description = "Read SecureString parameters under /outreach/${var.environment}/*"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters",
          "ssm:GetParametersByPath",
        ]
        Resource = [
          # GetParametersByPath authorises against the path arn
          # itself (no trailing slash) — AWS evaluates `parameter/outreach/<env>`
          # against the path resource, not against its children.
          "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/outreach/${var.environment}",
          # Individual parameter reads (GetParameter) go against the
          # child arns.
          "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/outreach/${var.environment}/*",
        ]
      },
      {
        # SSM SecureString values are encrypted with the default SSM KMS
        # key; EC2 needs Decrypt on that key to read them.
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:ViaService" = "ssm.${var.aws_region}.amazonaws.com"
          }
        }
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "outreach_ssm" {
  role       = aws_iam_role.outreach.name
  policy_arn = aws_iam_policy.outreach_ssm_read.arn
}

# --- ECR: read images ------------------------------------------------------

resource "aws_iam_role_policy_attachment" "outreach_ecr" {
  role       = aws_iam_role.outreach.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

# --- CloudWatch agent -----------------------------------------------------
# CloudWatchAgentServerPolicy is the AWS-managed policy that lets the
# agent push metrics + logs. Includes:
#   cloudwatch:PutMetricData
#   logs:PutLogEvents / CreateLogStream / CreateLogGroup (on the agent's
#   own log group — we also pre-create the app log groups below).
#   ec2messages:* (for SSM Session Manager association when we wire it)

resource "aws_iam_role_policy_attachment" "outreach_cloudwatch" {
  role       = aws_iam_role.outreach.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

# --- SSM Session Manager --------------------------------------------------
# Lets operators open a shell on the box via `aws ssm start-session`
# without any inbound SSH. Belt-and-braces for when we move EC2 to a
# private subnet in Phase D.

resource "aws_iam_role_policy_attachment" "outreach_ssm_core" {
  role       = aws_iam_role.outreach.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
