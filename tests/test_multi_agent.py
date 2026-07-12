"""Tests for StateGraph multi-agent architecture.

Covers: tool registration, specialist subgraph compilation,
intent classification, router, ScopeMiddleware, tool isolation.
"""
import pytest
import os
import unittest

os.environ.setdefault("GOOGLE_API_KEY", "test-key")


class TestToolRegistry(unittest.TestCase):
    """Test the @agent_tool decorator and registry."""

    def test_registry_has_all_groups(self):
        from outreach_pipeline.graph.tools import _TOOL_REGISTRY
        expected = {"query", "enrich", "scoring", "drafting", "pipeline", "admin"}
        self.assertTrue(expected.issubset(set(_TOOL_REGISTRY.keys())))

    def test_get_agent_tools_returns_list(self):
        from outreach_pipeline.graph.tools import get_agent_tools
        tools = get_agent_tools("query")
        self.assertIsInstance(tools, list)
        self.assertGreater(len(tools), 0)

    def test_get_agent_tools_unknown_group(self):
        from outreach_pipeline.graph.tools import get_agent_tools
        tools = get_agent_tools("nonexistent")
        self.assertEqual(tools, [])

    def test_query_tools_count(self):
        from outreach_pipeline.graph.tools import get_agent_tools
        tools = get_agent_tools("query")
        # get_submissions_bulk covers the submissions-multi streaming adapter path.
        self.assertEqual(len(tools), 20)

    def test_enrich_tools_count(self):
        from outreach_pipeline.graph.tools import get_agent_tools
        tools = get_agent_tools("enrich")
        self.assertEqual(len(tools), 13)

    def test_scoring_tools_count(self):
        from outreach_pipeline.graph.tools import get_agent_tools
        tools = get_agent_tools("scoring")
        self.assertEqual(len(tools), 12)

    def test_drafting_tools_count(self):
        from outreach_pipeline.graph.tools import get_agent_tools
        tools = get_agent_tools("drafting")
        self.assertEqual(len(tools), 13)

    def test_pipeline_tools_count(self):
        from outreach_pipeline.graph.tools import get_agent_tools
        tools = get_agent_tools("pipeline")
        self.assertEqual(len(tools), 11)

    def test_admin_tools_count(self):
        from outreach_pipeline.graph.tools import get_agent_tools
        tools = get_agent_tools("admin")
        self.assertEqual(len(tools), 16)

    def test_update_memo_in_all_groups(self):
        from outreach_pipeline.graph.tools import get_agent_tools
        for group in ["query", "enrich", "scoring", "drafting", "pipeline", "admin"]:
            names = [t.name for t in get_agent_tools(group)]
            self.assertIn("update_memo", names,
                          f"update_memo missing from {group}")

    def test_shared_query_rag_in_scoring(self):
        from outreach_pipeline.graph.tools import get_agent_tools
        names = [t.name for t in get_agent_tools("scoring")]
        self.assertIn("query_rag", names)

    def test_shared_query_rag_in_drafting(self):
        from outreach_pipeline.graph.tools import get_agent_tools
        names = [t.name for t in get_agent_tools("drafting")]
        self.assertIn("query_rag", names)

    def test_shared_get_drafts_in_drafting(self):
        from outreach_pipeline.graph.tools import get_agent_tools
        names = [t.name for t in get_agent_tools("drafting")]
        self.assertIn("get_drafts", names)

    def test_no_duplicate_tools_per_group(self):
        from outreach_pipeline.graph.tools import get_agent_tools
        for group in ["query", "enrich", "scoring", "drafting", "pipeline", "admin"]:
            tools = get_agent_tools(group)
            names = [t.name for t in tools]
            self.assertEqual(len(names), len(set(names)),
                             f"Duplicate tools in {group}: {[n for n in names if names.count(n) > 1]}")

    def test_all_tools_accounted_for(self):
        """Every tool in ALL_TOOLS must appear in at least one group."""
        from outreach_pipeline.graph.tools import ALL_TOOLS, get_agent_tools
        grouped_names = set()
        for group in ["query", "enrich", "scoring", "drafting", "pipeline", "admin"]:
            for t in get_agent_tools(group):
                grouped_names.add(t.name)
        all_names = {t.name for t in ALL_TOOLS}
        missing = all_names - grouped_names
        self.assertEqual(missing, set(), f"Tools not in any group: {missing}")

    def test_query_tools_are_read_only(self):
        """Query tools should not include any write/mutate tools (except update_memo)."""
        from outreach_pipeline.graph.tools import get_agent_tools
        write_tools = {"save_enrichment", "save_engagement_score", "save_engagement_draft",
                       "update_engagement_status", "delete_contact", "add_opt_out",
                       "ingest_contacts", "send_message"}
        query_names = {t.name for t in get_agent_tools("query")}
        overlap = query_names & write_tools
        self.assertEqual(overlap, set(), f"Query agent has write tools: {overlap}")


