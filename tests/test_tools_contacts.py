"""Unit tests for tools/contacts.py — contact tools."""
import pytest

from outreach_pipeline.db.contacts import upsert_contact, score_contact, enrich_contact


class TestGetLeads:
    @pytest.fixture(autouse=True)
    async def _seed(self, db):
        await upsert_contact("1", {"company": "Acme", "message": "Hi"})
        await upsert_contact("2", {"company": "Beta", "message": "Hello"})
        await score_contact("1", "high", ["pricing"], 90, "Strong")
        await score_contact("2", "low", [], 20, "Weak")

    @pytest.mark.asyncio
    async def test_get_leads_returns_contacts(self, db):
        from outreach_pipeline.tools.contacts import get_leads
        result = await get_leads()
        assert "leads" in result
        assert len(result["leads"]) > 0

    @pytest.mark.asyncio
    async def test_get_leads_min_score(self, db):
        from outreach_pipeline.tools.contacts import get_leads
        result = await get_leads(min_score=50)
        assert len(result["leads"]) == 1
        assert result["leads"][0]["id"] == "1"


class TestGetLeadsDateFilters:
    @pytest.fixture(autouse=True)
    async def _seed(self, db):
        await upsert_contact("1", {"company": "Jan"}, fetched_at="2026-01-15T10:00:00Z")
        await upsert_contact("2", {"company": "Feb"}, fetched_at="2026-02-15T10:00:00Z")
        await upsert_contact("3", {"company": "Mar"}, fetched_at="2026-03-15T10:00:00Z")
        await score_contact("1", "high", [], 80, "")
        await score_contact("2", "high", [], 70, "")
        await score_contact("3", "high", [], 90, "")

    @pytest.mark.asyncio
    async def test_fetched_date_filter(self, db):
        from outreach_pipeline.tools.contacts import get_leads
        result = await get_leads(fetched_date="2026-02-15")
        assert len(result["leads"]) == 1
        assert result["leads"][0]["id"] == "2"

    @pytest.mark.asyncio
    async def test_date_range_filter(self, db):
        from outreach_pipeline.tools.contacts import get_leads
        result = await get_leads(fetched_after="2026-01-01", fetched_before="2026-03-31")
        assert len(result["leads"]) == 3

    @pytest.mark.asyncio
    async def test_q1_filter(self, db):
        from outreach_pipeline.tools.contacts import get_leads
        result = await get_leads(fetched_after="2026-01-01", fetched_before="2026-01-31")
        assert len(result["leads"]) == 1
        assert result["leads"][0]["id"] == "1"


class TestGetLeadsMQLDateFilters:
    """Test date filters using MQL date from properties, not fetched_at."""

    @pytest.fixture(autouse=True)
    async def _seed(self, db):
        await upsert_contact("1", {
            "company": "Jan Co",
            "hs_v2_date_entered_marketingqualifiedlead": "2026-01-15T10:00:00Z",
        })
        await upsert_contact("2", {
            "company": "Feb Co",
            "hs_v2_date_entered_marketingqualifiedlead": "2026-02-15T10:00:00Z",
        })
        await upsert_contact("3", {
            "company": "Mar Co",
            "hs_v2_date_entered_marketingqualifiedlead": "2026-03-28T08:00:00Z",
        })
        await score_contact("1", "high", [], 80, "")
        await score_contact("2", "high", [], 70, "")
        await score_contact("3", "high", [], 90, "")

    @pytest.mark.asyncio
    async def test_mql_date_filter(self, db):
        from outreach_pipeline.tools.contacts import get_leads
        result = await get_leads(date="2026-03-28")
        assert len(result["leads"]) == 1
        assert result["leads"][0]["id"] == "3"

    @pytest.mark.asyncio
    async def test_mql_date_range_q1(self, db):
        from outreach_pipeline.tools.contacts import get_leads
        result = await get_leads(after="2026-01-01", before="2026-03-31")
        assert len(result["leads"]) == 3

    @pytest.mark.asyncio
    async def test_mql_date_range_jan_only(self, db):
        from outreach_pipeline.tools.contacts import get_leads
        result = await get_leads(after="2026-01-01", before="2026-01-31")
        assert len(result["leads"]) == 1
        assert result["leads"][0]["id"] == "1"

    @pytest.mark.asyncio
    async def test_mql_date_independent_of_fetched_at(self, db):
        """MQL date filter should work even when all contacts were fetched on the same day."""
        from outreach_pipeline.tools.contacts import get_leads
        result = await get_leads(date="2026-02-15")
        assert len(result["leads"]) == 1
        assert result["leads"][0]["properties"]["company"] == "Feb Co"


class TestGetLeadsReplyFilter:
    @pytest.fixture(autouse=True)
    async def _seed(self, db):
        await upsert_contact("1", {"company": "Replied"})
        await upsert_contact("2", {"company": "NoReply"})
        await upsert_contact("3", {"company": "NoDraft"})
        await score_contact("1", "high", [], 80, "")
        await score_contact("2", "high", [], 70, "")
        await score_contact("3", "high", [], 60, "")
        # Create drafts and mark sent
        from outreach_pipeline.db.drafts import save_draft, mark_sent
        draft1_id = await save_draft("1", "S", "B", 80)
        await mark_sent(draft1_id)
        draft2_id = await save_draft("2", "S", "B", 70)
        await mark_sent(draft2_id)
        # Contact 1 got a reply, contact 2 did not
        from outreach_pipeline.db.replies import save_reply
        await save_reply("1", "Re:", "Yes!", "INCOMING", "2026-03-20T10:00:00Z")

    @pytest.mark.asyncio
    async def test_no_reply_filter(self, db):
        from outreach_pipeline.tools.contacts import get_leads
        result = await get_leads(no_reply=True)
        assert len(result["leads"]) == 1
        assert result["leads"][0]["id"] == "2"  # sent but no reply

    @pytest.mark.asyncio
    async def test_no_reply_excludes_never_sent(self, db):
        from outreach_pipeline.tools.contacts import get_leads
        result = await get_leads(no_reply=True)
        ids = [lead["id"] for lead in result["leads"]]
        assert "3" not in ids  # never drafted/sent


class TestGetLeadDetails:
    @pytest.fixture(autouse=True)
    async def _seed(self, db):
        await upsert_contact("1", {"company": "Acme"})
        await enrich_contact("1", company_context="Makes rockets")

    @pytest.mark.asyncio
    async def test_get_lead_details(self, db):
        from outreach_pipeline.tools.contacts import get_lead_details
        result = await get_lead_details("1")
        assert result["contact"]["id"] == "1"
        assert result["contact"]["company_context"] == "Makes rockets"

    @pytest.mark.asyncio
    async def test_get_lead_details_not_found(self, db):
        """The tool returns {"contact": None} when the contact_id
        doesn't exist — the frontend renders "contact not found" on
        a null value. Empty dict would falsely render a blank card."""
        from outreach_pipeline.tools.contacts import get_lead_details
        result = await get_lead_details("999")
        assert result["contact"] is None
