"""Tests for Phase 5: Long-term memory via LangGraph PostgresStore.

Covers: preference CRUD, corrections, contact notes, cross-thread
persistence, store setup, and tool registration.
"""
import uuid

import pytest

from outreach_pipeline.graph import compile_graph
from outreach_pipeline.graph.tools import get_agent_tools


class TestMemoryToolRegistration:
    """Verify memory tools are registered in the right agent groups."""

    def test_query_has_all_memory_tools(self):
        tools = get_agent_tools("query")
        names = {t.name for t in tools}
        assert "save_preference" in names
        assert "get_preference" in names
        assert "list_preferences" in names
        assert "save_correction" in names
        assert "get_corrections" in names
        assert "save_contact_note" in names
        assert "get_contact_note" in names

    def test_drafting_has_read_only_memory(self):
        tools = get_agent_tools("drafting")
        names = {t.name for t in tools}
        assert "get_preference" in names
        assert "list_preferences" in names
        assert "get_corrections" in names
        assert "get_contact_note" in names
        # Write tools should NOT be in drafting
        assert "save_preference" not in names
        assert "save_correction" not in names

    def test_scoring_has_read_only_memory(self):
        tools = get_agent_tools("scoring")
        names = {t.name for t in tools}
        assert "get_preference" in names
        assert "get_corrections" in names

    def test_all_specialists_have_read_memory(self):
        for group in ["query", "enrich", "scoring", "drafting", "pipeline", "admin"]:
            tools = get_agent_tools(group)
            names = {t.name for t in tools}
            assert "get_preference" in names, f"{group} missing get_preference"
            assert "get_corrections" in names, f"{group} missing get_corrections"


class TestPostgresStore:
    """Test store operations via the compiled graph."""

    @pytest.mark.asyncio
    async def test_store_setup(self, db):
        """Store creates its tables in Postgres."""
        from langgraph.store.postgres import AsyncPostgresStore
        from tests.conftest import TEST_DATABASE_URL

        async with AsyncPostgresStore.from_conn_string(TEST_DATABASE_URL) as store:
            await store.setup()
            # Verify store tables exist
            pool = db
            tables = await pool.fetch(
                "SELECT tablename FROM pg_tables WHERE tablename LIKE 'store%'"
            )
            table_names = {t["tablename"] for t in tables}
            assert len(table_names) > 0

    @pytest.mark.asyncio
    async def test_store_put_and_get(self, db):
        """Basic put/get round-trip."""
        from langgraph.store.postgres import AsyncPostgresStore
        from tests.conftest import TEST_DATABASE_URL

        async with AsyncPostgresStore.from_conn_string(TEST_DATABASE_URL) as store:
            await store.setup()
            ns = ("test", "unit", str(uuid.uuid4().hex[:8]))
            await store.aput(ns, "key1", {"value": "hello"})
            item = await store.aget(ns, "key1")
            assert item is not None
            assert item.value == {"value": "hello"}

    @pytest.mark.asyncio
    async def test_store_search(self, db):
        """Search returns all items in namespace."""
        from langgraph.store.postgres import AsyncPostgresStore
        from tests.conftest import TEST_DATABASE_URL

        async with AsyncPostgresStore.from_conn_string(TEST_DATABASE_URL) as store:
            await store.setup()
            ns = ("test", "search", str(uuid.uuid4().hex[:8]))
            await store.aput(ns, "a", {"v": 1})
            await store.aput(ns, "b", {"v": 2})
            await store.aput(ns, "c", {"v": 3})
            items = await store.asearch(ns)
            assert len(list(items)) == 3

    @pytest.mark.asyncio
    async def test_store_delete(self, db):
        """Delete removes an item."""
        from langgraph.store.postgres import AsyncPostgresStore
        from tests.conftest import TEST_DATABASE_URL

        async with AsyncPostgresStore.from_conn_string(TEST_DATABASE_URL) as store:
            await store.setup()
            ns = ("test", "delete", str(uuid.uuid4().hex[:8]))
            await store.aput(ns, "key1", {"value": "hello"})
            await store.adelete(ns, "key1")
            item = await store.aget(ns, "key1")
            assert item is None

    @pytest.mark.asyncio
    async def test_store_persists_across_threads(self, db):
        """Store data is accessible regardless of thread_id."""
        from langgraph.store.postgres import AsyncPostgresStore
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from langchain_core.messages import HumanMessage
        from tests.conftest import TEST_DATABASE_URL

        async with AsyncPostgresStore.from_conn_string(TEST_DATABASE_URL) as store:
            await store.setup()
            # Write data directly to store
            ns = ("user", "default", "preferences")
            await store.aput(ns, "test_pref", {"value": "cross-thread"})

            # Verify accessible from the store (independent of any thread)
            item = await store.aget(ns, "test_pref")
            assert item is not None
            assert item.value["value"] == "cross-thread"

    @pytest.mark.asyncio
    async def test_compile_graph_with_store(self, db):
        """Graph compiles with both checkpointer and store."""
        from langgraph.store.postgres import AsyncPostgresStore
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from tests.conftest import TEST_DATABASE_URL

        async with AsyncPostgresSaver.from_conn_string(TEST_DATABASE_URL) as saver:
            await saver.setup()
            async with AsyncPostgresStore.from_conn_string(TEST_DATABASE_URL) as store:
                await store.setup()
                compiled = compile_graph(checkpointer=saver, store=store)
                assert compiled.checkpointer is not None
                assert compiled.store is not None


