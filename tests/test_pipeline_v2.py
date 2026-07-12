"""Tests for pipeline v2 features: off-topic classification, enrichment,
confidence scoring, draft status, pipeline run tracking, no-email blocking.
"""
import datetime
import json
import pytest
from unittest.mock import patch, MagicMock

from outreach_pipeline.db.contacts import upsert_contact, get_contact
from outreach_pipeline.db.submissions import create_submission
from outreach_pipeline.db.engagements import (
    create_engagement, add_submission,
    update_status, get_engagement,
)
from outreach_pipeline.db.drafts import save_draft, get_drafts
from outreach_pipeline.db.pool import get_pool


# ===== Off-Topic Classification =====

class TestOffTopicClassification:
    """Test off_topic classification for categories outside configured offerings."""

    def test_printer_ribbons_is_off_topic(self):
        from outreach_pipeline.policies.submission import classify_intent
        r = classify_intent(
            "Hi, we want pricing for YMCKO Fargo 45200 Color Ribbon"
        )
        assert r["intent_type"] == "off_topic"

    def test_office_supplies_is_off_topic(self):
        from outreach_pipeline.policies.submission import classify_intent
        r = classify_intent(
            "I need to purchase 500 boxes of A4 paper and toner cartridges"
        )
        assert r["intent_type"] == "off_topic"

    def test_wireless_link_is_sales(self):
        from outreach_pipeline.policies.submission import classify_intent
        r = classify_intent(
            "We need a point-to-point wireless link for our 5G backhaul"
        )
        assert r["intent_type"] == "sales_inquiry"

    def test_microwave_radio_mention_is_sales(self):
        from outreach_pipeline.policies.submission import classify_intent
        r = classify_intent("I need pricing for RADIO-A units")
        assert r["intent_type"] == "sales_inquiry"

    def test_networking_inquiry_is_sales(self):
        from outreach_pipeline.policies.submission import classify_intent
        r = classify_intent(
            "Looking for a microwave backhaul solution for our telecom network"
        )
        assert r["intent_type"] == "sales_inquiry"

    def test_empty_message_not_off_topic(self):
        from outreach_pipeline.policies.submission import classify_intent
        r = classify_intent("", form_name="Contact Us")
        assert r["intent_type"] == "sales_inquiry"

    def test_support_takes_priority_over_off_topic(self):
        from outreach_pipeline.policies.submission import classify_intent
        r = classify_intent("I need technical support for my purchase order #12345")
        assert r["intent_type"] == "support_request"

    def test_event_takes_priority_over_off_topic(self):
        from outreach_pipeline.policies.submission import classify_intent
        r = classify_intent("I want to meet you at MWC to buy products")
        assert r["intent_type"] == "event_meeting"

    def test_no_buying_language_not_off_topic(self):
        """Zero regex signals fire on 'Tell me about your company history',
        so the LLM tie-breaker is consulted (see TestLLMFallback in
        test_policies_submission.py). This test targets the regex-only
        path: with the LLM unavailable, the regex default (sales_inquiry)
        stands — which is what we want when there's no positive
        evidence of off_topic (no excluded product category named)."""
        from unittest.mock import patch
        from outreach_pipeline.policies import submission
        with patch.object(submission, "_llm_classify_intent", return_value=None):
            r = submission.classify_intent("Tell me about your company history")
        assert r["intent_type"] == "sales_inquiry"

    def test_buying_with_wireless_terms_is_sales(self):
        from outreach_pipeline.policies.submission import classify_intent
        r = classify_intent(
            "We want to purchase antenna equipment for our network"
        )
        assert r["intent_type"] == "sales_inquiry"

    def test_generic_partnership_inquiry_is_sales(self):
        """Regression: a partnership / evaluation message that asks
        for pricing and case studies but doesn't name an excluded
        product must flow to normal scoring — not pre-emptively
        blocked as off_topic. LLM scoring can still downgrade it on
        product_fit if it isn't a real fit.

        This is verbatim the Example Construction submission that
        got misrouted to off_topic in prod.
        """
        from outreach_pipeline.policies.submission import classify_intent
        r = classify_intent(
            "We are currently evaluating potential partners for an "
            "upcoming project, and after reviewing your website, we "
            "believe we could collaborate with your company's services "
            "in this area. Could you please share additional details "
            "about your offerings, pricing model, and any relevant "
            "case studies or references?"
        )
        assert r["intent_type"] == "sales_inquiry"

    def test_printers_still_off_topic(self):
        """Printers are an explicit excluded sample category — still blocked."""
        from outreach_pipeline.policies.submission import classify_intent
        r = classify_intent(
            "Hello, we need pricing for 20 office printers and toner cartridges."
        )
        assert r["intent_type"] == "off_topic"

    def test_legal_services_off_topic(self):
        from outreach_pipeline.policies.submission import classify_intent
        r = classify_intent(
            "Looking for pricing on legal services for our corporate team."
        )
        assert r["intent_type"] == "off_topic"


