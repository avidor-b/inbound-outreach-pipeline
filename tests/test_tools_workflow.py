"""Tests for workflow tools — scoring context, drafting context, pipeline stats, ingest."""
import pytest

from outreach_pipeline.db.contacts import upsert_contact, enrich_contact, score_contact
from outreach_pipeline.db.drafts import save_draft
from outreach_pipeline.db.submissions import create_submission
from outreach_pipeline.db.engagements import create_engagement, add_submission, score_engagement
from outreach_pipeline.db.guardrails import add_opt_out


async def _seed():
    """Seed a contact, submission, and scored engagement for tests."""
    await upsert_contact("c1", {
        "company": "Acme", "industry": "Telecom", "message": "Need pricing",
        "hs_analytics_num_page_views": "25",
    })
    await enrich_contact("c1", company_context="Acme is a telecom provider")
    sid = await create_submission("c1", message="Need pricing for RADIO-A",
                                  mql_date="2026-03-28T10:00:00Z")
    eid = await create_engagement("c1", kind="sales", primary_product="microwave_radio")
    await add_submission(eid, sid)
    await score_engagement(eid, score=85, score_reasoning="Strong signals")
    return sid, eid


class TestGetScoringContext:
    @pytest.mark.asyncio
    async def test_returns_engagement_info(self, db):
        sid, eid = await _seed()
        from outreach_pipeline.tools.workflow import get_scoring_context
        ctx = await get_scoring_context(eid)
        assert ctx["engagement"]["id"] == eid
        assert ctx["engagement"]["kind"] == "sales"

    @pytest.mark.asyncio
    async def test_returns_contact_info(self, db):
        sid, eid = await _seed()
        from outreach_pipeline.tools.workflow import get_scoring_context
        ctx = await get_scoring_context(eid)
        assert ctx["contact"]["id"] == "c1"
        assert ctx["contact"]["properties"]["company"] == "Acme"
        assert ctx["contact"]["company_context"] == "Acme is a telecom provider"

    @pytest.mark.asyncio
    async def test_returns_submissions(self, db):
        sid, eid = await _seed()
        from outreach_pipeline.tools.workflow import get_scoring_context
        ctx = await get_scoring_context(eid)
        assert len(ctx["submissions"]) == 1
        assert ctx["submissions"][0]["message"] == "Need pricing for RADIO-A"

    @pytest.mark.asyncio
    async def test_returns_none_for_missing(self, db):
        from outreach_pipeline.tools.workflow import get_scoring_context
        ctx = await get_scoring_context(9999)
        assert ctx["engagement"] is None

    @pytest.mark.asyncio
    async def test_multiple_submissions(self, db):
        sid, eid = await _seed()
        from outreach_pipeline.tools.workflow import get_scoring_context
        sid2 = await create_submission("c1", message="Also need accessories")
        await add_submission(eid, sid2)
        ctx = await get_scoring_context(eid)
        assert len(ctx["submissions"]) == 2


class TestGetDraftingContext:
    @pytest.mark.asyncio
    async def test_returns_engagement_and_submissions(self, db):
        sid, eid = await _seed()
        from outreach_pipeline.tools.workflow import get_drafting_context
        ctx = await get_drafting_context(eid)
        assert ctx["engagement"]["id"] == eid
        assert ctx["engagement"]["score"] == 85
        assert len(ctx["submissions"]) == 1

    @pytest.mark.asyncio
    async def test_blocked_engagement_returns_blocked(self, db):
        await _seed()
        from outreach_pipeline.tools.workflow import get_drafting_context
        eid = await create_engagement("c1", kind="support",
                                      status="blocked", block_terminal=True,
                                      block_code="SUPPORT_REQUEST")
        ctx = await get_drafting_context(eid)
        assert ctx["blocked"] is True
        assert ctx["block_code"] == "SUPPORT_REQUEST"

    @pytest.mark.asyncio
    async def test_returns_none_for_missing(self, db):
        from outreach_pipeline.tools.workflow import get_drafting_context
        ctx = await get_drafting_context(9999)
        assert ctx["engagement"] is None


class TestSaveEngagementDraft:
    @pytest.mark.asyncio
    async def test_saves_valid_draft(self, db):
        sid, eid = await _seed()
        from outreach_pipeline.tools.workflow import save_engagement_draft
        body = ("Hi [First Name],\n\nThe RADIO-A platform supports your use case. "
                "Want me to send the configuration guide?\n\nBest,\n[Sender Name]")
        result = await save_engagement_draft(eid, subject="RADIO-A for you", body=body)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_rejects_invalid_draft(self, db):
        sid, eid = await _seed()
        from outreach_pipeline.tools.workflow import save_engagement_draft
        result = await save_engagement_draft(
            eid, subject="Bad", body="Hey there, you viewed 35 pages on our site",
        )
        assert result["success"] is False
        assert "violations" in result

    @pytest.mark.asyncio
    async def test_rejects_missing_greeting(self, db):
        sid, eid = await _seed()
        from outreach_pipeline.tools.workflow import save_engagement_draft
        result = await save_engagement_draft(
            eid, subject="S", body="Hello, check this out.\nBest,\n[Sender Name]",
        )
        assert result["success"] is False


