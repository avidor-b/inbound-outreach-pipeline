# Outreach production infra. The EC2 instance lives in a private
# subnet (see vpc.tf) behind the ALB (alb.tf). No public IP; shell
# access via SSM Session Manager. RDS in private subnets, encrypted.
# CloudTrail + VPC flow logs on.

locals {
  name_prefix = "${var.project}-${var.environment}"

  common_tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# ---- Networking -----------------------------------------------------------
# VPC + subnets defined in vpc.tf. EC2 lives in private-a, reachable
# only via the ALB (public subnets) and SSM Session Manager.

# ---- SSH key (SSM tunnel only) -------------------------------------------
# The EC2 has no public IP and no SSH ingress, so this key is never
# exposed to the internet. It exists so the deploy script can rsync
# through a SSM port-forwarding session (aws ssm start-session +
# ProxyCommand). Private key is written to disk locally (0600).

resource "tls_private_key" "instance" {
  algorithm = "ED25519"
}

resource "aws_key_pair" "instance" {
  key_name   = "${local.name_prefix}-key"
  public_key = tls_private_key.instance.public_key_openssh

  tags = local.common_tags
}

resource "local_sensitive_file" "private_key" {
  content         = tls_private_key.instance.private_key_openssh
  filename        = "${path.module}/outreach-key.pem"
  file_permission = "0600"
}

# ---- Security group -------------------------------------------------------
# App ports accept traffic only from the ALB SG. Shell access is
# entirely via SSM Session Manager (no SSH port, no keypair).
# Egress wide open so docker pull / dnf / pip install all work; the
# instance reaches the internet via the NAT gateway in public-a.

resource "aws_security_group" "instance" {
  name_prefix = "${local.name_prefix}-sg-"
  description = "Outreach ${var.environment} - app ports from ALB only, shell access via SSM Session Manager"
  vpc_id      = aws_vpc.outreach.id

  # No SSH ingress. The instance lives in a private subnet and gets
  # no public IP; shell access is via SSM Session Manager:
  #     aws ssm start-session --target <instance-id>
  # The AmazonSSMManagedInstanceCore policy on the instance role
  # already enables this. No port, no key.

  ingress {
    description     = "FastAPI server (ALB only)"
    from_port       = var.server_host_port
    to_port         = var.server_host_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    description     = "Next.js frontend (ALB only)"
    from_port       = var.frontend_host_port
    to_port         = var.frontend_host_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    description = "All outbound (docker pull, SSM, ECR, LLM APIs, HubSpot)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-sg"
  })

  lifecycle {
    create_before_destroy = true
  }
}

# ---- AMI ------------------------------------------------------------------
# Amazon Linux 2023, x86_64. Comes with dnf, systemd, minimal footprint.

data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "state"
    values = ["available"]
  }
}

# ---- Instance -------------------------------------------------------------

resource "aws_instance" "outreach" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.instance_type
  key_name               = aws_key_pair.instance.key_name
  vpc_security_group_ids = [aws_security_group.instance.id]
  # Private subnet — no public IP. ALB (in public subnets) reaches
  # the app via VPC-internal routing; ops shell access goes through
  # SSM Session Manager, enabled by AmazonSSMManagedInstanceCore on
  # the instance role. Egress to the internet routes through the
  # NAT gateway in public-a.
  subnet_id                   = aws_subnet.private["a"].id
  associate_public_ip_address = false
  iam_instance_profile        = aws_iam_instance_profile.outreach.name

  user_data = templatefile("${path.module}/user_data.sh", {
    aws_region         = var.aws_region
    environment        = var.environment
    cwagent_config_ssm = aws_ssm_parameter.cwagent_config.name
    # Registry host is what docker compose + the credential helper
    # need; individual repo URLs are baked into compose tags at
    # deploy time, not here.
    ecr_registry = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.aws_region}.amazonaws.com"
    # FRONTEND_URL is baked into refresh-env.sh so the server's CORS
    # allowlist matches browser-origin requests. The generated script
    # initially uses the ALB DNS name and can be refreshed via SSM.
    # Don't pass app_fqdn here — doing so triggers
    # user_data_replace_on_change and recreates the EC2 instance.
    alb_dns_name = aws_lb.outreach.dns_name
  })
  # Don't destroy+recreate on user_data edits. In practice refresh-env.sh
  # changes get applied to the running /opt/outreach/refresh-env.sh
  # out-of-band via SSM, so the desired state is "tracked but not
  # applied until a human decides to cycle the box." An auto-replace
  # on edit here would wipe container state, chroma's EBS mount, and
  # whatever is in /opt/outreach every time someone edits the template.
  user_data_replace_on_change = false

  # EC2 refresh-env.sh runs at boot and pulls /outreach/<env>/* from
  # SSM into /opt/outreach/.env. If RDS creation runs in parallel
  # (both resources are otherwise independent), the DB's
  # DATABASE_URL SSM parameter may not exist yet when the EC2
  # first refreshes — leaving the app without a database
  # endpoint. Force EC2 creation to wait until DATABASE_URL is
  # in SSM.
  depends_on = [
    aws_ssm_parameter.database_url,
  ]

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_size_gb
    delete_on_termination = true
    encrypted             = true
    tags = merge(local.common_tags, {
      Name = "${local.name_prefix}-root"
    })
  }

  metadata_options {
    # IMDSv2 required — one of the cheapest security wins.
    http_tokens                 = "required"
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 2
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-server"
  })

  # Replacing the instance when user_data changes is the intended behaviour
  # for v0 — it's the cleanest way to reapply the bootstrap. If you need
  # in-place reboots instead, remove this lifecycle block.
  lifecycle {
    create_before_destroy = false
  }
}