# ===== No-Email Blocking at Intake =====

class TestNoEmailBlocking:
    """Test that contacts without email domain are blocked at intake."""

    @pytest.mark.asyncio
    async def test_no_email_blocked_at_intake(self, db):
        # Contact without email domain
        await upsert_contact("no_email", {"company": "Mystery Co"})
        from outreach_pipeline.services.intake import process_submission
        result = await process_submission(
            "no_email", message="I want to buy stuff",
            form_name="Contact Us", mql_date="2026-03-05",
        )
        assert result.get("blocked_reason") == "no_email"
        # Engagement should exist and be blocked
        eid = result.get("engagement_id")
        assert eid is not None
        eng = await get_engagement(eid)
        assert eng["status"] == "blocked"
        assert eng["block_code"] == "NO_EMAIL"

    @pytest.mark.asyncio
    async def test_with_email_not_blocked(self, db):
        await upsert_contact("c1", {
            "company": "Acme Corp", "industry": "Telecom",
            "email_domain": "customer.example.com",
        })
        from outreach_pipeline.services.intake import process_submission
        result = await process_submission(
            "c1", message="I want to buy RADIO-A",
            form_name="Contact Us", mql_date="2026-03-05",
        )
        assert result.get("blocked_reason") != "no_email"

    @pytest.mark.asyncio
    async def test_freemail_domain_still_passes(self, db):
        """Freemail domains are stripped at upsert -- email_domain will be empty."""
        await upsert_contact("freemail", {
            "company": "Some Guy", "email_domain": "",
        })
        from outreach_pipeline.services.intake import process_submission
        result = await process_submission(
            "freemail", message="I want pricing",
            form_name="Contact Us", mql_date="2026-03-05",
        )
        assert result.get("blocked_reason") == "no_email"


# ===== Off-Topic Blocking at Intake =====

class TestOffTopicBlocking:

    @pytest.mark.asyncio
    async def test_off_topic_blocked_at_intake(self, db):
        await upsert_contact("c1", {
            "company": "Acme Corp", "industry": "Telecom",
            "email_domain": "customer.example.com",
        })
        from outreach_pipeline.services.intake import process_submission
        result = await process_submission(
            "c1",
            message="I need to buy printer ribbons and toner",
            form_name="Contact Us", mql_date="2026-03-05",
        )
        eid = result.get("engagement_id")
        assert eid is not None
        eng = await get_engagement(eid)
        assert eng["status"] == "blocked"
        assert eng["block_code"] == "OFF_TOPIC"

    @pytest.mark.asyncio
    async def test_relevant_inquiry_not_blocked(self, db):
        await upsert_contact("c1", {
            "company": "Acme Corp", "industry": "Telecom",
            "email_domain": "customer.example.com",
        })
        from outreach_pipeline.services.intake import process_submission
        result = await process_submission(
            "c1",
            message="We need a backhaul solution for our wireless network",
            form_name="Contact Us", mql_date="2026-03-05",
        )
        eid = result.get("engagement_id")
        eng = await get_engagement(eid)
        assert eng["status"] != "blocked"


