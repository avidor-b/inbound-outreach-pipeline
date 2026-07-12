"""Lead scoring specialist agent.

Evaluates lead fitness using contact context, RAG results, and policy checks.
Called by services/scoring.py.
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_ai import Agent


class ScoreResult(BaseModel):
    """Structured output from the scorer agent."""
    buying_intent: str = Field(description="low, medium, or high")
    intent_signals: list[str] = Field(description="List of observed intent signals")
    score: int = Field(description="Score 1-100", ge=0, le=100)
    score_reasoning: str = Field(description="One sentence explaining the score")
    product_match: str = Field(default="", description="Best matching product if identifiable")


class ScorerDeps(BaseModel):
    """Dependencies for the scorer agent."""
    contact_context: str = ""
    rag_context: str = ""


scorer_agent = Agent(
    "test",
    deps_type=ScorerDeps,
    output_type=ScoreResult,
    instructions=(
        "Score this engagement for outreach fitness on a scale of 1-100. "
        "You will be given ALL submissions for this engagement — consider the "
        "full history, not just the latest message. Look for progression "
        "(content download → sales inquiry = stronger intent). "
        "Consider: job title/authority, message specificity, company fit, "
        "page views, submission history, and RAG context matches. "
        "80-100 = clear buying intent with timeline/authority. "
        "50-79 = genuine product interest. "
        "25-49 = vague interest. "
        "1-24 = unlikely fit."
    ),
)


@scorer_agent.tool_plain
def query_rag(query: str, collection: str = "sales_materials", top_k: int = 3) -> str:
    """Query sales materials RAG for context relevant to scoring."""
    from outreach_pipeline.adapters.chromadb_adapter import ChromaDBAdapter
    adapter = ChromaDBAdapter()
    results = adapter.query(query, top_k=top_k, collection_name=collection)
    if not results:
        return "No relevant materials found."
    return "\n\n".join(f"[{r.source}]: {r.text}" for r in results)


@scorer_agent.tool_plain
async def check_policy(contact_id: str) -> str:
    """Check contact-level policies (events, support, opt-out, duplicate)."""
    from outreach_pipeline.policies.contact import check_contact_policies
    decision = await check_contact_policies(contact_id)
    if not decision.allowed:
        return f"BLOCKED ({decision.code}): {decision.reason}"
    return "All policies passed."
