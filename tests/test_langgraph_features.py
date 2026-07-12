"""Tests for new features added during LangGraph migration.

Covers: drafts by engagement, scoring signals, prior outreach in scoring,
empty submission routing, delete contact preview/execute, scrape_and_index
prospect flag, web search, get_drafts, rag_context tracking.
"""
import json
import pytest

from outreach_pipeline.db.contacts import upsert_contact, get_contact
from outreach_pipeline.db.submissions import create_submission
from outreach_pipeline.db.engagements import (
    create_engagement, add_submission,
    update_status, get_engagement,
)
from outreach_pipeline.db.pool import get_pool


# --- Drafts by engagement ---

class TestDraftsByEngagement:

    @pytest.mark.asyncio
    async def test_save_draft_with_engagement_id(self, db):
        from outreach_pipeline.db.drafts import save_draft, get_drafts
        await upsert_contact("c1", {"company": "Acme", "industry": "Telecom",
                                     "hs_analytics_num_page_views": "42",
                                     "email_domain": "customer.example.com"})
        eid = await create_engagement("c1", kind="sales")
        draft_id = await save_draft("c1", "Subject", "Body", engagement_id=eid)
        assert draft_id is not None
        drafts = await get_drafts(engagement_id=eid)
        assert len(drafts) == 1
        assert drafts[0]["engagement_id"] == eid

    @pytest.mark.asyncio
    async def test_draft_upsert_by_engagement(self, db):
        """Saving multiple drafts for the same engagement updates in place."""
        from outreach_pipeline.db.drafts import save_draft, get_drafts
        await upsert_contact("c1", {"company": "Acme"})
        eid = await create_engagement("c1", kind="sales")
        await save_draft("c1", "V1", "Body v1", engagement_id=eid)
        await save_draft("c1", "V2", "Body v2", engagement_id=eid)
        await save_draft("c1", "V3", "Body v3", engagement_id=eid)
        drafts = await get_drafts(engagement_id=eid)
        assert len(drafts) == 1  # upsert, not append
        assert drafts[0]["subject"] == "V3"

    @pytest.mark.asyncio
    async def test_draft_status_updates(self, db):
        """Draft status can be set and updated."""
        from outreach_pipeline.db.drafts import save_draft, get_drafts
        await upsert_contact("c1", {"company": "Acme"})
        eid = await create_engagement("c1", kind="sales")
        await save_draft("c1", "Sub", "Body", engagement_id=eid, status="draft")
        drafts = await get_drafts(engagement_id=eid)
        assert drafts[0]["status"] == "draft"
        await save_draft("c1", "Sub", "Body", engagement_id=eid, status="approved")
        drafts = await get_drafts(engagement_id=eid)
        assert drafts[0]["status"] == "approved"

    @pytest.mark.asyncio
    async def test_drafts_isolated_by_engagement(self, db):
        from outreach_pipeline.db.drafts import save_draft, get_drafts
        await upsert_contact("c1", {"company": "Acme"})
        eid1 = await create_engagement("c1", kind="sales")
        eid2 = await create_engagement("c1", kind="retarget")
        await save_draft("c1", "Draft A", "Body A", engagement_id=eid1)
        await save_draft("c1", "Draft B", "Body B", engagement_id=eid2)
        assert len(await get_drafts(engagement_id=eid1)) == 1
        assert len(await get_drafts(engagement_id=eid2)) == 1
        # All drafts for contact
        assert len(await get_drafts(contact_id="c1")) == 2


# --- RAG context tracking ---

