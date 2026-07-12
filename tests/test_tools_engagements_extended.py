"""Tests for extended engagement tools — retarget, status update, reclassify."""
import pytest

from outreach_pipeline.db.contacts import upsert_contact, get_contact
from outreach_pipeline.db.submissions import create_submission, get_submission
from outreach_pipeline.db.engagements import (
    create_engagement, add_submission,
    update_status, get_engagement,
    score_engagement as db_score_engagement,
    get_submissions,
)
from outreach_pipeline.db.guardrails import add_opt_out, check_opt_out


async def _seed_contact(db):
    """Shared setup: single contact."""
    await upsert_contact("c1", {"company": "Acme"})


class TestUpdateEngagementStatus:
    @pytest.mark.asyncio
    async def test_valid_transition(self, db):
        from outreach_pipeline.tools.engagements import update_engagement_status
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        result = await update_engagement_status(eid, "scoring")
        assert result["success"]
        assert result["engagement"]["status"] == "scoring"

    @pytest.mark.asyncio
    async def test_invalid_transition(self, db):
        from outreach_pipeline.tools.engagements import update_engagement_status
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        result = await update_engagement_status(eid, "sent")
        assert not result["success"]
        assert "Invalid transition" in result["error"]

    @pytest.mark.asyncio
    async def test_block_with_code(self, db):
        from outreach_pipeline.tools.engagements import update_engagement_status
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        result = await update_engagement_status(
            eid, "blocked",
            block_code="COOLDOWN",
            block_reason="Sent 5 days ago",
        )
        assert result["success"]
        assert result["engagement"]["block_code"] == "COOLDOWN"


class TestCreateRetarget:
    async def _create_sent_engagement(self):
        eid = await create_engagement("c1", kind="sales", primary_product="microwave_radio")
        sid = await create_submission("c1", message="Need pricing")
        await add_submission(eid, sid)
        for s in ["scoring", "drafting", "pending_approval", "approved", "sent"]:
            await update_status(eid, s)
        return eid

    @pytest.mark.asyncio
    async def test_create_retarget_success(self, db):
        from outreach_pipeline.tools.engagements import create_retarget
        await _seed_contact(db)
        original = await self._create_sent_engagement()
        result = await create_retarget(original)
        assert result["success"]
        eng = await get_engagement(result["engagement_id"])
        assert eng["kind"] == "retarget"
        assert eng["retarget_of"] == original
        assert eng["primary_product"] == "microwave_radio"

    @pytest.mark.asyncio
    async def test_retarget_inherits_submissions(self, db):
        from outreach_pipeline.tools.engagements import create_retarget
        await _seed_contact(db)
        original = await self._create_sent_engagement()
        result = await create_retarget(original)
        subs = await get_submissions(result["engagement_id"])
        assert len(subs) > 0

    @pytest.mark.asyncio
    async def test_retarget_on_unsent_fails(self, db):
        from outreach_pipeline.tools.engagements import create_retarget
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        result = await create_retarget(eid)
        assert not result["success"]

    @pytest.mark.asyncio
    async def test_duplicate_retarget_fails(self, db):
        from outreach_pipeline.tools.engagements import create_retarget
        await _seed_contact(db)
        original = await self._create_sent_engagement()
        r1 = await create_retarget(original)
        r2 = await create_retarget(original)
        assert r1["success"]
        assert not r2["success"]


