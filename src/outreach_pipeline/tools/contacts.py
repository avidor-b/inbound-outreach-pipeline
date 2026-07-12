"""Contact tools — fetch, list, inspect, check policy.

Thin wrappers calling adapters and db functions.
"""
from __future__ import annotations

from typing import Any

from outreach_pipeline.db.contacts import get_contact, get_contacts


async def get_leads(
    min_score: int = 0,
    date: str = "",
    after: str = "",
    before: str = "",
    today_only: bool = False,
    unenriched: bool = False,
    unscored: bool = False,
    no_reply: bool = False,
    has_draft: bool | None = None,
    limit: int = 50,
    # Internal: filter by pipeline fetch date, not MQL date
    fetched_date: str = "",
    fetched_after: str = "",
    fetched_before: str = "",
) -> dict[str, Any]:
    """Get leads from the pipeline database.

    Date filters use the MQL date (when the contact entered HubSpot).
    """
    contacts = await get_contacts(
        min_score=min_score, today_only=today_only,
        unenriched=unenriched, unscored=unscored,
        date=date or None, after=after or None, before=before or None,
        fetched_date=fetched_date or None,
        fetched_after=fetched_after or None,
        fetched_before=fetched_before or None,
        no_reply=no_reply, has_draft=has_draft,
        limit=limit,
    )
    return {"leads": contacts, "count": len(contacts)}


async def get_lead_details(
    contact_id: str,
) -> dict[str, Any]:
    """Get full details for a single contact.

    Augments the stored contact row with PII-on-demand fields
    (jobtitle, country) pulled fresh from HubSpot so the UI can show
    a complete profile without persisting PII locally. Region is
    already stored under properties.region.
    """
    contact = await get_contact(contact_id)
    if not contact:
        return {"contact": None}

    pii: dict[str, Any] = {}
    try:
        from outreach_pipeline.tools.pii import afetch_contact_pii
        pii = await afetch_contact_pii(str(contact_id), ["jobtitle", "country"])
    except Exception:
        pii = {}

    props = contact.setdefault("properties", {}) or {}
    for k, v in pii.items():
        if v and not props.get(k):
            props[k] = v
    return {"contact": contact}


async def preview_delete_contact(
    contact_id: str,
) -> dict[str, Any]:
    """Preview what would be deleted for a contact. Does NOT delete anything.

    Returns contact info and counts of all associated data that would be removed.
    """
    from outreach_pipeline.db.submissions import list_submissions
    from outreach_pipeline.db.engagements import list_engagements
    from outreach_pipeline.db.pool import get_pool

    contact = await get_contact(contact_id)
    if not contact:
        return {"found": False, "contact_id": contact_id}

    props = contact.get("properties", {})
    submissions = await list_submissions(contact_id=contact_id, include_migration=True)
    engagements = await list_engagements(contact_id=contact_id)

    pool = get_pool()
    draft_row = await pool.fetchrow(
        "SELECT COUNT(*) as cnt FROM drafts WHERE contact_id=$1", str(contact_id),
    )
    draft_count = draft_row["cnt"]
    reply_row = await pool.fetchrow(
        "SELECT COUNT(*) as cnt FROM replies WHERE contact_id=$1", str(contact_id),
    )
    reply_count = reply_row["cnt"]
    opt_row = await pool.fetchrow(
        "SELECT COUNT(*) as cnt FROM do_not_contact WHERE contact_id=$1", str(contact_id),
    )
    opt_out = opt_row["cnt"]

    return {
        "found": True,
        "contact_id": contact_id,
        "company": props.get("company", ""),
        "score": contact.get("score", 0),
        "enriched": bool(contact.get("enriched_at")),
        "will_delete": {
            "submissions": len(submissions),
            "submission_details": [
                {"id": s["id"], "intent_type": s.get("intent_type", ""),
                 "form_name": s.get("form_name", ""), "mql_date": s.get("mql_date", "")}
                for s in submissions
            ],
            "engagements": len(engagements),
            "engagement_details": [
                {"id": e["id"], "kind": e.get("kind", ""), "status": e.get("status", ""),
                 "score": e.get("score", 0)}
                for e in engagements
            ],
            "drafts": draft_count,
            "replies": reply_count,
            "opt_out_entries": opt_out,
        },
    }


async def delete_contact(
    contact_id: str,
) -> dict[str, Any]:
    """Delete a contact and all associated data. Destructive, cannot be undone.

    Always call preview_delete_contact first to show the user what will be deleted.
    Only call this after the user has seen the preview and confirmed.
    """
    from outreach_pipeline.db.pool import get_pool

    contact = await get_contact(contact_id)
    if not contact:
        return {"success": False, "error": f"Contact {contact_id} not found"}

    pool = get_pool()

    # Delete in dependency order: children before parents.
    # drafts → engagement_submissions → engagements → submissions → contacts
    draft_row = await pool.fetchrow(
        "SELECT COUNT(*) as cnt FROM drafts WHERE contact_id=$1", str(contact_id),
    )
    draft_count = draft_row["cnt"]
    await pool.execute("DELETE FROM drafts WHERE contact_id=$1", str(contact_id))
    await pool.execute("DELETE FROM replies WHERE contact_id=$1", str(contact_id))
    await pool.execute(
        """DELETE FROM engagement_submissions WHERE engagement_id IN
           (SELECT id FROM engagements WHERE contact_id=$1)""",
        str(contact_id),
    )
    eng_row = await pool.fetchrow(
        "SELECT COUNT(*) as cnt FROM engagements WHERE contact_id=$1", str(contact_id),
    )
    eng_count = eng_row["cnt"]
    await pool.execute("DELETE FROM engagements WHERE contact_id=$1", str(contact_id))
    sub_row = await pool.fetchrow(
        "SELECT COUNT(*) as cnt FROM submissions WHERE contact_id=$1", str(contact_id),
    )
    sub_count = sub_row["cnt"]
    await pool.execute("DELETE FROM submissions WHERE contact_id=$1", str(contact_id))
    await pool.execute("DELETE FROM do_not_contact WHERE contact_id=$1", str(contact_id))
    await pool.execute("DELETE FROM contacts WHERE id=$1", str(contact_id))

    return {
        "success": True,
        "contact_id": contact_id,
        "deleted": {
            "submissions": sub_count,
            "engagements": eng_count,
            "drafts": draft_count,
        },
    }
