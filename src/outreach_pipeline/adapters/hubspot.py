"""HubSpot adapter — wraps hubspot_client.py and fetch_mqls.py."""
from __future__ import annotations

from typing import Any


class HubSpotAdapter:
    """Unified interface to HubSpot API operations."""

    def __init__(self, client):
        self.client = client

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None) -> HubSpotAdapter:
        if config is None:
            from outreach_pipeline.config import load_config
            config = load_config()
        from outreach_pipeline.hubspot_client import HubSpotClient
        client = HubSpotClient(config["hubspot_token"], config)
        return cls(client)

    def fetch_mqls(self, start_date: str, end_date: str) -> list[dict]:
        """Fetch MQLs from HubSpot for a date range. Returns raw contact dicts."""
        from outreach_pipeline.fetch_mqls import (
            build_filters, fetch_mqls, MQL_PROPERTIES, CONTACTS_SEARCH_URL,
        )
        config = self.client.config
        tz = config.get("report_timezone", "America/New_York")
        filter_groups = build_filters(start_date, end_date, tz)

        contacts = []
        after = None
        while True:
            payload = {
                "filterGroups": filter_groups,
                "properties": MQL_PROPERTIES,
                "limit": 100,
            }
            if after:
                payload["after"] = after
            response = self.client.request_with_retry(
                "POST", CONTACTS_SEARCH_URL, json=payload,
            )
            contacts.extend(response.get("results", []))
            after = response.get("paging", {}).get("next", {}).get("after")
            if not after:
                break
        return contacts

    def get_contact_emails(self, contact_id: str) -> list[dict]:
        """Fetch email engagements for a contact."""
        return self.client.get_contact_emails(contact_id)