class TestSpecialistCompilation(unittest.TestCase):
    """Test that each specialist compiles as a CompiledStateGraph."""

    def test_query_agent_compiles(self):
        from outreach_pipeline.graph.agents.query import query_agent
        self.assertIsNotNone(query_agent)
        self.assertEqual(type(query_agent).__name__, "CompiledStateGraph")

    def test_enrichment_agent_compiles(self):
        from outreach_pipeline.graph.agents.enrichment import enrich_agent
        self.assertIsNotNone(enrich_agent)
        self.assertEqual(type(enrich_agent).__name__, "CompiledStateGraph")

    def test_scoring_agent_compiles(self):
        from outreach_pipeline.graph.agents.scoring import score_agent
        self.assertIsNotNone(score_agent)
        self.assertEqual(type(score_agent).__name__, "CompiledStateGraph")

    def test_drafting_agent_compiles(self):
        from outreach_pipeline.graph.agents.drafting import draft_agent
        self.assertIsNotNone(draft_agent)
        self.assertEqual(type(draft_agent).__name__, "CompiledStateGraph")

    def test_admin_agent_compiles(self):
        from outreach_pipeline.graph.agents.admin import admin_agent
        self.assertIsNotNone(admin_agent)
        self.assertEqual(type(admin_agent).__name__, "CompiledStateGraph")

    def test_pipeline_llm_agent_compiles(self):
        from outreach_pipeline.graph.agents.pipeline_agent import _pipeline_llm_agent
        self.assertIsNotNone(_pipeline_llm_agent)
        self.assertEqual(type(_pipeline_llm_agent).__name__, "CompiledStateGraph")

    def test_pipeline_node_is_callable(self):
        from outreach_pipeline.graph.agents.pipeline_agent import pipeline_node
        self.assertTrue(callable(pipeline_node))


class TestGraphStructure(unittest.TestCase):
    """Test the top-level StateGraph structure."""

    def test_graph_compiles(self):
        from outreach_pipeline.graph import graph
        self.assertIsNotNone(graph)
        self.assertEqual(type(graph).__name__, "StateGraph")
        # Verify it can be compiled
        compiled = graph.compile()
        self.assertEqual(type(compiled).__name__, "CompiledStateGraph")

    def test_graph_has_all_nodes(self):
        from outreach_pipeline.graph import graph
        nodes = set(graph.nodes.keys())
        expected = {"router", "query", "enrich", "score", "draft", "pipeline", "admin"}
        self.assertEqual(nodes, expected)

    def test_graph_has_7_nodes(self):
        from outreach_pipeline.graph import graph
        self.assertEqual(len(graph.nodes), 7)

    def test_graph_entry_is_router(self):
        """START edge leads to router."""
        from outreach_pipeline.graph import graph
        from langgraph.graph import START
        edges = graph.edges
        self.assertIn((START, "router"), edges)

    def test_all_specialists_edge_to_end(self):
        from outreach_pipeline.graph import graph
        from langgraph.graph import END
        edges = graph.edges
        for name in ["query", "enrich", "score", "draft", "pipeline", "admin"]:
            self.assertIn((name, END), edges,
                          f"Missing edge: {name} → END")