# ===== Draft Status and Upsert =====

class TestDraftStatus:

    @pytest.mark.asyncio
    async def test_save_draft_with_status(self, db):
        await upsert_contact("c1", {
            "company": "Acme Corp", "industry": "Telecom",
            "email_domain": "customer.example.com",
        })
        eid = await create_engagement("c1", kind="sales")
        await save_draft("c1", "Sub", "Body", engagement_id=eid, status="draft")
        drafts = await get_drafts(engagement_id=eid)
        assert len(drafts) == 1
        assert drafts[0]["status"] == "draft"

    @pytest.mark.asyncio
    async def test_upsert_updates_status(self, db):
        await upsert_contact("c1", {"company": "Acme Corp", "email_domain": "customer.example.com"})
        eid = await create_engagement("c1", kind="sales")
        await save_draft("c1", "Sub", "Body v1", engagement_id=eid, status="draft")
        await save_draft("c1", "Sub", "Body v2", engagement_id=eid, status="approved")
        drafts = await get_drafts(engagement_id=eid)
        assert len(drafts) == 1
        assert drafts[0]["status"] == "approved"
        assert drafts[0]["body"] == "Body v2"

    @pytest.mark.asyncio
    async def test_upsert_preserves_single_row(self, db):
        await upsert_contact("c1", {"company": "Acme Corp", "email_domain": "customer.example.com"})
        eid = await create_engagement("c1", kind="sales")
        await save_draft("c1", "V1", "B1", engagement_id=eid)
        await save_draft("c1", "V2", "B2", engagement_id=eid)
        await save_draft("c1", "V3", "B3", engagement_id=eid)
        drafts = await get_drafts(engagement_id=eid)
        assert len(drafts) == 1
        assert drafts[0]["subject"] == "V3"

    @pytest.mark.asyncio
    async def test_rejected_draft_can_be_overwritten(self, db):
        await upsert_contact("c1", {"company": "Acme Corp", "email_domain": "customer.example.com"})
        eid = await create_engagement("c1", kind="sales")
        await save_draft("c1", "Old", "Rejected body", engagement_id=eid, status="rejected")
        await save_draft("c1", "New", "Fresh body", engagement_id=eid, status="draft")
        drafts = await get_drafts(engagement_id=eid)
        assert drafts[0]["status"] == "draft"
        assert drafts[0]["body"] == "Fresh body"

    @pytest.mark.asyncio
    async def test_different_engagements_separate_drafts(self, db):
        await upsert_contact("c1", {"company": "Acme Corp", "email_domain": "customer.example.com"})
        eid1 = await create_engagement("c1", kind="sales")
        eid2 = await create_engagement("c1", kind="retarget")
        await save_draft("c1", "Draft A", "Body A", engagement_id=eid1)
        await save_draft("c1", "Draft B", "Body B", engagement_id=eid2)
        assert len(await get_drafts(engagement_id=eid1)) == 1
        assert len(await get_drafts(engagement_id=eid2)) == 1

    @pytest.mark.asyncio
    async def test_save_draft_without_engagement_creates_new(self, db):
        """Drafts without engagement_id always insert (no upsert key)."""
        await upsert_contact("c1", {"company": "Acme Corp", "email_domain": "customer.example.com"})
        await save_draft("c1", "A", "Body A")
        await save_draft("c1", "B", "Body B")
        drafts = await get_drafts(contact_id="c1")
        assert len(drafts) == 2


# ===== Draft Status in save_engagement_draft =====

