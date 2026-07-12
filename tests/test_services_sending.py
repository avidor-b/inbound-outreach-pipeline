"""Unit tests for services/sending.py — send readiness checks."""
import pytest

from outreach_pipeline.db.threads import (
    create_user, create_thread, create_artifact,
    update_artifact_status,
)


class TestCheckSendReadiness:
    @pytest.mark.asyncio
    async def test_approved_artifact_is_ready(self, db):
        from outreach_pipeline.services.sending import check_send_readiness
        await create_user("u1", "u1@example.com", "User One")
        tid = await create_thread("T", "u1")
        aid = await create_artifact(tid, "draft", "c1", '{"subject":"Hi","body":"Hello"}')
        await update_artifact_status(aid, "pending_approval")
        await update_artifact_status(aid, "approved")
        decision = await check_send_readiness(aid)
        assert decision.allowed

    @pytest.mark.asyncio
    async def test_pending_artifact_not_ready(self, db):
        from outreach_pipeline.services.sending import check_send_readiness
        await create_user("u1", "u1@example.com", "User One")
        tid = await create_thread("T", "u1")
        aid = await create_artifact(tid, "draft", "c1", '{}')
        await update_artifact_status(aid, "pending_approval")
        decision = await check_send_readiness(aid)
        assert not decision.allowed

    @pytest.mark.asyncio
    async def test_already_sent_not_ready(self, db):
        from outreach_pipeline.services.sending import check_send_readiness
        await create_user("u1", "u1@example.com", "User One")
        tid = await create_thread("T", "u1")
        aid = await create_artifact(tid, "draft", "c1", '{}')
        await update_artifact_status(aid, "pending_approval")
        await update_artifact_status(aid, "approved")
        await update_artifact_status(aid, "sent")
        decision = await check_send_readiness(aid)
        assert not decision.allowed
