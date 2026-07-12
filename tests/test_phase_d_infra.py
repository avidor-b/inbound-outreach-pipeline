"""Phase D infrastructure regressions — lockdown.

CloudTrail + VPC flow logs + HTTPS-only + SSH removal + cookie-secure
flag. Phase D is the final hardening layer.
"""
from __future__ import annotations

from pathlib import Path

TERRAFORM_DIR = Path(__file__).parent.parent / "terraform"


def _read(path: str) -> str:
    return (TERRAFORM_DIR / path).read_text()


def test_cloudtrail_writes_to_encrypted_private_bucket():
    src = _read("phase_d_lockdown.tf")
    assert 'resource "aws_cloudtrail" "outreach"' in src
    assert 'resource "aws_s3_bucket_public_access_block" "cloudtrail"' in src
    assert "block_public_acls       = true" in src
    assert "block_public_policy     = true" in src
    assert 'resource "aws_s3_bucket_server_side_encryption_configuration" "cloudtrail"' in src


def test_cloudtrail_log_file_validation_enabled():
    """Tampering detection — CloudTrail hash-signs every log file."""
    src = _read("phase_d_lockdown.tf")
    assert "enable_log_file_validation    = true" in src


def test_cloudtrail_bucket_lifecycle_caps_cost():
    src = _read("phase_d_lockdown.tf")
    assert 'aws_s3_bucket_lifecycle_configuration' in src
    assert "days = 90" in src, "CloudTrail retention capped at 90 days"


def test_ec2_has_no_public_ip_and_lives_in_private_subnet():
    """Instance must sit in a private subnet with no public IP — no
    direct ingress from the internet, ALB is the only path in."""
    src = _read("main.tf")
    assert "associate_public_ip_address = false" in src
    assert 'subnet_id                   = aws_subnet.private["a"].id' in src


def test_no_ssh_ingress_on_instance_sg():
    """The instance SG must not allow port 22 from anywhere — shell
    access is via SSM Session Manager only."""
    src = _read("main.tf")
    # No "from_port = 22" or "to_port = 22" anywhere in the SG block.
    assert "from_port       = 22" not in src
    assert "to_port         = 22" not in src
    assert "shell access via SSM Session Manager" in src


def test_no_elastic_ip_resource():
    """Phase D removes the public EIP on the instance entirely."""
    src = _read("main.tf")
    assert 'resource "aws_eip" "outreach"' not in src


def test_alb_has_no_http_listener():
    """HTTPS-only — no cleartext listener, not even a 301 redirect.
    ALB SG should only accept 443."""
    src = _read("alb.tf")
    assert 'resource "aws_lb_listener" "http_redirect"' not in src
    # ALB SG: no port-80 ingress rule
    assert 'from_port   = 80' not in src
    assert 'to_port     = 80' not in src


def test_cookie_secure_flag_set_in_refresh_env():
    """Production cookies must carry Secure; HTTPS-only deployment
    means COOKIE_SECURE=true in the baked-in env."""
    src = _read("user_data.sh")
    assert "COOKIE_SECURE=true" in src
    assert "COOKIE_SECURE=false" not in src


def test_vpc_flow_logs_capture_rejects():
    """Flow logs are the forensic trail when CloudTrail can't see
    packet-level activity. REJECT is the higher-signal filter for
    lockdown posture."""
    import re
    src = _read("vpc.tf")
    assert 'resource "aws_flow_log"' in src
    assert re.search(r'traffic_type\s*=\s*"REJECT"', src), \
        "flow log must capture REJECT traffic"


def test_alb_uses_tls12_plus_policy():
    src = _read("alb.tf")
    # The modern TLS 1.2+ ELB policy family — accept either the 1.3
    # or the 1.2-2021-06 variant since both enforce a 1.2 floor.
    assert (
        "ElasticLoadBalancing-TLS-1-2-" in src
        or "ELBSecurityPolicy-TLS13-1-2-2021-06" in src
        or "ELBSecurityPolicy-TLS-1-2-2017-01" in src
    )