class TestSaveEngagementDraftStatus:

    @pytest.mark.asyncio
    async def test_default_status_is_draft(self, db):
        await upsert_contact("c1", {"company": "Acme Corp", "email_domain": "customer.example.com"})
        eid = await create_engagement("c1", kind="sales")
        from outreach_pipeline.tools.workflow import save_engagement_draft
        # Use a body that passes validation
        body = "Hi [First Name],\n\nI can help with your request.\n\nBest,\n[Sender Name]"
        r = await save_engagement_draft(eid, "Test Subject", body)
        assert r["success"]
        drafts = await get_drafts(engagement_id=eid)
        assert drafts[0]["status"] == "draft"

    @pytest.mark.asyncio
    async def test_approved_status(self, db):
        await upsert_contact("c1", {"company": "Acme Corp", "email_domain": "customer.example.com"})
        eid = await create_engagement("c1", kind="sales")
        from outreach_pipeline.tools.workflow import save_engagement_draft
        body = "Hi [First Name],\n\nI can help with your request.\n\nBest,\n[Sender Name]"
        r = await save_engagement_draft(eid, "Test", body, draft_status="approved")
        assert r["success"]
        drafts = await get_drafts(engagement_id=eid)
        assert drafts[0]["status"] == "approved"

    @pytest.mark.asyncio
    async def test_validation_still_runs(self, db):
        await upsert_contact("c1", {"company": "Acme Corp", "email_domain": "customer.example.com"})
        eid = await create_engagement("c1", kind="sales")
        from outreach_pipeline.tools.workflow import save_engagement_draft
        r = await save_engagement_draft(eid, "Test", "CALL ME NOW!!!")
        assert not r["success"]
        assert "violations" in r


# ===== Score Confidence =====

class TestScoreConfidence:

    @pytest.mark.asyncio
    async def test_score_confidence_saved(self, db):
        await upsert_contact("c1", {"company": "Acme Corp", "email_domain": "customer.example.com"})
        eid = await create_engagement("c1", kind="sales")
        from outreach_pipeline.tools.engagements import score_engagement
        await score_engagement(
            eid, 75, "Good fit", "microwave_radio",
            buying_intent="high", intent_signals=["pricing_inquiry"],
            score_confidence="high",
        )
        eng = await get_engagement(eid)
        assert eng.get("score_confidence", "") == "high"

    @pytest.mark.asyncio
    async def test_score_confidence_default_empty(self, db):
        await upsert_contact("c1", {"company": "Acme Corp", "email_domain": "customer.example.com"})
        eid = await create_engagement("c1", kind="sales")
        from outreach_pipeline.tools.engagements import score_engagement
        await score_engagement(eid, 50, "Moderate", "")
        eng = await get_engagement(eid)
        # No confidence passed -- should be empty
        assert eng.get("score_confidence", "") == ""

    @pytest.mark.asyncio
    async def test_score_confidence_low(self, db):
        await upsert_contact("c1", {"company": "Acme Corp", "email_domain": "customer.example.com"})
        eid = await create_engagement("c1", kind="sales")
        from outreach_pipeline.tools.engagements import score_engagement
        await score_engagement(
            eid, 10, "Wrong product", "",
            score_confidence="low",
        )
        eng = await get_engagement(eid)
        assert eng.get("score_confidence", "") == "low"


# ===== Enrichment Confidence =====

class TestEnrichmentConfidence:

    @pytest.mark.asyncio
    async def test_enrichment_confidence_saved(self, db):
        await upsert_contact("c1", {"company": "Acme Corp", "email_domain": "customer.example.com"})
        from outreach_pipeline.tools.engagements import save_enrichment
        await save_enrichment("c1", "Telecom company", enrichment_confidence="high")
        c = await get_contact("c1")
        assert c.get("enrichment_confidence", "") == "high"

    @pytest.mark.asyncio
    async def test_enrichment_confidence_default_empty(self, db):
        await upsert_contact("c1", {"company": "Acme Corp", "email_domain": "customer.example.com"})
        from outreach_pipeline.tools.engagements import save_enrichment
        await save_enrichment("c1", "Some company")
        c = await get_contact("c1")
        assert c.get("enrichment_confidence", "") == ""


# ===== Pipeline Run Tracking =====