class TestReclassifySubmission:
    @pytest.mark.asyncio
    async def test_reclassify(self, db):
        from outreach_pipeline.tools.engagements import reclassify_submission
        await _seed_contact(db)
        sid = await create_submission("c1", message="Need pricing")
        result = await reclassify_submission(
            sid, intent_type="sales_inquiry", product_family="microwave_radio",
        )
        assert result["success"]
        sub = await get_submission(sid)
        assert sub["intent_type"] == "sales_inquiry"
        assert sub["product_family"] == "microwave_radio"

    @pytest.mark.asyncio
    async def test_reclassify_by_contact_id_updates_all_submissions(self, db):
        """When a contact_id is passed via the graph-tool wrapper,
        every submission-with-a-message for that contact should flip —
        users saying 'reclassify Example Logistics' mean all of them."""
        from outreach_pipeline.db.submissions import classify_submission

        # Contact IDs are large numbers that overflow int32 (the wrapper
        # uses 2_000_000_000 as the threshold to distinguish them from
        # real submission IDs). HubSpot-style IDs fit that.
        big_contact_id = "123456789012345"
        await upsert_contact(big_contact_id, {"company": "Example Logistics"})

        sid1 = await create_submission(big_contact_id, message="We need wireless radios")
        sid2 = await create_submission(big_contact_id, message="Pricing request")
        sid3 = await create_submission(big_contact_id, message="")  # no message — should be skipped

        # Both seeded as off_topic so we can verify the flip.
        for sid in (sid1, sid2):
            await classify_submission(sid, intent_type="off_topic")

        # Call through the graph-tool wrapper (not the low-level tool)
        # — that's the path that does contact_id fan-out.
        from outreach_pipeline.graph.tools import reclassify_submission as graph_reclassify
        result = await graph_reclassify.ainvoke({
            "submission_id": int(big_contact_id),
            "intent_type": "sales_inquiry",
        })
        assert result["success"]
        assert set(result["updated_submission_ids"]) == {sid1, sid2}
        assert sid3 not in result["updated_submission_ids"]

        for sid in (sid1, sid2):
            sub = await get_submission(sid)
            assert sub["intent_type"] == "sales_inquiry", \
                f"submission {sid} should have flipped to sales_inquiry"


class TestScoreEngagement:
    @pytest.mark.asyncio
    async def test_score_saves(self, db):
        from outreach_pipeline.tools.engagements import score_engagement
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        result = await score_engagement(eid, 75, "Strong signals", "microwave_radio")
        assert result["success"]
        assert result["score"] == 75
        eng = await get_engagement(eid)
        assert eng["score"] == 75
        assert eng["score_reasoning"] == "Strong signals"
        assert eng["primary_product"] == "microwave_radio"

    @pytest.mark.asyncio
    async def test_score_zero(self, db):
        from outreach_pipeline.tools.engagements import score_engagement
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        result = await score_engagement(eid, 0, "No fit")
        assert result["success"]
        eng = await get_engagement(eid)
        assert eng["score"] == 0

    @pytest.mark.asyncio
    async def test_score_updates_existing(self, db):
        from outreach_pipeline.tools.engagements import score_engagement
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        await score_engagement(eid, 30, "Low")
        await score_engagement(eid, 85, "Re-evaluated", "millimeter_wave_radio")
        eng = await get_engagement(eid)
        assert eng["score"] == 85
        assert eng["score_reasoning"] == "Re-evaluated"

    @pytest.mark.asyncio
    async def test_score_saves_buying_intent_to_contact(self, db):
        from outreach_pipeline.tools.engagements import score_engagement
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        result = await score_engagement(
            eid, 80, "Clear buying intent", "microwave_radio",
            buying_intent="high",
            intent_signals=["pricing_inquiry", "decision_maker"],
        )
        assert result["success"]
        assert result["buying_intent"] == "high"
        assert result["intent_signals"] == ["pricing_inquiry", "decision_maker"]
        # Verify contact-level fields updated
        contact = await get_contact("c1")
        assert contact["buying_intent"] == "high"
        assert contact["score"] == 80
        assert contact["product_match"] == "microwave_radio"
        assert contact["scored_at"] is not None

    @pytest.mark.asyncio
    async def test_score_high_advances_to_drafting(self, db):
        from outreach_pipeline.tools.engagements import score_engagement
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        result = await score_engagement(eid, 60, "Moderate interest")
        eng = await get_engagement(eid)
        assert eng["status"] == "drafting"
        assert result["status"] == "drafting"

    @pytest.mark.asyncio
    async def test_score_low_closes(self, db):
        from outreach_pipeline.tools.engagements import score_engagement
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        result = await score_engagement(eid, 15, "Support request, no fit")
        eng = await get_engagement(eid)
        assert eng["status"] == "closed"
        assert result["status"] == "closed"

    @pytest.mark.asyncio
    async def test_score_mid_stays_scoring(self, db):
        from outreach_pipeline.tools.engagements import score_engagement
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        result = await score_engagement(eid, 35, "Vague interest")
        eng = await get_engagement(eid)
        assert eng["status"] == "scoring"
        # Drafting threshold is 40 — scores 26-39 stay in scoring.
        assert "26-39" in result["status_note"]

    @pytest.mark.asyncio
    async def test_score_at_drafting_threshold_advances(self, db):
        """Regression: threshold is 40, so score=40 must advance."""
        from outreach_pipeline.tools.engagements import score_engagement
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        result = await score_engagement(eid, 40, "Borderline fit")
        eng = await get_engagement(eid)
        assert eng["status"] == "drafting"
        assert "drafting" in result["status_note"].lower()

    @pytest.mark.asyncio
    async def test_score_does_not_regress_status(self, db):
        from outreach_pipeline.tools.engagements import score_engagement
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        await update_status(eid, "scoring")
        await update_status(eid, "drafting")
        # Rescoring should NOT regress status back to scoring
        await score_engagement(eid, 90, "Re-scored")
        eng = await get_engagement(eid)
        assert eng["status"] == "drafting"

    @pytest.mark.asyncio
    async def test_score_not_found(self, db):
        from outreach_pipeline.tools.engagements import score_engagement
        result = await score_engagement(99999, 50, "Test")
        assert not result["success"]

    @pytest.mark.asyncio
    async def test_score_defaults_buying_intent(self, db):
        from outreach_pipeline.tools.engagements import score_engagement
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        # No buying_intent provided — should default to "medium"
        await score_engagement(eid, 55, "Moderate")
        contact = await get_contact("c1")
        assert contact["buying_intent"] == "medium"


