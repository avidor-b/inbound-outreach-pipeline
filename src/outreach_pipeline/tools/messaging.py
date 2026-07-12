"""Messaging domain tools — generate, validate, convenience composition.

generate_message and validate_message are distinct, separable capabilities.
generate_validated_message is a messaging convenience, not a general abstraction.
"""
from __future__ import annotations

from outreach_pipeline.validation.tone import validate, ValidationResult


def validate_message(text: str, profile: str = "cold_outreach") -> ValidationResult:
    """Validate message text against a named profile.

    This is a standalone capability — it can be called independently
    of generate_message for ad-hoc validation.
    """
    return validate(text, profile=profile)
