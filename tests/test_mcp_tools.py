"""Tests for MCP tool functions -- stats, domain lookup, draft validation."""
import json
import pytest

from outreach_pipeline.db.contacts import upsert_contact, score_contact, enrich_contact
from outreach_pipeline.db.drafts import save_draft, mark_sent
from outreach_pipeline.db.replies import save_reply
from outreach_pipeline.db.submissions import create_submission
from outreach_pipeline.db.engagements import (
    create_engagement, add_submission,
    update_status, score_engagement,
)
from outreach_pipeline.db.guardrails import add_opt_out
from outreach_pipeline.db.pool import get_pool


class TestPipelineStats:

    @pytest.mark.asyncio
    async def test_empty_pipeline(self, db):
        """Stats on empty DB should return zeros, not crash."""
        from outreach_pipeline.db.contacts import count_contacts
        counts = await count_contacts()
        assert counts["total"] == 0

    @pytest.mark.asyncio
    async def test_stats_with_data(self, db):
        """Stats should count contacts, submissions, engagements, drafts."""
        await upsert_contact("c1", {"company": "Acme", "message": "Hi"})
        await upsert_contact("c2", {"company": "Beta", "message": "Hello"})
        await enrich_contact("c1", "Context")
        await score_contact("c1", "high", [], 80, "")
        await save_draft("c1", "Subject", "Body", 80)

        sid = await create_submission("c1", message="Hi", mql_date="2026-03-28T10:00:00Z")
        eid = await create_engagement("c1", kind="sales")
        await add_submission(eid, sid)
        await score_engagement(eid, score=80)

        from outreach_pipeline.db.contacts import count_contacts
        counts = await count_contacts()
        assert counts["total"] == 2
        assert counts["enriched"] == 1
        assert counts["scored"] == 1

        pool = get_pool()
        # Check submission count
        row = await pool.fetchrow("SELECT COUNT(*) as cnt FROM submissions")
        assert row["cnt"] == 1

        # Check engagement count
        row = await pool.fetchrow("SELECT COUNT(*) as cnt FROM engagements")
        assert row["cnt"] == 1

        # Check draft count
        row = await pool.fetchrow("SELECT COUNT(*) as cnt FROM drafts")
        assert row["cnt"] == 1

    @pytest.mark.asyncio
    async def test_stats_engagement_by_status(self, db):
        await upsert_contact("c1", {"company": "A"})
        await upsert_contact("c2", {"company": "B"})
        await create_engagement("c1", kind="sales")
        await create_engagement("c2", kind="support",
                                status="blocked", block_terminal=True)

        pool = get_pool()
        rows = await pool.fetch(
            "SELECT status, COUNT(*) as cnt FROM engagements GROUP BY status"
        )
        statuses = {r["status"]: r["cnt"] for r in rows}
        assert statuses.get("open", 0) == 1
        assert statuses.get("blocked", 0) == 1

    @pytest.mark.asyncio
    async def test_stats_engagement_by_kind(self, db):
        await upsert_contact("c1", {"company": "A"})
        await create_engagement("c1", kind="sales")
        await create_engagement("c1", kind="support",
                                status="blocked", block_terminal=True)

        pool = get_pool()
        rows = await pool.fetch(
            "SELECT kind, COUNT(*) as cnt FROM engagements GROUP BY kind"
        )
        kinds = {r["kind"]: r["cnt"] for r in rows}
        assert kinds["sales"] == 1
        assert kinds["support"] == 1


class TestProspectDomain:

    @pytest.mark.asyncio
    async def test_email_domain_found(self, db):
        await upsert_contact("c1", {
            "company": "Example Networks", "email": "user@networks.example.com",
        })
        from outreach_pipeline.db.contacts import get_contact
        c = await get_contact("c1")
        assert c["properties"]["email_domain"] == "networks.example.com"

    @pytest.mark.asyncio
    async def test_freemail_sets_has_email(self, db):
        """Freemail: email_domain skipped but has_email flag set."""
        await upsert_contact("c1", {
            "company": "Example Networks", "email": "user@example.com",
        })
        from outreach_pipeline.db.contacts import get_contact
        c = await get_contact("c1")
        assert "email_domain" not in c["properties"]
        assert c["properties"]["has_email"] is True

    @pytest.mark.asyncio
    async def test_no_email_no_domain(self, db):
        await upsert_contact("c1", {"company": "Example Networks"})
        from outreach_pipeline.db.contacts import get_contact
        c = await get_contact("c1")
        assert "email_domain" not in c["properties"]

    @pytest.mark.asyncio
    async def test_first_url_available(self, db):
        await upsert_contact("c1", {
            "company": "Example Networks",
            "hs_analytics_first_url": "https://www.networks.example.com/products",
        })
        from outreach_pipeline.db.contacts import get_contact
        c = await get_contact("c1")
        assert "networks.example.com" in c["properties"]["hs_analytics_first_url"]


class TestDraftValidation:

    def test_valid_draft_body_passes(self):
        """Body with correct greeting and signoff should pass validation."""
        from outreach_pipeline.validation.tone import validate
        body = (
            "Hi [First Name],\n\n"
            "The RADIO-A platform supports your use case. "
            "Want me to send the configuration guide?\n\n"
            "Best,\n[Sender Name]"
        )
        result = validate(body, profile="cold_outreach")
        assert result.passed, f"Unexpected violations: {result.violations}"

    def test_subject_prepended_to_body_breaks_greeting(self):
        """Prepending subject to body should break greeting check -- this is the bug we fixed."""
        from outreach_pipeline.validation.tone import validate
        subject = "Your PtP link inquiry"
        body = (
            "Hi [First Name],\n\n"
            "The RADIO-A platform supports your use case.\n\n"
            "Best,\n[Sender Name]"
        )
        # Subject + body (the old broken behavior)
        combined = f"{subject}\n{body}"
        result = validate(combined, profile="cold_outreach")
        # This SHOULD fail because greeting is not at the start
        labels = [v["label"] for v in result.violations]
        assert "MISSING_GREETING" in labels

    def test_body_only_validation_passes(self):
        """Body-only validation (the fix) should pass."""
        from outreach_pipeline.validation.tone import validate
        body = (
            "Hi [First Name],\n\n"
            "The RADIO-A platform supports your use case.\n\n"
            "Best,\n[Sender Name]"
        )
        result = validate(body, profile="cold_outreach")
        assert result.passed

    def test_missing_greeting_caught(self):
        from outreach_pipeline.validation.tone import validate
        body = "Hello there,\n\nSome content.\n\nBest,\n[Sender Name]"
        result = validate(body, profile="cold_outreach")
        labels = [v["label"] for v in result.violations]
        assert "MISSING_GREETING" in labels

    def test_missing_signoff_caught(self):
        from outreach_pipeline.validation.tone import validate
        body = "Hi [First Name],\n\nSome content.\n\nCheers,\nJohn"
        result = validate(body, profile="cold_outreach")
        labels = [v["label"] for v in result.violations]
        assert "MISSING_SIGNOFF" in labels
