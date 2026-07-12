# SSM Parameter Store — every secret the app reads.
#
# Why SSM (not Secrets Manager): parameters are $0 if we stay under the
# standard tier (4KB). Secrets Manager is $0.40/secret-month + API
# charges. For 8 small keys SSM is free, SM would be ~$3.50/mo.
#
# Values are set via `terraform apply -var='secret_name=...'` OR
# out-of-band via `aws ssm put-parameter`. We intentionally do NOT
# version them in Terraform state with their plaintext values — each
# resource declares the parameter shell; the actual value is written
# separately so rotating a key doesn't require a terraform apply.
#
# On first apply, you'll see `value = "PLACEHOLDER"` — immediately
# after apply, run:
#     aws ssm put-parameter --overwrite --type SecureString \
#         --name /outreach/prod/JWT_SECRET --value "$(openssl rand -hex 32)"
# etc. for each parameter.
#
# The `lifecycle.ignore_changes = [value]` below tells Terraform to
# leave the actual secret alone on subsequent runs — so operators
# rotating keys via the CLI don't see Terraform try to revert them.

locals {
  # Every key the app reads. Loaded by docker/entrypoint.py on boot.
  app_secrets = toset([
    "JWT_SECRET",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "HUBSPOT_TOKEN",
    "LANGSMITH_API_KEY",
    # Non-secret but centrally managed for uniformity.
    "OUTREACH_MODEL",
    "LANGSMITH_TRACING",
    "ALLOWED_EMAILS",
  ])
}

resource "aws_ssm_parameter" "secret" {
  for_each = local.app_secrets

  name        = "/outreach/${var.environment}/${each.key}"
  description = "Outreach ${var.environment} — ${each.key}"
  type        = "SecureString"
  value       = "PLACEHOLDER-set-via-aws-ssm-put-parameter"

  # Allow out-of-band rotation without Terraform trying to revert.
  lifecycle {
    ignore_changes = [value]
  }

  tags = local.common_tags
}
