"""Submission intake — classify, deduplicate, and route to engagements.

This is the entry point for new form submissions. It:
1. Deduplicates
2. Stores the submission
3. Classifies intent and product family
4. Routes to an existing or new engagement
"""
from __future__ import annotations

from typing import Any

from outreach_pipeline.db.submissions import (
    create_submission, classify_submission, is_duplicate, list_submissions,
)
from outreach_pipeline.db.engagements import (
    create_engagement, add_submission, list_engagements,
    close_superseded_retargets,
)
from outreach_pipeline.db.guardrails import check_opt_out
from outreach_pipeline.policies.submission import classify_intent, classify_product_family


async def process_submission(
    contact_id: str,
    message: str = "",
    form_name: str = "",
    form_id: str = "",
    page_url: str = "",
    page_title: str = "",
    mql_date: str = "",
    properties_snapshot: dict | None = None,
    source: str = "hubspot",
    topic: str = "",
) -> dict[str, Any]:
    """Process a new submission through the full intake pipeline.

    Returns dict with: submission_id, engagement_id, duplicate, blocked_reason.
    """
    # 0. Reject empty submissions — nothing meaningful to store
    has_message = bool(message and message.strip())
    has_form = bool(form_name or form_id)
    has_url = bool(page_url)
    if not has_message and not has_form and not has_url:
        return {"submission_id": None, "engagement_id": None, "skipped": True,
                "reason": "No message, form, or URL — nothing to store"}

    # 1. Redact PII from message before storing or classifying
    from outreach_pipeline.db.contacts import _redact_message_pii
    if message:
        message = _redact_message_pii(message)

    # 2. Dedup check (uses redacted message for hashing)
    if await is_duplicate(contact_id, message=message, form_id=form_id, mql_date=mql_date):
        return {"submission_id": None, "engagement_id": None, "duplicate": True}

    # 3. Translate non-English messages once at ingestion so the UI,
    # drafter, and classifier all see the English version without
    # re-invoking the LLM per read. Blank when the original is
    # already English or translation failed — callers render the
    # original when this is empty.
    message_translation = ""
    if message and message.strip():
        from outreach_pipeline.policies.submission import translate_to_english
        try:
            message_translation = translate_to_english(message)
        except Exception:
            message_translation = ""

    # 3. Store submission (always, even for opted-out contacts)
    sid = await create_submission(
        contact_id, message=message, form_name=form_name,
        form_id=form_id, page_url=page_url, page_title=page_title,
        mql_date=mql_date, properties_snapshot=properties_snapshot,
        source=source, message_translation=message_translation,
    )

    # 3. Classify. `topic` is the HubSpot form-dropdown selection —
    # when set to Partnerships / Support / Careers / Investor
    # relations, that wins over the regex heuristics.
    intent = classify_intent(message, form_name, page_url, topic=topic)
    product_family = classify_product_family(message, page_url)
    await classify_submission(
        sid,
        intent_type=intent["intent_type"],
        product_family=product_family,
        support_flag=intent["support_flag"],
        event_flag=intent["event_flag"],
    )

    # 4. Check opt-out (blocks engagement creation, not ingestion)
    blocked, reason = await check_opt_out(contact_id)
    if blocked:
        return {
            "submission_id": sid, "engagement_id": None,
            "blocked_reason": "opted_out",
        }

    # 4b. Check if contact has an email domain — can't outreach without one
    from outreach_pipeline.db.contacts import get_contact
    contact = await get_contact(contact_id)
    props = (contact.get("properties") or {}) if contact else {}
    has_email = props.get("has_email") or props.get("email_domain")
    if not has_email:
        eid = await create_engagement(
            contact_id, kind="sales",
            status="blocked", block_code="NO_EMAIL",
            block_reason="No email domain — cannot contact",
            block_terminal=True,
        )
        await add_submission(eid, sid)
        return {"submission_id": sid, "engagement_id": eid, "blocked_reason": "no_email"}

    # 5. Close any open retargets (new submission supersedes them)
    await close_superseded_retargets(contact_id)

    # 6. Route to engagement
    engagement_id = await _route_to_engagement(
        contact_id, intent["intent_type"], product_family, sid,
    )

    return {"submission_id": sid, "engagement_id": engagement_id}


