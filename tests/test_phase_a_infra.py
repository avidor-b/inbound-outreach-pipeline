"""Phase A infrastructure regressions.

These are STATIC tests — they read the terraform + user_data files
and assert on their content. No AWS calls. Pre-commit safe.

What they protect:
  - IAM policy is least-privilege (scoped to /outreach/<env>/*)
  - SSM parameters don't accidentally commit real secrets
  - CloudWatch alarms point at the right dimensions
  - user_data.sh pulls from SSM + launches the CWAgent
  - Dependabot config covers all ecosystems we ship
  - IAM role only attaches the four specific managed policies

A `terraform validate` would cover syntax but these assert *intent*.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

TERRAFORM_DIR = Path(__file__).parent.parent / "terraform"


def _read(path: str) -> str:
    return (TERRAFORM_DIR / path).read_text()


def test_iam_policy_scopes_ssm_to_outreach_prefix_only():
    src = _read("iam.tf")
    # The inline policy Resource must be scoped to /outreach/<env>/*.
    assert 'parameter/outreach/$' in src, "SSM policy must be env-scoped, not wildcard"
    # KMS Decrypt must be conditional on ssm.<region>.amazonaws.com — not
    # a bare `Resource: *` with no conditions.
    assert '"kms:ViaService"' in src
    assert '"ssm.' in src


def test_iam_policy_grants_both_path_and_children_for_getbypath():
    """`ssm:GetParametersByPath` authorises against the path arn itself
    (no trailing slash), not against `.../parameter/path/*`. Missing
    the bare path arn returns AccessDeniedException and breaks the
    user_data bootstrap (we learned this the hard way).
    """
    src = _read("iam.tf")
    # Bare path arn (no /* trailing) must be present.
    assert 'parameter/outreach/${var.environment}"' in src, \
        "SSM policy must include the bare path arn for GetParametersByPath"
    # Plus the children pattern for GetParameter calls.
    assert 'parameter/outreach/${var.environment}/*"' in src, \
        "SSM policy must include the children wildcard for individual gets"


def test_iam_role_only_attaches_expected_managed_policies():
    """Any new AWS managed policy attached to the EC2 role is a
    security review trigger — force a code change here to list it.
    """
    src = _read("iam.tf")
    expected = {
        "AmazonEC2ContainerRegistryReadOnly",
        "CloudWatchAgentServerPolicy",
        "AmazonSSMManagedInstanceCore",
    }
    attached = set(re.findall(r"arn:aws:iam::aws:policy/([A-Za-z0-9]+)", src))
    assert attached == expected, \
        f"Unexpected managed policies on the EC2 role: {attached ^ expected}"


def test_ssm_parameters_declare_placeholder_not_real_secret():
    """Terraform state leaks plaintext values — every SecureString
    must be declared with PLACEHOLDER so operators rotate via CLI.
    """
    src = _read("ssm.tf")
    assert "PLACEHOLDER" in src, "SSM shell parameters must use placeholder values"
    assert "ignore_changes = [value]" in src, \
        "SSM values must be ignored by Terraform post-creation"


def test_cloudwatch_alarms_target_instance_id():
    src = _read("cloudwatch.tf")
    # Each alarm dimension must include InstanceId bound to the outreach EC2.
    assert src.count("InstanceId = aws_instance.outreach.id") >= 4, \
        "All four alarms must target the specific EC2 instance"
    # Memory + disk come from the CWAgent namespace.
    assert 'namespace           = "CWAgent"' in src
    # Email subscription is present.
    assert 'protocol  = "email"' in src


def test_user_data_pulls_secrets_from_ssm_not_file():
    src = _read("user_data.sh")
    assert "aws ssm get-parameters-by-path" in src, \
        "Bootstrap must pull secrets from SSM — NOT from a baked .env"
    assert "with-decryption" in src, \
        "SecureStrings must be decrypted at pull time"
    # Must clean the env file before writing so rotated keys replace
    # old values instead of appending.
    assert ': > "$ENV_FILE"' in src
    # ECR credential helper must be installed (deploys rely on it).
    assert "docker-credential-ecr-login" in src


def test_user_data_installs_cloudwatch_agent():
    src = _read("user_data.sh")
    assert "amazon-cloudwatch-agent" in src
    assert "amazon-cloudwatch-agent-ctl" in src
    assert "systemctl enable amazon-cloudwatch-agent" in src


def test_ecr_has_scan_on_push_and_lifecycle():
    src = _read("ecr.tf")
    assert src.count("scan_on_push = true") >= 2, \
        "Both repos must have scan_on_push enabled"
    # Lifecycle keeps a bounded number of images to cap storage cost.
    assert '"imageCountMoreThan"' in src


def test_ec2_uses_instance_profile_and_imdsv2():
    src = _read("main.tf")
    assert "iam_instance_profile        = aws_iam_instance_profile.outreach.name" in src
    # IMDSv2 must remain required — pre-existing but regression-tested.
    assert 'http_tokens                 = "required"' in src


def test_dependabot_covers_all_ecosystems():
    cfg = (TERRAFORM_DIR.parent / ".github" / "dependabot.yml").read_text()
    # pip + npm + docker (root + frontend) + github-actions.
    for expected in ('"pip"', '"npm"', '"docker"', '"github-actions"'):
        assert expected in cfg, f"Dependabot missing {expected} ecosystem"
    # Frontend is a separate directory — must be listed explicitly.
    assert "/frontend" in cfg
