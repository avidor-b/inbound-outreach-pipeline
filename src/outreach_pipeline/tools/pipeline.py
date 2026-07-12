"""Pipeline tools — stats, metrics, lead summaries, ingestion."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from outreach_pipeline.db.contacts import count_contacts, get_contacts


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def get_stats() -> dict[str, Any]:
    """Get pipeline summary statistics."""
    counts = await count_contacts()
    return counts


async def get_leads_summary(
    min_score: int = 0,
    date: str = "",
    after: str = "",
    before: str = "",
    no_reply: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    """Get a filtered lead summary using MQL dates."""
    leads = await get_contacts(
        min_score=min_score,
        date=date or None, after=after or None, before=before or None,
        no_reply=no_reply, limit=limit,
    )
    return {"leads": leads, "count": len(leads)}


async def ingest_contacts(
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    """Fetch MQLs from HubSpot, save contacts, classify, route to engagements.

    Fully async: shared HubSpot client → upsert_contact → process_submission.
    """
    from outreach_pipeline.fetch_mqls import build_filters, MQL_PROPERTIES
    from outreach_pipeline.hubspot_client import get_async_hubspot
    from outreach_pipeline.db.contacts import upsert_contact
    from outreach_pipeline.services.intake import process_submission
    from outreach_pipeline.tools.date_ranges import resolve_date_range

    # Async HubSpot fetch
    client = get_async_hubspot()
    tz = client.config.get("report_timezone", "America/New_York")
    start_date, end_date = resolve_date_range(
        start_date,
        end_date,
        default="yesterday",
        now=_now(),
        timezone_name=tz,
    )
    filter_groups = build_filters(start_date, end_date, tz)
    contacts = await client.asearch_contacts(filter_groups, MQL_PROPERTIES)

    # Save contacts via async Postgres upsert
    for contact in contacts:
        await upsert_contact(contact["id"], contact.get("properties", {}))

    created = blocked = skipped = subs_found = 0
    contacts_processed = []

    # Process each contact. Prefer form-submissions as the source of
    # truth (they have form_id + page_url), attaching the contact-level
    # `message` property to the MOST RECENT form submission — HubSpot
    # stores only the latest message text, so older submissions stay
    # messageless. If no form submissions exist, fall back to a single
    # "Contact Us" row so the contact is still in the DB.
    for contact in contacts:
        props = contact.get("properties", {})
        cid = contact["id"]
        contact_msg = props.get("message", "")
        form_subs: list[dict] = []
        try:
            profile = await client.aget_contact_profile(cid)
            form_subs = profile.get("form-submissions", []) or []
        except Exception:
            form_subs = []

        r: dict = {}
        if form_subs:
            sorted_subs = sorted(
                form_subs, key=lambda fs: fs.get("timestamp", 0), reverse=True,
            )
            for i, fs in enumerate(sorted_subs):
                ts = fs.get("timestamp", 0)
                sub_date = (
                    datetime.fromtimestamp(ts / 1000).isoformat() if ts else ""
                )
                one_r = await process_submission(
                    cid,
                    # Contact-level message belongs to the most recent
                    # form submission — that's the one that made them
                    # the current MQL. Older submissions we can't
                    # recover message text for.
                    message=contact_msg if i == 0 else "",
                    form_name=fs.get("title", ""),
                    form_id=fs.get("form-id", ""),
                    page_url=fs.get("page-url", ""),
                    mql_date=sub_date,
                    topic=props.get("topic", ""),
                )
                subs_found += 1
                if i == 0:
                    r = one_r  # most-recent drives the engagement status
        else:
            # Prefer the deployment's stable MQL date over HubSpot's
            # lifecycle timestamp, which can change when automations
            # briefly move a contact between lifecycle stages.
            mql_date = (
                props.get("mql_date")
                or props.get("hs_v2_date_entered_marketingqualifiedlead")
                or ""
            )
            r = await process_submission(
                cid,
                message=contact_msg,
                form_name="Contact Us",
                mql_date=mql_date,
                topic=props.get("topic", ""),
            )

        contacts_processed.append({
            "contact_id": str(cid),
            "company": props.get("company", ""),
            "engagement_id": r.get("engagement_id"),
            "duplicate": r.get("duplicate", False),
            "blocked": r.get("blocked_reason", ""),
        })
        if r.get("duplicate"):
            skipped += 1
        elif r.get("engagement_id"):
            created += 1
        elif r.get("blocked_reason"):
            blocked += 1

    return {
        "success": True,
        "fetched": len(contacts),
        "saved": len(contacts),
        "engagements_created": created,
        "blocked": blocked,
        "duplicates_skipped": skipped,
        "form_submissions_captured": subs_found,
        "date_range": f"{start_date} to {end_date}",
        "contacts": contacts_processed,
    }
