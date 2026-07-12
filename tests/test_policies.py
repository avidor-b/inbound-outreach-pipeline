"""Unit tests for policies/ — PolicyDecision and policy facades."""
import pytest

from outreach_pipeline.db.contacts import upsert_contact
from outreach_pipeline.db.guardrails import add_opt_out, add_scrape_block
from outreach_pipeline.db.drafts import log_send
from outreach_pipeline.policies.codes import Codes
from outreach_pipeline.policies.decisions import PolicyDecision
from outreach_pipeline.policies.contact import check_contact_policies
from outreach_pipeline.policies.send import check_send_policies
from outreach_pipeline.policies.scrape import check_scrape_policies


class TestPolicyDecision:
    def test_allow(self):
        d = PolicyDecision.allow()
        assert d.allowed
        assert d.code == ""

    def test_deny(self):
        d = PolicyDecision.deny("OPT_OUT", "Contact opted out", contact_id="123")
        assert not d.allowed
        assert d.code == "OPT_OUT"
        assert d.reason == "Contact opted out"
        assert d.details["contact_id"] == "123"


class TestContactPolicies:
    @pytest.mark.asyncio
    async def test_allows_clean_contact(self, db):
        await upsert_contact("1", {"message": "I need pricing"})
        d = await check_contact_policies("1")
        assert d.allowed

    @pytest.mark.asyncio
    async def test_denies_opt_out(self, db):
        await upsert_contact("1", {})
        await add_opt_out("1", "Not interested")
        d = await check_contact_policies("1")
        assert not d.allowed
        assert d.code == Codes.OPT_OUT

    @pytest.mark.asyncio
    async def test_denies_events_contact(self, db):
        await upsert_contact("1", {
            "hs_analytics_first_url": "https://example.com/events/mwc",
        })
        d = await check_contact_policies("1")
        assert not d.allowed
        assert d.code == Codes.EVENT_BLOCK

    @pytest.mark.asyncio
    async def test_denies_support_request(self, db):
        await upsert_contact("1", {
            "message": "Need technical support for our equipment",
        })
        d = await check_contact_policies("1")
        assert not d.allowed
        assert d.code == Codes.SUPPORT_REQUEST_BLOCK

    @pytest.mark.asyncio
    async def test_denies_duplicate(self, db):
        await upsert_contact("1", {})
        await log_send("1", "Hi")
        d = await check_contact_policies("1")
        assert not d.allowed
        assert d.code == Codes.DUPLICATE_COOLDOWN

    @pytest.mark.asyncio
    async def test_events_checked_before_support(self, db):
        """Events check runs first -- verify ordering."""
        await upsert_contact("1", {
            "hs_analytics_first_url": "https://example.com/events/mwc",
            "message": "Need technical support",
        })
        d = await check_contact_policies("1")
        assert d.code == Codes.EVENT_BLOCK


class TestSendPolicies:
    @pytest.mark.asyncio
    async def test_allows_under_limit(self, db):
        d = await check_send_policies()
        assert d.allowed

    @pytest.mark.asyncio
    async def test_denies_at_limit(self, db):
        for i in range(20):
            await upsert_contact(str(i), {})
            await log_send(str(i), "Hi")
        d = await check_send_policies()
        assert not d.allowed
        assert d.code == Codes.SEND_LIMIT_REACHED


class TestScrapePolicies:
    @pytest.mark.asyncio
    async def test_allows_clean_domain(self, db):
        d = await check_scrape_policies("example.com")
        assert d.allowed

    @pytest.mark.asyncio
    async def test_denies_blocklisted(self, db):
        await add_scrape_block("competitor.example.com", "Competitor")
        d = await check_scrape_policies("competitor.example.com")
        assert not d.allowed
        assert d.code == Codes.SCRAPE_BLOCKLIST
