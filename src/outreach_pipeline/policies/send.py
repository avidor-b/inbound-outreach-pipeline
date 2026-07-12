"""Send policies — preconditions for sending an email.

Facade over db/guardrails.py functions.
"""
from __future__ import annotations

from outreach_pipeline.policies.codes import Codes
from outreach_pipeline.policies.decisions import PolicyDecision


async def check_send_policies() -> PolicyDecision:
    """Check send-level policies (daily limit)."""
    from outreach_pipeline.db.guardrails import check_send_limit

    blocked, reason, count = await check_send_limit()
    if blocked:
        return PolicyDecision.deny(Codes.SEND_LIMIT_REACHED, reason, sends_today=count)

    return PolicyDecision.allow()
