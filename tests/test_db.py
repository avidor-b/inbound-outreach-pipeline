"""Async Postgres tests for contacts, drafts, guardrails, replies, pipeline runs."""
import pytest

from outreach_pipeline.db.contacts import (
    upsert_contact, enrich_contact, score_contact,
    get_contact, get_contacts, count_contacts,
)
from outreach_pipeline.db.drafts import save_draft, mark_sent, get_drafts, log_send
from outreach_pipeline.db.guardrails import (
    check_opt_out, add_opt_out, check_duplicate, check_send_limit,
    check_scrape_blocklist, add_scrape_block,
)
from outreach_pipeline.db.replies import save_reply, get_hot_leads, start_run, update_run
from outreach_pipeline.db.pool import get_pool


class TestContacts:
    @pytest.mark.asyncio
    async def test_upsert_and_get(self, db):
        await upsert_contact("1", {"company": "Acme", "message": "Hi"})
        c = await get_contact("1")
        assert c["properties"]["company"] == "Acme"

    @pytest.mark.asyncio
    async def test_upsert_strips_pii(self, db):
        await upsert_contact("1", {
            "company": "Acme", "firstname": "John", "lastname": "Doe",
            "email": "john@example.com", "phone": "+1-555-1234", "message": "Hi",
        })
        c = await get_contact("1")
        assert "firstname" not in c["properties"]
        assert "lastname" not in c["properties"]
        assert "email" not in c["properties"]
        assert "phone" not in c["properties"]
        assert c["properties"]["company"] == "Acme"
        assert c["properties"]["message"] == "Hi"

    @pytest.mark.asyncio
    async def test_upsert_preserves_email_domain(self, db):
        await upsert_contact("1", {
            "company": "Acme", "email": "john@customer.example.com",
        })
        c = await get_contact("1")
        assert c["properties"]["email_domain"] == "customer.example.com"
        assert "email" not in c["properties"]

    @pytest.mark.asyncio
    async def test_upsert_freemail_sets_has_email(self, db):
        """Freemail: email_domain skipped but has_email flag set."""
        await upsert_contact("1", {
            "company": "Acme", "email": "john@example.com",
        })
        c = await get_contact("1")
        assert "email_domain" not in c["properties"]
        assert c["properties"]["has_email"] is True

    @pytest.mark.asyncio
    async def test_refetch_resets_scoring_and_enrichment(self, db):
        """Re-fetching a contact resets scores and enrichment because properties may have changed."""
        await upsert_contact("1", {"message": "Old message"})
        await enrich_contact("1", company_context="Old context")
        await score_contact("1", "high", ["pricing"], 90, "Strong signals")
        c = await get_contact("1")
        assert c["score"] == 90
        assert c["enriched_at"]

        # Re-fetch with new properties
        await upsert_contact("1", {"message": "New message"})
        c = await get_contact("1")
        assert c["properties"]["message"] == "New message"
        assert c["score"] == 0
        assert c["company_context"] == ""
        assert not c["enriched_at"]  # '' after None sanitization
        assert not c["scored_at"]

    @pytest.mark.asyncio
    async def test_enrich(self, db):
        await upsert_contact("1", {})
        await enrich_contact("1", company_context="Makes robots", crm_context="Past customer")
        c = await get_contact("1")
        assert c["company_context"] == "Makes robots"
        assert c["enriched_at"]

    @pytest.mark.asyncio
    async def test_score(self, db):
        await upsert_contact("1", {})
        await score_contact("1", "high", ["pricing"], 90, "Strong signals")
        c = await get_contact("1")
        assert c["score"] == 90
        assert c["intent_signals"] == ["pricing"]

    @pytest.mark.asyncio
    async def test_get_contacts_sorted_by_score(self, db):
        await upsert_contact("1", {})
        await upsert_contact("2", {})
        await score_contact("1", "low", [], 20, "")
        await score_contact("2", "high", [], 90, "")
        contacts = await get_contacts()
        assert contacts[0]["id"] == "2"

    @pytest.mark.asyncio
    async def test_get_contacts_min_score(self, db):
        await upsert_contact("1", {})
        await upsert_contact("2", {})
        await score_contact("1", "low", [], 20, "")
        await score_contact("2", "high", [], 90, "")
        contacts = await get_contacts(min_score=50)
        assert len(contacts) == 1
        assert contacts[0]["id"] == "2"

    @pytest.mark.asyncio
    async def test_count(self, db):
        await upsert_contact("1", {})
        await upsert_contact("2", {})
        await enrich_contact("1", "ctx")
        counts = await count_contacts()
        assert counts["total"] == 2
        assert counts["enriched"] == 1


