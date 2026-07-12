"""Unit tests for policies/engagement.py -- engagement-level policies."""
import pytest

from outreach_pipeline.db.contacts import upsert_contact
from outreach_pipeline.db.engagements import (
    create_engagement, update_status, score_engagement,
)


async def _seed(db):
    """Shared setup: one contact."""
    await upsert_contact("c1", {"company": "Acme"})


class TestCooldownPolicy:
    @pytest.mark.asyncio
    async def test_no_cooldown_if_never_sent(self, db):
        await _seed(db)
        from outreach_pipeline.policies.engagement import check_cooldown
        eid = await create_engagement("c1", kind="sales")
        decision = await check_cooldown(eid)
        assert decision.allowed

    @pytest.mark.asyncio
    async def test_cooldown_after_recent_send(self, db):
        await _seed(db)
        from outreach_pipeline.policies.engagement import check_cooldown
        eid = await create_engagement("c1", kind="sales")
        for s in ["scoring", "drafting", "pending_approval", "approved", "sent"]:
            await update_status(eid, s)
        decision = await check_cooldown(eid)
        assert not decision.allowed
        assert decision.code == "COOLDOWN"


class TestScoreThreshold:
    @pytest.mark.asyncio
    async def test_above_threshold(self, db):
        await _seed(db)
        from outreach_pipeline.policies.engagement import check_score_threshold
        eid = await create_engagement("c1", kind="sales")
        await score_engagement(eid, score=75)
        decision = await check_score_threshold(eid, threshold=50)
        assert decision.allowed

    @pytest.mark.asyncio
    async def test_below_threshold(self, db):
        await _seed(db)
        from outreach_pipeline.policies.engagement import check_score_threshold
        eid = await create_engagement("c1", kind="sales")
        await score_engagement(eid, score=30)
        decision = await check_score_threshold(eid, threshold=50)
        assert not decision.allowed
        assert decision.code == "BELOW_SCORE_THRESHOLD"