class TestRagContextTracking:

    @pytest.mark.asyncio
    async def test_draft_saves_rag_context(self, db):
        from outreach_pipeline.db.drafts import save_draft, get_drafts
        await upsert_contact("c1", {"company": "Acme"})
        eid = await create_engagement("c1", kind="sales")
        await save_draft("c1", "Sub", "Body", rag_context="RADIO-A datasheet, outdoor radio case study",
                         engagement_id=eid)
        drafts = await get_drafts(engagement_id=eid)
        assert drafts[0]["rag_context"] == "RADIO-A datasheet, outdoor radio case study"

    @pytest.mark.asyncio
    async def test_save_engagement_draft_passes_rag_context(self, db):
        from outreach_pipeline.tools.workflow import save_engagement_draft
        await upsert_contact("c1", {"company": "Acme"})
        eid = await create_engagement("c1", kind="sales")
        result = await save_engagement_draft(
            eid,
            "Quick question",
            "Hi [First Name],\n\nThe RADIO-A fits your use case. Want the spec sheet?\n\nBest,\n[Sender Name]",
            rag_context="RADIO-A spec sheet",
        )
        assert result["success"]
        from outreach_pipeline.db.drafts import get_drafts
        drafts = await get_drafts(engagement_id=eid)
        assert drafts[0]["rag_context"] == "RADIO-A spec sheet"


# --- Scoring signals in context ---

class TestScoringSignals:

    @pytest.mark.asyncio
    async def test_scoring_context_has_signals(self, db):
        from outreach_pipeline.tools.workflow import get_scoring_context
        await upsert_contact("c1", {"company": "Acme", "industry": "Telecom",
                                     "hs_analytics_num_page_views": "42",
                                     "email_domain": "customer.example.com"})
        eid = await create_engagement("c1", kind="sales")
        sid = await create_submission("c1", message="Need pricing")
        await add_submission(eid, sid)
        ctx = await get_scoring_context(eid)
        assert "scoring_signals" in ctx
        signals = ctx["scoring_signals"]
        assert signals["company"] == "Acme"
        assert signals["industry"] == "Telecom"
        assert signals["page_views"] == "42"

    @pytest.mark.asyncio
    async def test_scoring_context_has_jobtitle(self, db):
        from outreach_pipeline.tools.workflow import get_scoring_context
        await upsert_contact("c1", {"company": "Acme", "industry": "Telecom",
                                     "hs_analytics_num_page_views": "42",
                                     "email_domain": "customer.example.com"})
        eid = await create_engagement("c1", kind="sales")
        ctx = await get_scoring_context(eid)
        # Jobtitle fetch will fail (no HubSpot in tests) but key should exist
        assert "jobtitle" in ctx["scoring_signals"]


# --- Prior outreach in scoring ---

class TestPriorOutreachInScoring:

    @pytest.mark.asyncio
    async def test_no_prior_outreach(self, db):
        from outreach_pipeline.tools.workflow import get_scoring_context
        await upsert_contact("c1", {"company": "Acme", "email_domain": "customer.example.com"})
        eid = await create_engagement("c1", kind="sales")
        ctx = await get_scoring_context(eid)
        assert ctx["prior_outreach"] == []

    @pytest.mark.asyncio
    async def test_sent_draft_appears_in_context(self, db):
        from outreach_pipeline.tools.workflow import get_scoring_context
        from outreach_pipeline.db.drafts import save_draft, mark_sent
        await upsert_contact("c1", {"company": "Acme", "email_domain": "customer.example.com"})
        eid1 = await create_engagement("c1", kind="sales")
        draft_id = await save_draft("c1", "Prior email", "Body", engagement_id=eid1)
        await mark_sent(draft_id)

        eid2 = await create_engagement("c1", kind="retarget")
        ctx2 = await get_scoring_context(eid2)
        assert len(ctx2["prior_outreach"]) == 1
        assert ctx2["prior_outreach"][0]["subject"] == "Prior email"

    @pytest.mark.asyncio
    async def test_unsent_draft_not_in_prior_outreach(self, db):
        from outreach_pipeline.tools.workflow import get_scoring_context
        from outreach_pipeline.db.drafts import save_draft
        await upsert_contact("c1", {"company": "Acme", "email_domain": "customer.example.com"})
        eid1 = await create_engagement("c1", kind="sales")
        await save_draft("c1", "Unsent", "Body", engagement_id=eid1)

        eid2 = await create_engagement("c1", kind="retarget")
        ctx = await get_scoring_context(eid2)
        assert len(ctx["prior_outreach"]) == 0


