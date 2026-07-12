"""Phase B infrastructure regressions.

VPC + ALB + TLS wiring. Static-only checks — no AWS calls. Pre-
commit safe.
"""
from __future__ import annotations

import re
from pathlib import Path

TERRAFORM_DIR = Path(__file__).parent.parent / "terraform"


def _read(path: str) -> str:
    return (TERRAFORM_DIR / path).read_text()


def test_vpc_has_public_and_private_subnets_in_two_azs():
    src = _read("vpc.tf")
    # Four subnets: public a/b, private a/b.
    assert '"10.0.0.0/24"' in src and '"10.0.1.0/24"' in src, "public subnets missing"
    assert '"10.0.10.0/24"' in src and '"10.0.11.0/24"' in src, "private subnets missing"
    # Multi-AZ comes from data.aws_availability_zones.
    assert "data.aws_availability_zones.available.names" in src


def test_vpc_has_nat_for_private_egress():
    """Private subnets need NAT to reach SSM/ECR/LLM APIs once
    EC2 moves there in Phase D.
    """
    src = _read("vpc.tf")
    assert 'resource "aws_nat_gateway"' in src
    assert 'nat_gateway_id = aws_nat_gateway.outreach.id' in src, \
        "private route table must use the NAT"


def test_vpc_flow_logs_enabled():
    src = _read("vpc.tf")
    assert 'resource "aws_flow_log"' in src
    # Only REJECTs to keep CloudWatch cost down — widen if needed.
    assert 'traffic_type         = "REJECT"' in src


def test_alb_forces_tls_on_443():
    src = _read("alb.tf")
    # HTTPS listener exists with TLS 1.2+ minimum.
    assert 'port              = 443' in src
    assert 'protocol          = "HTTPS"' in src
    assert 'TLS13' in src or 'TLS-1-2' in src, \
        "ALB ssl_policy must enforce modern TLS"


def test_alb_listener_is_https_only():
    """Phase D: no HTTP listener at all — ALB serves HTTPS exclusively.
    Cleartext traffic is rejected at the SG, not redirected.
    """
    src = _read("alb.tf")
    assert 'resource "aws_lb_listener" "https"' in src
    assert 'resource "aws_lb_listener" "http_redirect"' not in src


def test_alb_backend_paths_routed_to_server_tg():
    """API paths (/auth, /copilotkit, /health, etc.) must route to the
    server target group — not the frontend. Frontend is the default.
    ALB caps a single path_pattern condition at 5 values, so the rules
    are intentionally split across multiple listener rules.
    """
    src = _read("alb.tf")
    # Each prefix shows up as a wildcard pattern (/auth*, /threads*, ...).
    for prefix in ["/health", "/auth*", "/threads*", "/contacts*", "/engagements*", "/pipeline*", "/copilotkit*"]:
        assert prefix in src, f"API path {prefix} must be routed to the server target group"
    # Both listener rules must target the server target group.
    assert src.count("aws_lb_target_group.server.arn") >= 2, \
        "expected ≥2 references to server target group (core + pipeline rules)"


def test_alb_uses_dns_validated_acm_certificate():
    alb = _read("alb.tf")
    dns = _read("dns_cert.tf")
    assert 'resource "tls_self_signed_cert"' not in alb
    assert 'resource "aws_acm_certificate" "app"' in dns
    assert 'resource "aws_acm_certificate_validation" "app"' in dns
    assert "aws_acm_certificate_validation.app.certificate_arn" in alb


def test_instance_sg_accepts_app_ports_only_from_alb():
    """App ports on EC2 must NOT be open to the public internet —
    only the ALB's SG should reach them. The public sees HTTPS on
    the ALB and that's it.
    """
    src = _read("main.tf")
    # An ingress block ends at a brace that matches its opener.
    # Use a forgiving match: capture from `ingress {` up to the next
    # block keyword or the SG close.
    blocks = re.split(r"\n\s*ingress\s*\{", src)
    app_port_blocks = [b for b in blocks if "FastAPI" in b or "Next.js" in b]
    assert len(app_port_blocks) >= 2, \
        f"expected ≥2 app-port ingress blocks, got {len(app_port_blocks)}"
    for block in app_port_blocks:
        # Slice to the first `}` so we don't bleed into the next
        # ingress block's attributes.
        head = block.split("}")[0]
        assert "security_groups" in head, \
            "app-port ingress must use SG reference"
        assert "aws_security_group.alb.id" in head
        assert "cidr_blocks" not in head, \
            "app-port ingress must NOT use cidr_blocks — ALB SG only"


def test_ec2_lives_in_new_vpc():
    """Phase D moves the instance into private-a — still in the
    Outreach VPC, just off the public subnets."""
    src = _read("main.tf")
    assert 'aws_subnet.private["a"].id' in src, \
        "EC2 must be in the new VPC's private-a subnet (Phase D lockdown)"
    # Default VPC data source must be gone.
    assert 'data "aws_vpc" "default"' not in src
    assert 'data.aws_subnets' not in src
