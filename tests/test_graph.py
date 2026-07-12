"""Tests for the LangGraph agent -- compilation, tools, state."""
import os

import pytest

# Ensure env is loaded for model init
os.environ.setdefault("GOOGLE_API_KEY", "test-key")


class TestGraphCompilation:
    def test_graph_imports(self):
        from outreach_pipeline.graph import graph
        assert graph is not None

    def test_graph_is_compiled_state_graph(self):
        from outreach_pipeline.graph import graph
        from langgraph.graph import StateGraph
        assert isinstance(graph, StateGraph)

    def test_state_schema(self):
        from outreach_pipeline.graph.state import OutreachState
        # Should have messages (from BaseAgentState) and our custom fields
        annotations = OutreachState.__annotations__
        assert "current_engagement" in annotations
        assert "pipeline_progress" in annotations


class TestToolsRegistered:
    def test_all_tools_list(self):
        from outreach_pipeline.graph.tools import ALL_TOOLS
        # Should have 29 tools
        assert len(ALL_TOOLS) >= 25

    def test_tool_names(self):
        from outreach_pipeline.graph.tools import ALL_TOOLS
        names = [t.name for t in ALL_TOOLS]
        # Query tools
        assert "get_leads" in names
        assert "get_engagements" in names
        assert "get_engagement_details" in names
        assert "get_contact_history" in names
        assert "get_submissions" in names
        assert "get_pipeline_stats" in names
        assert "query_rag" in names
        assert "get_email_guidelines" in names
        # Enrichment
        assert "get_prospect_domain" in names
        assert "scrape_and_index" in names
        assert "save_enrichment" in names
        # Scoring
        assert "get_scoring_context" in names
        assert "save_engagement_score" in names
        # Drafting
        assert "get_drafting_context" in names
        assert "validate_message" in names
        assert "save_engagement_draft" in names
        # Actions
        assert "update_engagement_status" in names
        assert "send_message" in names
        assert "add_opt_out" in names
        # Pipeline
        assert "ingest_contacts" in names
        assert "monitor_replies" in names
        assert "export_pipeline" in names
        assert "grade_collection" in names

    def test_tools_have_descriptions(self):
        from outreach_pipeline.graph.tools import ALL_TOOLS
        for t in ALL_TOOLS:
            assert t.description, f"Tool {t.name} has no description"

    def test_no_duplicate_tool_names(self):
        from outreach_pipeline.graph.tools import ALL_TOOLS
        names = [t.name for t in ALL_TOOLS]
        assert len(names) == len(set(names)), f"Duplicate tool names: {[n for n in names if names.count(n) > 1]}"


class TestToolExecution:
    """Test that tool wrappers call through to the tools/ layer correctly."""

    @pytest.mark.asyncio
    async def test_get_email_guidelines(self):
        from outreach_pipeline.graph.tools import get_email_guidelines
        result = await get_email_guidelines.ainvoke({})
        assert isinstance(result, str)
        assert len(result) > 10

    @pytest.mark.asyncio
    async def test_validate_message_passes(self):
        from outreach_pipeline.graph.tools import validate_message
        result = await validate_message.ainvoke({
            "text": "Hi [First Name], thanks for your interest. Best, [Sender Name]",
            "profile": "cold_outreach",
        })
        assert "passed" in result

    @pytest.mark.asyncio
    async def test_validate_message_catches_violations(self):
        from outreach_pipeline.graph.tools import validate_message
        result = await validate_message.ainvoke({
            "text": "CALL ME NOW!!!",
            "profile": "cold_outreach",
        })
        assert "violations" in result


class TestAgentConfig:
    def test_date_context(self):
        from outreach_pipeline.graph.agent import _build_date_context
        ctx = _build_date_context()
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert today in ctx
        assert "YYYY-MM-DD" in ctx

    def test_model_from_env(self):
        from outreach_pipeline.graph.agent import MODEL
        assert isinstance(MODEL, str)
        assert ":" in MODEL  # Should be provider:model format