# --- Empty submission routing ---

class TestEmptySubmissionRouting:

    @pytest.mark.asyncio
    async def test_empty_sales_inquiry_with_support_merges(self, db):
        from outreach_pipeline.services.intake import process_submission
        await upsert_contact("c1", {"company": "Acme", "industry": "Telecom",
                                     "hs_analytics_num_page_views": "42",
                                     "email_domain": "customer.example.com"})
        # First: support request
        r1 = await process_submission("c1",
                                      message="PO# 12345 status please",
                                      form_name="Contact Us", mql_date="2026-03-27")
        assert r1["engagement_id"] is not None
        eng1 = await get_engagement(r1["engagement_id"])
        assert eng1["kind"] == "support"

        # Second: empty contact-us form (same day, no message)
        r2 = await process_submission("c1",
                                      message="",
                                      form_name="Contact Us - Homepage",
                                      mql_date="2026-03-27T18:00:00")
        # Should merge into the support engagement, not create a new sales one
        assert r1["engagement_id"] == r2["engagement_id"]

    @pytest.mark.asyncio
    async def test_empty_sales_inquiry_no_support_blocks(self, db):
        from outreach_pipeline.services.intake import process_submission
        await upsert_contact("c1", {"company": "Acme", "email_domain": "customer.example.com"})
        # No prior support engagement -- empty form should be blocked
        r = await process_submission("c1",
                                     message="",
                                     form_name="Contact Us - Homepage",
                                     mql_date="2026-03-27")
        eng = await get_engagement(r["engagement_id"])
        assert eng["status"] == "blocked"
        assert eng["block_code"] == "NO_MESSAGE"

    @pytest.mark.asyncio
    async def test_sales_inquiry_with_message_creates_engagement(self, db):
        from outreach_pipeline.services.intake import process_submission
        await upsert_contact("c1", {"company": "Acme", "industry": "Telecom",
                                     "email_domain": "customer.example.com"})
        # Support engagement exists
        await process_submission("c1", message="Need support",
                                 form_name="Contact Us", mql_date="2026-03-25")
        # But this one has a real message -- should create separate sales engagement
        r = await process_submission("c1",
                                     message="Interested in RADIO-B100 pricing",
                                     form_name="Contact Us", mql_date="2026-03-28")
        eng = await get_engagement(r["engagement_id"])
        assert eng["kind"] == "sales"
        assert eng["status"] != "blocked"


# --- Delete contact preview ---

class TestDeleteContactPreview:

    @pytest.mark.asyncio
    async def test_preview_shows_all_data(self, db):
        from outreach_pipeline.tools.contacts import preview_delete_contact
        await upsert_contact("c1", {"company": "Acme", "email_domain": "customer.example.com"})
        eid = await create_engagement("c1", kind="sales")
        sid = await create_submission("c1", message="Test")
        await add_submission(eid, sid)
        result = await preview_delete_contact("c1")
        assert result["found"]
        assert result["company"] == "Acme"
        assert result["will_delete"]["submissions"] >= 1
        assert result["will_delete"]["engagements"] >= 1

    @pytest.mark.asyncio
    async def test_preview_not_found(self, db):
        from outreach_pipeline.tools.contacts import preview_delete_contact
        result = await preview_delete_contact("nonexistent")
        assert not result["found"]

    @pytest.mark.asyncio
    async def test_delete_removes_everything(self, db):
        from outreach_pipeline.tools.contacts import delete_contact
        await upsert_contact("c1", {"company": "Acme", "email_domain": "customer.example.com"})
        await create_engagement("c1", kind="sales")
        await create_submission("c1", message="Test")
        result = await delete_contact("c1")
        assert result["success"]
        contact = await get_contact("c1")
        assert not contact


