"""Sending tools — send approved messages."""
from __future__ import annotations

from typing import Any

from outreach_pipeline.db.threads import get_artifact, update_artifact_status
from outreach_pipeline.services.sending import check_send_readiness


async def send_message(
    artifact_id: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Send an approved message artifact.

    Checks send readiness (status=approved + send policies),
    then delivers via SMTP adapter.
    """
    decision = await check_send_readiness(artifact_id)
    if not decision.allowed:
        return {"success": False, "reason": decision.reason, "code": decision.code}

    artifact = await get_artifact(artifact_id)
    import json
    data = artifact["data"] if isinstance(artifact["data"], dict) else json.loads(artifact["data"])

    from outreach_pipeline.adapters.smtp import SMTPAdapter
    adapter = SMTPAdapter()
    result = adapter.send(
        contact_id=artifact["contact_id"],
        subject=data.get("subject", ""),
        body=data.get("body", ""),
        dry_run=dry_run,
    )

    if result.get("success") and result.get("sent"):
        await update_artifact_status(artifact_id, "sent")

    return result
