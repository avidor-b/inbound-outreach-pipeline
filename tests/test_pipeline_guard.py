"""Unit tests for pipeline_guard.py hook and db.py support-request detection.

The pipeline_guard hook (PreToolUse) is sync Python using the old SQLite path.
check_support_request in the async db module takes only contact_id (no conn).
The hook's check_support_request still uses the old sync signature (conn, id).
We test BOTH: the sync hook functions (with forced MagicMock to avoid AsyncMock)
and the async DB guardrail.
"""
import os
import sys

import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".claude", "hooks"))
from pipeline_guard import extract_flag, check_email_guardrails, check_scrape_guardrails

from outreach_pipeline.db.contacts import upsert_contact
from outreach_pipeline.db.guardrails import check_support_request


class TestExtractFlag:
    def test_extracts_double_quoted(self):
        assert extract_flag('draft-email --contact-id "123" --subject "Hi"', "--contact-id") == "123"

    def test_extracts_unquoted(self):
        assert extract_flag("draft-email --contact-id 456 --subject Hi", "--contact-id") == "456"

    def test_extracts_url(self):
        assert extract_flag("scrape-site --url https://example.com --max-pages 10", "--url") == "https://example.com"

    def test_returns_none_when_missing(self):
        assert extract_flag("draft-email --subject Hi", "--contact-id") is None


class TestCheckEmailGuardrails:
    """Test the sync hook. Force MagicMock to prevent auto-AsyncMock."""

    @patch("pipeline_guard.check_send_limit", new_callable=MagicMock, return_value=(False, "", 0))
    @patch("pipeline_guard.check_duplicate", new_callable=MagicMock, return_value=(False, ""))
    @patch("pipeline_guard.check_opt_out", new_callable=MagicMock, return_value=(True, "Not interested"))
    @patch("pipeline_guard.check_support_request", new_callable=MagicMock, return_value=(False, ""))
    @patch("pipeline_guard.check_events_contact", new_callable=MagicMock, return_value=(False, ""))
    @patch("pipeline_guard.get_db", new_callable=MagicMock)
    def test_blocks_opt_out(self, *_):
        blocked, reasons = check_email_guardrails("draft-email --contact-id 123")
        assert blocked
        assert any("OPT-OUT" in r for r in reasons)

    @patch("pipeline_guard.check_send_limit", new_callable=MagicMock, return_value=(False, "", 0))
    @patch("pipeline_guard.check_duplicate", new_callable=MagicMock, return_value=(True, "Emailed 3 days ago"))
    @patch("pipeline_guard.check_opt_out", new_callable=MagicMock, return_value=(False, ""))
    @patch("pipeline_guard.check_support_request", new_callable=MagicMock, return_value=(False, ""))
    @patch("pipeline_guard.check_events_contact", new_callable=MagicMock, return_value=(False, ""))
    @patch("pipeline_guard.get_db", new_callable=MagicMock)
    def test_blocks_duplicate(self, *_):
        blocked, reasons = check_email_guardrails("draft-email --contact-id 123")
        assert blocked
        assert any("DUPLICATE" in r for r in reasons)

    @patch("pipeline_guard.check_send_limit", new_callable=MagicMock, return_value=(True, "Limit reached", 20))
    @patch("pipeline_guard.check_duplicate", new_callable=MagicMock, return_value=(False, ""))
    @patch("pipeline_guard.check_opt_out", new_callable=MagicMock, return_value=(False, ""))
    @patch("pipeline_guard.check_support_request", new_callable=MagicMock, return_value=(False, ""))
    @patch("pipeline_guard.check_events_contact", new_callable=MagicMock, return_value=(False, ""))
    @patch("pipeline_guard.get_db", new_callable=MagicMock)
    def test_blocks_send_limit(self, *_):
        blocked, reasons = check_email_guardrails("draft-email --contact-id 123")
        assert blocked
        assert any("SEND LIMIT" in r for r in reasons)

    @patch("pipeline_guard.check_send_limit", new_callable=MagicMock, return_value=(False, "", 5))
    @patch("pipeline_guard.check_duplicate", new_callable=MagicMock, return_value=(False, ""))
    @patch("pipeline_guard.check_opt_out", new_callable=MagicMock, return_value=(False, ""))
    @patch("pipeline_guard.check_support_request", new_callable=MagicMock, return_value=(False, ""))
    @patch("pipeline_guard.check_events_contact", new_callable=MagicMock, return_value=(False, ""))
    @patch("pipeline_guard.get_db", new_callable=MagicMock)
    def test_allows_clean(self, *_):
        blocked, reasons = check_email_guardrails("draft-email --contact-id 123")
        assert not blocked

    @patch("pipeline_guard.check_send_limit", new_callable=MagicMock, return_value=(True, "Limit", 20))
    @patch("pipeline_guard.check_duplicate", new_callable=MagicMock, return_value=(True, "Recent"))
    @patch("pipeline_guard.check_opt_out", new_callable=MagicMock, return_value=(True, "Opted out"))
    @patch("pipeline_guard.check_support_request", new_callable=MagicMock, return_value=(False, ""))
    @patch("pipeline_guard.check_events_contact", new_callable=MagicMock, return_value=(False, ""))
    @patch("pipeline_guard.get_db", new_callable=MagicMock)
    def test_reports_all_violations(self, *_):
        blocked, reasons = check_email_guardrails("draft-email --contact-id 123")
        assert blocked
        assert len(reasons) == 3

    @patch("pipeline_guard.check_send_limit", new_callable=MagicMock, return_value=(False, "", 0))
    @patch("pipeline_guard.check_duplicate", new_callable=MagicMock, return_value=(False, ""))
    @patch("pipeline_guard.check_opt_out", new_callable=MagicMock, return_value=(False, ""))
    @patch("pipeline_guard.check_support_request", new_callable=MagicMock, return_value=(False, ""))
    @patch("pipeline_guard.check_events_contact", new_callable=MagicMock, return_value=(True, "Events contact"))
    @patch("pipeline_guard.get_db", new_callable=MagicMock)
    def test_blocks_events_contact(self, *_):
        blocked, reasons = check_email_guardrails("draft-email --contact-id 123")
        assert blocked
        assert any("EVENTS CONTACT" in r for r in reasons)

    @patch("pipeline_guard.check_send_limit", new_callable=MagicMock, return_value=(False, "", 0))
    @patch("pipeline_guard.check_duplicate", new_callable=MagicMock, return_value=(False, ""))
    @patch("pipeline_guard.check_opt_out", new_callable=MagicMock, return_value=(False, ""))
    @patch("pipeline_guard.check_support_request", new_callable=MagicMock, return_value=(True, "Support request"))
    @patch("pipeline_guard.check_events_contact", new_callable=MagicMock, return_value=(False, ""))
    @patch("pipeline_guard.get_db", new_callable=MagicMock)
    def test_blocks_support_request(self, *_):
        blocked, reasons = check_email_guardrails("draft-email --contact-id 123")
        assert blocked
        assert any("SUPPORT REQUEST" in r for r in reasons)

    def test_no_contact_id_passes(self):
        blocked, _ = check_email_guardrails("draft-email --subject Hi")
        assert not blocked


