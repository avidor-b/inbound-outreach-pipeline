"""Data migration — convert existing contacts to the 3-layer model.

Migrates: contact with message → 1 submission (source=migration) + 1 engagement.
Contacts without messages are skipped.
Idempotent: re-running skips already-migrated contacts.
"""
from __future__ import annotations

from typing import Any

from outreach_pipeline.db.contacts import get_contact, get_contacts
from outreach_pipeline.db.submissions import create_submission, list_submissions, is_duplicate
from outreach_pipeline.db.engagements import (
    create_engagement, add_submission, score_engagement,
    list_engagements,
)


async def migrate_contact(contact_id: str) -> dict[str, Any]:
    """Migrate a single contact to the 3-layer model.

    Creates a submission (source=migration) and an engagement from
    the contact's existing score/intent data.

    Returns: {migrated: bool, submission_id, engagement_id}
    """
    contact = await get_contact(contact_id)
    if not contact:
        return {"migrated": False, "submission_id": None, "engagement_id": None}

    props = contact.get("properties", {})
    message = props.get("message", "")

    if not message or not message.strip():
        return {"migrated": False, "submission_id": None, "engagement_id": None}

    # Check if already migrated (has a migration submission)
    existing = await list_submissions(contact_id=contact_id, include_migration=True)
    migration_subs = [s for s in existing if s.get("source") == "migration"]
    if migration_subs:
        return {"migrated": False, "submission_id": None, "engagement_id": None}

    # Create submission from existing data
    mql_date = props.get("hs_v2_date_entered_marketingqualifiedlead", "")
    sid = await create_submission(
        contact_id,
        message=message,
        form_name="Contact Us (migrated)",
        mql_date=mql_date or contact.get("fetched_at", ""),
        properties_snapshot=props,
        source="migration",
    )

    # Determine engagement kind from existing data
    buying_intent = contact.get("buying_intent", "")
    if buying_intent == "support_request":
        kind = "support"
        status = "blocked"
        block_code = "SUPPORT_REQUEST"
        block_terminal = True
    elif buying_intent == "event_meeting_request":
        kind = "events"
        status = "blocked"
        block_code = "EVENT_BLOCK"
        block_terminal = True
    else:
        kind = "sales"
        status = "open"
        block_code = ""
        block_terminal = False

    # Create engagement
    eid = await create_engagement(
        contact_id, kind=kind, status=status,
        block_code=block_code, block_terminal=block_terminal,
        primary_product=contact.get("product_match", ""),
    )
    await add_submission(eid, sid)

    # Carry over score
    score = contact.get("score", 0)
    score_reasoning = contact.get("score_reasoning", "")
    product_match = contact.get("product_match", "")
    if score > 0:
        await score_engagement(eid, score, score_reasoning, product_match)

    return {"migrated": True, "submission_id": sid, "engagement_id": eid}


async def migrate_all_contacts() -> dict[str, Any]:
    """Migrate all existing contacts to the 3-layer model.

    Returns: {total, migrated, skipped}
    """
    contacts = await get_contacts(limit=10000)
    total = len(contacts)
    migrated = 0
    skipped = 0

    for contact in contacts:
        result = await migrate_contact(contact["id"])
        if result["migrated"]:
            migrated += 1
        else:
            skipped += 1

    return {"total": total, "migrated": migrated, "skipped": skipped}