class TestStoreEdgeCases:
    """Edge cases for the PostgresStore."""

    @pytest.mark.asyncio
    async def test_overwrite_existing_key(self, db):
        """aput with same key should upsert, not duplicate."""
        from langgraph.store.postgres import AsyncPostgresStore
        from tests.conftest import TEST_DATABASE_URL

        async with AsyncPostgresStore.from_conn_string(TEST_DATABASE_URL) as store:
            await store.setup()
            ns = ("test", "overwrite", uuid.uuid4().hex[:8])
            await store.aput(ns, "k", {"v": "first"})
            await store.aput(ns, "k", {"v": "second"})
            item = await store.aget(ns, "k")
            assert item.value == {"v": "second"}
            # Should be exactly 1 item, not 2
            items = list(await store.asearch(ns))
            assert len(items) == 1

    @pytest.mark.asyncio
    async def test_empty_key(self, db):
        """Empty string key should work."""
        from langgraph.store.postgres import AsyncPostgresStore
        from tests.conftest import TEST_DATABASE_URL

        async with AsyncPostgresStore.from_conn_string(TEST_DATABASE_URL) as store:
            await store.setup()
            ns = ("test", "empty_key", uuid.uuid4().hex[:8])
            await store.aput(ns, "", {"v": "empty key"})
            item = await store.aget(ns, "")
            assert item is not None
            assert item.value["v"] == "empty key"

    @pytest.mark.asyncio
    async def test_empty_value(self, db):
        """Empty dict value should work."""
        from langgraph.store.postgres import AsyncPostgresStore
        from tests.conftest import TEST_DATABASE_URL

        async with AsyncPostgresStore.from_conn_string(TEST_DATABASE_URL) as store:
            await store.setup()
            ns = ("test", "empty_val", uuid.uuid4().hex[:8])
            await store.aput(ns, "k", {})
            item = await store.aget(ns, "k")
            assert item is not None
            assert item.value == {}

    @pytest.mark.asyncio
    async def test_special_characters_in_key(self, db):
        """Keys with special chars should work."""
        from langgraph.store.postgres import AsyncPostgresStore
        from tests.conftest import TEST_DATABASE_URL

        async with AsyncPostgresStore.from_conn_string(TEST_DATABASE_URL) as store:
            await store.setup()
            ns = ("test", "special", uuid.uuid4().hex[:8])
            for key in ["with spaces", "slashes/here", "unicode-émoji-🚀", "dots.and.more"]:
                await store.aput(ns, key, {"v": key})
                item = await store.aget(ns, key)
                assert item is not None, f"Failed for key: {key}"
                assert item.value["v"] == key

    @pytest.mark.asyncio
    async def test_long_value(self, db):
        """Large values should be stored and retrieved correctly."""
        from langgraph.store.postgres import AsyncPostgresStore
        from tests.conftest import TEST_DATABASE_URL

        async with AsyncPostgresStore.from_conn_string(TEST_DATABASE_URL) as store:
            await store.setup()
            ns = ("test", "long", uuid.uuid4().hex[:8])
            long_text = "x" * 50000
            await store.aput(ns, "big", {"v": long_text})
            item = await store.aget(ns, "big")
            assert item is not None
            assert len(item.value["v"]) == 50000

    @pytest.mark.asyncio
    async def test_get_nonexistent_key(self, db):
        """Getting a key that doesn't exist returns None."""
        from langgraph.store.postgres import AsyncPostgresStore
        from tests.conftest import TEST_DATABASE_URL

        async with AsyncPostgresStore.from_conn_string(TEST_DATABASE_URL) as store:
            await store.setup()
            ns = ("test", "missing", uuid.uuid4().hex[:8])
            item = await store.aget(ns, "nope")
            assert item is None

    @pytest.mark.asyncio
    async def test_search_empty_namespace(self, db):
        """Searching an empty namespace returns empty list."""
        from langgraph.store.postgres import AsyncPostgresStore
        from tests.conftest import TEST_DATABASE_URL

        async with AsyncPostgresStore.from_conn_string(TEST_DATABASE_URL) as store:
            await store.setup()
            ns = ("test", "empty_ns", uuid.uuid4().hex[:8])
            items = list(await store.asearch(ns))
            assert items == []

    @pytest.mark.asyncio
    async def test_namespace_isolation(self, db):
        """Different namespaces should be fully isolated."""
        from langgraph.store.postgres import AsyncPostgresStore
        from tests.conftest import TEST_DATABASE_URL

        async with AsyncPostgresStore.from_conn_string(TEST_DATABASE_URL) as store:
            await store.setup()
            tag = uuid.uuid4().hex[:8]
            ns_prefs = ("test", tag, "preferences")
            ns_notes = ("test", tag, "contacts")
            await store.aput(ns_prefs, "k1", {"v": "pref"})
            await store.aput(ns_notes, "k1", {"v": "note"})
            # Same key "k1" in different namespaces — different values
            pref = await store.aget(ns_prefs, "k1")
            note = await store.aget(ns_notes, "k1")
            assert pref.value["v"] == "pref"
            assert note.value["v"] == "note"
            # Search returns only items from that namespace
            pref_items = list(await store.asearch(ns_prefs))
            note_items = list(await store.asearch(ns_notes))
            assert len(pref_items) == 1
            assert len(note_items) == 1

    @pytest.mark.asyncio
    async def test_delete_nonexistent_key(self, db):
        """Deleting a key that doesn't exist should not error."""
        from langgraph.store.postgres import AsyncPostgresStore
        from tests.conftest import TEST_DATABASE_URL

        async with AsyncPostgresStore.from_conn_string(TEST_DATABASE_URL) as store:
            await store.setup()
            ns = ("test", "del_missing", uuid.uuid4().hex[:8])
            # Should not raise
            await store.adelete(ns, "nonexistent")

    @pytest.mark.asyncio
    async def test_put_after_delete_restores(self, db):
        """Put after delete should restore the key."""
        from langgraph.store.postgres import AsyncPostgresStore
        from tests.conftest import TEST_DATABASE_URL

        async with AsyncPostgresStore.from_conn_string(TEST_DATABASE_URL) as store:
            await store.setup()
            ns = ("test", "restore", uuid.uuid4().hex[:8])
            await store.aput(ns, "k", {"v": "original"})
            await store.adelete(ns, "k")
            assert await store.aget(ns, "k") is None
            await store.aput(ns, "k", {"v": "restored"})
            item = await store.aget(ns, "k")
            assert item is not None
            assert item.value["v"] == "restored"

    @pytest.mark.asyncio
    async def test_many_keys_in_namespace(self, db):
        """Namespace should handle many keys."""
        from langgraph.store.postgres import AsyncPostgresStore
        from tests.conftest import TEST_DATABASE_URL

        async with AsyncPostgresStore.from_conn_string(TEST_DATABASE_URL) as store:
            await store.setup()
            ns = ("test", "many", uuid.uuid4().hex[:8])
            for i in range(50):
                await store.aput(ns, f"key_{i}", {"i": i})
            items = list(await store.asearch(ns, limit=100))
            assert len(items) == 50
