"""In-memory HubSpot stand-in for the local staging stack.

Enabled by `OUTREACH_MOCK_HUBSPOT=1`. Returns a small set of
deterministic fixture contacts so the `ingest_contacts` flow works
end-to-end without a real HubSpot token — useful for demos, the
`ui_flow_test`, and the pre-commit UI regression.

Fixture contacts use ids starting with `stg-fetch-` so they don't
collide with the `stg-c*` rows planted by `seed_staging.py`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from outreach_pipeline.organization import get_organization_profile


_PROFILE = get_organization_profile()
_PRIMARY_DOMAIN = _PROFILE.primary_domains[0] if _PROFILE.primary_domains else "example.com"
_WEBSITE = f"https://www.{_PRIMARY_DOMAIN}"

def _yesterday_ts() -> int:
    y = datetime.now(timezone.utc).replace(hour=14, minute=0, second=0, microsecond=0)
    y -= timedelta(days=1)
    return int(y.timestamp() * 1000)


def _iso_yesterday() -> str:
    y = datetime.now(timezone.utc) - timedelta(days=1)
    return y.isoformat()


# Fixture: three "fresh" MQLs that would have come from yesterday's
# HubSpot pull. Intentionally different personas + message lengths
# from the seeded fixtures so a full demo can show the ingest
# flow producing new rows alongside the existing ones.
_FIXTURE_CONTACTS: list[dict[str, Any]] = [
    {
        "id": "stg-fetch-1",
        "properties": {
            "company": "Example Industries",
            "firstname": "Anita",
            "lastname": "Rao",
            "email": "anita.rao@industries.example.com",
            "jobtitle": "Director of Manufacturing IT",
            "country": "United States",
            "region": "NORTH AMERICA",
            "industry": "industrial manufacturing",
            "message": "Expanding a plant campus — need backhaul between buildings.",
            "hs_v2_date_entered_marketingqualifiedlead": _iso_yesterday(),
            "createdate": _iso_yesterday(),
            "lifecyclestage": "marketingqualifiedlead",
            "hs_analytics_first_url": f"{_WEBSITE}/solutions/smart-campuses",
            "hs_analytics_num_page_views": "2",
        },
    },
    {
        "id": "stg-fetch-2",
        "properties": {
            "company": "Example Logistics",
            "firstname": "Kofi",
            "lastname": "Mensah",
            "email": "kmensah@logistics.example.com",
            "jobtitle": "VP Network Ops",
            "country": "Canada",
            "region": "NORTH AMERICA",
            "industry": "3PL logistics",
            "message": "Requesting a quote.",
            "hs_v2_date_entered_marketingqualifiedlead": _iso_yesterday(),
            "createdate": _iso_yesterday(),
            "lifecyclestage": "marketingqualifiedlead",
            "hs_analytics_first_url": f"{_WEBSITE}/products/radio-b100",
            "hs_analytics_num_page_views": "1",
        },
    },
    {
        "id": "stg-fetch-3",
        "properties": {
            "company": "Example Robotics Broadcasting",
            "firstname": "Helena",
            "lastname": "Kovač",
            "email": "h.kovac@broadcasting.example.com",
            "jobtitle": "Chief Broadcast Engineer",
            "country": "Slovenia",
            "region": "EMEA",
            "industry": "broadcast media",
            "message": (
                "We're upgrading our contribution network between two "
                "studios 14km apart (currently using leased fiber) and "
                "need sub-millisecond jitter for live feeds. What are "
                "our options for redundant high-cap links?"
            ),
            "hs_v2_date_entered_marketingqualifiedlead": _iso_yesterday(),
            "createdate": _iso_yesterday(),
            "lifecyclestage": "marketingqualifiedlead",
            "hs_analytics_first_url": f"{_WEBSITE}/solutions/broadcast",
            "hs_analytics_num_page_views": "4",
        },
    },
]


def _form_submission(contact_id: str, page_url: str, page_title: str, form_name: str) -> dict:
    return {
        "timestamp": _yesterday_ts(),
        "form-id": f"mock-form-{contact_id}",
        "form-type": "HUBSPOT",
        "title": form_name,
        "page-url": page_url,
        "page-title": page_title,
    }


# Profile-level data: per-contact `form-submissions` — the ingest
# loop uses these to route submissions into the DB.
_FIXTURE_PROFILES: dict[str, dict[str, Any]] = {
    "stg-fetch-1": {
        "form-submissions": [
            _form_submission(
                "stg-fetch-1",
                f"{_WEBSITE}/solutions/smart-campuses",
                "Smart Campuses Solution Brief",
                "Contact Us",
            ),
        ],
    },
    "stg-fetch-2": {
        "form-submissions": [
            _form_submission(
                "stg-fetch-2",
                f"{_WEBSITE}/products/radio-b100",
                "RADIO-B100 datasheet",
                "Request a Quote",
            ),
        ],
    },
    "stg-fetch-3": {
        "form-submissions": [
            _form_submission(
                "stg-fetch-3",
                f"{_WEBSITE}/resources/broadcast-case-study",
                "Case Study: Broadcast contribution over wireless",
                "Resource Download",
            ),
            _form_submission(
                "stg-fetch-3",
                f"{_WEBSITE}/solutions/broadcast",
                "Broadcast Solutions",
                "Contact Sales",
            ),
        ],
    },
}


class MockAsyncHubSpotClient:
    """Drop-in replacement for AsyncHubSpotClient used on staging.

    Only the methods the pipeline actually calls are implemented;
    anything else raises so we notice accidentally wider coupling.
    """

    def __init__(self) -> None:
        self.config: dict[str, Any] = {"report_timezone": "America/New_York"}

    async def asearch_contacts(
        self, filter_groups: list[dict], properties: list[str], limit: int = 100
    ) -> list[dict]:
        # Ignore filters — this fixture is "whatever yesterday looked
        # like" and we want it to show up on every ingest call.
        return _FIXTURE_CONTACTS[:limit]

    async def aget_contact_profile(self, contact_id: str) -> dict:
        return _FIXTURE_PROFILES.get(contact_id, {"form-submissions": []})

    async def aget_contact(self, contact_id: str, properties: list[str] | None = None) -> dict:
        # stg-fetch-* rows served by the ingest fixture above.
        for c in _FIXTURE_CONTACTS:
            if c["id"] == contact_id:
                return c
        # stg-c* rows come from scripts/seed_staging.py. PII is
        # stripped before it reaches contacts.properties, so the
        # PII-on-demand endpoint has nothing real to serve — we
        # synthesize a plausible fake so the UI round-trip is
        # demonstrable.
        _STG_C_PERSONAS = {
            "stg-c1": ("Patricia", "Nguyen"),
            "stg-c2": ("Marco", "Silva"),
            "stg-c3": ("Dana", "Okafor"),
            "stg-c4": ("Jin-Ho", "Park"),
            "stg-c5": ("Reese", "Tanaka"),
        }
        if contact_id in _STG_C_PERSONAS:
            first, last = _STG_C_PERSONAS[contact_id]
            return {
                "id": str(contact_id),
                "properties": {
                    "email": f"{first}.{last}@{contact_id}.example.com".lower(),
                    "firstname": first,
                    "lastname": last,
                    "jobtitle": "Test Persona",
                    "country": "United States",
                },
            }
        return {}

    async def aget_contact_emails(self, contact_id: str) -> list[dict]:
        return []

    async def arequest(self, method: str, url: str, **_kw) -> Any:
        # Any other HubSpot endpoint is a no-op in mock mode.
        return {"results": [], "properties": {}}
