"""Text validation using tone_guard's check_tone(), filtered by profile.

This is the shared validation infrastructure. It knows about text quality
checks and profiles, not about emails or grades. Services call it.
"""
from __future__ import annotations

import os
import sys
from typing import Any

from pydantic import BaseModel, Field

from outreach_pipeline.validation.profiles import PROFILES


class ValidationResult(BaseModel):
    """Result of validating text against a profile."""
    violations: list[dict[str, Any]] = Field(default_factory=list)
    passed: bool = True
    profile: str = ""


def _import_check_tone():
    """Import check_tone from the hooks directory."""
    hooks_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", ".claude", "hooks")
    hooks_dir = os.path.normpath(hooks_dir)
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)
    from tone_guard import check_tone
    return check_tone


def validate(text: str, profile: str) -> ValidationResult:
    """Validate text against a named profile.

    Args:
        text: The text to validate.
        profile: Profile name (must exist in PROFILES).

    Returns:
        ValidationResult with violations filtered to the profile's check list.
        In 'block' mode, passed=False if any violations found.
        In 'audit' mode, passed=True always (violations still reported).

    Raises:
        ValueError: If profile name is not recognized.
    """
    if profile not in PROFILES:
        raise ValueError(f"Unknown validation profile: {profile!r}. Available: {list(PROFILES.keys())}")

    profile_def = PROFILES[profile]
    allowed_checks = set(profile_def["checks"])
    mode = profile_def["mode"]

    check_tone = _import_check_tone()
    all_violations = check_tone(text, body=text)

    # Filter to only checks in this profile
    filtered = [v for v in all_violations if v.get("label", "") in allowed_checks]

    if mode == "audit":
        return ValidationResult(violations=filtered, passed=True, profile=profile)
    else:
        return ValidationResult(violations=filtered, passed=len(filtered) == 0, profile=profile)