class TestSaveEnrichmentTool:
    @pytest.mark.asyncio
    async def test_save_enrichment(self, db):
        from outreach_pipeline.tools.engagements import save_enrichment
        await _seed_contact(db)
        result = await save_enrichment("c1", "Regional network integrator", "CRM note")
        assert result["success"]
        contact = await get_contact("c1")
        assert contact["company_context"] == "Regional network integrator"
        assert contact["crm_context"] == "CRM note"

    @pytest.mark.asyncio
    async def test_save_enrichment_empty_crm_context(self, db):
        from outreach_pipeline.tools.engagements import save_enrichment
        await _seed_contact(db)
        result = await save_enrichment("c1", "Telecom company")
        assert result["success"]
        contact = await get_contact("c1")
        assert contact["company_context"] == "Telecom company"


class TestCheckEngagementPolicy:
    @pytest.mark.asyncio
    async def test_policy_passes(self, db):
        from outreach_pipeline.tools.engagements import check_engagement_policy
        await _seed_contact(db)
        eid = await create_engagement("c1", kind="sales")
        result = await check_engagement_policy(eid)
        assert result["found"]
        assert result["allowed"]

    @pytest.mark.asyncio
    async def test_policy_not_found(self, db):
        from outreach_pipeline.tools.engagements import check_engagement_policy
        result = await check_engagement_policy(99999)
        assert not result["found"]

    @pytest.mark.asyncio
    async def test_policy_blocked_opt_out(self, db):
        from outreach_pipeline.tools.engagements import check_engagement_policy
        await _seed_contact(db)
        await add_opt_out("c1", "Unsubscribed")
        eid = await create_engagement("c1", kind="sales")
        result = await check_engagement_policy(eid)
        assert result["found"]
        assert not result["allowed"]
        assert result["code"] == "OPT_OUT"


class TestAddOptOut:
    @pytest.mark.asyncio
    async def test_add_opt_out(self, db):
        from outreach_pipeline.tools.engagements import add_opt_out
        await _seed_contact(db)
        result = await add_opt_out("c1", "Requested removal")
        assert result["success"]
        blocked, reason = await check_opt_out("c1")
        assert blocked

    @pytest.mark.asyncio
    async def test_add_opt_out_duplicate(self, db):
        from outreach_pipeline.tools.engagements import add_opt_out
        await _seed_contact(db)
        r1 = await add_opt_out("c1", "First")
        r2 = await add_opt_out("c1", "Second")
        assert r1["success"]
        assert not r2["success"]  # already on list
