# Real domain + ACM cert for the ALB.
#
# Production traffic is intentionally routed only for the canonical
# app hostname. A fresh AWS account must set its own domain_root and
# provide or create the matching Route 53 hosted zone in that account.
# Do not reuse the original production domain in downstream accounts.

variable "domain_root" {
  description = "Registered apex domain for this deployment, for example example.com. Required; must have a public Route 53 hosted zone in this AWS account."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9.-]*\\.[A-Za-z]{2,}$", var.domain_root))
    error_message = "domain_root must be a real apex/domain name such as example.com. Create or delegate a public Route 53 hosted zone in this AWS account before apply."
  }
}

variable "app_subdomain" {
  description = "Subdomain the app is served on. The apex stays free for future use."
  type        = string
  default     = "app"
}

locals {
  app_fqdn = "${var.app_subdomain}.${var.domain_root}"
}

data "aws_route53_zone" "root" {
  name         = var.domain_root
  private_zone = false
}

# --- ACM cert for the app FQDN -----------------------------------------------

resource "aws_acm_certificate" "app" {
  domain_name       = local.app_fqdn
  validation_method = "DNS"

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-app-cert"
  })

  lifecycle {
    create_before_destroy = true
  }
}

# Validation CNAMEs (ACM writes one per domain_name in the cert).
resource "aws_route53_record" "cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.app.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      type   = dvo.resource_record_type
      record = dvo.resource_record_value
    }
  }

  zone_id         = data.aws_route53_zone.root.zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "app" {
  certificate_arn         = aws_acm_certificate.app.arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

# --- A-alias record → ALB ----------------------------------------------------

resource "aws_route53_record" "app" {
  zone_id = data.aws_route53_zone.root.zone_id
  name    = local.app_fqdn
  type    = "A"

  alias {
    name                   = aws_lb.outreach.dns_name
    zone_id                = aws_lb.outreach.zone_id
    evaluate_target_health = true
  }
}

output "app_url" {
  description = "Public HTTPS URL of the app."
  value       = "https://${local.app_fqdn}"
}
