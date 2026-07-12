"""Async Postgres tests for engagements — CRUD, lifecycle, retarget."""
import pytest
import asyncpg

from outreach_pipeline.db.contacts import upsert_contact
from outreach_pipeline.db.submissions import create_submission
from outreach_pipeline.db.engagements import (
    create_engagement, get_engagement, update_status,
    score_engagement, add_submission, get_submissions,
    list_engagements, close_superseded_retargets,
)


async def _seed_contact(db):
    """Seed a contact for FK references."""
    await upsert_contact("c1", {"company": "Acme"})


class TestCreateEngagement:
    @pytest.mark.asyncio
    async def test_create_and_get(self, db):
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        eng = await get_engagement(eid)
        assert eng["contact_id"] == "c1"
        assert eng["kind"] == "sales"
        assert eng["status"] == "open"
        assert eng["opened_at"]

    @pytest.mark.asyncio
    async def test_create_blocked_support(self, db):
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="support",
                                      status="blocked", block_code="SUPPORT_REQUEST",
                                      block_reason="Route to support", block_terminal=True)
        eng = await get_engagement(eid)
        assert eng["status"] == "blocked"
        assert eng["block_terminal"] is True


class TestStatusTransitions:
    @pytest.mark.asyncio
    async def test_valid_transition_open_to_scoring(self, db):
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        await update_status(eid, "scoring")
        assert (await get_engagement(eid))["status"] == "scoring"

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, db):
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        for status in ["scoring", "drafting", "pending_approval", "approved", "sent", "closed"]:
            await update_status(eid, status)
            assert (await get_engagement(eid))["status"] == status

    @pytest.mark.asyncio
    async def test_invalid_transition_raises(self, db):
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        with pytest.raises(ValueError):
            await update_status(eid, "sent")  # open -> sent is invalid

    @pytest.mark.asyncio
    async def test_closed_is_terminal(self, db):
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        await update_status(eid, "closed")
        with pytest.raises(ValueError):
            await update_status(eid, "open")

    @pytest.mark.asyncio
    async def test_policy_blocked_can_reopen(self, db):
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        await update_status(eid, "blocked", block_code="COOLDOWN",
                            block_reason="Sent 5 days ago")
        eng = await get_engagement(eid)
        assert eng["block_terminal"] is False
        await update_status(eid, "open")
        assert (await get_engagement(eid))["status"] == "open"

    @pytest.mark.asyncio
    async def test_terminal_blocked_cannot_reopen(self, db):
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="support",
                                      status="blocked", block_code="SUPPORT_REQUEST",
                                      block_terminal=True)
        with pytest.raises(ValueError):
            await update_status(eid, "open")

    @pytest.mark.asyncio
    async def test_terminal_blocked_can_close(self, db):
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="support",
                                      status="blocked", block_terminal=True)
        await update_status(eid, "closed")
        assert (await get_engagement(eid))["status"] == "closed"

    @pytest.mark.asyncio
    async def test_rejection_goes_back_to_drafting(self, db):
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        await update_status(eid, "scoring")
        await update_status(eid, "drafting")
        await update_status(eid, "pending_approval")
        await update_status(eid, "drafting")  # re-draft after rejection
        assert (await get_engagement(eid))["status"] == "drafting"


class TestEngagementSubmissions:
    @pytest.mark.asyncio
    async def test_add_submission(self, db):
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        sid = await create_submission("c1", message="Need pricing")
        await add_submission(eid, sid)
        subs = await get_submissions(eid)
        assert len(subs) == 1
        assert subs[0]["message"] == "Need pricing"

    @pytest.mark.asyncio
    async def test_multiple_submissions(self, db):
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        sid1 = await create_submission("c1", message="First")
        sid2 = await create_submission("c1", message="Second")
        await add_submission(eid, sid1)
        await add_submission(eid, sid2)
        subs = await get_submissions(eid)
        assert len(subs) == 2


class TestListEngagements:
    @pytest.mark.asyncio
    async def test_list_by_contact(self, db):
        await _seed_contact(db)
        await create_engagement("c1", kind="sales")
        await create_engagement("c1", kind="support", status="blocked", block_terminal=True)
        engs = await list_engagements(contact_id="c1")
        assert len(engs) == 2

    @pytest.mark.asyncio
    async def test_list_by_kind(self, db):
        await _seed_contact(db)
        await create_engagement("c1", kind="sales")
        await create_engagement("c1", kind="support", status="blocked", block_terminal=True)
        engs = await list_engagements(kind="sales")
        assert len(engs) == 1

    @pytest.mark.asyncio
    async def test_list_by_status(self, db):
        await _seed_contact(db)
        eid1 = await create_engagement("c1", kind="sales")
        eid2 = await create_engagement("c1", kind="sales")
        await update_status(eid1, "scoring")
        engs = await list_engagements(status="open")
        assert len(engs) == 1
        assert engs[0]["id"] == eid2


class TestScoreEngagement:
    @pytest.mark.asyncio
    async def test_score(self, db):
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        await score_engagement(eid, score=85, score_reasoning="Strong signals",
                               primary_product="microwave_radio")
        eng = await get_engagement(eid)
        assert eng["score"] == 85
        assert eng["primary_product"] == "microwave_radio"


class TestRetarget:
    @pytest.mark.asyncio
    async def test_create_retarget(self, db):
        await _seed_contact(db)
        original = await create_engagement("c1", kind="sales")
        await update_status(original, "scoring")
        await update_status(original, "drafting")
        await update_status(original, "pending_approval")
        await update_status(original, "approved")
        await update_status(original, "sent")

        retarget = await create_engagement("c1", kind="retarget",
                                           retarget_of=original)
        eng = await get_engagement(retarget)
        assert eng["kind"] == "retarget"
        assert eng["retarget_of"] == original

    @pytest.mark.asyncio
    async def test_only_one_retarget_per_origin(self, db):
        await _seed_contact(db)
        original = await create_engagement("c1", kind="sales")
        await update_status(original, "scoring")
        await update_status(original, "drafting")
        await update_status(original, "pending_approval")
        await update_status(original, "approved")
        await update_status(original, "sent")

        await create_engagement("c1", kind="retarget", retarget_of=original)
        with pytest.raises(asyncpg.UniqueViolationError):
            await create_engagement("c1", kind="retarget", retarget_of=original)

    @pytest.mark.asyncio
    async def test_new_submission_supersedes_retarget(self, db):
        await _seed_contact(db)
        original = await create_engagement("c1", kind="sales")
        for s in ["scoring", "drafting", "pending_approval", "approved", "sent"]:
            await update_status(original, s)

        retarget = await create_engagement("c1", kind="retarget",
                                           retarget_of=original)
        # New submission arrives — close retarget
        await close_superseded_retargets("c1")
        eng = await get_engagement(retarget)
        assert eng["status"] == "closed"
        assert eng["closed_reason"] == "superseded_by_new_submission"