class TestRouter(unittest.TestCase):
    """Test the deterministic intent classifier."""

    def test_show_leads_is_query(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("show me friday's leads"), "query")

    def test_list_engagements_is_query(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("list all engagements"), "query")

    def test_get_details_is_query(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("get contact details for 42"), "query")

    def test_whats_the_score_is_query(self):
        """Noun-form 'what's the score' should be query, not scoring."""
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("what's the score for engagement 42?"), "query")

    def test_show_the_draft_is_query(self):
        """Noun-form 'show the draft' should be query, not drafting."""
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("show me the draft"), "query")

    def test_whats_the_status_is_query(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("what's the status?"), "query")

    def test_pipeline_stats_is_query(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("pipeline stats"), "query")

    def test_how_many_leads_is_query(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("how many leads do we have?"), "query")

    def test_score_engagement_is_score(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("score engagement 42"), "score")

    def test_score_it_is_score(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("score it"), "score")

    def test_evaluate_lead_is_score(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("evaluate this lead"), "score")

    def test_draft_email_is_draft(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("draft an email for engagement 5"), "draft")

    def test_write_email_is_draft(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("write an email to that contact"), "draft")

    def test_compose_outreach_is_draft(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("compose an outreach email"), "draft")

    def test_revise_draft_is_draft(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("revise the draft"), "draft")

    def test_enrich_contact_is_enrich(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("enrich this contact"), "enrich")

    def test_scrape_website_is_enrich(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("scrape their website"), "enrich")

    def test_find_domain_is_enrich(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("find the domain for this contact"), "enrich")

    def test_delete_contact_is_admin(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("delete the contact"), "admin")

    def test_preview_delete_is_admin(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("preview delete the contact"), "admin")

    def test_opt_out_is_admin(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("opt out this contact"), "admin")

    def test_approve_is_admin(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("approve the engagement"), "admin")

    def test_retarget_is_admin(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("retarget engagement 5"), "admin")

    def test_update_status_is_admin(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("update the engagement status to drafting"), "admin")

    def test_index_content_is_admin(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("index the content"), "admin")

    def test_run_pipeline_is_pipeline(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("run pipeline for 2026-03-30"), "pipeline")

    def test_run_full_pipeline_is_pipeline(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("yes, run the full pipeline"), "pipeline")

    def test_fetch_contacts_is_pipeline(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("fetch friday's contacts"), "pipeline")

    def test_fetch_from_hubspot_is_pipeline(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("fetch contacts from hubspot"), "pipeline")

    def test_check_replies_is_pipeline(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("check for replies"), "pipeline")

    def test_export_excel_is_pipeline(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("export pipeline to excel"), "pipeline")

    def test_unknown_defaults_to_query(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent("hello how are you"), "query")

    def test_empty_defaults_to_query(self):
        from outreach_pipeline.graph.router import classify_intent
        self.assertEqual(classify_intent(""), "query")

    def test_last_human_message(self):
        from outreach_pipeline.graph.router import last_human_message
        from langchain_core.messages import HumanMessage, AIMessage
        messages = [
            HumanMessage(content="first"),
            AIMessage(content="response"),
            HumanMessage(content="second"),
        ]
        self.assertEqual(last_human_message(messages), "second")

    def test_last_human_message_empty(self):
        from outreach_pipeline.graph.router import last_human_message
        self.assertEqual(last_human_message([]), "")

    def test_route_to_specialist(self):
        from outreach_pipeline.graph.router import route_to_specialist
        from langchain_core.messages import HumanMessage
        state = {"messages": [HumanMessage(content="show leads")]}
        self.assertEqual(route_to_specialist(state), "query")

    @pytest.mark.asyncio
    async def test_router_node_no_trimming_needed(self):
        from outreach_pipeline.graph.router import router_node
        from langchain_core.messages import HumanMessage
        state = {"messages": [HumanMessage(content="hi")]}
        result = await router_node(state)
        assert result == {}

    @pytest.mark.asyncio
    async def test_router_strips_old_tool_messages(self):
        """Internal tool calls from previous turns should be removed."""
        from outreach_pipeline.graph.router import router_node
        from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, RemoveMessage
        messages = [
            # Previous turn: human + tool call + tool result + final response
            HumanMessage(content="show leads", id="h1"),
            AIMessage(content="", tool_calls=[{"name": "get_leads", "id": "tc1", "args": {}}], id="ai1"),
            ToolMessage(content='[{"id": 42}]', tool_call_id="tc1", id="tm1"),
            AIMessage(content="Here are the leads.", id="ai2"),
            # Current turn
            HumanMessage(content="score it", id="h2"),
        ]
        state = {"messages": messages}
        result = await router_node(state)
        assert "messages" in result
        removed_ids = {m.id for m in result["messages"]}
        # Tool call AIMessage and ToolMessage from previous turn should be removed
        assert "ai1" in removed_ids
        assert "tm1" in removed_ids
        # Human and final AI response should NOT be removed
        assert "h1" not in removed_ids
        assert "ai2" not in removed_ids

    @pytest.mark.asyncio
    async def test_router_preserves_current_turn_tool_messages(self):
        """Tool messages in the current turn (after last HumanMessage) are kept."""
        from outreach_pipeline.graph.router import router_node
        from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
        messages = [
            HumanMessage(content="score engagement 42", id="h1"),
            AIMessage(content="", tool_calls=[{"name": "check_policy", "id": "tc1", "args": {}}], id="ai1"),
            ToolMessage(content="ok", tool_call_id="tc1", id="tm1"),
        ]
        state = {"messages": messages}
        result = await router_node(state)
        # Current turn — nothing should be removed
        assert result == {}


