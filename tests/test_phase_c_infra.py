"""Phase C infrastructure regressions — RDS Postgres migration."""
from __future__ import annotations

import re
from pathlib import Path

TERRAFORM_DIR = Path(__file__).parent.parent / "terraform"


def _read(path: str) -> str:
    return (TERRAFORM_DIR / path).read_text()


def test_rds_in_private_subnets_only():
    src = _read("rds.tf")
    # Subnet group uses the private subnets, never the public ones.
    assert "aws_subnet.private" in src
    assert "aws_subnet.public" not in src, \
        "RDS subnet group must not include public subnets"


def test_rds_encryption_at_rest_enabled():
    src = _read("rds.tf")
    assert "storage_encrypted     = true" in src, \
        "RDS storage_encrypted must be true (EBS volumes use default KMS key)"


def test_rds_not_publicly_accessible():
    src = _read("rds.tf")
    assert "publicly_accessible    = false" in src


def test_rds_backups_configured():
    src = _read("rds.tf")
    assert re.search(r"backup_retention_period\s*=\s*([1-9]|[1-2]\d|30)", src), \
        "backup_retention_period must be ≥1 day (≤35)"


def test_rds_sg_restricts_ingress_to_ec2_sg():
    """Only the application EC2 SG can reach 5432. No CIDR-based
    allowances, no public exposure.
    """
    src = _read("rds.tf")
    # Scope to the ingress block on the RDS SG.
    sg_block = re.search(
        r'resource "aws_security_group" "rds"[\s\S]+?^\}',
        src, flags=re.MULTILINE,
    )
    assert sg_block
    text = sg_block.group(0)
    # Must use SG reference, never a cidr_block.
    assert "security_groups = [aws_security_group.instance.id]" in text
    # No cidr_blocks on ingress in this SG.
    ingress_block = re.search(
        r"ingress\s*\{[\s\S]+?^\s*\}", text, flags=re.MULTILINE,
    )
    assert ingress_block
    assert "cidr_blocks" not in ingress_block.group(0), \
        "RDS ingress must be SG-to-SG only, never CIDR"


def test_database_url_stored_in_ssm_securestring():
    src = _read("rds.tf")
    assert 'resource "aws_ssm_parameter" "database_url"' in src
    assert 'type        = "SecureString"' in src


def test_rds_password_generated_not_hardcoded():
    src = _read("rds.tf")
    assert 'resource "random_password" "rds_master"' in src
    assert "length  = 32" in src, "password must be at least 32 chars"
    # No hardcoded password anywhere.
    assert "password = \"" not in src


def test_remote_deploy_no_longer_overwrites_env():
    src = _read("remote_deploy.sh")
    # The old script wrote DATABASE_URL into a hand-crafted heredoc.
    # Post-Phase-C, secrets live only in SSM and refresh-env.sh
    # regenerates .env on every deploy.
    assert "postgresql://pipeline:pipeline@postgres" not in src, \
        "DATABASE_URL must not be hardcoded in the deploy script"
    assert "/opt/outreach/refresh-env.sh" in src, \
        "Deploy script must refresh .env via the SSM-pull helper"
    # No more --profile dev: dockerized postgres gone, RDS handles it.
    assert "--profile dev" not in src, \
        "Default compose profile now, no local postgres in prod"


def test_user_data_installs_refresh_script():
    src = _read("user_data.sh")
    assert "/opt/outreach/refresh-env.sh" in src, \
        "user_data must install refresh-env.sh so deploys can re-pull secrets"
