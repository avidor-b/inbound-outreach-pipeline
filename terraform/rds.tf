# RDS Postgres — the production database, encrypted + backed up.
#
# Replaces the dockerized postgres in the dev compose profile that
# had been carrying prod data on the EC2's root EBS volume (no
# encryption, no backups, no PITR, disk failure = total loss).
#
# Size: db.t3.micro is free tier eligible and ample for Outreach's
# workload (thousands of contacts, tens of thousands of engagements,
# LangGraph checkpoints). Upgrade to db.t3.small when concurrent
# users push > 10 or storage > 15 GB.

# --- Subnet group ---------------------------------------------------------
# RDS requires subnets in at least 2 AZs for the subnet group even
# when the DB itself is single-AZ. Uses the private subnets created
# in vpc.tf.

resource "aws_db_subnet_group" "outreach" {
  name_prefix = "${local.name_prefix}-db-"
  description = "Private subnets for the Outreach RDS Postgres"
  subnet_ids  = [for s in aws_subnet.private : s.id]

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-db-subnet-group"
  })
}

# --- Security group -------------------------------------------------------
# Only the EC2 instance's SG can talk to 5432. No public exposure.

resource "aws_security_group" "rds" {
  name_prefix = "${local.name_prefix}-rds-"
  description = "RDS Postgres - accepts 5432 from the EC2 SG only"
  vpc_id      = aws_vpc.outreach.id

  ingress {
    description     = "Postgres from app EC2"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.instance.id]
  }

  egress {
    description = "All outbound (for cross-AZ replication and backups)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-rds-sg"
  })

  lifecycle {
    create_before_destroy = true
  }
}

# --- Parameter group ------------------------------------------------------
# Pin to Postgres 16 and enable pgcrypto (langgraph_checkpoint_postgres
# needs it). Default PG16 parameter group is good enough for the rest.

resource "aws_db_parameter_group" "outreach" {
  name_prefix = "${local.name_prefix}-pg16-"
  family      = "postgres16"
  description = "Outreach ${var.environment} - default PG16 knobs with shared_preload_libraries"

  parameter {
    name  = "log_statement"
    value = "ddl" # log all DDL; skip DML to keep cost / noise down
  }

  parameter {
    name  = "log_min_duration_statement"
    value = "2000" # log slow queries > 2s
  }

  tags = local.common_tags

  lifecycle {
    create_before_destroy = true
  }
}

# --- RDS password ---------------------------------------------------------
# Generate once, store in SSM so the app can read it via the same
# PII-on-demand path the other secrets use.

resource "random_password" "rds_master" {
  length  = 32
  special = true
  # RDS Postgres rejects a handful of special chars in the password.
  override_special = "!#$%^&*()-_=+[]{}<>?"
}

resource "aws_ssm_parameter" "database_url" {
  name        = "/outreach/${var.environment}/DATABASE_URL"
  description = "Connection string for the RDS Postgres. App reads via entrypoint."
  type        = "SecureString"

  # URL-encode the password so special chars survive the driver parse.
  # `sensitive = true` on the output; terraform-fmt strips the
  # actual value from non-detailed logs.
  value = "postgresql://${aws_db_instance.outreach.username}:${urlencode(random_password.rds_master.result)}@${aws_db_instance.outreach.address}:${aws_db_instance.outreach.port}/${aws_db_instance.outreach.db_name}"

  tags = local.common_tags

  lifecycle {
    ignore_changes = [value]
  }
}

# --- RDS instance ---------------------------------------------------------

resource "aws_db_instance" "outreach" {
  identifier_prefix = "${local.name_prefix}-db-"

  engine         = "postgres"
  engine_version = "16.4"
  instance_class = "db.t3.micro"

  allocated_storage     = 20
  max_allocated_storage = 100 # auto-scale up to 100 GB
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "outreach"
  username = "pipeline"
  password = random_password.rds_master.result
  port     = 5432

  db_subnet_group_name   = aws_db_subnet_group.outreach.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  parameter_group_name   = aws_db_parameter_group.outreach.name
  publicly_accessible    = false

  backup_retention_period  = 7
  backup_window            = "05:00-05:30" # UTC, low-traffic window
  maintenance_window       = "sun:05:45-sun:06:45"
  delete_automated_backups = false

  # Protect against accidental destroy — both the terraform stanza and
  # the RDS side. `terraform destroy` will refuse until you toggle
  # `deletion_protection = false`, re-apply, then destroy. The extra
  # friction is the point.
  deletion_protection = true

  # Enable Performance Insights for cheap query-level visibility.
  performance_insights_enabled = true

  # Always take a final snapshot so a destroy isn't catastrophic.
  # Snapshot id includes a timestamp so re-creates don't collide with
  # AWS's 30-day name reservation on deleted snapshots.
  skip_final_snapshot       = false
  final_snapshot_identifier = "${local.name_prefix}-db-final-${formatdate("YYYYMMDD-hhmm", timestamp())}"

  # Tune for Outreach's needs. LangGraph checkpoints update frequently
  # so disable auto minor-version upgrades during user hours; do them
  # manually in the maintenance window.
  auto_minor_version_upgrade = false

  # Enhanced monitoring: 60-second granularity on OS-level metrics.
  # Free tier includes limited monitoring; this adds CPU, memory,
  # disk I/O at instance level. Role + policy are required.
  monitoring_interval = 60
  monitoring_role_arn = aws_iam_role.rds_monitoring.arn

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-rds"
  })

  lifecycle {
    # Terraform keeps trying to reapply the password from the
    # random_password resource on every run otherwise. The DB
    # password is its own thing; rotate via the RDS CLI when
    # needed.
    ignore_changes = [
      password,
      final_snapshot_identifier,
    ]
  }
}

# --- Enhanced monitoring IAM role ----------------------------------------

resource "aws_iam_role" "rds_monitoring" {
  name_prefix = "${local.name_prefix}-rds-mon-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "monitoring.rds.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  role       = aws_iam_role.rds_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}
