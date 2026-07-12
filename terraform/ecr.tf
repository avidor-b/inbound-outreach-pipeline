# ECR repositories for server + frontend images.
#
# Build + push happens on a dev workstation or CI runner (not on the
# EC2). This cuts the EC2 side of a deploy from 10 min (Next.js build)
# to ~30s (docker pull).
#
# Lifecycle policy keeps the last 10 images per repo; older tags are
# auto-deleted to cap storage around 15 GB = $1.50/month.
#
# Scan-on-push catches known CVEs in the base images before they
# reach prod.

resource "aws_ecr_repository" "server" {
  name = "${var.project}/server"
  # IMMUTABLE so a compromised pusher can't silently overwrite a
  # deployed tag. Deploys must push a new tag (the compose setup uses
  # ${OUTREACH_VERSION:-local}) — existing tags are frozen.
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = local.common_tags
}

resource "aws_ecr_repository" "frontend" {
  name = "${var.project}/frontend"
  # IMMUTABLE so a compromised pusher can't silently overwrite a
  # deployed tag. Deploys must push a new tag (the compose setup uses
  # ${OUTREACH_VERSION:-local}) — existing tags are frozen.
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = local.common_tags
}

locals {
  ecr_lifecycle_policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 10 images, delete older"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_ecr_lifecycle_policy" "server" {
  repository = aws_ecr_repository.server.name
  policy     = local.ecr_lifecycle_policy
}

resource "aws_ecr_lifecycle_policy" "frontend" {
  repository = aws_ecr_repository.frontend.name
  policy     = local.ecr_lifecycle_policy
}
