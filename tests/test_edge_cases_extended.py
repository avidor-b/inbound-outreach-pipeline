"""Extended edge case tests — migration, policy interactions, lifecycle completeness."""
import pytest

from outreach_pipeline.db.contacts import upsert_contact, score_contact, enrich_contact
from outreach_pipeline.db.engagements import (
    create_engagement, update_status, get_engagement, list_engagements,
    score_engagement,
)
from outreach_pipeline.db.guardrails import add_opt_out
from outreach_pipeline.services.intake import process_submission
from outreach_pipeline.services.sending import check_engagement_send_readiness
from outreach_pipeline.policies.engagement import check_cooldown, check_score_threshold


async def _seed_contact(contact_id="c1"):
    await upsert_contact(contact_id, {"company": "Acme", "email_domain": "customer.example.com"})


class TestSendReadinessEdgeCases:
    @pytest.mark.asyncio
    async def test_send_readiness_checks_opt_out(self, db):
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        for s in ["scoring", "drafting", "pending_approval", "approved"]:
            await update_status(eid, s)
        await add_opt_out("c1", "Not interested")
        decision = await check_engagement_send_readiness(eid)
        assert not decision.allowed
        assert decision.code == "OPT_OUT"

    @pytest.mark.asyncio
    async def test_send_readiness_terminal_blocked(self, db):
        await _seed_contact()
        eid = await create_engagement("c1", kind="support",
                                      status="blocked", block_code="SUPPORT_REQUEST",
                                      block_terminal=True)
        decision = await check_engagement_send_readiness(eid)
        assert not decision.allowed

    @pytest.mark.asyncio
    async def test_send_readiness_nonexistent(self, db):
        decision = await check_engagement_send_readiness(99999)
        assert not decision.allowed


class TestPolicyInteractions:
    @pytest.mark.asyncio
    async def test_cooldown_on_closed_engagement(self, db):
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        for s in ["scoring", "drafting", "pending_approval", "approved", "sent", "closed"]:
            await update_status(eid, s)
        decision = await check_cooldown(eid)
        assert decision.allowed  # closed -> no cooldown

    @pytest.mark.asyncio
    async def test_score_threshold_zero(self, db):
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        await score_engagement(eid, score=0)
        decision = await check_score_threshold(eid, threshold=50)
        assert not decision.allowed

    @pytest.mark.asyncio
    async def test_score_threshold_exact_boundary(self, db):
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        await score_engagement(eid, score=50)
        decision = await check_score_threshold(eid, threshold=50)
        assert decision.allowed


class TestMigrationEdgeCases:
    @pytest.mark.asyncio
    async def test_migrate_support_contact(self, db):
        from outreach_pipeline.services.migration import migrate_contact
        await _seed_contact()
        await upsert_contact("c1", {"message": "Need technical support"})
        await score_contact("c1", "support_request", [], 20, "Support")
        result = await migrate_contact("c1")
        eng = await get_engagement(result["engagement_id"])
        assert eng["kind"] == "support"
        assert eng["status"] == "blocked"
        assert eng["block_terminal"]

    @pytest.mark.asyncio
    async def test_migrate_event_contact(self, db):
        from outreach_pipeline.services.migration import migrate_contact
        await _seed_contact()
        await upsert_contact("c1", {"message": "Meet at MWC"})
        await score_contact("c1", "event_meeting_request", [], 15, "Events")
        result = await migrate_contact("c1")
        eng = await get_engagement(result["engagement_id"])
        assert eng["kind"] == "events"
        assert eng["block_terminal"]

    @pytest.mark.asyncio
    async def test_migrate_preserves_score(self, db):
        from outreach_pipeline.services.migration import migrate_contact
        await _seed_contact()
        await upsert_contact("c1", {"message": "Need pricing", "email_domain": "customer.example.com"})
        await score_contact("c1", "high", ["pricing", "timeline"], 92, "Very strong")
        result = await migrate_contact("c1")
        eng = await get_engagement(result["engagement_id"])
        assert eng["score"] == 92


class TestIntakeAfterMigration:
    @pytest.mark.asyncio
    async def test_new_submission_after_migration_creates_new_engagement(self, db):
        """After migration, a new submission should create a new engagement (not join the migrated one)."""
        from outreach_pipeline.services.migration import migrate_contact
        await _seed_contact()
        await upsert_contact("c1", {"message": "Old inquiry", "email_domain": "customer.example.com"})
        await score_contact("c1", "high", [], 80, "")
        await migrate_contact("c1")

        # Close the migrated engagement (it was already handled)
        engs = await list_engagements(contact_id="c1")
        await update_status(engs[0]["id"], "closed")

        # New submission
        result = await process_submission("c1", message="New inquiry about RADIO-B",
                                          form_name="Contact Us", mql_date="2026-04-01T10:00:00Z")
        assert result["engagement_id"] is not None
        new_eng = await get_engagement(result["engagement_id"])
        assert new_eng["status"] == "open"
        assert result["engagement_id"] != engs[0]["id"]


class TestValidationProfileEdgeCases:
    @pytest.mark.asyncio
    async def test_retargeting_profile_allows_more(self, db):
        from outreach_pipeline.validation.tone import validate
        # Retargeting doesn't block call requests (it's re-engagement, different tone)
        result = validate("Can we schedule a call?", profile="retargeting")
        # retargeting profile doesn't include CALL_REQUEST
        labels = [v["label"] for v in result.violations]
        assert "CALL_REQUEST" not in labels

    @pytest.mark.asyncio
    async def test_followup_profile_minimal(self, db):
        from outreach_pipeline.validation.tone import validate
        # Followup only checks TRACKING_DATA, PII_EMAIL, PII_PHONE
        result = validate(
            "Our industry-leading solution is cutting-edge.",
            profile="followup",
        )
        assert result.passed  # OVERSELLING not in followup checks