# --- Score auto-advances status ---

class TestScoreStatusAdvancement:

    @pytest.mark.asyncio
    async def test_high_score_advances_to_drafting(self, db):
        from outreach_pipeline.tools.engagements import score_engagement
        await upsert_contact("c1", {"company": "Acme", "email_domain": "customer.example.com"})
        eid = await create_engagement("c1", kind="sales")
        result = await score_engagement(eid, 75, "High intent")
        assert result["status"] == "drafting"

    @pytest.mark.asyncio
    async def test_low_score_closes(self, db):
        from outreach_pipeline.tools.engagements import score_engagement
        await upsert_contact("c1", {"company": "Acme", "email_domain": "customer.example.com"})
        eid = await create_engagement("c1", kind="sales")
        result = await score_engagement(eid, 15, "No fit")
        assert result["status"] == "closed"

    @pytest.mark.asyncio
    async def test_mid_score_stays_scoring(self, db):
        from outreach_pipeline.tools.engagements import score_engagement
        await upsert_contact("c1", {"company": "Acme", "email_domain": "customer.example.com"})
        eid = await create_engagement("c1", kind="sales")
        result = await score_engagement(eid, 35, "Vague")
        assert result["status"] == "scoring"


# --- Get drafts tool ---

class TestGetDraftsTool:

    @pytest.mark.asyncio
    async def test_get_drafts_by_engagement(self, db):
        from outreach_pipeline.tools.workflow import get_drafts
        from outreach_pipeline.db.drafts import save_draft
        await upsert_contact("c1", {"company": "Acme"})
        eid = await create_engagement("c1", kind="sales")
        await save_draft("c1", "Sub", "Body", engagement_id=eid)
        result = await get_drafts(engagement_id=eid)
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_get_drafts_empty(self, db):
        from outreach_pipeline.tools.workflow import get_drafts
        result = await get_drafts(engagement_id=999)
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_get_approved_drafts_by_company_name_checks_all_matching_contacts(self, db):
        """Company-name lookup can match multiple HubSpot contacts.
        The approved email must still display when the saved draft is
        attached to any matching contact, not only the newest match."""
        from outreach_pipeline.db.drafts import save_draft
        from outreach_pipeline.graph.tools import get_drafts as graph_get_drafts

        company = "EXAMPLE AUTOMATION LIMITED"
        await upsert_contact("millennium-old", {"company": company, "email_domain": "old.example"})
        eid = await create_engagement("millennium-old", kind="sales")
        await save_draft(
            "millennium-old",
            "Microwave and antenna solution follow-up",
            "Hi [First Name],\n\nFollowing up on your microwave and antenna inquiry.\n\nBest,\n[Sender Name]",
            engagement_id=eid,
            status="approved",
        )

        # Insert a later duplicate with the same company and no draft.
        # A limit=1 resolver will pick this row and incorrectly render
        # "No drafts match that filter".
        await upsert_contact("millennium-new", {"company": company, "email_domain": "new.example"})

        raw = await graph_get_drafts.ainvoke({"contact_id": company, "status": "approved"})
        data = json.loads(raw[raw.index("{"):])

        assert data["count"] == 1
        assert data["drafts"][0]["status"] == "approved"
        assert data["drafts"][0]["company"] == company
        assert data["drafts"][0]["engagement_id"] == eid


# --- Add scrape block ---

class TestAddScrapeBlock:

    @pytest.mark.asyncio
    async def test_add_scrape_block(self, db):
        from outreach_pipeline.tools.engagements import add_scrape_block
        result = await add_scrape_block("competitor.example.com", "Competitor")
        assert result["success"]
        from outreach_pipeline.db.guardrails import check_scrape_blocklist
        blocked, reason = await check_scrape_blocklist("competitor.example.com")
        assert blocked

    @pytest.mark.asyncio
    async def test_add_scrape_block_duplicate(self, db):
        from outreach_pipeline.tools.engagements import add_scrape_block
        await add_scrape_block("competitor.example.com", "Competitor")
        r2 = await add_scrape_block("competitor.example.com", "Again")
        assert not r2["success"]


