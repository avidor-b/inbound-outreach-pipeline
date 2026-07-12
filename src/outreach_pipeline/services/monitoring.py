"""Reply monitoring service — fetch and classify replies."""
from __future__ import annotations

from typing import Any


def fetch_replies(since_date: str) -> dict[str, Any]:
    """Fetch replies from HubSpot since a date."""
    from outreach_pipeline.monitor_replies import fetch_replies as _fetch
    result = _fetch(since_date)
    return {"replies": result if isinstance(result, list) else [], "since": since_date}
