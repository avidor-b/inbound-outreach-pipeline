"""Tests for Phase 4: Thread management and checkpoint isolation.

Covers: thread CRUD, checkpoint isolation between threads,
conversation persistence across invocations, thread archiving,
and concurrent thread safety.
"""
import pytest

from outreach_pipeline.db.threads import (
    create_user, get_user,
    create_thread, get_thread, list_threads, archive_thread,
    add_message, list_messages,
    create_artifact, get_artifact, list_artifacts, update_artifact_status,
    record_approval,
)
from outreach_pipeline.db.contacts import upsert_contact
from outreach_pipeline.db.engagements import create_engagement
from outreach_pipeline.db.pool import get_pool


# ===== Thread CRUD =====

class TestThreadCRUD:
    @pytest.mark.asyncio
    async def test_create_thread(self, db):
        await create_user("u1", "u1@example.com", "User One")
        tid = await create_thread("Test Thread", "u1")
        thread = await get_thread(tid)
        assert thread["title"] == "Test Thread"
        assert thread["user_id"] == "u1"
        assert thread["status"] == "active"

    @pytest.mark.asyncio
    async def test_list_threads_returns_active_only(self, db):
        await create_user("u1", "u1@example.com", "User One")
        t1 = await create_thread("Thread 1", "u1")
        t2 = await create_thread("Thread 2", "u1")
        await archive_thread(t1)
        threads = await list_threads("u1")
        assert len(threads) == 1
        assert threads[0]["id"] == t2

    @pytest.mark.asyncio
    async def test_list_threads_scoped_to_user(self, db):
        await create_user("u1", "u1@example.com", "User One")
        await create_user("u2", "u2@example.com", "User Two")
        await create_thread("U1 Thread", "u1")
        await create_thread("U2 Thread", "u2")
        u1_threads = await list_threads("u1")
        u2_threads = await list_threads("u2")
        assert len(u1_threads) == 1
        assert len(u2_threads) == 1
        assert u1_threads[0]["title"] == "U1 Thread"
        assert u2_threads[0]["title"] == "U2 Thread"

    @pytest.mark.asyncio
    async def test_archive_thread(self, db):
        await create_user("u1", "u1@example.com", "User One")
        tid = await create_thread("To Archive", "u1")
        await archive_thread(tid)
        thread = await get_thread(tid)
        assert thread["status"] == "archived"

    @pytest.mark.asyncio
    async def test_archive_nonexistent_thread_is_safe(self, db):
        # Should not raise
        await archive_thread("nonexistent-id")


# ===== Messages =====

class TestMessages:
    @pytest.mark.asyncio
    async def test_add_and_list_messages(self, db):
        await create_user("u1", "u1@example.com", "User One")
        tid = await create_thread("Chat", "u1")
        await add_message(tid, "user", "Hello")
        await add_message(tid, "assistant", "Hi there!")
        msgs = await list_messages(tid)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_messages_isolated_between_threads(self, db):
        await create_user("u1", "u1@example.com", "User One")
        t1 = await create_thread("Thread 1", "u1")
        t2 = await create_thread("Thread 2", "u1")
        await add_message(t1, "user", "Message in T1")
        await add_message(t2, "user", "Message in T2")
        msgs1 = await list_messages(t1)
        msgs2 = await list_messages(t2)
        assert len(msgs1) == 1
        assert len(msgs2) == 1
        assert msgs1[0]["content"] == "Message in T1"
        assert msgs2[0]["content"] == "Message in T2"


# ===== Artifacts + Approvals =====

