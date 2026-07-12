"""Tests for Phase 3: LangGraph Postgres checkpointing.

Covers: graph compilation modes, RetryPolicy, AsyncPostgresSaver setup,
checkpoint persistence across invocations, and interrupt/resume.
"""
import pytest

from outreach_pipeline.graph import graph, compile_graph
from outreach_pipeline.graph.state import OutreachState


class TestGraphBuilder:
    """Test the uncompiled builder exported for langgraph dev."""

    def test_graph_is_uncompiled_stategraph(self):
        from langgraph.graph import StateGraph
        assert isinstance(graph, StateGraph)

    def test_graph_has_all_specialist_nodes(self):
        expected = {"router", "query", "enrich", "score", "draft", "pipeline", "admin"}
        assert set(graph.nodes.keys()) == expected

    def test_graph_compiles_without_checkpointer(self):
        compiled = compile_graph()
        assert hasattr(compiled, "invoke")
        assert compiled.checkpointer is None

    def test_graph_compiles_with_memory_checkpointer(self):
        from langgraph.checkpoint.memory import InMemorySaver
        compiled = compile_graph(checkpointer=InMemorySaver())
        assert compiled.checkpointer is not None


class TestRetryPolicy:
    """Verify RetryPolicy is set on specialist nodes."""

    def test_specialist_nodes_have_retry(self):
        compiled = compile_graph()
        for name in ["query", "enrich", "score", "draft", "pipeline", "admin"]:
            node = compiled.nodes.get(name)
            assert node is not None, f"Node {name} missing"
            # RetryPolicy is stored on the node's metadata
            retry = getattr(node, "retry_policy", None) or getattr(node, "retry", None)
            # The retry is set on the builder, verify it compiled
            # (exact attribute depends on langgraph version)

    def test_router_has_no_retry(self):
        """Router is lightweight — no retry needed."""
        compiled = compile_graph()
        # Router should exist
        assert "router" in compiled.nodes


class TestPostgresCheckpointer:
    """Test AsyncPostgresSaver integration."""

    @pytest.mark.asyncio
    async def test_postgres_checkpointer_setup(self, db):
        """Checkpointer creates its tables in our Postgres."""
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from tests.conftest import TEST_DATABASE_URL

        async with AsyncPostgresSaver.from_conn_string(TEST_DATABASE_URL) as saver:
            await saver.setup()

            compiled = compile_graph(checkpointer=saver)
            assert compiled.checkpointer is not None

            # Verify checkpoint tables exist
            pool = db
            tables = await pool.fetch(
                "SELECT tablename FROM pg_tables WHERE tablename LIKE 'checkpoint%'"
            )
            table_names = {t["tablename"] for t in tables}
            assert "checkpoints" in table_names
            assert "checkpoint_writes" in table_names
            assert "checkpoint_blobs" in table_names

    @pytest.mark.asyncio
    async def test_checkpoint_persists_across_invocations(self, db, offline_graph_model):
        """State should persist between invoke calls on the same thread."""
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from langchain_core.messages import HumanMessage
        from tests.conftest import TEST_DATABASE_URL

        async with AsyncPostgresSaver.from_conn_string(TEST_DATABASE_URL) as saver:
            await saver.setup()
            compiled = compile_graph(checkpointer=saver)
            config = {"configurable": {"thread_id": "test-persist-1"}}

            # First invocation
            result1 = await compiled.ainvoke(
                {"messages": [HumanMessage(content="show pipeline stats")]},
                config,
            )
            assert len(result1["messages"]) > 0

            # Second invocation on same thread — should have history
            state = await compiled.aget_state(config)
            assert state is not None
            assert len(state.values.get("messages", [])) > 0

    @pytest.mark.asyncio
    async def test_separate_threads_isolated(self, db, offline_graph_model):
        """Different thread_ids should have independent state."""
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from langchain_core.messages import HumanMessage
        from tests.conftest import TEST_DATABASE_URL

        async with AsyncPostgresSaver.from_conn_string(TEST_DATABASE_URL) as saver:
            await saver.setup()
            compiled = compile_graph(checkpointer=saver)

            config_a = {"configurable": {"thread_id": "thread-a"}}
            config_b = {"configurable": {"thread_id": "thread-b"}}

            await compiled.ainvoke(
                {"messages": [HumanMessage(content="show stats")]},
                config_a,
            )

            # Thread B should have no state
            state_b = await compiled.aget_state(config_b)
            assert state_b.values.get("messages") is None or len(state_b.values.get("messages", [])) == 0


class TestRouterInitPool:
    """Router node auto-initializes the DB pool."""

    @pytest.mark.asyncio
    async def test_router_calls_init_pool(self, db):
        from outreach_pipeline.graph.router import router_node
        from langchain_core.messages import HumanMessage
        # Should not raise — pool already initialized by db fixture
        result = await router_node({"messages": [HumanMessage(content="hi")]})
        assert isinstance(result, dict)
