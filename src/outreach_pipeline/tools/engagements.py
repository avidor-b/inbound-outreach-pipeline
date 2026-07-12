"""Engagement-aware tools — the primary tool surface for the 3-layer model.

These are pure Python functions, framework-agnostic. MCP server wraps them,
future LangGraph/PydanticAI agents will wrap them the same way.
"""
from __future__ import annotations

from typing import Any

from outreach_pipeline.db.contacts import get_contact
from outreach_pipeline.db.engagements import (
    get_engagement as _get_engagement,
    list_engagements as _list_engagements,
    get_submissions as _get_engagement_submissions,
    create_engagement as _create_engagement,
    update_status as _update_status,
    score_engagement as _score_engagement,
    close_superseded_retargets,
)
from outreach_pipeline.db.submissions import (
    list_submissions as _list_submissions,
    classify_submission as _classify_submission,
)


async def _sync_draft_status(engagement_id: int, new_status: str):
    """Sync the latest draft's status when engagement is approved or sent.

    Only updates the most recent draft for the engagement.
    """
    from outreach_pipeline.db.pool import get_pool, now
    pool = get_pool()
    if new_status == "approved":
        await pool.execute(
            """UPDATE drafts SET status='approved'
               WHERE id = (SELECT id FROM drafts WHERE engagement_id=$1
                           ORDER BY created_at DESC LIMIT 1)""",
            engagement_id,
        )
    elif new_status == "sent":
        await pool.execute(
            """UPDATE drafts SET status='sent', sent_at=$1
               WHERE id = (SELECT id FROM drafts WHERE engagement_id=$2
                           ORDER BY created_at DESC LIMIT 1)""",
            now(), engagement_id,
        )


