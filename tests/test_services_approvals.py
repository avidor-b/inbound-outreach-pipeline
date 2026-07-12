"""Unit tests for services/approvals.py — approval lifecycle."""
import pytest

from outreach_pipeline.db.threads import (
    create_user, create_thread, create_artifact, get_artifact,
)
from outreach_pipeline.services.approvals import (
    request_approval, approve, reject, get_pending,
)


async def _seed_thread():
    """Create a user, thread, and draft artifact. Returns (thread_id, artifact_id)."""
    await create_user("u1", "u1@example.com", "User One")
    tid = await create_thread("T", "u1")
    aid = await create_artifact(tid, "draft", "c1", '{"subject":"Hi"}')
    return tid, aid


class TestApprovalService:
    @pytest.mark.asyncio
    async def test_request_approval_sets_pending(self, db):
        tid, aid = await _seed_thread()
        await request_approval(aid)
        art = await get_artifact(aid)
        assert art["status"] == "pending_approval"

    @pytest.mark.asyncio
    async def test_approve_sets_approved(self, db):
        tid, aid = await _seed_thread()
        await request_approval(aid)
        await approve(aid, "u1")
        art = await get_artifact(aid)
        assert art["status"] == "approved"

    @pytest.mark.asyncio
    async def test_reject_sets_rejected_with_feedback(self, db):
        tid, aid = await _seed_thread()
        await request_approval(aid)
        await reject(aid, "u1", feedback="Too long")
        art = await get_artifact(aid)
        assert art["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_get_pending_returns_pending_artifacts(self, db):
        tid, aid = await _seed_thread()
        await request_approval(aid)
        pending = await get_pending(tid)
        assert len(pending) == 1
        assert pending[0]["id"] == aid

    @pytest.mark.asyncio
    async def test_get_pending_excludes_approved(self, db):
        tid, aid = await _seed_thread()
        await request_approval(aid)
        await approve(aid, "u1")
        pending = await get_pending(tid)
        assert len(pending) == 0
