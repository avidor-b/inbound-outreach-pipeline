"""Engagement-level policies — cooldown, score threshold."""
from __future__ import annotations

from outreach_pipeline.db.engagements import get_engagement
from outreach_pipeline.policies.decisions import PolicyDecision


async def check_cooldown(engagement_id: int, cooldown_days: int = 30) -> PolicyDecision:
    """Check if an engagement was sent too recently."""
    eng = await get_engagement(engagement_id)
    if not eng:
        return PolicyDecision.deny("ENGAGEMENT_NOT_FOUND", f"Engagement {engagement_id} not found")

    if eng["status"] == "sent":
        # Check if sent within cooldown window
        if eng.get("closed_at"):
            # Already closed — cooldown doesn't apply
            return PolicyDecision.allow()
        # Was sent and not closed — still in cooldown
        return PolicyDecision.deny("COOLDOWN", f"Engagement was already sent")

    return PolicyDecision.allow()


async def check_score_threshold(engagement_id: int, threshold: int = 40) -> PolicyDecision:
    """Check if engagement score meets the threshold for drafting."""
    eng = await get_engagement(engagement_id)
    if not eng:
        return PolicyDecision.deny("ENGAGEMENT_NOT_FOUND", f"Engagement {engagement_id} not found")

    if eng["score"] < threshold:
        return PolicyDecision.deny(
            "BELOW_SCORE_THRESHOLD",
            f"Score {eng['score']} below threshold {threshold}",
        )

    return PolicyDecision.allow()