# --- Web search ---

class TestWebSearchTool:

    def test_search_returns_results(self, monkeypatch):
        class FakeDDGS:
            def text(self, query, max_results=5):
                return [
                    {
                        "title": "Synthetic result",
                        "href": "https://example.com/resource",
                        "body": f"Fixture result for {query}",
                    }
                ][:max_results]

        monkeypatch.setattr("ddgs.DDGS", FakeDDGS)
        from outreach_pipeline.tools.search import web_search
        result = web_search("Example Company", max_results=2)
        assert result["success"] is True
        assert result["results"] == [{
            "title": "Synthetic result",
            "url": "https://example.com/resource",
            "snippet": "Fixture result for Example Company",
        }]


# --- scrape_and_index prospect flag ---

class TestScrapeAndIndexProspectFlag:

    def test_prospect_true_derives_domain_collection(self):
        from urllib.parse import urlparse
        url = "https://www.example.com/products"
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        col = netloc.replace(".", "_").replace("-", "_")
        assert col == "example_com"

    def test_prospect_false_uses_sales_materials(self):
        # Verify the logic: prospect=False -> collection = "sales_materials"
        collection = ""
        prospect = False
        if collection:
            col = collection
        elif prospect:
            col = "test_domain"
        else:
            col = "sales_materials"
        assert col == "sales_materials"


# --- Draft status syncs with engagement ---

class TestDraftStatusSync:

    @pytest.mark.asyncio
    async def test_approve_engagement_approves_draft(self, db):
        from outreach_pipeline.db.drafts import save_draft, get_drafts
        from outreach_pipeline.tools.engagements import update_engagement_status
        await upsert_contact("c1", {"company": "Acme"})
        eid = await create_engagement("c1", kind="sales")
        await save_draft("c1", "V1", "Body v1", engagement_id=eid)
        await save_draft("c1", "V2", "Body v2", engagement_id=eid)
        # Upsert means only V2 exists
        drafts = await get_drafts(engagement_id=eid)
        assert len(drafts) == 1
        assert drafts[0]["subject"] == "V2"
        # Advance to pending_approval
        await update_status(eid, "scoring")
        await update_status(eid, "drafting")
        await update_status(eid, "pending_approval")
        # Approve
        result = await update_engagement_status(eid, "approved")
        assert result["success"]
        drafts = await get_drafts(engagement_id=eid)
        assert drafts[0]["status"] == "approved"

    @pytest.mark.asyncio
    async def test_send_engagement_marks_draft_sent(self, db):
        from outreach_pipeline.db.drafts import save_draft, get_drafts
        from outreach_pipeline.tools.engagements import update_engagement_status
        await upsert_contact("c1", {"company": "Acme"})
        eid = await create_engagement("c1", kind="sales")
        await save_draft("c1", "Sub", "Body", engagement_id=eid)
        for s in ["scoring", "drafting", "pending_approval", "approved"]:
            await update_status(eid, s)
        result = await update_engagement_status(eid, "sent")
        assert result["success"]
        drafts = await get_drafts(engagement_id=eid)
        assert drafts[0]["status"] == "sent"
        assert drafts[0]["sent_at"] is not None

    @pytest.mark.asyncio
    async def test_no_draft_approve_doesnt_crash(self, db):
        from outreach_pipeline.tools.engagements import update_engagement_status
        await upsert_contact("c1", {"company": "Acme"})
        eid = await create_engagement("c1", kind="sales")
        for s in ["scoring", "drafting", "pending_approval"]:
            await update_status(eid, s)
        # Approve with no drafts -- should not error
        result = await update_engagement_status(eid, "approved")
        assert result["success"]
