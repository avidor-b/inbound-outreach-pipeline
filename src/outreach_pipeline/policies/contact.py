"""Contact policies — preconditions for operating on a contact.

Facade over db/guardrails.py functions. Middleware and hooks delegate here.
"""
from __future__ import annotations

from outreach_pipeline.policies.codes import Codes
from outreach_pipeline.policies.decisions import PolicyDecision


async def check_contact_policies(contact_id: str) -> PolicyDecision:
    """Run all contact-level policy checks. Returns first denial or allow.

    Checks (in order):
    1. Events contact (trade show meeting request)
    2. Support request (existing customer fault report)
    3. Vendor pitch (supplier trying to sell TO us)
    4. Opt-out (do-not-contact list)
    5. Duplicate cooldown (emailed recently)
    """
    from outreach_pipeline.db.guardrails import (
        check_events_contact,
        check_support_request,
        check_vendor_pitch,
        check_opt_out,
        check_duplicate,
    )

    checks = [
        (Codes.EVENT_BLOCK, check_events_contact),
        (Codes.SUPPORT_REQUEST_BLOCK, check_support_request),
        (Codes.VENDOR_PITCH_BLOCK, check_vendor_pitch),
        (Codes.OPT_OUT, check_opt_out),
        (Codes.DUPLICATE_COOLDOWN, check_duplicate),
    ]

    for code, check_fn in checks:
        blocked, reason = await check_fn(contact_id)
        if blocked:
            return PolicyDecision.deny(code, reason)

    return PolicyDecision.allow()