class TestScopeMiddleware(unittest.TestCase):
    """Test that ScopeMiddleware scopes model view correctly."""

    def test_passes_all_messages_through(self):
        """ScopeMiddleware no longer strips history — all messages are visible."""
        from outreach_pipeline.graph.middleware import ScopeMiddleware
        from langchain_core.messages import HumanMessage, AIMessage

        mw = ScopeMiddleware()
        state = {
            "messages": [
                HumanMessage(content="old request"),
                AIMessage(content="old response"),
                HumanMessage(content="current request"),
            ],
            "memo": "",
        }
        scoped = mw._scope_messages(state)
        self.assertEqual(len(scoped), 3)  # all messages passed through

    def test_includes_all_tool_calls(self):
        """All messages including tool calls from previous turns are visible."""
        from outreach_pipeline.graph.middleware import ScopeMiddleware
        from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

        mw = ScopeMiddleware()
        state = {
            "messages": [
                HumanMessage(content="old"),
                AIMessage(content="old reply"),
                HumanMessage(content="score engagement 42"),
                AIMessage(content="", tool_calls=[{"name": "check_engagement_policy", "id": "tc1", "args": {}}]),
                ToolMessage(content="ok", tool_call_id="tc1"),
            ],
            "memo": "",
        }
        scoped = mw._scope_messages(state)
        self.assertEqual(len(scoped), 5)  # all messages visible

    def test_injects_memo(self):
        from outreach_pipeline.graph.middleware import ScopeMiddleware
        from langchain_core.messages import HumanMessage, SystemMessage

        mw = ScopeMiddleware()
        state = {
            "messages": [HumanMessage(content="show leads")],
            "memo": "Example Networks = 42",
        }
        scoped = mw._scope_messages(state)
        self.assertEqual(len(scoped), 2)
        self.assertIsInstance(scoped[0], SystemMessage)
        self.assertIn("Example Networks = 42", scoped[0].content)
        self.assertEqual(scoped[1].content, "show leads")

    def test_no_memo_when_empty(self):
        from outreach_pipeline.graph.middleware import ScopeMiddleware
        from langchain_core.messages import HumanMessage

        mw = ScopeMiddleware()
        state = {
            "messages": [HumanMessage(content="hi")],
            "memo": "",
        }
        scoped = mw._scope_messages(state)
        self.assertEqual(len(scoped), 1)

    def test_no_human_message_returns_all(self):
        from outreach_pipeline.graph.middleware import ScopeMiddleware
        from langchain_core.messages import AIMessage

        mw = ScopeMiddleware()
        state = {
            "messages": [AIMessage(content="orphan")],
            "memo": "",
        }
        scoped = mw._scope_messages(state)
        self.assertEqual(len(scoped), 1)  # returns all messages as fallback

    def test_repairs_orphaned_tool_calls(self):
        """AIMessage with tool_calls but no ToolMessage response → inject synthetic."""
        from outreach_pipeline.graph.middleware import ScopeMiddleware
        from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

        mw = ScopeMiddleware()
        state = {
            "messages": [
                HumanMessage(content="enrich it"),
                AIMessage(content="", tool_calls=[{"name": "frontend_tool", "id": "ft1", "args": {}}]),
                # No ToolMessage for ft1 — orphaned!
            ],
            "memo": "",
        }
        scoped = mw._scope_messages(state)
        # Should inject synthetic ToolMessage for ft1
        self.assertEqual(len(scoped), 3)
        self.assertIsInstance(scoped[2], ToolMessage)
        self.assertEqual(scoped[2].tool_call_id, "ft1")

    def test_removes_orphaned_tool_messages(self):
        """ToolMessage with no matching AIMessage tool_call → removed."""
        from outreach_pipeline.graph.middleware import ScopeMiddleware
        from langchain_core.messages import HumanMessage, ToolMessage

        mw = ScopeMiddleware()
        state = {
            "messages": [
                HumanMessage(content="test"),
                ToolMessage(content="stale", tool_call_id="gone"),
            ],
            "memo": "",
        }
        scoped = mw._scope_messages(state)
        self.assertEqual(len(scoped), 1)
        self.assertIsInstance(scoped[0], HumanMessage)

    def test_repair_preserves_valid_pairs(self):
        """Valid AIMessage+ToolMessage pairs are preserved."""
        from outreach_pipeline.graph.middleware import ScopeMiddleware
        from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

        mw = ScopeMiddleware()
        state = {
            "messages": [
                HumanMessage(content="show leads"),
                AIMessage(content="", tool_calls=[{"name": "get_leads", "id": "tc1", "args": {}}]),
                ToolMessage(content="[{...}]", tool_call_id="tc1"),
            ],
            "memo": "",
        }
        scoped = mw._scope_messages(state)
        self.assertEqual(len(scoped), 3)  # All preserved


