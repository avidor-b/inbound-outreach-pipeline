"""Typed artifacts for the pipeline.

All models use Pydantic BaseModel for validation, serialization, and schema generation.
OperationContext is kept as a dataclass for backward compat with the operator layer
(will be removed when operators are removed in Phase 6).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field


# --- Entity types (things being processed) ---

class Contact(BaseModel):
    """A contact/lead moving through the pipeline."""
    id: str
    properties: dict[str, Any] = Field(default_factory=dict)
    company_context: str = ""
    crm_context: str = ""
    buying_intent: str = ""
    intent_signals: list[str] = Field(default_factory=list)
    score: int = 0
    score_reasoning: str = ""
    product_match: str = ""
    fetched_at: str | None = None
    enriched_at: str | None = None
    scored_at: str | None = None

    @classmethod
    def from_db_row(cls, row: dict) -> Contact:
        """Construct from a db row dict."""
        return cls(
            id=row["id"],
            properties=row.get("properties", {}),
            company_context=row.get("company_context", "") or "",
            crm_context=row.get("crm_context", "") or "",
            buying_intent=row.get("buying_intent", "") or "",
            intent_signals=row.get("intent_signals", []) or [],
            score=row.get("score", 0) or 0,
            score_reasoning=row.get("score_reasoning", "") or "",
            product_match=row.get("product_match", "") or "",
            fetched_at=row.get("fetched_at"),
            enriched_at=row.get("enriched_at"),
            scored_at=row.get("scored_at"),
        )

    def to_db_dict(self) -> dict:
        """Convert to the shape db functions expect."""
        return {
            "id": self.id,
            "properties": self.properties,
            "company_context": self.company_context,
            "crm_context": self.crm_context,
            "buying_intent": self.buying_intent,
            "intent_signals": self.intent_signals,
            "score": self.score,
            "score_reasoning": self.score_reasoning,
            "product_match": self.product_match,
        }


class ContentPage(BaseModel):
    """A web page or document for content grading."""
    url: str
    title: str
    text: str
    source_file: str = ""


# --- Artifact types (things being produced) ---

class EmailDraft(BaseModel):
    """An email draft artifact."""
    contact_id: str
    subject: str = ""
    body: str = ""
    score_at_send: int = 0
    rag_context: str = ""
    status: str = "draft"
    revisions: int = 0
    created_at: str | None = None


class ContentGrade(BaseModel):
    """A content quality grade for a single page/chunk."""
    page: ContentPage
    violations: list[dict[str, Any]] = Field(default_factory=list)
    quality_score: float = 100.0
    category_scores: dict[str, float] = Field(default_factory=dict)


class GradingReport(BaseModel):
    """Aggregated content grading report."""
    total_chunks: int = 0
    violations_by_type: dict[str, int] = Field(default_factory=dict)
    worst_chunks: list[ContentGrade] = Field(default_factory=list)
    overall_score: float = 100.0


class RAGResult(BaseModel):
    """A retrieval result from the vector store."""
    source: str
    text: str
    distance: float | None = None
    collection: str = ""


class Reply(BaseModel):
    """An incoming reply from a prospect."""
    contact_id: str
    subject: str = ""
    body_preview: str = ""
    direction: str = ""
    timestamp: str = ""
    urgency: str = ""
    sentiment: str = ""
    response_time_hours: float | None = None


# --- Operation context (backward compat — removed in Phase 6) ---

@dataclass
class OperationContext:
    """Carries metadata through an operator chain.

    Will be replaced by PydanticAI's RunContext when operators are removed.
    """
    trace_id: str = ""
    step_name: str = ""
    recipe_name: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    db_path: str = "data/pipeline.db"
    audit_trail: list[dict] = field(default_factory=list)

    def log(self, event: str, **kwargs):
        self.audit_trail.append({"event": event, "step": self.step_name, **kwargs})
