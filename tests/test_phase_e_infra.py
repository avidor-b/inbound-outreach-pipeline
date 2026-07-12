"""Phase E — security-review follow-ups.

Locks in the fixes from the post-hardening security review: ECR
immutable tags, ALB drop_invalid_header_fields, RDS deletion
protection + final snapshot, user_data checksum verification.
"""
from __future__ import annotations

import re
from pathlib import Path

TERRAFORM_DIR = Path(__file__).parent.parent / "terraform"


def _read(path: str) -> str:
    return (TERRAFORM_DIR / path).read_text()


def test_ecr_repos_are_immutable():
    src = _read("ecr.tf")
    assert src.count('image_tag_mutability = "IMMUTABLE"') == 2, \
        "both server and frontend ECR repos must use IMMUTABLE tags"
    assert 'image_tag_mutability = "MUTABLE"' not in src


def test_alb_drops_invalid_header_fields():
    src = _read("alb.tf")
    assert re.search(r"drop_invalid_header_fields\s*=\s*true", src), \
        "ALB must drop malformed headers before forwarding"


def test_rds_has_deletion_protection_and_final_snapshot():
    src = _read("rds.tf")
    assert re.search(r"deletion_protection\s*=\s*true", src), \
        "RDS deletion_protection must be enabled"
    assert "skip_final_snapshot = false" in src or \
           re.search(r"skip_final_snapshot\s*=\s*false", src), \
        "RDS must take a final snapshot on destroy"
    assert "final_snapshot_identifier" in src


def test_user_data_verifies_compose_checksum():
    src = _read("user_data.sh")
    assert "COMPOSE_SHA256=" in src
    assert "sha256sum -c -" in src
    # Hash must be a real 64-char hex string, not a placeholder.
    match = re.search(r'COMPOSE_SHA256="([0-9a-f]+)"', src)
    assert match, "COMPOSE_SHA256 must be set"
    assert len(match.group(1)) == 64, "COMPOSE_SHA256 must be 64 hex chars"


def test_user_data_verifies_ecr_helper_checksum():
    src = _read("user_data.sh")
    assert "ECR_HELPER_SHA256=" in src
    match = re.search(r'ECR_HELPER_SHA256="([0-9a-f]+)"', src)
    assert match, "ECR_HELPER_SHA256 must be set"
    assert len(match.group(1)) == 64


def test_dependabot_tracks_terraform_providers():
    cfg = (TERRAFORM_DIR.parent / ".github" / "dependabot.yml").read_text()
    assert 'package-ecosystem: "terraform"' in cfg, \
        "dependabot must track /terraform for provider bumps"
