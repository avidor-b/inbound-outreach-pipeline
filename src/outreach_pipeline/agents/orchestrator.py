"""Orchestrator agent — routes user intent to the right tools.

Has a narrow, high-level tool surface. Role-based tool filtering
ensures users only see tools they're authorized to use.

Hooks handle cross-cutting concerns only:
- Audit logging
- PII redaction from chat text
"""
from __future__ import annotations

from pydantic_ai import Agent, Tool

from outreach_pipeline.tools.contacts import get_leads, get_lead_details
from outreach_pipeline.tools.messaging import validate_message
from outreach_pipeline.tools.pipeline import get_stats, get_leads_summary
from outreach_pipeline.tools.rag import query_rag, index_content
from outreach_pipeline.tools.grading import grade_collection
from outreach_pipeline.tools.engagements import (
    get_engagements, get_engagement_details, get_contact_history, get_submissions,
)

# Engagement-level scoring (thin wrapper for orchestrator use)
def _score_engagement(engagement_id: int, score: int, score_reasoning: str, primary_product: str = "") -> str:
    from outreach_pipeline.db.connection import get_db
    from outreach_pipeline.services.scoring import save_engagement_score
    conn = get_db()
    save_engagement_score(conn, engagement_id, score, score_reasoning, primary_product)
    conn.close()
    return f"Engagement #{engagement_id} scored: {score}"


# --- Tool registry ---

ALL_TOOLS: dict[str, callable] = {
    # Engagement-aware (primary surface)
    "get_engagements": get_engagements,
    "get_engagement_details": get_engagement_details,
    "get_contact_history": get_contact_history,
    "get_submissions": get_submissions,
    "score_engagement": _score_engagement,
    # Contact-level (kept for backward compat)
    "get_leads": get_leads,
    "get_lead_details": get_lead_details,
    "get_stats": get_stats,
    "get_leads_summary": get_leads_summary,
    "query_rag": query_rag,
    # Messaging
    "validate_message": validate_message,
    "generate_validated_message": validate_message,
    # Sending (stub — not implemented yet to prevent accidental sends)
    "send_message": lambda artifact_id, **kw: {"error": "Sending not implemented yet"},
    # Content grading
    "grade_collection": grade_collection,
    # RAG management
    "index_content": index_content,
}

# Role → tool names
ROLE_TOOLS: dict[str, list[str]] = {
    "viewer": [
        "get_engagements", "get_engagement_details", "get_contact_history",
        "get_submissions",
        "get_leads", "get_lead_details", "get_stats",
        "get_leads_summary", "query_rag",
    ],
    "drafter": [
        "get_engagements", "get_engagement_details", "get_contact_history",
        "get_submissions", "score_engagement",
        "get_leads", "get_lead_details", "get_stats",
        "get_leads_summary", "query_rag",
        "validate_message", "generate_validated_message",
        "grade_collection",
    ],
    "sender": [
        "get_engagements", "get_engagement_details", "get_contact_history",
        "get_submissions", "score_engagement",
        "get_leads", "get_lead_details", "get_stats",
        "get_leads_summary", "query_rag",
        "validate_message", "generate_validated_message",
        "send_message",
        "grade_collection", "index_content",
    ],
    "admin": list(ALL_TOOLS.keys()),
}


def build_orchestrator(role: str = "admin", model: str = "test") -> Agent:
    """Build an orchestrator agent with tools filtered by role."""
    allowed_tool_names = ROLE_TOOLS.get(role, ROLE_TOOLS["viewer"])
    tools = [
        Tool(ALL_TOOLS[name], name=name)
        for name in allowed_tool_names
        if name in ALL_TOOLS
    ]

    return Agent(
        model,
        tools=tools,
        instructions=(
            "You are a marketing automation agent. Help users manage their "
            "outreach pipeline. Engagements are the unit of work — each groups "
            "submissions from a contact into one actionable track. "
            "Use the available tools to fulfill requests. "
            "Be concise in your responses."
        ),
    )
