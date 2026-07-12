"""Scoring domain service — engagement-aware scoring."""
from __future__ import annotations

from typing import Any

from outreach_pipeline.db.contacts import get_contact, score_contact
from outreach_pipeline.db.engagements import (
    get_engagement, get_submissions as get_engagement_submissions,
    score_engagement as _score_engagement,
)


# --- Legacy contact-level scoring (kept for backward compat) ---

async def build_score_context(contact_id: str) -> dict[str, Any]:
    """Assemble context needed for scoring a contact."""
    contact = await get_contact(contact_id)
    return {"contact": contact, "contact_id": contact_id}


async def save_score(
    contact_id: str, buying_intent: str, intent_signals: list[str],
    score: int, score_reasoning: str, product_match: str = "",
) -> None:
    """Persist a score to the database (legacy contact-level)."""
    await score_contact(contact_id, buying_intent, intent_signals,
                        score, score_reasoning, product_match)


# --- Engagement-aware scoring ---

async def build_engagement_context(engagement_id: int) -> dict[str, Any]:
    """Assemble full context for scoring an engagement.

    Returns engagement, all submissions, and contact data.
    """
    eng = await get_engagement(engagement_id)
    if not eng:
        return {"engagement": None, "submissions": [], "contact": None}

    submissions = await get_engagement_submissions(engagement_id)
    contact = await get_contact(eng["contact_id"])

    return {
        "engagement": eng,
        "submissions": submissions,
        "contact": contact,
    }


async def save_engagement_score(
    engagement_id: int, score: int,
    score_reasoning: str = "", primary_product: str = "",
) -> None:
    """Persist a score on an engagement."""
    await _score_engagement(engagement_id, score, score_reasoning, primary_product)