class TestGetPipelineStats:
    @pytest.mark.asyncio
    async def test_returns_all_categories(self, db):
        sid, eid = await _seed()
        from outreach_pipeline.tools.workflow import get_pipeline_stats
        await save_draft("c1", "Subject", "Body", 80)
        stats = await get_pipeline_stats()
        assert "contacts" in stats
        assert "submissions" in stats
        assert "engagements" in stats
        assert "drafts" in stats
        assert "replies" in stats

    @pytest.mark.asyncio
    async def test_contact_counts(self, db):
        await _seed()
        from outreach_pipeline.tools.workflow import get_pipeline_stats
        stats = await get_pipeline_stats()
        assert stats["contacts"]["total"] == 1
        assert stats["contacts"]["enriched"] == 1

    @pytest.mark.asyncio
    async def test_engagement_counts(self, db):
        await _seed()
        from outreach_pipeline.tools.workflow import get_pipeline_stats
        await create_engagement("c1", kind="support",
                                status="blocked", block_terminal=True)
        stats = await get_pipeline_stats()
        assert stats["engagements"]["total"] == 2
        assert "sales" in stats["engagements"]["by_kind"]
        assert "support" in stats["engagements"]["by_kind"]

    @pytest.mark.asyncio
    async def test_submission_counts(self, db):
        sid, eid = await _seed()
        from outreach_pipeline.tools.workflow import get_pipeline_stats
        from outreach_pipeline.db.submissions import classify_submission
        await classify_submission(sid, intent_type="sales_inquiry")
        stats = await get_pipeline_stats()
        assert stats["submissions"]["total"] == 1

    @pytest.mark.asyncio
    async def test_empty_db(self, db):
        from outreach_pipeline.tools.workflow import get_pipeline_stats
        stats = await get_pipeline_stats()
        assert stats["contacts"]["total"] == 0
        assert stats["engagements"]["total"] == 0


class TestGetProspectDomain:
    @pytest.mark.asyncio
    async def test_email_domain(self, db):
        await _seed()
        from outreach_pipeline.tools.workflow import get_prospect_domain
        await upsert_contact("c2", {"email": "user@prospect.example.net", "company": "Example Networks"})
        result = await get_prospect_domain("c2")
        assert result["email_domain"] == "prospect.example.net"

    @pytest.mark.asyncio
    async def test_freemail_no_domain(self, db):
        """Freemail: email_domain empty, enrichment falls back to web search."""
        await _seed()
        from outreach_pipeline.tools.workflow import get_prospect_domain
        await upsert_contact("c2", {"email": "user@example.com", "company": "Example Networks"})
        result = await get_prospect_domain("c2")
        assert result["email_domain"] == ""

    @pytest.mark.asyncio
    async def test_first_url_domain(self, db):
        await _seed()
        from outreach_pipeline.tools.workflow import get_prospect_domain
        await upsert_contact("c2", {
            "company": "Example Networks",
            "hs_analytics_first_url": "https://www.prospect.example.net/products",
        })
        result = await get_prospect_domain("c2")
        assert result["first_url_domain"] == "www.prospect.example.net"

    @pytest.mark.asyncio
    async def test_first_party_url_is_not_a_prospect_domain(self, db):
        await _seed()
        from outreach_pipeline.tools.workflow import get_prospect_domain
        await upsert_contact("c2", {
            "company": "Example Networks",
            "hs_analytics_first_url": "https://www.example.com/contact-us",
        })
        result = await get_prospect_domain("c2")
        assert result["first_url_domain"] == ""

    @pytest.mark.asyncio
    async def test_similar_external_hostname_is_not_treated_as_first_party(self, db):
        await _seed()
        from outreach_pipeline.tools.workflow import get_prospect_domain
        await upsert_contact("c2", {
            "company": "Example Networks",
            "hs_analytics_first_url": "https://example.com.external.example.net/path",
        })
        result = await get_prospect_domain("c2")
        assert result["first_url_domain"] == "example.com.external.example.net"

    @pytest.mark.asyncio
    async def test_missing_contact(self, db):
        from outreach_pipeline.tools.workflow import get_prospect_domain
        result = await get_prospect_domain("missing")
        assert result["contact"] is None
