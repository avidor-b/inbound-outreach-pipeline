#!/usr/bin/env python3
"""Fetch MQLs with populated message field between dates."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pytz

from outreach_pipeline.hubspot_client import HubSpotClient
from outreach_pipeline.config import load_config

CONTACTS_SEARCH_URL = "https://api.hubapi.com/crm/v3/objects/contacts/search"

MQL_PROPERTIES = [
    "message", "topic", "lifecyclestage", "createdate", "jobtitle",
    "firstname", "email",
    "company", "industry", "region",
    "hs_analytics_source", "hs_analytics_source_data_1", "hs_analytics_source_data_2",
    "hs_analytics_first_url", "hs_analytics_last_url", "hs_analytics_num_page_views",
    "hs_analytics_first_touch_converting_campaign", "recent_conversion_event_name",
    "num_contacted_notes", "notes_last_contacted",
    "hs_email_open", "hs_email_click",
    "hs_analytics_first_visit_timestamp", "hs_analytics_last_visit_timestamp",
    # Generic CRM MQL metadata. Deployments can map these neutral public
    # names to their own source-system properties outside the repo.
    "mql_date",
    "lead_status",
    "mql_type",
    "crm_lead_id",
    "hs_v2_date_entered_marketingqualifiedlead",
]


def parse_args(args=None):
    parser = argparse.ArgumentParser(description="Fetch MQLs with message field between dates")
    parser.add_argument("--start-date", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", required=True, help="End date (YYYY-MM-DD)")
    return parser.parse_args(args)


def build_filters(start_date, end_date, timezone):
    """Build HubSpot search filter groups for MQLs in date range.

    Returns a single-group filter (AND across all conditions):
      - mql_date BETWEEN start..end
      - lead_status = "MQL"
      - message HAS_PROPERTY               (we need free-text to draft
        against; newsletter-click MQLs without a reply are out.)
      - mql_source NOT IN (event/webinar values) — outreach isn't
        the right channel for event attendees; they're handled by the
        events team.

    Update _OFFLINE_OR_EVENT_SOURCES when your CRM introduces another
    offline event/webinar source category.
    """
    tz = pytz.timezone(timezone)
    start_dt = tz.localize(datetime.strptime(start_date, "%Y-%m-%d"))
    end_dt = tz.localize(datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59))

    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    return [
        {"filters": [
            {"propertyName": "mql_date", "operator": "BETWEEN",
             "value": str(start_ms), "highValue": str(end_ms)},
            {"propertyName": "lead_status", "operator": "EQ", "value": "MQL"},
            {"propertyName": "message", "operator": "HAS_PROPERTY"},
            {"propertyName": "mql_source", "operator": "NOT_IN",
             "values": list(_OFFLINE_OR_EVENT_SOURCES)},
        ]},
    ]


# mql_source values that aren't outreach-eligible — event/webinar
# contacts are handled by other teams. Keep this tight; empty /
# miscellaneous values (e.g. "High Behavior score") should still flow
# through so a real sales-intent MQL without a standard source tag
# doesn't get silently dropped.
_OFFLINE_OR_EVENT_SOURCES = (
    "Event / Tradeshow",
    "Attended webinar",
)


def fetch_mqls(start_date, end_date):
    """Fetch all MQLs with message field between dates."""
    config = load_config()
    client = HubSpotClient(config["hubspot_token"], config)
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

        response = client.request_with_retry("POST", CONTACTS_SEARCH_URL, json=payload)
        contacts.extend(response.get("results", []))

        paging = response.get("paging", {})
        after = paging.get("next", {}).get("after")
        if not after:
            break

    return {
        "contacts": contacts,
        "total": len(contacts),
        "start_date": start_date,
        "end_date": end_date,
    }


async def save_to_db(result, db_path=None):
    """Save fetched contacts to async Postgres.

    ``db_path`` is accepted for old callers but ignored; the runtime data
    layer is asyncpg-backed Postgres.
    """
    from outreach_pipeline.db.pool import init_pool
    from outreach_pipeline.db.contacts import upsert_contact

    await init_pool()
    for contact in result.get("contacts", []):
        await upsert_contact(contact["id"], contact.get("properties", {}))
    return len(result.get("contacts", []))


def main():
    import asyncio
    args = parse_args()
    result = fetch_mqls(args.start_date, args.end_date)
    count = asyncio.run(save_to_db(result))
    print(json.dumps({"total": result["total"], "saved_to_db": count}, indent=2))


if __name__ == "__main__":
    main()