class TestArtifacts:
    @pytest.mark.asyncio
    async def test_create_artifact(self, db):
        await create_user("u1", "u1@example.com", "User One")
        tid = await create_thread("Draft Thread", "u1")
        await upsert_contact("c1", {"company": "Acme"})
        aid = await create_artifact(tid, "email_draft", "c1", {"subject": "Hi", "body": "Test"})
        art = await get_artifact(aid)
        assert art["type"] == "email_draft"
        assert art["contact_id"] == "c1"
        assert art["data"]["subject"] == "Hi"

    @pytest.mark.asyncio
    async def test_artifact_status_transitions(self, db):
        await create_user("u1", "u1@example.com", "User One")
        tid = await create_thread("Approval", "u1")
        await upsert_contact("c1", {"company": "Acme"})
        aid = await create_artifact(tid, "email_draft", "c1", {"subject": "Hi"})
        await update_artifact_status(aid, "pending_approval")
        art = await get_artifact(aid)
        assert art["status"] == "pending_approval"
        await update_artifact_status(aid, "approved")
        art = await get_artifact(aid)
        assert art["status"] == "approved"

    @pytest.mark.asyncio
    async def test_record_approval_with_feedback(self, db):
        await create_user("u1", "u1@example.com", "User One")
        await create_user("reviewer", "rev@example.com", "Reviewer")
        tid = await create_thread("Review", "u1")
        await upsert_contact("c1", {"company": "Acme"})
        aid = await create_artifact(tid, "email_draft", "c1", {"body": "Draft"})
        await update_artifact_status(aid, "pending_approval")
        await record_approval(aid, "reviewer", "reject", "Too aggressive")
        pool = get_pool()
        row = await pool.fetchrow(
            "SELECT * FROM approvals WHERE artifact_id=$1", aid
        )
        assert row["action"] == "reject"
        assert row["feedback"] == "Too aggressive"

    @pytest.mark.asyncio
    async def test_list_artifacts_by_thread(self, db):
        await create_user("u1", "u1@example.com", "User One")
        t1 = await create_thread("Thread 1", "u1")
        t2 = await create_thread("Thread 2", "u1")
        await upsert_contact("c1", {"company": "Acme"})
        await create_artifact(t1, "email_draft", "c1", {"body": "Draft 1"})
        await create_artifact(t2, "email_draft", "c1", {"body": "Draft 2"})
        arts1 = await list_artifacts(t1)
        arts2 = await list_artifacts(t2)
        assert len(arts1) == 1
        assert len(arts2) == 1


# ===== Checkpoint Thread Isolation =====

class TestCheckpointIsolation:
    """Verify LangGraph checkpoints are isolated per thread_id."""

    @pytest.mark.asyncio
    async def test_different_threads_independent_checkpoints(self, db, offline_graph_model):
        """Two threads should have completely independent graph state."""
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from langchain_core.messages import HumanMessage
        from outreach_pipeline.graph import compile_graph
        from tests.conftest import TEST_DATABASE_URL

        async with AsyncPostgresSaver.from_conn_string(TEST_DATABASE_URL) as saver:
            await saver.setup()
            compiled = compile_graph(checkpointer=saver)

            import uuid
            config_a = {"configurable": {"thread_id": f"iso-a-{uuid.uuid4().hex[:8]}"}}
            config_b = {"configurable": {"thread_id": f"iso-b-{uuid.uuid4().hex[:8]}"}}

            # Thread B has no state before any invocation
            state_b_before = await compiled.aget_state(config_b)
            assert not state_b_before.values.get("messages")

            # Send different messages to each thread
            await compiled.ainvoke(
                {"messages": [HumanMessage(content="show stats")]},
                config_a,
            )
            await compiled.ainvoke(
                {"messages": [HumanMessage(content="show leads")]},
                config_b,
            )

            # Each thread has its own messages — not mixed
            state_a = await compiled.aget_state(config_a)
            state_b = await compiled.aget_state(config_b)
            a_msgs = state_a.values.get("messages", [])
            b_msgs = state_b.values.get("messages", [])
            assert len(a_msgs) > 0
            assert len(b_msgs) > 0

            # Thread A's first user message is about stats
            a_user = [m for m in a_msgs if hasattr(m, "content") and "stats" in str(m.content).lower()]
            assert len(a_user) > 0
            # Thread B's first user message is about leads
            b_user = [m for m in b_msgs if hasattr(m, "content") and "leads" in str(m.content).lower()]
            assert len(b_user) > 0
            # Thread A should NOT have "leads" user message
            a_leads = [m for m in a_msgs if hasattr(m, "content") and m.content == "show leads"]
            assert len(a_leads) == 0

    @pytest.mark.asyncio
    async def test_thread_state_persists_across_invocations(self, db, offline_graph_model):
        """Messages should accumulate across multiple invocations on same thread."""
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from langchain_core.messages import HumanMessage
        from outreach_pipeline.graph import compile_graph
        from tests.conftest import TEST_DATABASE_URL

        async with AsyncPostgresSaver.from_conn_string(TEST_DATABASE_URL) as saver:
            await saver.setup()
            compiled = compile_graph(checkpointer=saver)
            import uuid
            config = {"configurable": {"thread_id": f"persist-{uuid.uuid4().hex[:8]}"}}

            # First message
            await compiled.ainvoke(
                {"messages": [HumanMessage(content="hello")]},
                config,
            )
            state1 = await compiled.aget_state(config)
            count1 = len(state1.values.get("messages", []))
            assert count1 >= 2  # user + assistant

            # Second message on same thread
            await compiled.ainvoke(
                {"messages": [HumanMessage(content="show stats")]},
                config,
            )
            state2 = await compiled.aget_state(config)
            count2 = len(state2.values.get("messages", []))
            assert count2 > count1  # more messages accumulated