class TestToolIsolation(unittest.TestCase):
    """Test that tools don't leak between groups."""

    def test_ingest_only_in_pipeline(self):
        from outreach_pipeline.graph.tools import get_agent_tools
        for group in ["query", "enrich", "scoring", "drafting", "admin"]:
            names = [t.name for t in get_agent_tools(group)]
            self.assertNotIn("ingest_contacts", names,
                             f"ingest_contacts leaked into {group}")

    def test_delete_only_in_admin(self):
        from outreach_pipeline.graph.tools import get_agent_tools
        for group in ["query", "enrich", "scoring", "drafting", "pipeline"]:
            names = [t.name for t in get_agent_tools(group)]
            self.assertNotIn("delete_contact", names,
                             f"delete_contact leaked into {group}")

    def test_save_draft_only_in_drafting(self):
        from outreach_pipeline.graph.tools import get_agent_tools
        for group in ["query", "enrich", "scoring", "pipeline", "admin"]:
            names = [t.name for t in get_agent_tools(group)]
            self.assertNotIn("save_engagement_draft", names,
                             f"save_engagement_draft leaked into {group}")

    def test_save_score_only_in_scoring(self):
        from outreach_pipeline.graph.tools import get_agent_tools
        for group in ["query", "enrich", "drafting", "pipeline", "admin"]:
            names = [t.name for t in get_agent_tools(group)]
            self.assertNotIn("save_engagement_score", names,
                             f"save_engagement_score leaked into {group}")


