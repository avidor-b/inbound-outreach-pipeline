"""Unit tests for services/intake.py — submission intake and routing."""
import pytest

from outreach_pipeline.db.contacts import upsert_contact
from outreach_pipeline.db.engagements import get_engagement, get_submissions
from outreach_pipeline.db.guardrails import add_opt_out
from outreach_pipeline.services.intake import process_submission


async def _seed_contact():
    await upsert_contact("c1", {"company": "Acme", "email_domain": "customer.example.com"})


class TestProcessSubmission:
    @pytest.mark.asyncio
    async def test_sales_inquiry_creates_engagement(self, db):
        await _seed_contact()
        result = await process_submission(
            "c1", message="Need pricing for RADIO-A",
            form_name="Contact Us", mql_date="2026-03-28T10:00:00Z",
        )
        assert result["submission_id"] is not None
        assert result["engagement_id"] is not None
        eng = await get_engagement(result["engagement_id"])
        assert eng["kind"] == "sales"
        assert eng["status"] == "open"

    @pytest.mark.asyncio
    async def test_content_download_no_engagement(self, db):
        await _seed_contact()
        result = await process_submission(
            "c1", message="",
            form_name="Solution Brief Download",
            page_url="https://example.com/resources/mesh-radio",
            mql_date="2026-03-20T10:00:00Z",
        )
        assert result["submission_id"] is not None
        assert result["engagement_id"] is None

    @pytest.mark.asyncio
    async def test_support_creates_blocked_engagement(self, db):
        await _seed_contact()
        result = await process_submission(
            "c1",
            message="I have some RADIO-A units and they are not working properly",
            form_name="Contact Us", mql_date="2026-03-28T10:00:00Z",
        )
        eng = await get_engagement(result["engagement_id"])
        assert eng["kind"] == "support"
        assert eng["status"] == "blocked"
        assert eng["block_terminal"]

    @pytest.mark.asyncio
    async def test_partnerships_topic_buyer_language_routes_to_sales(self, db):
        await _seed_contact()
        result = await process_submission(
            "c1",
            message="quero comprar radios de alta capacidad",
            form_name="Contact Us - Homepage",
            page_url="https://www.example.com/contact-us",
            mql_date="2026-04-02T10:00:00Z",
            topic="Partnerships",
        )
        eng = await get_engagement(result["engagement_id"])
        assert eng["kind"] == "sales"
        assert eng["status"] == "open"
        subs = await get_submissions(result["engagement_id"])
        assert subs[0]["intent_type"] == "sales_inquiry"

    @pytest.mark.asyncio
    async def test_event_creates_blocked_engagement(self, db):
        await _seed_contact()
        result = await process_submission(
            "c1",
            message="Meet at MWC next week",
            form_name="Contact Us",
            page_url="https://example.com/events/mwc",
            mql_date="2026-03-28T10:00:00Z",
        )
        eng = await get_engagement(result["engagement_id"])
        assert eng["kind"] == "events"
        assert eng["block_terminal"]

    @pytest.mark.asyncio
    async def test_duplicate_submission_skipped(self, db):
        await _seed_contact()
        r1 = await process_submission(
            "c1", message="Need pricing",
            form_id="f1", mql_date="2026-03-28T10:00:00Z",
        )
        r2 = await process_submission(
            "c1", message="Need pricing",
            form_id="f1", mql_date="2026-03-28T15:00:00Z",
        )
        assert r1["submission_id"] is not None
        assert r2["submission_id"] is None  # skipped
        assert r2.get("duplicate", False)

    @pytest.mark.asyncio
    async def test_second_submission_joins_existing_engagement(self, db):
        await _seed_contact()
        r1 = await process_submission(
            "c1", message="Need pricing for RADIO-A",
            form_name="Contact Us", mql_date="2026-03-28T10:00:00Z",
        )
        r2 = await process_submission(
            "c1", message="Also need accessories",
            form_name="Contact Us", mql_date="2026-03-29T10:00:00Z",
        )
        assert r1["engagement_id"] == r2["engagement_id"]
        subs = await get_submissions(r1["engagement_id"])
        assert len(subs) == 2

    @pytest.mark.asyncio
    async def test_support_does_not_block_sales_engagement(self, db):
        await _seed_contact()
        r_sales = await process_submission(
            "c1", message="Need pricing for RADIO-A",
            form_name="Contact Us", mql_date="2026-03-25T10:00:00Z",
        )
        r_support = await process_submission(
            "c1",
            message="I have some RADIO-A units and they are not working",
            form_name="Contact Us", mql_date="2026-03-28T10:00:00Z",
        )
        # Sales engagement should still be open
        sales_eng = await get_engagement(r_sales["engagement_id"])
        assert sales_eng["status"] == "open"
        # Support engagement is separate and blocked
        support_eng = await get_engagement(r_support["engagement_id"])
        assert support_eng["kind"] == "support"
        assert r_sales["engagement_id"] != r_support["engagement_id"]

    @pytest.mark.asyncio
    async def test_content_followup_detection(self, db):
        await _seed_contact()
        # First: content download
        await process_submission(
            "c1", message="",
            form_name="mesh radio Brief",
            page_url="https://example.com/resources/mesh-radio",
            mql_date="2026-03-20T10:00:00Z",
        )
        # Then: sales inquiry for same product family within 30 days
        r = await process_submission(
            "c1", message="Need info about mesh radio",
            form_name="Contact Us",
            mql_date="2026-03-28T10:00:00Z",
        )
        eng = await get_engagement(r["engagement_id"])
        assert eng["kind"] == "content_followup"

    @pytest.mark.asyncio
    async def test_opted_out_contact_still_ingested(self, db):
        await _seed_contact()
        await add_opt_out("c1", "Not interested")
        result = await process_submission(
            "c1", message="New inquiry",
            form_name="Contact Us", mql_date="2026-03-28T10:00:00Z",
        )
        # Submission stored
        assert result["submission_id"] is not None
        # But no engagement created (opted out)
        assert result["engagement_id"] is None
        assert result.get("blocked_reason") == "opted_out"
