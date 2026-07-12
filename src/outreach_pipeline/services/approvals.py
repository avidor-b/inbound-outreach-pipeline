"""Approval service — cross-domain approval lifecycle.

Manages artifact state transitions for human review:
    created → pending_approval → approved | rejected

This is the audit log + state machine, not domain-specific logic.
"""
from __future__ import annotations

from outreach_pipeline.db.threads import (
    update_artifact_status, record_approval as _record_approval,
    list_artifacts,
)


async def request_approval(artifact_id: int) -> None:
    """Move artifact to pending_approval state."""
    await update_artifact_status(artifact_id, "pending_approval")


async def approve(artifact_id: int, user_id: str) -> None:
    """Approve an artifact. Updates status and records audit entry."""
    await _record_approval(artifact_id, user_id, "approve")


async def reject(artifact_id: int, user_id: str, feedback: str = "") -> None:
    """Reject an artifact with feedback. Updates status and records audit entry."""
    await _record_approval(artifact_id, user_id, "reject", feedback=feedback)


async def get_pending(thread_id: str) -> list[dict]:
    """Get all pending_approval artifacts for a thread."""
    return await list_artifacts(thread_id, status="pending_approval")
