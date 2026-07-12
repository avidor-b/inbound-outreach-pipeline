"""Contact enrichment specialist agent.

Summarizes company context from scraped website content.
Called by services/enrichment.py.
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_ai import Agent


class EnrichmentResult(BaseModel):
    """Structured output from the enricher agent."""
    company_context: str = Field(description="2-3 sentence summary of what the company does")
    crm_context: str = Field(default="", description="CRM context if available")


class EnricherDeps(BaseModel):
    """Dependencies for the enricher agent."""
    scraped_content: str = ""
    contact_properties: dict = Field(default_factory=dict)


enricher_agent = Agent(
    "test",
    deps_type=EnricherDeps,
    output_type=EnrichmentResult,
    instructions=(
        "Summarize the company based on the scraped website content. "
        "Focus on: what the company does, their industry, their size/scale, "
        "and relevance to wireless/networking products. Keep it to 2-3 sentences."
    ),
)


@enricher_agent.tool_plain
def query_rag(query: str, collection: str = "sales_materials", top_k: int = 3) -> str:
    """Query prospect RAG collection for scraped content."""
    from outreach_pipeline.adapters.chromadb_adapter import ChromaDBAdapter
    adapter = ChromaDBAdapter()
    results = adapter.query(query, top_k=top_k, collection_name=collection)
    if not results:
        return "No content found."
    return "\n\n".join(f"[{r.source}]: {r.text}" for r in results)
