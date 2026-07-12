"""Async Postgres tests for threads — threads, messages, artifacts, approvals, users."""
import pytest

from outreach_pipeline.db.threads import (
    create_user, get_user,
    create_thread, get_thread, list_threads, archive_thread,
    add_message, list_messages,
    create_artifact, get_artifact, update_artifact_status, list_artifacts,
    record_approval, get_approvals,
)


class TestUsers:
    @pytest.mark.asyncio
    async def test_create_user(self, db):
        await create_user("u1", "alice@example.com", "Alice", "drafter")
        user = await get_user("u1")
        assert user["email"] == "alice@example.com"
        assert user["role"] == "drafter"

    @pytest.mark.asyncio
    async def test_create_duplicate_user(self, db):
        await create_user("u1", "alice@example.com", "Alice", "drafter")
        assert not await create_user("u1", "bob@example.com", "Bob", "viewer")


class TestThreads:
    @pytest.mark.asyncio
    async def test_create_and_get_thread(self, db):
        await create_user("u1", "alice@example.com", "Alice", "drafter")
        tid = await create_thread("Draft batch", "u1")
        thread = await get_thread(tid)
        assert thread["title"] == "Draft batch"
        assert thread["user_id"] == "u1"
        assert thread["status"] == "active"

    @pytest.mark.asyncio
    async def test_list_threads_for_user(self, db):
        await create_user("u1", "a@example.com", "A", "drafter")
        await create_user("u2", "b@example.com", "B", "drafter")
        await create_thread("Thread A", "u1")
        await create_thread("Thread B", "u2")
        await create_thread("Thread C", "u1")
        threads = await list_threads(user_id="u1")
        assert len(threads) == 2

    @pytest.mark.asyncio
    async def test_archive_thread(self, db):
        await create_user("u1", "a@example.com", "A", "drafter")
        tid = await create_thread("T", "u1")
        await archive_thread(tid)
        assert (await get_thread(tid))["status"] == "archived"


class TestMessages:
    @pytest.mark.asyncio
    async def test_add_and_list_messages(self, db):
        await create_user("u1", "a@example.com", "A", "drafter")
        tid = await create_thread("T", "u1")
        await add_message(tid, "user", "Hello")
        await add_message(tid, "assistant", "Hi there")
        msgs = await list_messages(tid)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_add_tool_call_message(self, db):
        await create_user("u1", "a@example.com", "A", "drafter")
        tid = await create_thread("T", "u1")
        await add_message(tid, "tool_call", "", tool_name="fetch_contacts",
                          tool_args='{"start_date": "2026-03-01"}')
        msgs = await list_messages(tid)
        assert msgs[0]["tool_name"] == "fetch_contacts"

    @pytest.mark.asyncio
    async def test_messages_ordered(self, db):
        await create_user("u1", "a@example.com", "A", "drafter")
        tid = await create_thread("T", "u1")
        await add_message(tid, "user", "first")
        await add_message(tid, "assistant", "second")
        await add_message(tid, "user", "third")
        msgs = await list_messages(tid)
        assert [m["content"] for m in msgs] == ["first", "second", "third"]


class TestArtifacts:
    @pytest.mark.asyncio
    async def test_create_artifact(self, db):
        await create_user("u1", "a@example.com", "A", "drafter")
        tid = await create_thread("T", "u1")
        aid = await create_artifact(tid, "draft", "c1", '{"subject": "Hi"}')
        art = await get_artifact(aid)
        assert art["type"] == "draft"
        assert art["status"] == "created"
        assert art["contact_id"] == "c1"

    @pytest.mark.asyncio
    async def test_artifact_status_transitions(self, db):
        await create_user("u1", "a@example.com", "A", "drafter")
        tid = await create_thread("T", "u1")
        aid = await create_artifact(tid, "draft", "c1", '{}')

        # created -> pending_approval
        await update_artifact_status(aid, "pending_approval")
        assert (await get_artifact(aid))["status"] == "pending_approval"

        # pending_approval -> approved
        await update_artifact_status(aid, "approved")
        assert (await get_artifact(aid))["status"] == "approved"

        # approved -> sent
        await update_artifact_status(aid, "sent")
        assert (await get_artifact(aid))["status"] == "sent"

    @pytest.mark.asyncio
    async def test_artifact_rejection(self, db):
        await create_user("u1", "a@example.com", "A", "drafter")
        tid = await create_thread("T", "u1")
        aid = await create_artifact(tid, "draft", "c1", '{}')
        await update_artifact_status(aid, "pending_approval")
        await update_artifact_status(aid, "rejected")
        assert (await get_artifact(aid))["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_invalid_status_transition(self, db):
        await create_user("u1", "a@example.com", "A", "drafter")
        tid = await create_thread("T", "u1")
        aid = await create_artifact(tid, "draft", "c1", '{}')
        # created -> sent is not valid (must go through pending_approval first)
        with pytest.raises(ValueError):
            await update_artifact_status(aid, "sent")

    @pytest.mark.asyncio
    async def test_list_artifacts_by_thread(self, db):
        await create_user("u1", "a@example.com", "A", "drafter")
        tid = await create_thread("T", "u1")
        await create_artifact(tid, "draft", "c1", '{}')
        await create_artifact(tid, "score", "c2", '{}')
        arts = await list_artifacts(tid)
        assert len(arts) == 2


class TestApprovals:
    @pytest.mark.asyncio
    async def test_record_approval(self, db):
        await create_user("u1", "a@example.com", "A", "drafter")
        tid = await create_thread("T", "u1")
        aid = await create_artifact(tid, "draft", "c1", '{}')
        await update_artifact_status(aid, "pending_approval")

        await record_approval(aid, "u1", "approve", feedback="")
        approvals = await get_approvals(aid)
        assert len(approvals) == 1
        assert approvals[0]["action"] == "approve"

    @pytest.mark.asyncio
    async def test_record_rejection_with_feedback(self, db):
        await create_user("u1", "a@example.com", "A", "drafter")
        tid = await create_thread("T", "u1")
        aid = await create_artifact(tid, "draft", "c1", '{}')
        await update_artifact_status(aid, "pending_approval")

        await record_approval(aid, "u1", "reject", feedback="Too long, shorten it")
        approvals = await get_approvals(aid)
        assert approvals[0]["action"] == "reject"
        assert approvals[0]["feedback"] == "Too long, shorten it"

    @pytest.mark.asyncio
    async def test_approval_updates_artifact_status(self, db):
        await create_user("u1", "a@example.com", "A", "drafter")
        tid = await create_thread("T", "u1")
        aid = await create_artifact(tid, "draft", "c1", '{}')
        await update_artifact_status(aid, "pending_approval")

        await record_approval(aid, "u1", "approve", feedback="")
        assert (await get_artifact(aid))["status"] == "approved"

    @pytest.mark.asyncio
    async def test_rejection_updates_artifact_status(self, db):
        await create_user("u1", "a@example.com", "A", "drafter")
        tid = await create_thread("T", "u1")
        aid = await create_artifact(tid, "draft", "c1", '{}')
        await update_artifact_status(aid, "pending_approval")

        await record_approval(aid, "u1", "reject", feedback="Fix tone")
        assert (await get_artifact(aid))["status"] == "rejected"