class TestPipelineRunTracking:

    @pytest.mark.asyncio
    async def test_start_run(self, db):
        from outreach_pipeline.db.replies import start_run
        run_id = await start_run("2026-03-05", "2026-03-05")
        assert run_id is not None
        pool = get_pool()
        row = await pool.fetchrow("SELECT * FROM pipeline_runs WHERE id=$1", run_id)
        assert row["start_date"] == datetime.date(2026, 3, 5)
        assert row["started_at"] is not None

    @pytest.mark.asyncio
    async def test_update_run(self, db):
        from outreach_pipeline.db.replies import start_run, update_run
        run_id = await start_run("2026-03-05", "2026-03-05")
        await update_run(run_id,
                         contacts_fetched=10, emails_drafted=3,
                         summary="## Pipeline Complete",
                         completed_at=datetime.datetime(2026, 3, 5, 12, 0, 0))
        pool = get_pool()
        row = await pool.fetchrow("SELECT * FROM pipeline_runs WHERE id=$1", run_id)
        assert row["contacts_fetched"] == 10
        assert row["emails_drafted"] == 3
        assert "Pipeline Complete" in row["summary"]
        assert row["completed_at"] is not None

    @pytest.mark.asyncio
    async def test_multiple_runs(self, db):
        from outreach_pipeline.db.replies import start_run
        r1 = await start_run("2026-03-04", "2026-03-04")
        r2 = await start_run("2026-03-05", "2026-03-05")
        assert r1 != r2
        pool = get_pool()
        row = await pool.fetchrow("SELECT COUNT(*) as cnt FROM pipeline_runs")
        assert row["cnt"] == 2


# ===== Enrichment Company (unit tests, no network) =====

class TestEnrichTools:

    @pytest.mark.asyncio
    async def test_fetch_page_invalid_url(self):
        from outreach_pipeline.tools.enrich_company import afetch_page
        result = await afetch_page("https://does-not-exist.example.com")
        assert result["text"] == ""
        assert "error" in result

    @pytest.mark.asyncio
    async def test_fetch_page_returns_text(self):
        from outreach_pipeline.tools.enrich_company import afetch_page
        result = await afetch_page("https://www.example.com")
        assert result["url"] == "https://www.example.com"
        # Should get some text (may vary)
        assert isinstance(result["text"], str)

    def test_enrichment_confidence_model(self):
        from outreach_pipeline.tools.enrich_company import EnrichmentConfidence
        ec = EnrichmentConfidence(confidence="high", reason="test", summary="test summary")
        assert ec.confidence == "high"
        assert ec.summary == "test summary"


# ===== Translation for Off-Topic Detection =====

class TestTranslation:

    def test_english_not_translated(self):
        from outreach_pipeline.policies.submission import _translate_if_needed
        text = "I want to buy some products for my office"
        result = _translate_if_needed(text)
        assert result == text

    def test_short_text_not_translated(self):
        from outreach_pipeline.policies.submission import _translate_if_needed
        result = _translate_if_needed("hola")
        assert result == "hola"

    def test_empty_text_returned(self):
        from outreach_pipeline.policies.submission import _translate_if_needed
        assert _translate_if_needed("") == ""
        assert _translate_if_needed(None) is None


# ===== Pipeline Summary Formatting =====

