"""PII on-demand fetch — retrieves PII-adjacent fields from HubSpot when needed.

These fields are NOT stored locally. They're fetched fresh each time
to keep the pipeline DB PII-free. Used by scoring context, drafting
context, and send-time placeholder resolution.
"""
from __future__ import annotations

from typing import Any


def fetch_contact_pii(contact_id: str, fields: list[str] | None = None) -> dict[str, Any]:
    """Fetch PII-adjacent fields from HubSpot for a specific contact (sync).

    Args:
        contact_id: HubSpot contact ID.
        fields: List of HubSpot property names to fetch.
                Defaults to ["jobtitle", "firstname", "email"].

    Returns:
        Dict of field → value. Returns empty values on error.
    """
    if fields is None:
        fields = ["jobtitle", "firstname", "email"]

    try:
        from outreach_pipeline.config import load_config
        from outreach_pipeline.hubspot_client import HubSpotClient
        config = load_config()
        client = HubSpotClient(config["hubspot_token"], config)
        resp = client.request_with_retry(
            "GET",
            f"https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}",
            json={"properties": fields},
        )
        props = resp.get("properties", {})
        return {f: props.get(f, "") or "" for f in fields}
    except Exception:
        return {f: "" for f in fields}


def fetch_jobtitle(contact_id: str) -> str:
    """Fetch just the job title for a contact (sync)."""
    result = fetch_contact_pii(contact_id, ["jobtitle"])
    return result.get("jobtitle", "")


def fetch_send_fields(contact_id: str) -> tuple[str, str]:
    """Fetch firstname and email for send-time placeholder resolution (sync).

    Returns (firstname, email). Firstname defaults to "there" if empty.
    """
    result = fetch_contact_pii(contact_id, ["firstname", "email"])
    firstname = result.get("firstname", "") or "there"
    email = result.get("email", "")
    return firstname, email


# ---------------------------------------------------------------------------
# Async versions — used by the LangGraph agent graph
# ---------------------------------------------------------------------------

async def afetch_contact_pii(contact_id: str, fields: list[str] | None = None) -> dict[str, Any]:
    """Fetch PII-adjacent fields from HubSpot (async)."""
    if fields is None:
        fields = ["jobtitle", "firstname", "email"]

    try:
        from outreach_pipeline.hubspot_client import get_async_hubspot
        client = get_async_hubspot()
        resp = await client.aget_contact(contact_id, properties=fields)
        props = resp.get("properties", {})
        return {f: props.get(f, "") or "" for f in fields}
    except Exception:
        return {f: "" for f in fields}


async def afetch_jobtitle(contact_id: str) -> str:
    """Fetch just the job title for a contact (async)."""
    result = await afetch_contact_pii(contact_id, ["jobtitle"])
    return result.get("jobtitle", "")


async def afetch_send_fields(contact_id: str) -> tuple[str, str]:
    """Fetch firstname and email for send-time placeholder resolution (async).

    Returns (firstname, email). Firstname defaults to "there" if empty.
    """
    result = await afetch_contact_pii(contact_id, ["firstname", "email"])
    firstname = result.get("firstname", "") or "there"
    email = result.get("email", "")
    return firstname, email