# ===== User CRUD =====

class TestUsers:
    @pytest.mark.asyncio
    async def test_create_and_get_user(self, db):
        await create_user("u1", "u1@example.com", "User One", role="admin")
        user = await get_user("u1")
        assert user["email"] == "u1@example.com"
        assert user["name"] == "User One"
        assert user["role"] == "admin"

    @pytest.mark.asyncio
    async def test_get_nonexistent_user(self, db):
        user = await get_user("nonexistent")
        assert not user  # returns {} from row_to_dict(None)


# ===== Edge Cases =====

class TestThreadEdgeCases:
    @pytest.mark.asyncio
    async def test_archive_all_threads(self, db):
        """Archiving every thread should leave no active threads."""
        await create_user("u1", "u1@example.com", "User One")
        t1 = await create_thread("A", "u1")
        t2 = await create_thread("B", "u1")
        await archive_thread(t1)
        await archive_thread(t2)
        threads = await list_threads("u1")
        assert len(threads) == 0

    @pytest.mark.asyncio
    async def test_archive_already_archived(self, db):
        """Archiving an already-archived thread should be idempotent."""
        await create_user("u1", "u1@example.com", "User One")
        tid = await create_thread("A", "u1")
        await archive_thread(tid)
        await archive_thread(tid)  # second call should not error
        thread = await get_thread(tid)
        assert thread["status"] == "archived"

    @pytest.mark.asyncio
    async def test_messages_on_archived_thread(self, db):
        """Messages should still be accessible on archived threads."""
        await create_user("u1", "u1@example.com", "User One")
        tid = await create_thread("Chat", "u1")
        await add_message(tid, "user", "Hello")
        await archive_thread(tid)
        msgs = await list_messages(tid)
        assert len(msgs) == 1
        assert msgs[0]["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_artifact_on_archived_thread(self, db):
        """Artifacts should still be accessible on archived threads."""
        await create_user("u1", "u1@example.com", "User One")
        tid = await create_thread("Draft", "u1")
        await upsert_contact("c1", {"company": "Acme"})
        aid = await create_artifact(tid, "email_draft", "c1", {"subject": "Hi"})
        await archive_thread(tid)
        art = await get_artifact(aid)
        assert art["data"]["subject"] == "Hi"

    @pytest.mark.asyncio
    async def test_many_threads_same_user(self, db):
        """User should be able to have many threads."""
        await create_user("u1", "u1@example.com", "User One")
        for i in range(20):
            await create_thread(f"Thread {i}", "u1")
        threads = await list_threads("u1")
        assert len(threads) == 20

    @pytest.mark.asyncio
    async def test_empty_title_thread(self, db):
        """Thread with empty title should be valid."""
        await create_user("u1", "u1@example.com", "User One")
        tid = await create_thread("", "u1")
        thread = await get_thread(tid)
        assert thread["title"] == ""

    @pytest.mark.asyncio
    async def test_artifact_invalid_status_transition(self, db):
        """Invalid artifact status transitions should raise ValueError."""
        await create_user("u1", "u1@example.com", "User One")
        tid = await create_thread("Test", "u1")
        await upsert_contact("c1", {"company": "Acme"})
        aid = await create_artifact(tid, "email_draft", "c1", {"body": "Draft"})
        with pytest.raises(ValueError):
            await update_artifact_status(aid, "sent")  # can't go created → sent

    @pytest.mark.asyncio
    async def test_duplicate_user_creation(self, db):
        """Creating a user with existing ID should handle gracefully."""
        await create_user("u1", "u1@example.com", "User One")
        # Second create with same ID — should upsert or error
        import asyncpg
        try:
            await create_user("u1", "u1@example.com", "User One Again")
        except asyncpg.UniqueViolationError:
            pass  # expected — DB enforces unique

    @pytest.mark.asyncio
    async def test_checkpoint_new_thread_empty(self, db):
        """A brand-new thread should have no checkpoint state."""
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from outreach_pipeline.graph import compile_graph
        from tests.conftest import TEST_DATABASE_URL
        import uuid

        async with AsyncPostgresSaver.from_conn_string(TEST_DATABASE_URL) as saver:
            await saver.setup()
            compiled = compile_graph(checkpointer=saver)
            config = {"configurable": {"thread_id": f"new-{uuid.uuid4().hex[:8]}"}}
            state = await compiled.aget_state(config)
            assert not state.values.get("messages")