class TestCheckScrapeGuardrails:
    @patch("pipeline_guard.check_scrape_blocklist", new_callable=MagicMock, return_value=(True, "Competitor"))
    @patch("pipeline_guard.get_db", new_callable=MagicMock)
    def test_blocks_blocked_domain(self, mock_db, _):
        blocked, reasons = check_scrape_guardrails("scrape-site --url https://competitor.example.com")
        assert blocked
        assert any("SCRAPE BLOCKED" in r for r in reasons)

    @patch("pipeline_guard.check_scrape_blocklist", new_callable=MagicMock, return_value=(False, ""))
    @patch("pipeline_guard.get_db", new_callable=MagicMock)
    def test_allows_clean_domain(self, mock_db, _):
        blocked, _ = check_scrape_guardrails("scrape-site --url https://prospect.example.com")
        assert not blocked

    def test_no_url_passes(self):
        blocked, _ = check_scrape_guardrails("scrape-site --max-pages 10")
        assert not blocked


class TestCheckSupportRequestAsync:
    """Test the async Postgres check_support_request from db.guardrails."""

    async def _check(self, db, message):
        """Insert a contact with the given message, then check it."""
        await upsert_contact("1", {"message": message})
        blocked, reason = await check_support_request("1")
        return blocked, reason

    @pytest.mark.asyncio
    async def test_catches_ownership_plus_cant_get_working(self, db):
        blocked, _ = await self._check(
            db,
            "I have some RADIO-F1200 I have tried different frequencies "
            "and different tx power ratings and still I can not get them working",
        )
        assert blocked

    @pytest.mark.asyncio
    async def test_catches_ownership_plus_sw_update(self, db):
        blocked, _ = await self._check(
            db,
            "I have 5 years using outdoor radio and I was recommended to update all my outdoor_radios SW versions",
        )
        assert blocked

    @pytest.mark.asyncio
    async def test_catches_technical_support_standalone(self, db):
        blocked, _ = await self._check(db, "inop MHT-200, need to contact technical support")
        assert blocked

    @pytest.mark.asyncio
    async def test_catches_rma_standalone(self, db):
        blocked, _ = await self._check(db, "We need an RMA for a unit that stopped working after the storm.")
        assert blocked

    @pytest.mark.asyncio
    async def test_catches_arabic_reset(self, db):
        blocked, _ = await self._check(db, "\u062a\u0635\u0641\u064a\u0631 \u0627\u0644\u0645\u0639\u0627\u0644\u062c")
        assert blocked

    @pytest.mark.asyncio
    async def test_allows_new_prospect_inquiry(self, db):
        blocked, _ = await self._check(
            db,
            "We are evaluating backhaul options for our rural broadband project. "
            "Can you send me the RADIO-A spec sheet?",
        )
        assert not blocked

    @pytest.mark.asyncio
    async def test_allows_pricing_inquiry(self, db):
        blocked, _ = await self._check(
            db,
            "I have a project coming up that needs 15 links at 7 GHz. "
            "Can you provide pricing?",
        )
        assert not blocked

    @pytest.mark.asyncio
    async def test_missing_contact_returns_false(self, db):
        blocked, _ = await check_support_request("999")  # non-existent id
        assert not blocked