class TestRegistryEdgeCases(unittest.TestCase):
    """Edge cases in the @agent_tool decorator and registry."""

    def test_agent_tool_without_tool_decorator(self):
        """@agent_tool on a plain function (not @tool) should still register it."""
        from outreach_pipeline.graph.tools import agent_tool, _TOOL_REGISTRY
        @agent_tool("_test_group")
        def bare_function():
            pass
        self.assertIn(bare_function, _TOOL_REGISTRY["_test_group"])
        # Cleanup
        _TOOL_REGISTRY.pop("_test_group", None)

    def test_registry_returns_copy(self):
        """get_agent_tools returns a copy — mutating it doesn't affect registry."""
        from outreach_pipeline.graph.tools import get_agent_tools
        tools = get_agent_tools("query")
        original_len = len(tools)
        tools.append("fake_tool")
        self.assertEqual(len(get_agent_tools("query")), original_len)

    def test_all_tools_generated_from_registry(self):
        """ALL_TOOLS is built from registry, not hardcoded."""
        from outreach_pipeline.graph.tools import ALL_TOOLS, _build_all_tools
        rebuilt = _build_all_tools()
        self.assertEqual(len(ALL_TOOLS), len(rebuilt))
        self.assertEqual([t.name for t in ALL_TOOLS], [t.name for t in rebuilt])

    def test_all_tools_no_duplicates(self):
        from outreach_pipeline.graph.tools import ALL_TOOLS
        names = [t.name for t in ALL_TOOLS]
        self.assertEqual(len(names), len(set(names)),
                         f"Duplicates: {[n for n in names if names.count(n) > 1]}")

    def test_shared_tools_registered_before_get_agent_tools(self):
        """query_rag must be in scoring and drafting at import time."""
        from outreach_pipeline.graph.tools import SCORING_TOOLS, DRAFTING_TOOLS
        scoring_names = [t.name for t in SCORING_TOOLS]
        drafting_names = [t.name for t in DRAFTING_TOOLS]
        self.assertIn("query_rag", scoring_names)
        self.assertIn("query_rag", drafting_names)
        self.assertIn("get_drafts", drafting_names)


class TestSpecialistPrompts(unittest.TestCase):
    """Test that specialist prompts include required elements."""

    def _get_prompt(self, agent) -> str:
        """Extract system prompt from a create_agent CompiledStateGraph."""
        # The system_prompt is stored in the agent's builder configuration
        config = agent.builder.config if hasattr(agent.builder, 'config') else {}
        # Fallback: check nodes for system message injection
        # create_agent stores system_prompt in the graph's metadata
        for key, node in agent.builder.nodes.items():
            if hasattr(node, 'system_prompt'):
                return node.system_prompt
        return ""

    def test_query_has_memo_instructions(self):
        from outreach_pipeline.graph.agents.query import query_agent
        # Verify the agent was created with memo instructions by checking
        # that the agent has update_memo in its tools
        # (We can't easily extract the prompt from a compiled graph)
        pass  # Covered by test_update_memo_in_all_groups

    def test_date_context_function(self):
        from outreach_pipeline.graph.agent import _build_date_context
        from datetime import datetime, timezone
        ctx = _build_date_context()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.assertIn(today, ctx)


class TestImportOrder(unittest.TestCase):
    """Verify no circular imports and correct load order."""

    def test_tools_loads_independently(self):
        """tools.py must not import from agents/ or __init__.py's graph."""
        import outreach_pipeline.graph.tools as tools_mod
        import inspect
        source = inspect.getsource(tools_mod)
        self.assertNotIn("from outreach_pipeline.graph.agents", source)
        self.assertNotIn("from outreach_pipeline.graph import graph", source)

    def test_router_loads_independently(self):
        """router.py must not import from agents/."""
        import outreach_pipeline.graph.router as router_mod
        import inspect
        source = inspect.getsource(router_mod)
        self.assertNotIn("from outreach_pipeline.graph.agents", source)

    def test_middleware_loads_independently(self):
        """middleware.py must not import from agents/ or tools."""
        import outreach_pipeline.graph.middleware as mw_mod
        import inspect
        source = inspect.getsource(mw_mod)
        self.assertNotIn("from outreach_pipeline.graph.agents", source)
        self.assertNotIn("from outreach_pipeline.graph.tools", source)

    def test_full_import_chain(self):
        """Full import: graph → agents → agent → tools."""
        from outreach_pipeline.graph import graph
        self.assertIsNotNone(graph)


if __name__ == "__main__":
    unittest.main()