class TestPipelineSummaryFormatting:

    def test_format_with_all_sections(self):
        from outreach_pipeline.graph.agents.pipeline_agent import _format_pipeline_summary
        result = {
            "log": [
                json.dumps({"type": "ingest", "fetched": 4, "to_process": 4, "date_range": "2026-03-04"}),
                json.dumps({"type": "contact", "contact_id": "123", "company": "Acme",
                            "enriched": True, "engagements": [
                                {"id": 1, "score": 85, "status": "drafting", "product": "microwave_radio", "kind": "sales"}
                            ], "ready_count": 1}),
                json.dumps({"type": "drafts", "results": [
                    {"engagement_id": 1, "status": "approved", "subject": "Test Subject", "company": "Acme"},
                ]}),
            ]
        }
        summary = _format_pipeline_summary(result)
        assert "Pipeline Complete" in summary
        assert "Acme" in summary
        assert "85" in summary
        assert "microwave_radio" in summary
        assert "Test Subject" in summary
        assert "1 approved" in summary

    def test_format_empty_pipeline(self):
        from outreach_pipeline.graph.agents.pipeline_agent import _format_pipeline_summary
        result = {"log": []}
        summary = _format_pipeline_summary(result)
        assert "Pipeline Complete" in summary

    def test_format_no_drafts(self):
        from outreach_pipeline.graph.agents.pipeline_agent import _format_pipeline_summary
        result = {
            "log": [
                json.dumps({"type": "ingest", "fetched": 2, "to_process": 0, "date_range": "2026-03-04"}),
            ]
        }
        summary = _format_pipeline_summary(result)
        assert "0 to process" in summary

    def test_format_rejected_drafts(self):
        from outreach_pipeline.graph.agents.pipeline_agent import _format_pipeline_summary
        result = {
            "log": [
                json.dumps({"type": "drafts", "results": [
                    {"engagement_id": 1, "status": "rejected"},
                    {"engagement_id": 2, "status": "rejected"},
                ]}),
            ]
        }
        summary = _format_pipeline_summary(result)
        assert "2 rejected" in summary


# ===== Router Patterns =====

class TestRouterPipelinePatterns:

    def test_list_pipeline_runs(self):
        from outreach_pipeline.graph.router import classify_intent
        assert classify_intent("list pipeline runs") == "pipeline"

    def test_show_pipeline_run_number(self):
        from outreach_pipeline.graph.router import classify_intent
        assert classify_intent("pipeline run #3") == "pipeline"

    def test_pipeline_history(self):
        from outreach_pipeline.graph.router import classify_intent
        assert classify_intent("pipeline history") == "pipeline"

    def test_fetch_contacts(self):
        from outreach_pipeline.graph.router import classify_intent
        assert classify_intent("fetch friday's contacts") == "pipeline"


# ===== ScoreResult Pydantic Model =====

class TestScoreResultModel:

    def _make_result(self, **overrides):
        from outreach_pipeline.graph.workflows.full_pipeline import ScoreResult, DimensionScore
        defaults = {
            "product_fit": DimensionScore(score=7, evidence="Asks about PTP link"),
            "buying_intent": DimensionScore(score=8, evidence="Requests quote"),
            "authority": DimensionScore(score=5, evidence="Senior manager"),
            "engagement_signals": DimensionScore(score=3, evidence="Single contact form"),
            "company_fit": DimensionScore(score=6, evidence="Telecom company"),
            "confidence": "high",
            "primary_product": "microwave_radio",
        }
        defaults.update(overrides)
        return ScoreResult(**defaults)

    def test_valid_score_result(self):
        r = self._make_result()
        assert 1 <= r.weighted_score <= 100
        assert r.confidence == "high"
        assert "Product:7/10" in r.reasoning_summary

    def test_weighted_score_calculation(self):
        # All 10s = 100, all 5s = 50, all 0s = 1 (clamped)
        from outreach_pipeline.graph.workflows.full_pipeline import DimensionScore
        r_max = self._make_result(
            product_fit=DimensionScore(score=10, evidence="x"),
            buying_intent=DimensionScore(score=10, evidence="x"),
            authority=DimensionScore(score=10, evidence="x"),
            engagement_signals=DimensionScore(score=10, evidence="x"),
            company_fit=DimensionScore(score=10, evidence="x"),
        )
        assert r_max.weighted_score == 100

        r_mid = self._make_result(
            product_fit=DimensionScore(score=5, evidence="x"),
            buying_intent=DimensionScore(score=5, evidence="x"),
            authority=DimensionScore(score=5, evidence="x"),
            engagement_signals=DimensionScore(score=5, evidence="x"),
            company_fit=DimensionScore(score=5, evidence="x"),
        )
        assert r_mid.weighted_score == 50

    def test_dimension_score_bounds(self):
        from outreach_pipeline.graph.workflows.full_pipeline import DimensionScore
        with pytest.raises(Exception):
            DimensionScore(score=11, evidence="Too high")
        with pytest.raises(Exception):
            DimensionScore(score=-1, evidence="Too low")

    def test_invalid_confidence(self):
        from outreach_pipeline.graph.workflows.full_pipeline import ScoreResult
        with pytest.raises(Exception):
            ScoreResult(score=50, score_reasoning="Ok", confidence="very_high")

    def test_defaults(self):
        r = self._make_result()
        assert r.confidence == "high"
        assert r.primary_product == "microwave_radio"
        assert r.weighted_score > 0


