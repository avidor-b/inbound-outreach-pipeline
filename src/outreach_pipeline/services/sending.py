"""Sending service — email delivery with readiness checks.

Checks artifact status and engagement status before delivering.
NEVER sends email — that requires explicit user action through the send tool.
"""
from __future__ import annotations

from outreach_pipeline.db.threads import get_artifact
from outreach_pipeline.db.engagements import get_engagement
from outreach_pipeline.policies.decisions import PolicyDecision


async def check_send_readiness(artifact_id: int) -> PolicyDecision:
    """Check if an artifact is ready to send (legacy, artifact-level)."""
    artifact = await get_artifact(artifact_id)
    if not artifact:
        return PolicyDecision.deny("ARTIFACT_NOT_FOUND", f"Artifact {artifact_id} not found")
    if artifact["status"] != "approved":
        return PolicyDecision.deny(
            "NOT_APPROVED",
            f"Artifact status is '{artifact['status']}', must be 'approved' to send",
        )
    from outreach_pipeline.policies.send import check_send_policies
    return await check_send_policies()


async def check_engagement_send_readiness(engagement_id: int) -> PolicyDecision:
    """Check if an engagement is ready to send.

    Requirements:
    - Engagement must exist
    - Status must be 'approved'
    - Not blocked
    - Contact not opted out
    - Daily send limit not reached
    """
    eng = await get_engagement(engagement_id)
    if not eng:
        return PolicyDecision.deny("ENGAGEMENT_NOT_FOUND", f"Engagement {engagement_id} not found")

    if eng["status"] == "blocked":
        return PolicyDecision.deny(
            eng.get("block_code", "BLOCKED"),
            eng.get("block_reason", "Engagement is blocked"),
        )

    if eng["status"] != "approved":
        return PolicyDecision.deny(
            "NOT_APPROVED",
            f"Engagement status is '{eng['status']}', must be 'approved' to send",
        )

    # Contact-level opt-out
    from outreach_pipeline.db.guardrails import check_opt_out
    blocked, reason = await check_opt_out(eng["contact_id"])
    if blocked:
        return PolicyDecision.deny("OPT_OUT", reason)

    # System-level send limit
    from outreach_pipeline.policies.send import check_send_policies
    return await check_send_policies()