async def _route_to_engagement(
    contact_id: str, intent_type: str, product_family: str,
    submission_id: int,
) -> int | None:
    """Route a classified submission to an engagement."""

    # Content downloads don't create engagements
    if intent_type == "content_download":
        return None

    # Empty sales inquiries (no message, e.g. bare contact-us form hit) →
    # attach to existing blocked engagement if one exists, otherwise block as NO_MESSAGE.
    # These have no actionable content to score or draft against.
    from outreach_pipeline.db.submissions import get_submission
    sub = await get_submission(submission_id)
    sub_message = (sub.get("message", "") or "").strip() if sub else ""
    if intent_type == "sales_inquiry" and not sub_message:
        # Try to attach to existing active sales engagement first
        for status in ("open", "scoring", "drafting"):
            active_engs = await list_engagements(contact_id=contact_id, status=status)
            for eng in active_engs:
                if eng["kind"] in ("sales", "content_followup"):
                    await add_submission(eng["id"], submission_id)
                    return eng["id"]
        # Attach to ANY existing blocked engagement — an empty
        # lead-capture fill adds no new actionable signal, so we
        # don't need a separate blocked record.
        blocked_engs = await list_engagements(contact_id=contact_id, status="blocked")
        if blocked_engs:
            await add_submission(blocked_engs[0]["id"], submission_id)
            return blocked_engs[0]["id"]
        # No existing engagement — block as no_message
        eid = await create_engagement(
            contact_id, kind="sales",
            status="blocked", block_code="NO_MESSAGE",
            block_reason="Empty form submission — no message to act on",
            block_terminal=True,
        )
        await add_submission(eid, submission_id)
        return eid

    # Support → terminal blocked engagement
    if intent_type == "support_request":
        eid = await create_engagement(
            contact_id, kind="support",
            status="blocked", block_code="SUPPORT_REQUEST",
            block_reason="Route to support team",
            block_terminal=True, primary_product=product_family,
        )
        await add_submission(eid, submission_id)
        return eid

    # Events → terminal blocked engagement
    if intent_type == "event_meeting":
        eid = await create_engagement(
            contact_id, kind="events",
            status="blocked", block_code="EVENT_BLOCK",
            block_reason="Route to events team",
            block_terminal=True,
        )
        await add_submission(eid, submission_id)
        return eid

    # Vendor pitch → blocked (supplier selling TO us)
    if intent_type == "vendor_pitch":
        eid = await create_engagement(
            contact_id, kind="vendor",
            status="blocked", block_code="VENDOR_PITCH",
            block_reason="Vendor/supplier pitch — selling to us, not buying from us",
            block_terminal=True,
        )
        await add_submission(eid, submission_id)
        return eid

    # Off-topic → blocked (outside the configured offerings)
    if intent_type == "off_topic":
        eid = await create_engagement(
            contact_id, kind="sales",
            status="blocked", block_code="OFF_TOPIC",
            block_reason="Inquiry is outside the configured offerings",
            block_terminal=True,
        )
        await add_submission(eid, submission_id)
        return eid

    # Sales inquiry → find or create engagement
    return await _route_sales(contact_id, product_family, submission_id)


async def _route_sales(
    contact_id: str, product_family: str, submission_id: int,
) -> int:
    """Route a sales inquiry to an existing or new engagement."""
    # Check for open sales/content_followup engagements
    open_engs = await list_engagements(contact_id=contact_id, status="open")
    open_engs += await list_engagements(contact_id=contact_id, status="scoring")
    open_engs += await list_engagements(contact_id=contact_id, status="drafting")

    sales_engs = [e for e in open_engs if e["kind"] in ("sales", "content_followup")]

    for eng in sales_engs:
        # Same product family or general → join existing
        if eng["primary_product"] in (product_family, "general", "") or product_family == "general":
            await add_submission(eng["id"], submission_id)
            return eng["id"]

    # No matching open engagement → check for content_followup promotion
    kind = await _check_content_followup(contact_id, product_family)

    eid = await create_engagement(
        contact_id, kind=kind, primary_product=product_family,
    )
    await add_submission(eid, submission_id)
    return eid


async def _check_content_followup(contact_id: str, product_family: str) -> str:
    """Check if this sales inquiry should be a content_followup.

    Returns 'content_followup' if prior content download exists for same
    product family within 30 days, otherwise 'sales'.
    """
    subs = await list_submissions(contact_id=contact_id, include_migration=True)
    for sub in subs:
        if sub["intent_type"] == "content_download" and sub["product_family"] == product_family:
            # Check if within 30 days (simplified: just check if it exists)
            # In production, compare mql_date timestamps
            return "content_followup"

    return "sales"
