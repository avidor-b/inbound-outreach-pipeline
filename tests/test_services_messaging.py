"""Unit tests for services/messaging.py — messaging domain flow."""
import json
import pytest

from outreach_pipeline.db.contacts import upsert_contact
from outreach_pipeline.db.threads import create_user, create_thread, get_artifact


class TestBuildMessageContext:
    @pytest.mark.asyncio
    async def test_build_context_loads_contact(self, db):
        await upsert_contact("c1", {
            "company": "Acme", "message": "Need pricing for RADIO-B100",
            "jobtitle": "CTO", "industry": "Telecom",
        })
        from outreach_pipeline.services.messaging import build_message_context
        ctx = await build_message_context("c1")
        assert ctx["contact"]["id"] == "c1"
        assert ctx["contact"]["properties"]["company"] == "Acme"

    @pytest.mark.asyncio
    async def test_build_context_missing_contact(self, db):
        from outreach_pipeline.services.messaging import build_message_context
        ctx = await build_message_context("nonexistent")
        assert not ctx["contact"]


class TestSaveMessage:
    @pytest.mark.asyncio
    async def test_save_creates_artifact(self, db):
        from outreach_pipeline.services.messaging import save_message
        await create_user("u1", "u1@example.com", "User One")
        tid = await create_thread("Test", "u1")
        aid = await save_message(
            tid, contact_id="c1",
            subject="Hi", body="Hello there", score=80,
        )
        art = await get_artifact(aid)
        assert art["type"] == "draft"
        assert art["status"] == "created"
        assert art["contact_id"] == "c1"
        data = art["data"] if isinstance(art["data"], dict) else json.loads(art["data"])
        assert data["subject"] == "Hi"
        assert data["body"] == "Hello there"
