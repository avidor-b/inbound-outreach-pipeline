"""Unit tests for tools/sending.py -- send approved messages."""
import json
import pytest
from unittest.mock import patch

from outreach_pipeline.db.threads import (
    create_user, create_thread, create_artifact,
    update_artifact_status, get_artifact,
)


class TestSendMessageTool:

    @pytest.mark.asyncio
    async def test_rejects_unapproved_artifact(self, db):
        from outreach_pipeline.tools.sending import send_message
        await create_user("u1", "u1@example.com", "User One")
        tid = await create_thread("T", "u1")
        aid = await create_artifact(tid, "draft", "c1",
                                    json.dumps({"subject": "Hi", "body": "Hello"}))
        # Status is "created" -- not approved
        result = await send_message(aid)
        assert not result.get("success", True)

    @pytest.mark.asyncio
    async def test_rejects_pending_artifact(self, db):
        from outreach_pipeline.tools.sending import send_message
        await create_user("u1", "u1@example.com", "User One")
        tid = await create_thread("T", "u1")
        aid = await create_artifact(tid, "draft", "c1",
                                    json.dumps({"subject": "Hi", "body": "Hello"}))
        await update_artifact_status(aid, "pending_approval")
        result = await send_message(aid)
        assert not result.get("success", True)

    @pytest.mark.asyncio
    async def test_rejects_already_sent(self, db):
        from outreach_pipeline.tools.sending import send_message
        await create_user("u1", "u1@example.com", "User One")
        tid = await create_thread("T", "u1")
        aid = await create_artifact(tid, "draft", "c1",
                                    json.dumps({"subject": "Hi", "body": "Hello"}))
        await update_artifact_status(aid, "pending_approval")
        await update_artifact_status(aid, "approved")
        await update_artifact_status(aid, "sent")
        result = await send_message(aid)
        assert not result.get("success", True)


class TestSendMessageDryRun:

    @pytest.mark.asyncio
    @patch("outreach_pipeline.adapters.smtp.SMTPAdapter.send")
    async def test_dry_run_approved_artifact(self, mock_send, db, monkeypatch):
        from outreach_pipeline.tools.sending import send_message
        monkeypatch.setenv("CONFIG_PATH", "docker/config.default.yaml")
        mock_send.return_value = {"success": True, "sent": False, "dry_run": True}
        await create_user("u1", "u1@example.com", "User One")
        tid = await create_thread("T", "u1")
        aid = await create_artifact(tid, "draft", "c1",
                                    json.dumps({"subject": "Hi", "body": "Hello"}))
        await update_artifact_status(aid, "pending_approval")
        await update_artifact_status(aid, "approved")
        result = await send_message(aid, dry_run=True)
        assert result.get("success")
        # Dry run should NOT update status to sent
        art = await get_artifact(aid)
        assert art["status"] == "approved"
