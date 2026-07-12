"""Async Postgres tests for guardrails — opt-out, duplicate, send limit, scrape blocklist."""
import pytest

from outreach_pipeline.db.contacts import upsert_contact
from outreach_pipeline.db.guardrails import (
    check_opt_out, check_duplicate, check_send_limit,
    check_scrape_blocklist, add_opt_out, add_scrape_block,
)
from outreach_pipeline.db.drafts import log_send


class TestCheckDuplicate:
    @pytest.mark.asyncio
    async def test_no_duplicate(self, db):
        assert not (await check_duplicate("123"))[0]

    @pytest.mark.asyncio
    async def test_blocks_recent_send(self, db):
        await upsert_contact("456", {})
        await log_send("456", "Hi")
        assert (await check_duplicate("456"))[0]

    @pytest.mark.asyncio
    async def test_allows_fresh_mql(self, db):
        await upsert_contact("789", {})
        await log_send("789", "Hi")
        assert not (await check_duplicate("789", mql_date="2099-01-01T00:00:00Z"))[0]

    @pytest.mark.asyncio
    async def test_blocks_stale_mql(self, db):
        await upsert_contact("111", {})
        await log_send("111", "Hi")
        assert (await check_duplicate("111", mql_date="2020-01-01T00:00:00Z"))[0]


class TestCheckSendLimit:
    @pytest.mark.asyncio
    async def test_under_limit(self, db):
        assert not (await check_send_limit(20))[0]

    @pytest.mark.asyncio
    async def test_at_limit(self, db):
        for i in range(5):
            await upsert_contact(str(i), {})
            await log_send(str(i), "Hi")
        assert (await check_send_limit(5))[0]


class TestCheckOptOut:
    @pytest.mark.asyncio
    async def test_not_on_list(self, db):
        assert not (await check_opt_out("123"))[0]

    @pytest.mark.asyncio
    async def test_on_list(self, db):
        await add_opt_out("123", "Not interested")
        assert (await check_opt_out("123"))[0]

    @pytest.mark.asyncio
    async def test_add_duplicate(self, db):
        await add_opt_out("123", "First")
        assert not await add_opt_out("123", "Second")


class TestCheckScrape:
    @pytest.mark.asyncio
    async def test_not_blocked(self, db):
        assert not (await check_scrape_blocklist("example.com"))[0]

    @pytest.mark.asyncio
    async def test_blocked(self, db):
        await add_scrape_block("competitor.example.com", "Competitor")
        assert (await check_scrape_blocklist("competitor.example.com"))[0]

    @pytest.mark.asyncio
    async def test_case_insensitive(self, db):
        await add_scrape_block("Competitor.EXAMPLE.COM", "Competitor")
        assert (await check_scrape_blocklist("competitor.example.com"))[0]


class TestLogSend:
    @pytest.mark.asyncio
    async def test_logs_and_counts(self, db):
        await upsert_contact("1", {})
        await upsert_contact("2", {})
        await log_send("1", "A")
        await log_send("2", "B")
        _, _, count = await check_send_limit(20)
        assert count == 2
