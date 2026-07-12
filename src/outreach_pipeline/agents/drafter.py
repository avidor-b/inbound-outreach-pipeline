"""Email drafting specialist agent.

Writes email copy given contact context and RAG results.
Called by services/messaging.py, not directly by tools.
Uses email-guidelines.md as system prompt.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext


class MessageDraftOutput(BaseModel):
    """Structured output from the drafter agent."""
    subject: str = Field(description="Email subject line")
    body: str = Field(description="Email body text")


class DrafterDeps(BaseModel):
    """Dependencies injected into the drafter agent."""
    contact_context: str = ""
    rag_context: str = ""
    feedback: str = ""


def _load_guidelines() -> str:
    """Load email guidelines from the skill file."""
    guidelines_path = Path(__file__).parent.parent.parent.parent / ".claude" / "skills" / "outreach-pipeline" / "email-guidelines.md"
    if guidelines_path.exists():
        return guidelines_path.read_text(encoding="utf-8")
    return "Draft a concise 3-4 sentence email. Start with 'Hi [First Name],' and end with 'Best,\\n[Sender Name]'."


# The drafter agent — specialist for writing email copy
drafter_agent = Agent(
    "test",  # model set at runtime; "test" for unit tests
    deps_type=DrafterDeps,
    output_type=MessageDraftOutput,
    instructions=(
        _load_guidelines() + "\n\n"
        "IMPORTANT: You will receive context about ALL submissions for this "
        "engagement, not just the latest message. Use the full history to "
        "understand their interest, but ONLY reference information the contact "
        "explicitly shared in their messages. NEVER mention content downloads, "
        "page views, or browsing behavior — that is tracking data they did not "
        "disclose. If they have multiple messages, address the most recent one."
    ),
)


@drafter_agent.tool_plain
def query_rag(query: str, collection: str = "sales_materials", top_k: int = 3) -> str:
    """Query the RAG vector store for relevant sales materials."""
    from outreach_pipeline.adapters.chromadb_adapter import ChromaDBAdapter
    adapter = ChromaDBAdapter()
    results = adapter.query(query, top_k=top_k, collection_name=collection)
    if not results:
        return "No relevant materials found."
    return "\n\n".join(f"[{r.source}]: {r.text}" for r in results)