# ===== EnrichmentConfidence Pydantic Model =====

class TestEnrichmentConfidenceModel:

    def test_valid_confidence(self):
        from outreach_pipeline.tools.enrich_company import EnrichmentConfidence
        r = EnrichmentConfidence(
            confidence="high",
            reason="Company name appears in content",
            summary="Acme Corp is a telecom company.",
        )
        assert r.confidence == "high"

    def test_invalid_confidence_value(self):
        from outreach_pipeline.tools.enrich_company import EnrichmentConfidence
        with pytest.raises(Exception):
            EnrichmentConfidence(confidence="very_high", reason="", summary="")

    def test_defaults(self):
        from outreach_pipeline.tools.enrich_company import EnrichmentConfidence
        r = EnrichmentConfidence(reason="No data", summary="")
        assert r.confidence == "low"
        assert r.summary == ""


# ===== Draft Approval Status Tests =====

class TestDraftApprovalStatus:
    """Test that approving a draft advances engagement status."""

    @pytest.mark.asyncio
    async def test_save_draft_approved_advances_to_pending_approval(self, db):
        from outreach_pipeline.db.engagements import create_engagement, get_engagement
        from outreach_pipeline.db.contacts import upsert_contact
        from outreach_pipeline.tools.workflow import save_engagement_draft

        await upsert_contact("test_approval_1", {"company": "TestCo"})
        eid = await create_engagement("test_approval_1", kind="sales")
        # Advance through open → scoring → drafting
        from outreach_pipeline.db.engagements import update_status
        await update_status(eid, "scoring")
        await update_status(eid, "drafting")

        result = await save_engagement_draft(
            eid, "Test Subject", "Hi [First Name],\n\nTest body.\n\nBest,\n[Sender Name]",
            draft_status="approved",
        )
        assert result["success"]

        eng = await get_engagement(eid)
        assert eng["status"] == "pending_approval"

    @pytest.mark.asyncio
    async def test_save_draft_without_approved_stays_drafting(self, db):
        from outreach_pipeline.db.engagements import create_engagement, get_engagement
        from outreach_pipeline.db.contacts import upsert_contact
        from outreach_pipeline.tools.workflow import save_engagement_draft
        from outreach_pipeline.db.engagements import update_status

        await upsert_contact("test_approval_2", {"company": "TestCo2"})
        eid = await create_engagement("test_approval_2", kind="sales")
        await update_status(eid, "scoring")
        await update_status(eid, "drafting")

        result = await save_engagement_draft(
            eid, "Test Subject", "Hi [First Name],\n\nTest body.\n\nBest,\n[Sender Name]",
        )
        assert result["success"]

        eng = await get_engagement(eid)
        assert eng["status"] == "drafting"


# ===== Multi-Dimension Scoring Tests =====

