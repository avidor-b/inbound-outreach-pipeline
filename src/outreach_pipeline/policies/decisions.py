"""Structured policy decision model."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PolicyDecision:
    """Result of a policy check."""
    allowed: bool
    code: str
    reason: str
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def allow(cls) -> PolicyDecision:
        return cls(allowed=True, code="", reason="")

    @classmethod
    def deny(cls, code: str, reason: str, **details) -> PolicyDecision:
        return cls(allowed=False, code=code, reason=reason, details=details)
