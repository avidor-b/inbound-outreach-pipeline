"""Unit tests for engagement-aware services — scoring, messaging, approvals, sending."""
import json
import pytest

from outreach_pipeline.db.contacts import upsert_contact
from outreach_pipeline.db.submissions import create_submission
from outreach_pipeline.db.engagements import (
    create_engagement, add_submission, update_status,
    get_engagement, score_engagement,
)
from outreach_pipeline.db.threads import create_user, create_thread, get_artifact


async def _seed_engagement():
    """Create a contact, submission, and engagement. Returns (submission_id, engagement_id)."""
    await upsert_contact("c1", {"company": "Acme", "message": "Need pricing"})
    sid = await create_submission("c1", message="Need pricing for RADIO-A",
                                 mql_date="2026-03-28T10:00:00Z")
    eid = await create_engagement("c1", kind="sales")
    await add_submission(eid, sid)
    return sid, eid


class TestScoringService:
    @pytest.mark.asyncio
    async def test_build_engagement_context(self, db):
        sid, eid = await _seed_engagement()
        from outreach_pipeline.services.scoring import build_engagement_context
        ctx = await build_engagement_context(eid)
        assert ctx["engagement"]["id"] == eid
        assert len(ctx["submissions"]) == 1
        assert ctx["submissions"][0]["message"] == "Need pricing for RADIO-A"
        assert ctx["contact"]["id"] == "c1"

    @pytest.mark.asyncio
    async def test_build_context_multiple_submissions(self, db):
        sid, eid = await _seed_engagement()
        from outreach_pipeline.services.scoring import build_engagement_context
        sid2 = await create_submission("c1", message="Also need accessories")
        await add_submission(eid, sid2)
        ctx = await build_engagement_context(eid)
        assert len(ctx["submissions"]) == 2

    @pytest.mark.asyncio
    async def test_save_engagement_score(self, db):
        sid, eid = await _seed_engagement()
        from outreach_pipeline.services.scoring import save_engagement_score
        await save_engagement_score(eid, score=85,
                                    score_reasoning="Strong signals", primary_product="microwave_radio")
        eng = await get_engagement(eid)
        assert eng["score"] == 85
        assert eng["primary_product"] == "microwave_radio"


class TestMessagingService:
    @pytest.mark.asyncio
    async def test_build_engagement_message_context(self, db):
        sid, eid = await _seed_engagement()
        from outreach_pipeline.services.messaging import build_engagement_message_context
        await score_engagement(eid, score=80)
        ctx = await build_engagement_message_context(eid)
        assert ctx["engagement"]["id"] == eid
        assert ctx["contact"]["id"] == "c1"
        assert len(ctx["submissions"]) > 0

    @pytest.mark.asyncio
    async def test_save_engagement_draft(self, db):
        sid, eid = await _seed_engagement()
        from outreach_pipeline.services.messaging import save_engagement_draft
        await create_user("u1", "u1@example.com", "User One")
        tid = await create_thread("Test", "u1")
        aid = await save_engagement_draft(
            eid, tid,
            subject="Hi there", body="Hello from us", score=80,
        )
        assert aid is not None
        art = await get_artifact(aid)
        assert art["type"] == "draft"
        data = art["data"] if isinstance(art["data"], dict) else json.loads(art["data"])
        assert data["engagement_id"] == eid


class TestApprovalsService:
    @pytest.mark.asyncio
    async def test_approve_engagement_draft(self, db):
        sid, eid = await _seed_engagement()
        from outreach_pipeline.services.messaging import save_engagement_draft
        from outreach_pipeline.services.approvals import request_approval, approve
        await create_user("u1", "u1@example.com", "User One")
        tid = await create_thread("Test", "u1")
        await score_engagement(eid, score=80)
        await update_status(eid, "scoring")
        await update_status(eid, "drafting")
        aid = await save_engagement_draft(eid, tid,
                                          subject="Hi", body="Hello", score=80)
        await request_approval(aid)
        await update_status(eid, "pending_approval")
        await approve(aid, "u1")
        # Artifact should be approved
        art = await get_artifact(aid)
        assert art["status"] == "approved"

    @pytest.mark.asyncio
    async def test_reject_engagement_draft(self, db):
        sid, eid = await _seed_engagement()
        from outreach_pipeline.services.messaging import save_engagement_draft
        from outreach_pipeline.services.approvals import request_approval, reject
        await create_user("u1", "u1@example.com", "User One")
        tid = await create_thread("Test", "u1")
        aid = await save_engagement_draft(eid, tid,
                                          subject="Hi", body="Hello", score=80)
        await request_approval(aid)
        await reject(aid, "u1", feedback="Too long")
        art = await get_artifact(aid)
        assert art["status"] == "rejected"


class TestSendingService:
    @pytest.mark.asyncio
    async def test_check_engagement_send_readiness(self, db):
        sid, eid = await _seed_engagement()
        from outreach_pipeline.services.sending import check_engagement_send_readiness
        await score_engagement(eid, score=80)
        # Not approved yet
        decision = await check_engagement_send_readiness(eid)
        assert not decision.allowed

    @pytest.mark.asyncio
    async def test_approved_engagement_ready(self, db):
        sid, eid = await _seed_engagement()
        from outreach_pipeline.services.sending import check_engagement_send_readiness
        await score_engagement(eid, score=80)
        for s in ["scoring", "drafting", "pending_approval", "approved"]:
            await update_status(eid, s)
        decision = await check_engagement_send_readiness(eid)
        assert decision.allowed

    @pytest.mark.asyncio
    async def test_blocked_engagement_not_ready(self, db):
        sid, eid = await _seed_engagement()
        from outreach_pipeline.services.sending import check_engagement_send_readiness
        await update_status(eid, "blocked", block_code="COOLDOWN")
        decision = await check_engagement_send_readiness(eid)
        assert not decision.allowed