class TestDrafts:
    @pytest.mark.asyncio
    async def test_save_and_get(self, db):
        await upsert_contact("1", {})
        await save_draft("1", "Subject", "Body", 85)
        drafts = await get_drafts()
        assert len(drafts) == 1
        assert drafts[0]["subject"] == "Subject"

    @pytest.mark.asyncio
    async def test_mark_sent(self, db):
        await upsert_contact("1", {})
        draft_id = await save_draft("1", "S", "B")
        await mark_sent(draft_id)
        drafts = await get_drafts(status="sent")
        assert len(drafts) == 1

    @pytest.mark.asyncio
    async def test_resource_fields_default_to_empty_lists(self, db):
        # When save_draft is called without resource kwargs, the JSONB
        # columns must round-trip as [] — consumers (UI + drafter) rely
        # on iterable shapes without None-checks.
        await upsert_contact("1", {})
        await save_draft("1", "Subject", "Body")
        drafts = await get_drafts()
        assert drafts[0]["promised_resources"] == []
        assert drafts[0]["referenced_resource_ids"] == []
        assert drafts[0]["unverified_promises"] == []

    @pytest.mark.asyncio
    async def test_save_persists_promised_resources(self, db):
        await upsert_contact("1", {})
        resources = [
            {"id": "sb1", "title": "Utilities Brief",
             "url": "https://example.com/sb", "asset_type": "solution-brief",
             "summary": "Short", "match_score": 14.0},
            {"id": "cs1", "title": "Offshore Case Study",
             "url": "https://example.com/cs", "asset_type": "case-study",
             "summary": "Longer summary", "match_score": 12.0},
        ]
        await save_draft(
            "1", "Subj", "Body", score=70,
            promised_resources=resources,
            referenced_resource_ids=["sb1"],
            unverified_promises=["solar-powered setup brief"],
        )
        drafts = await get_drafts()
        got = drafts[0]
        assert len(got["promised_resources"]) == 2
        assert got["promised_resources"][0]["id"] == "sb1"
        assert got["promised_resources"][0]["match_score"] == 14.0
        assert got["referenced_resource_ids"] == ["sb1"]
        assert got["unverified_promises"] == ["solar-powered setup brief"]

    @pytest.mark.asyncio
    async def test_upsert_overwrites_resource_fields(self, db):
        # Save_draft upserts on engagement_id — the resource fields should
        # be replaced, not appended, on the second call.
        from outreach_pipeline.db.engagements import create_engagement
        await upsert_contact("1", {})
        eid = await create_engagement("1", kind="inbound_mql")

        await save_draft(
            "1", "v1", "body1", engagement_id=eid,
            promised_resources=[{"id": "old", "title": "Old"}],
            referenced_resource_ids=["old"],
            unverified_promises=["old phrase"],
        )
        await save_draft(
            "1", "v2", "body2", engagement_id=eid,
            promised_resources=[{"id": "new", "title": "New"}],
            referenced_resource_ids=[],
            unverified_promises=[],
        )
        drafts = await get_drafts(engagement_id=eid)
        assert len(drafts) == 1  # still one row (upsert)
        assert drafts[0]["subject"] == "v2"
        assert drafts[0]["promised_resources"] == [{"id": "new", "title": "New"}]
        assert drafts[0]["referenced_resource_ids"] == []
        assert drafts[0]["unverified_promises"] == []


class TestGuardrails:
    @pytest.mark.asyncio
    async def test_opt_out(self, db):
        assert not (await check_opt_out("1"))[0]
        await add_opt_out("1", "Not interested")
        assert (await check_opt_out("1"))[0]

    @pytest.mark.asyncio
    async def test_duplicate_no_history(self, db):
        assert not (await check_duplicate("1"))[0]

    @pytest.mark.asyncio
    async def test_duplicate_recent_send(self, db):
        await upsert_contact("1", {})
        await log_send("1", "Hi")
        assert (await check_duplicate("1"))[0]

    @pytest.mark.asyncio
    async def test_duplicate_allows_fresh_mql(self, db):
        await upsert_contact("1", {})
        await log_send("1", "Hi")
        assert not (await check_duplicate("1", mql_date="2099-01-01T00:00:00Z"))[0]

    @pytest.mark.asyncio
    async def test_send_limit(self, db):
        assert not (await check_send_limit(2))[0]
        await upsert_contact("1", {})
        await upsert_contact("2", {})
        await log_send("1", "A")
        await log_send("2", "B")
        assert (await check_send_limit(2))[0]

    @pytest.mark.asyncio
    async def test_scrape_blocklist(self, db):
        assert not (await check_scrape_blocklist("customer.example.com"))[0]
        await add_scrape_block("customer.example.com", "Competitor")
        assert (await check_scrape_blocklist("customer.example.com"))[0]


class TestReplies:
    @pytest.mark.asyncio
    async def test_save_and_get_hot(self, db):
        await upsert_contact("1", {})
        await score_contact("1", "high", [], 90, "")
        await save_reply("1", "Re: Hi", "Yes let's talk", "INCOMING", "2026-02-20", urgency="high")
        hot = await get_hot_leads()
        assert len(hot) == 1
        assert hot[0]["contact_id"] == "1"


class TestPipelineRuns:
    @pytest.mark.asyncio
    async def test_start_and_update(self, db):
        run_id = await start_run("2026-02-16", "2026-02-22")
        await update_run(run_id, contacts_fetched=5, emails_drafted=3)
        pool = get_pool()
        row = await pool.fetchrow("SELECT * FROM pipeline_runs WHERE id=$1", run_id)
        assert row["contacts_fetched"] == 5