async def get_engagements(
    kind: str = "",
    status: str = "",
    contact_id: str = "",
    min_score: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """Get engagements with optional filters."""
    engs = await _list_engagements(
        kind=kind or None,
        status=status or None,
        contact_id=contact_id or None,
        limit=limit,
    )
    if min_score > 0:
        engs = [e for e in engs if e.get("score", 0) >= min_score]

    # Lift company, industry, region onto each row so the Engagements
    # table can render them without the caller doing a second fetch.
    cids = list({e.get("contact_id") for e in engs if e.get("contact_id")})
    if cids:
        from outreach_pipeline.db.pool import get_pool
        pool = get_pool()
        rows = await pool.fetch(
            "SELECT id, properties FROM contacts WHERE id = ANY($1::text[])",
            cids,
        )
        import json
        cmap: dict[str, dict] = {}
        for r in rows:
            p = r["properties"] or {}
            if isinstance(p, str):
                try:
                    p = json.loads(p)
                except Exception:
                    p = {}
            cmap[r["id"]] = p
        for e in engs:
            p = cmap.get(e.get("contact_id"), {})
            e.setdefault("company", p.get("company", ""))
            e.setdefault("industry", p.get("industry", ""))
            e.setdefault("region", p.get("region", ""))

    return {"engagements": engs, "count": len(engs)}


async def get_engagement_details(
    engagement_id: int,
) -> dict[str, Any]:
    """Get full context for an engagement: engagement + contact + submissions."""
    eng = await _get_engagement(engagement_id)
    if not eng:
        return {"engagement": None, "contact": None, "submissions": []}

    contact = await get_contact(eng["contact_id"])
    submissions = await _get_engagement_submissions(engagement_id)
    return {
        "engagement": eng,
        "contact": contact,
        "submissions": submissions,
    }


async def get_contact_history(
    contact_id: str,
) -> dict[str, Any]:
    """Full picture: contact + all submissions + all engagements."""
    contact = await get_contact(contact_id)
    submissions = await _list_submissions(contact_id=contact_id, include_migration=True)
    engagements = await _list_engagements(contact_id=contact_id)
    return {
        "contact": contact,
        "submissions": submissions,
        "engagements": engagements,
    }


async def get_submissions(
    contact_id: str,
    include_migration: bool = False,
) -> dict[str, Any]:
    """Get submission history for a contact."""
    subs = await _list_submissions(contact_id=contact_id, include_migration=include_migration)
    return {"submissions": subs, "count": len(subs)}


async def update_engagement_status(
    engagement_id: int,
    new_status: str,
    block_code: str = "",
    block_reason: str = "",
) -> dict[str, Any]:
    """Update engagement status with transition validation."""
    try:
        await _update_status(engagement_id, new_status, block_code, block_reason)

        # Sync draft status with engagement status
        if new_status in ("approved", "sent"):
            await _sync_draft_status(engagement_id, new_status)

        eng = await _get_engagement(engagement_id)
        return {"success": True, "engagement": eng}
    except ValueError as e:
        return {"success": False, "error": str(e)}


async def create_retarget(
    original_engagement_id: int,
) -> dict[str, Any]:
    """Create a retarget engagement for a non-replied sent engagement.

    Only one retarget per original. Original must be in 'sent' or 'closed' status.
    """
    original = await _get_engagement(original_engagement_id)
    if not original:
        return {"success": False, "error": f"Engagement {original_engagement_id} not found"}

    if original["status"] not in ("sent", "closed"):
        return {"success": False, "error": f"Original status is '{original['status']}', must be 'sent' or 'closed'"}

    try:
        eid = await _create_engagement(
            original["contact_id"],
            kind="retarget",
            retarget_of=original_engagement_id,
            primary_product=original.get("primary_product", ""),
        )
        # Link original submissions for context
        original_subs = await _get_engagement_submissions(original_engagement_id)
        from outreach_pipeline.db.engagements import add_submission
        for sub in original_subs:
            try:
                await add_submission(eid, sub["id"])
            except Exception:
                pass
        return {"success": True, "engagement_id": eid}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def reclassify_submission(
    submission_id: int,
    intent_type: str = "",
    product_family: str = "",
    support_flag: bool | None = None,
    event_flag: bool | None = None,
) -> dict[str, Any]:
    """Manually reclassify a submission. Only fields explicitly provided
    are updated; previously-classified values (product_match,
    intent_signals, classification_reasoning) are preserved so a
    partial correction doesn't clobber the original LLM reasoning."""
    from outreach_pipeline.db.pool import get_pool, now

    sets: list[str] = []
    params: list[Any] = []
    idx = 1
    if intent_type:
        sets.append(f"intent_type = ${idx}")
        params.append(intent_type)
        idx += 1
    if product_family:
        sets.append(f"product_family = ${idx}")
        params.append(product_family)
        idx += 1
    if support_flag is not None:
        sets.append(f"support_flag = ${idx}")
        params.append(support_flag)
        idx += 1
    if event_flag is not None:
        sets.append(f"event_flag = ${idx}")
        params.append(event_flag)
        idx += 1
    if not sets:
        return {"success": False, "error": "No fields provided to reclassify"}

    sets.append(f"classified_at = ${idx}")
    params.append(now())
    idx += 1
    params.append(submission_id)

    pool = get_pool()
    result = await pool.execute(
        f"UPDATE submissions SET {', '.join(sets)} WHERE id = ${idx}",
        *params,
    )
    # asyncpg returns "UPDATE <rowcount>"; 0 means the id didn't match.
    if result.split()[-1] == "0":
        return {"success": False, "error": f"Submission {submission_id} not found"}
    return {"success": True, "submission_id": submission_id}


async def score_engagement(
    engagement_id: int,
    score: int,
    score_reasoning: str = "",
    primary_product: str = "",
    buying_intent: str = "",
    intent_signals: list[str] | None = None,
    score_confidence: str = "",
) -> dict[str, Any]:
    """Save a score on an engagement and update the contact's scoring fields.

    buying_intent: 'low', 'medium', or 'high'.
    intent_signals: list of observed signals (e.g., ['pricing_inquiry', 'existing_customer']).
    score_confidence: 'high', 'medium', or 'low'.
    """
    from outreach_pipeline.services.scoring import save_engagement_score
    from outreach_pipeline.db.engagements import get_engagement as _get_eng, update_status
    from outreach_pipeline.db.contacts import score_contact

    eng = await _get_eng(engagement_id)
    if not eng:
        return {"success": False, "error": f"Engagement {engagement_id} not found"}

    # Save score on engagement
    await save_engagement_score(engagement_id, score, score_reasoning, primary_product)

    # Save confidence
    if score_confidence:
        try:
            from outreach_pipeline.db.pool import get_pool
            pool = get_pool()
            await pool.execute(
                "UPDATE engagements SET score_confidence=$1 WHERE id=$2",
                score_confidence, engagement_id,
            )
        except Exception:
            pass  # column might not exist yet

    # Advance status based on score
    status = eng["status"]
    try:
        if status == "open":
            await update_status(engagement_id, "scoring")
            status = "scoring"
        if status == "scoring":
            if score >= 40:
                # High enough to draft — advance.
                await update_status(engagement_id, "drafting")
            elif score <= 25:
                # Too low (support/events/no fit) — close
                await update_status(engagement_id, "closed")
            # 26-39: stays in "scoring" — agent decides next step
    except ValueError:
        pass

    # Update contact-level scoring fields
    await score_contact(
        eng["contact_id"],
        buying_intent=buying_intent or "medium",
        intent_signals=intent_signals or [],
        score=score,
        score_reasoning=score_reasoning,
        product_match=primary_product,
    )

    # Re-read to get final status
    eng_after = await _get_eng(engagement_id)
    return {
        "success": True,
        "engagement_id": engagement_id,
        "score": score,
        "buying_intent": buying_intent,
        "intent_signals": intent_signals or [],
        "status": eng_after["status"] if eng_after else "unknown",
        "status_note": (
            "Advanced to drafting (score >= 40)" if eng_after and eng_after["status"] == "drafting"
            else "Closed (score <= 25)" if eng_after and eng_after["status"] == "closed"
            else "Stays in scoring (score 26-39) — decide next step" if eng_after and eng_after["status"] == "scoring"
            else ""
        ),
    }


async def save_enrichment(
    contact_id: str,
    company_context: str,
    crm_context: str = "",
    enrichment_confidence: str = "",
) -> dict[str, Any]:
    """Save enrichment data (company context) for a contact."""
    from outreach_pipeline.services.enrichment import save_enrichment as _save
    await _save(contact_id, company_context, crm_context, enrichment_confidence)
    return {"success": True, "contact_id": contact_id}


async def check_engagement_policy(
    engagement_id: int,
) -> dict[str, Any]:
    """Check all policies for an engagement's contact."""
    from outreach_pipeline.db.engagements import get_engagement as _get_engagement
    from outreach_pipeline.policies.contact import check_contact_policies
    eng = await _get_engagement(engagement_id)
    if not eng:
        return {"engagement_id": engagement_id, "found": False}
    decision = await check_contact_policies(eng["contact_id"])
    return {
        "engagement_id": engagement_id,
        "found": True,
        "allowed": decision.allowed,
        "code": decision.code,
        "reason": decision.reason,
    }


async def add_opt_out(
    contact_id: str,
    reason: str = "Requested removal",
) -> dict[str, Any]:
    """Add a contact to the do-not-contact list."""
    from outreach_pipeline.db.guardrails import add_opt_out as _add
    success = await _add(contact_id, reason)
    return {"success": success, "contact_id": contact_id}


async def add_scrape_block(
    domain: str,
    reason: str = "",
) -> dict[str, Any]:
    """Add a domain to the scrape blocklist (competitor, vendor, etc.)."""
    from outreach_pipeline.db.guardrails import add_scrape_block as _add
    success = await _add(domain, reason)
    return {"success": success, "domain": domain}


# --- Scaffolding (not yet exposed to graph agent) ---

async def save_reply(
    contact_id: str,
    subject: str,
    body_preview: str,
    direction: str = "INCOMING",
    timestamp: str = "",
    urgency: str = "",
    sentiment: str = "",
) -> dict[str, Any]:
    """Save a reply with sentiment/urgency analysis. Scaffolding — not exposed to agent yet."""
    from outreach_pipeline.db.replies import save_reply as _save
    await _save(contact_id, subject, body_preview, direction, timestamp,
                urgency=urgency, sentiment=sentiment)
    return {"success": True, "contact_id": contact_id}


async def opt_out_negative_reply(
    contact_id: str,
    reply_subject: str = "",
) -> dict[str, Any]:
    """Auto opt-out a contact after a negative reply. Scaffolding — not exposed to agent yet."""
    from outreach_pipeline.db.guardrails import add_opt_out as _add
    reason = f"Negative reply: {reply_subject}" if reply_subject else "Negative reply"
    success = await _add(contact_id, reason)
    return {"success": success, "contact_id": contact_id}
