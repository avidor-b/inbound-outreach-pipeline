"""Integration test: full artifact lifecycle from creation to send."""
import json
import pytest

from outreach_pipeline.db.contacts import upsert_contact
from outreach_pipeline.db.threads import (
    create_user, create_thread, get_artifact, list_artifacts,
    update_artifact_status, get_approvals,
)
from outreach_pipeline.db.pool import get_pool


class TestFullArtifactLifecycle:
    """Walk the complete path: create -> pending -> approve -> verify ready."""

    @pytest.mark.asyncio
    async def test_draft_approval_lifecycle(self, db):
        """create artifact -> request approval -> approve -> check send readiness."""
        from outreach_pipeline.services.messaging import save_message
        from outreach_pipeline.services.approvals import request_approval, approve, get_pending
        from outreach_pipeline.services.sending import check_send_readiness

        await upsert_contact("c1", {"company": "Acme", "message": "Need pricing"})
        await create_user("u1", "u1@example.com", "User One")
        await create_user("reviewer1", "reviewer1@example.com", "Reviewer")

        # 1. Create thread + draft artifact
        tid = await create_thread("Outreach batch", "u1")
        aid = await save_message(tid, "c1", "Hi there", "Hello from us", score=80)
        art = await get_artifact(aid)
        assert art["status"] == "created"

        # 2. Request approval
        await request_approval(aid)
        art = await get_artifact(aid)
        assert art["status"] == "pending_approval"

        # 3. Verify it shows up in pending
        pending = await get_pending(tid)
        assert len(pending) == 1

        # 4. Approve
        await approve(aid, "reviewer1")
        art = await get_artifact(aid)
        assert art["status"] == "approved"

        # 5. Check send readiness
        decision = await check_send_readiness(aid)
        assert decision.allowed

        # 6. Pending list should be empty
        pending = await get_pending(tid)
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_draft_rejection_and_redraft(self, db):
        """create -> request -> reject with feedback -> re-draft -> re-request -> approve."""
        from outreach_pipeline.services.messaging import save_message
        from outreach_pipeline.services.approvals import request_approval, reject, approve

        await upsert_contact("c1", {"company": "Acme", "message": "Need pricing"})
        await create_user("u1", "u1@example.com", "User One")
        await create_user("reviewer1", "reviewer1@example.com", "Reviewer")

        # 1. Create + request
        tid = await create_thread("Outreach", "u1")
        aid = await save_message(tid, "c1", "Bad subject", "Bad body", score=80)
        await request_approval(aid)

        # 2. Reject with feedback
        await reject(aid, "reviewer1", feedback="Too long, shorten it")
        art = await get_artifact(aid)
        assert art["status"] == "rejected"

        # 3. Check feedback in approvals
        approvals = await get_approvals(aid)
        assert approvals[0]["feedback"] == "Too long, shorten it"

        # 4. Re-draft: update artifact data (simulates agent re-drafting)
        pool = get_pool()
        await pool.execute(
            "UPDATE artifacts SET data=$1 WHERE id=$2",
            json.dumps({"subject": "Better subject", "body": "Shorter body"}), aid,
        )

        # 5. Re-submit for approval (rejected -> pending_approval is valid)
        await update_artifact_status(aid, "pending_approval")
        art = await get_artifact(aid)
        assert art["status"] == "pending_approval"

        # 6. Approve second time
        await approve(aid, "reviewer1")
        art = await get_artifact(aid)
        assert art["status"] == "approved"

        # 7. Two approval records exist
        approvals = await get_approvals(aid)
        assert len(approvals) == 2
        assert approvals[0]["action"] == "reject"
        assert approvals[1]["action"] == "approve"

    @pytest.mark.asyncio
    async def test_multiple_artifacts_per_thread(self, db):
        """Thread can have multiple artifacts at different lifecycle stages."""
        from outreach_pipeline.services.messaging import save_message
        from outreach_pipeline.services.approvals import request_approval, approve

        await upsert_contact("c1", {"company": "Acme", "message": "Need pricing"})
        await upsert_contact("c2", {"company": "Beta", "message": "Hello"})
        await create_user("u1", "u1@example.com", "User One")

        tid = await create_thread("Batch", "u1")
        aid1 = await save_message(tid, "c1", "Email 1", "Body 1", score=90)
        aid2 = await save_message(tid, "c2", "Email 2", "Body 2", score=70)

        await request_approval(aid1)
        await approve(aid1, "u1")

        await request_approval(aid2)
        # aid2 still pending

        artifacts = await list_artifacts(tid)
        statuses = {a["id"]: a["status"] for a in artifacts}
        assert statuses[aid1] == "approved"
        assert statuses[aid2] == "pending_approval"
