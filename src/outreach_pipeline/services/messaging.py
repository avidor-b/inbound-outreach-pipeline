"""Messaging domain service — context assembly, message persistence.

This service handles the email/message artifact domain:
- Building context for the drafter agent (engagement-aware)
- Saving generated messages as artifacts
- Does NOT generate the message itself (that's the drafter agent's job)
- Does NOT validate content (that's the validation layer's job)
"""
from __future__ import annotations

import json
from typing import Any

from outreach_pipeline.db.contacts import get_contact
from outreach_pipeline.db.engagements import (
    get_engagement, get_submissions as get_engagement_submissions,
)
from outreach_pipeline.db.threads import create_artifact


# --- Legacy contact-level context (kept for backward compat) ---

async def build_message_context(contact_id: str) -> dict[str, Any]:
    """Assemble context for drafting (legacy, contact-level)."""
    contact = await get_contact(contact_id)
    return {"contact": contact, "contact_id": contact_id}


async def save_message(
    thread_id: str, contact_id: str,
    subject: str, body: str, score: int = 0, rag_context: str = "",
) -> int:
    """Save a message as a draft artifact (legacy). Returns artifact ID."""
    data = json.dumps({
        "subject": subject, "body": body,
        "score": score, "rag_context": rag_context,
    })
    return await create_artifact(thread_id, "draft", contact_id, data)


# --- Engagement-aware messaging ---

async def build_engagement_message_context(engagement_id: int) -> dict[str, Any]:
    """Assemble full context for drafting against an engagement.

    Returns engagement, all submissions, contact, and company context.
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


async def save_engagement_draft(
    engagement_id: int, thread_id: str,
    subject: str, body: str, score: int = 0, rag_context: str = "",
) -> int:
    """Save a draft linked to an engagement. Returns artifact ID."""
    eng = await get_engagement(engagement_id)
    contact_id = eng["contact_id"] if eng else ""
    data = json.dumps({
        "engagement_id": engagement_id,
        "subject": subject, "body": body,
        "score": score, "rag_context": rag_context,
    })
    return await create_artifact(thread_id, "draft", contact_id, data)