class TestMultiDimensionScoring:
    """Test the multi-dimension scoring model."""

    def test_weighted_score_all_tens(self):
        from outreach_pipeline.graph.workflows.full_pipeline import ScoreResult, DimensionScore
        r = ScoreResult(
            product_fit=DimensionScore(score=10, evidence="Names RADIO-A"),
            buying_intent=DimensionScore(score=10, evidence="Requests quote with timeline"),
            authority=DimensionScore(score=10, evidence="CTO"),
            engagement_signals=DimensionScore(score=10, evidence="5 page views + download"),
            company_fit=DimensionScore(score=10, evidence="Tier 1 operator"),
            confidence="high", primary_product="microwave_radio",
        )
        assert r.weighted_score == 100

    def test_weighted_score_mixed_applies_buying_floor(self):
        """When product_fit and buying_intent both ≥ 7 on average, the
        score is floored at 70 — a clear quote request shouldn't be
        demoted to "send a brochure" just because authority/engagement
        are weak. Raw weighted value for this case is 57; the floor
        raises it to 70. See weighted_score docstring."""
        from outreach_pipeline.graph.workflows.full_pipeline import ScoreResult, DimensionScore
        r = ScoreResult(
            product_fit=DimensionScore(score=7, evidence="PTP link"),
            buying_intent=DimensionScore(score=8, evidence="Requests quote"),
            authority=DimensionScore(score=3, evidence="No title"),
            engagement_signals=DimensionScore(score=3, evidence="Single form"),
            company_fit=DimensionScore(score=6, evidence="Telecom"),
            confidence="medium", primary_product="general",
        )
        assert r.weighted_score == 70

    def test_weighted_score_mixed_no_floor(self):
        """When product_fit+buying_intent don't clear the floor cutoff,
        the raw weighted average is what gets returned."""
        from outreach_pipeline.graph.workflows.full_pipeline import ScoreResult, DimensionScore
        r = ScoreResult(
            product_fit=DimensionScore(score=5, evidence="Adjacent product"),
            buying_intent=DimensionScore(score=5, evidence="Asking what's available"),
            authority=DimensionScore(score=3, evidence="No title"),
            engagement_signals=DimensionScore(score=3, evidence="Single form"),
            company_fit=DimensionScore(score=6, evidence="Telecom"),
            confidence="medium", primary_product="general",
        )
        # 5*0.25 + 5*0.25 + 3*0.20 + 3*0.15 + 6*0.15 = 4.45 → 44 (banker's
        # rounding; floor at 70 not triggered because
        # (product_fit + buying_intent) / 2 = 5.0 < 7.0).
        assert r.weighted_score == 44

    def test_weighted_score_all_zeros(self):
        from outreach_pipeline.graph.workflows.full_pipeline import ScoreResult, DimensionScore
        r = ScoreResult(
            product_fit=DimensionScore(score=0, evidence="Wrong category"),
            buying_intent=DimensionScore(score=0, evidence="No message"),
            authority=DimensionScore(score=0, evidence="Unknown"),
            engagement_signals=DimensionScore(score=0, evidence="None"),
            company_fit=DimensionScore(score=0, evidence="Unknown"),
            confidence="low", primary_product="general",
        )
        assert r.weighted_score == 1  # clamped to 1

    def test_reasoning_summary_format(self):
        from outreach_pipeline.graph.workflows.full_pipeline import ScoreResult, DimensionScore
        r = ScoreResult(
            product_fit=DimensionScore(score=8, evidence="Asks about RADIO-A for 5G backhaul"),
            buying_intent=DimensionScore(score=6, evidence="General inquiry"),
            authority=DimensionScore(score=5, evidence="Manager"),
            engagement_signals=DimensionScore(score=3, evidence="One form"),
            company_fit=DimensionScore(score=7, evidence="ISP company"),
            confidence="medium", primary_product="microwave_radio",
        )
        summary = r.reasoning_summary
        assert "Product:8/10" in summary
        assert "Intent:6/10" in summary
        assert "Asks about RADIO-A" in summary  # top evidence

    def test_dimension_score_bounds(self):
        from outreach_pipeline.graph.workflows.full_pipeline import DimensionScore
        with pytest.raises(Exception):
            DimensionScore(score=11, evidence="Over max")
        with pytest.raises(Exception):
            DimensionScore(score=-1, evidence="Under min")
