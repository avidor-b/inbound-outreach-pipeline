"""Workflow tools — scoring context, drafting context, stats, domain lookup.

These are the compound tools that assemble context from multiple sources.
Framework-agnostic: MCP wraps them, future agents call them directly.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from outreach_pipeline.db.contacts import get_contact, count_contacts
from outreach_pipeline.db.engagements import (
    get_engagement, get_submissions as get_engagement_submissions,
    list_engagements,
)
from outreach_pipeline.organization import get_organization_profile


async def _resolve_engagement(raw_id: int) -> dict | None:
    """Smart ID resolution: try as engagement_id, then contact_id, then submission_id.

    Agents frequently confuse the three ID types. This resolves the correct
    engagement regardless of which type was passed.
    """
    # 1. Try as engagement_id (most common correct case)
    eng = await get_engagement(raw_id)
    if eng:
        return eng

    # 2. Try as contact_id — find their most relevant open engagement
    engs = await list_engagements(contact_id=str(raw_id))
    if engs:
        # Prefer: scoring > open > drafting > any non-closed
        for status in ("scoring", "open", "drafting"):
            for e in engs:
                if e.get("status") == status:
                    return e
        # Fall back to most recent non-closed
        for e in engs:
            if e.get("status") not in ("closed", "blocked"):
                return e
        return engs[0]  # last resort: most recent

    # 3. Try as submission_id — look up the submission's contact, then their engagement
    try:
        from outreach_pipeline.db.submissions import get_submission
        sub = await get_submission(raw_id)
        if sub and sub.get("contact_id"):
            engs = await list_engagements(contact_id=sub["contact_id"])
            if engs:
                for status in ("scoring", "open", "drafting"):
                    for e in engs:
                        if e.get("status") == status:
                            return e
                return engs[0]
    except Exception:
        pass

    return None


async def get_scoring_context(
    engagement_id: int,
) -> dict[str, Any]:
    """Assemble full context for scoring an engagement.

    Returns: engagement, contact (with jobtitle), submissions, and RAG matches.
    Jobtitle is fetched live from HubSpot (not stored locally).
    Smart ID resolution: if engagement_id doesn't match an engagement,
    tries it as a contact_id or submission_id and resolves automatically.
    """
    eng = await _resolve_engagement(engagement_id)
    if not eng:
        return {"engagement": None, "contact": None, "submissions": [], "rag_matches": [], "jobtitle": "",
                "error": f"ID {engagement_id} not found as engagement, contact, or submission."}

    resolved_eid = eng["id"]
    contact = await get_contact(eng["contact_id"])
    submissions = await get_engagement_submissions(resolved_eid)

    # Prior outreach history — sent drafts for this contact
    prior_outreach = []
    try:
        from outreach_pipeline.db.drafts import get_drafts
        sent_drafts = await get_drafts(contact_id=eng["contact_id"], status="sent")
        prior_outreach = [
            {
                "engagement_id": d.get("engagement_id"),
                "subject": d.get("subject", ""),
                "sent_at": d.get("sent_at", ""),
            }
            for d in sent_drafts
        ]
    except Exception:
        pass

    # Fetch jobtitle from HubSpot (on-demand, not stored)
    jobtitle = ""
    try:
        from outreach_pipeline.tools.pii import afetch_jobtitle
        jobtitle = await afetch_jobtitle(eng["contact_id"])
    except Exception:
        pass

    # Query RAG for context
    rag_matches = []
    try:
        from outreach_pipeline.adapters.chromadb_adapter import ChromaDBAdapter
        rag = ChromaDBAdapter()
        query_parts = [s.get("message", "")[:100] for s in submissions if s.get("message")]
        query = " ".join(query_parts) if query_parts else (contact or {}).get("properties", {}).get("company", "")
        if query:
            # Scoring: broader recall to match prospect against diverse case studies
            results = await rag.aquery(query, top_k=8, collection_name="sales_materials")
            rag_matches = [{"source": r.source, "text": r.text[:800]} for r in results]
    except Exception:
        pass

    # Extract key scoring signals from HubSpot properties so the LLM doesn't
    # have to parse raw property names
    props = (contact or {}).get("properties", {})
    scoring_signals = {
        "jobtitle": jobtitle or "(not set in HubSpot)",
        "company": props.get("company", ""),
        "industry": props.get("industry", ""),
        "page_views": props.get("hs_analytics_num_page_views", "0"),
        "first_url": props.get("hs_analytics_first_url", ""),
        "last_url": props.get("hs_analytics_last_url", ""),
        "traffic_source": props.get("hs_analytics_source", ""),
        "email_opens": props.get("hs_email_open", "0"),
        "email_clicks": props.get("hs_email_click", "0"),
        "first_visit": props.get("hs_analytics_first_visit_timestamp", ""),
        "last_visit": props.get("hs_analytics_last_visit_timestamp", ""),
        "conversion_event": props.get("recent_conversion_event_name", ""),
        "message": props.get("message", ""),
    }

    return {
        "engagement": eng,
        "contact": contact,
        "submissions": submissions,
        "rag_matches": rag_matches,
        "scoring_signals": scoring_signals,
        "prior_outreach": prior_outreach,
    }


async def get_drafting_context(
    engagement_id: int,
) -> dict[str, Any]:
    """Assemble full context for drafting an email for an engagement.

    Returns: engagement, contact, submissions, RAG matches, blocked status.
    Smart ID resolution: tries engagement, contact, submission.
    """
    eng = await _resolve_engagement(engagement_id)
    if not eng:
        return {"engagement": None, "contact": None, "submissions": [],
                "rag_matches": [], "blocked": False,
                "error": f"ID {engagement_id} not found as engagement, contact, or submission."}

    if eng["status"] == "blocked":
        return {
            "engagement": eng, "contact": None, "submissions": [],
            "rag_matches": [], "blocked": True,
            "block_code": eng.get("block_code", ""),
            "block_reason": eng.get("block_reason", ""),
        }

    resolved_eid = eng["id"]
    contact = await get_contact(eng["contact_id"])
    submissions = await get_engagement_submissions(resolved_eid)

    # RAG context — build a targeted query from industry/product context,
    # not raw submission text (which matches boilerplate press releases).
    rag_matches = []
    try:
        from outreach_pipeline.adapters.chromadb_adapter import ChromaDBAdapter
        rag = ChromaDBAdapter()
        props = (contact or {}).get("properties", {})
        company_ctx = (contact or {}).get("company_context", "") or ""
        product = eng.get("primary_product", "") or ""
        industry = props.get("industry", "") or ""
        company = props.get("company", "") or ""

        # Prefer product match + industry; fall back to company context snippet
        query_parts = []
        if product:
            query_parts.append(product)
        if industry:
            query_parts.append(industry)
        if company_ctx:
            query_parts.append(company_ctx[:150])
        elif company:
            query_parts.append(company)
        # Add a short hint from the submission if nothing else worked
        if not query_parts:
            for s in submissions:
                msg = (s.get("message") or "")[:100]
                if msg:
                    query_parts.append(msg)
                    break

        query = " ".join(query_parts)
        if query:
            # Drafting: precise match for a citeable case study
            results = await rag.aquery(query, top_k=5, collection_name="sales_materials")
            rag_matches = [{"source": r.source, "text": r.text[:800]} for r in results]
    except Exception:
        pass

    return {
        "engagement": eng,
        "contact": contact,
        "submissions": submissions,
        "rag_matches": rag_matches,
        "blocked": False,
    }


async def save_engagement_draft(
    engagement_id: int,
    subject: str,
    body: str,
    rag_context: str = "",
    *,
    draft_status: str = "draft",
    promised_resources: list | None = None,
    referenced_resource_ids: list | None = None,
    unverified_promises: list | None = None,
) -> dict[str, Any]:
    """Validate and save a draft for an engagement.

    rag_context: comma-separated summary of RAG sources used.
    draft_status: "draft" (pending), "approved", "rejected", "sent".
    promised_resources: catalog entries surfaced during drafting (top-N
      from find_sendable_asset).
    referenced_resource_ids: subset whose URL appears in the body.
    unverified_promises: body phrases that look like resource offers but
      don't match the catalog.

    Returns: {success, violations (if failed), engagement_id}
    """
    from outreach_pipeline.validation.tone import validate
    result = validate(body, profile="cold_outreach")
    if not result.passed:
        return {
            "success": False,
            "violations": [v["label"] for v in result.violations],
            "engagement_id": engagement_id,
        }

    eng = await get_engagement(engagement_id)
    if not eng:
        return {"success": False, "error": f"Engagement {engagement_id} not found"}

    from outreach_pipeline.db.drafts import save_draft
    from outreach_pipeline.db.engagements import update_status
    draft_id = await save_draft(
        eng["contact_id"], subject, body, eng.get("score", 0),
        rag_context=rag_context, engagement_id=engagement_id,
        status=draft_status,
        promised_resources=promised_resources,
        referenced_resource_ids=referenced_resource_ids,
        unverified_promises=unverified_promises,
    )

    # Advance engagement status if appropriate
    try:
        if eng["status"] in ("open", "scoring"):
            if eng["status"] == "open":
                await update_status(engagement_id, "scoring")
            await update_status(engagement_id, "drafting")
        if draft_status == "approved" and eng["status"] in ("open", "scoring", "drafting"):
            if eng["status"] != "drafting":
                await update_status(engagement_id, "drafting")
            await update_status(engagement_id, "pending_approval")
    except ValueError:
        pass

    return {"success": True, "engagement_id": engagement_id, "subject": subject}


async def get_drafts(
    engagement_id: int = 0,
    contact_id: str = "",
    status: str = "",
) -> dict[str, Any]:
    """Get saved email drafts, filterable by engagement, contact, or status.

    `status` accepts BOTH draft-level statuses (draft, approved, sent,
    rejected) AND engagement-level statuses (pending_approval, drafting,
    blocked, sdr_handoff, closed). When the requested status is an
    engagement status, drafts attached to matching engagements are
    returned regardless of the draft's own status — that matches how
    users think ("show me SDR handoff drafts" = drafts for engagements
    flagged for SDR follow-up).
    """
    from outreach_pipeline.db.drafts import get_drafts as _get_drafts
    from outreach_pipeline.db.pool import get_pool, rows_to_dicts

    # Engagement-scope status filters — route through a JOIN so we catch
    # drafts whose engagement has the requested status, even if the
    # draft's own status column lags behind.
    ENG_STATUSES = {
        "pending_approval", "drafting", "blocked",
        "sdr_handoff", "closed",
    }
    if status in ENG_STATUSES:
        pool = get_pool()
        where = ["e.status = $1"]
        params: list = [status]
        if engagement_id:
            params.append(int(engagement_id))
            where.append(f"d.engagement_id = ${len(params)}")
        if contact_id:
            params.append(str(contact_id))
            where.append(f"d.contact_id = ${len(params)}")
        rows = await pool.fetch(
            f"""SELECT d.* FROM drafts d
                JOIN engagements e ON e.id = d.engagement_id
                WHERE {' AND '.join(where)}
                ORDER BY d.created_at DESC""",
            *params,
        )
        drafts = rows_to_dicts(rows)
    else:
        drafts = await _get_drafts(
            engagement_id=engagement_id or None,
            contact_id=contact_id or None,
            status=status or None,
        )
    if not drafts:
        # Empty result — include a status breakdown from an
        # unfiltered query so the caller can say "0 pending, but 3
        # approved" instead of just "nothing here."
        pool = get_pool()
        rows = await pool.fetch(
            "SELECT status, COUNT(*) AS c FROM drafts GROUP BY status ORDER BY c DESC"
        )
        breakdown = {r["status"]: r["c"] for r in rows}
        return {
            "drafts": [],
            "count": 0,
            "filter": {"status": status, "engagement_id": engagement_id, "contact_id": contact_id},
            "total_drafts": sum(breakdown.values()),
            "by_status": breakdown,
        }

    pool = get_pool()
    contact_ids = list({d["contact_id"] for d in drafts if d.get("contact_id")})
    contact_rows = await pool.fetch(
        "SELECT id, properties FROM contacts WHERE id = ANY($1::text[])",
        contact_ids,
    )
    contacts: dict[str, dict] = {}
    for r in contact_rows:
        props = r["properties"] or {}
        if isinstance(props, str):
            import json
            try:
                props = json.loads(props)
            except Exception:
                props = {}
        contacts[r["id"]] = {
            "company": props.get("company", ""),
            "email": props.get("email", ""),
            "email_domain": props.get("email_domain", ""),
            "firstname": props.get("firstname", ""),
            "lastname": props.get("lastname", ""),
            "industry": props.get("industry", ""),
            "region": props.get("region", ""),
        }

    # Latest submission per contact — we key off contact because
    # engagement_submissions links are inconsistent across backfills.
    sub_rows = await pool.fetch(
        """
        SELECT DISTINCT ON (contact_id)
            contact_id, message, page_url, form_name, mql_date, intent_type
        FROM submissions
        WHERE contact_id = ANY($1::text[])
          AND message != ''
        ORDER BY contact_id, mql_date DESC NULLS LAST, id DESC
        """,
        contact_ids,
    )
    subs = {r["contact_id"]: dict(r) for r in sub_rows}

    # All submission pages per contact — for the "Visited pages" panel
    # on the drafts card. Deduped, ordered most-recent first.
    page_rows = await pool.fetch(
        """
        SELECT contact_id, page_url, page_title, mql_date
        FROM submissions
        WHERE contact_id = ANY($1::text[]) AND page_url != ''
        ORDER BY contact_id, mql_date DESC NULLS LAST, id DESC
        """,
        contact_ids,
    )
    pages_by_cid: dict[str, list[dict]] = {}
    for r in page_rows:
        bucket = pages_by_cid.setdefault(r["contact_id"], [])
        if any(p["url"] == r["page_url"] for p in bucket):
            continue  # dedup by URL
        bucket.append({
            "url": r["page_url"],
            "title": r["page_title"] or "",
        })

    # Batch-fetch engagement status — the UI's "Mark as sent" button
    # needs the ENGAGEMENT's status, not the draft's. They usually
    # match but can lag (the drafter saves status=approved while the
    # engagement is still pending_approval).
    eng_ids = [d.get("engagement_id") for d in drafts if d.get("engagement_id")]
    eng_status: dict[int, str] = {}
    if eng_ids:
        eng_rows = await pool.fetch(
            "SELECT id, status FROM engagements WHERE id = ANY($1::int[])",
            eng_ids,
        )
        eng_status = {r["id"]: r["status"] for r in eng_rows}

    for d in drafts:
        cid = d.get("contact_id", "")
        c = contacts.get(cid, {})
        s = subs.get(cid, {})
        d["company"] = c.get("company", "")
        d["contact_email"] = c.get("email", "")
        d["contact_name"] = " ".join(
            part for part in (c.get("firstname", ""), c.get("lastname", "")) if part
        )
        d["contact_website"] = (
            f"https://{c['email_domain']}"
            if c.get("email_domain") else ""
        )
        d["industry"] = c.get("industry", "")
        d["region"] = c.get("region", "")
        d["original_message"] = s.get("message", "")
        d["original_form"] = s.get("form_name", "")
        d["original_page_url"] = s.get("page_url", "")
        d["original_intent"] = s.get("intent_type", "")
        d["visited_pages"] = pages_by_cid.get(cid, [])[:5]
        d["engagement_status"] = eng_status.get(d.get("engagement_id"), "")
    return {"drafts": drafts, "count": len(drafts)}


async def get_pipeline_stats(
    after: str = "",
    before: str = "",
) -> dict[str, Any]:
    """Get pipeline statistics, optionally filtered to a date range.

    after/before are YYYY-MM-DD strings. Both are inclusive. When set,
    all counts are scoped to records created/opened within the range.
    Unset = all-time.
    """
    from outreach_pipeline.db.pool import get_pool, parse_date

    pool = get_pool()

    # Build reusable date filter fragments. Each table has a different
    # timestamp column so we parameterize the column name; the actual
    # values are bound via $N.
    after_date = parse_date(after)
    before_date = parse_date(before)

    def _date_clause(col: str, params: list) -> str:
        """Append date WHERE fragments + params. Returns SQL fragment."""
        parts = []
        if after_date:
            params.append(after_date)
            parts.append(f"{col}::date >= ${len(params)}")
        if before_date:
            params.append(before_date)
            parts.append(f"{col}::date <= ${len(params)}")
        return (" AND " + " AND ".join(parts)) if parts else ""

    # Contacts — fetched_at (when we first pulled them from HubSpot)
    c_params: list = []
    c_where = _date_clause("fetched_at", c_params)
    c_total = (await pool.fetchrow(
        f"SELECT COUNT(*) as cnt FROM contacts WHERE true{c_where}", *c_params,
    ))["cnt"]
    c_enriched = (await pool.fetchrow(
        f"SELECT COUNT(*) as cnt FROM contacts WHERE company_context != ''{c_where}", *c_params,
    ))["cnt"]

    # Submissions — captured_at (when the form submission was recorded)
    s_params: list = []
    s_where = _date_clause("captured_at", s_params)
    sub_total = (await pool.fetchrow(
        f"SELECT COUNT(*) as cnt FROM submissions WHERE source != 'migration'{s_where}", *s_params,
    ))["cnt"]
    sub_rows = await pool.fetch(
        f"SELECT intent_type, COUNT(*) as cnt FROM submissions "
        f"WHERE intent_type != '' AND source != 'migration'{s_where} GROUP BY intent_type", *s_params,
    )
    sub_by_intent = {r["intent_type"]: r["cnt"] for r in sub_rows}

    # Engagements — opened_at
    e_params: list = []
    e_where = _date_clause("opened_at", e_params)
    eng_total = (await pool.fetchrow(
        f"SELECT COUNT(*) as cnt FROM engagements WHERE true{e_where}", *e_params,
    ))["cnt"]
    eng_status_rows = await pool.fetch(
        f"SELECT status, COUNT(*) as cnt FROM engagements WHERE true{e_where} GROUP BY status", *e_params,
    )
    eng_by_status = {r["status"]: r["cnt"] for r in eng_status_rows}
    eng_kind_rows = await pool.fetch(
        f"SELECT kind, COUNT(*) as cnt FROM engagements WHERE true{e_where} GROUP BY kind", *e_params,
    )
    eng_by_kind = {r["kind"]: r["cnt"] for r in eng_kind_rows}

    # Score distribution (same date filter)
    high_row = await pool.fetchrow(
        f"SELECT COUNT(*) as cnt FROM engagements WHERE score >= 80{e_where}", *e_params)
    med_row = await pool.fetchrow(
        f"SELECT COUNT(*) as cnt FROM engagements WHERE score >= 40 AND score < 80{e_where}", *e_params)
    low_row = await pool.fetchrow(
        f"SELECT COUNT(*) as cnt FROM engagements WHERE score >= 1 AND score < 40{e_where}", *e_params)
    unscored_row = await pool.fetchrow(
        f"SELECT COUNT(*) as cnt FROM engagements WHERE score = 0{e_where}", *e_params)
    score_dist = {
        "high": high_row["cnt"],
        "medium": med_row["cnt"],
        "low": low_row["cnt"],
        "unscored": unscored_row["cnt"],
    }

    # Drafts — created_at
    d_params: list = []
    d_where = _date_clause("created_at", d_params)
    draft_total = (await pool.fetchrow(
        f"SELECT COUNT(*) as cnt FROM drafts WHERE true{d_where}", *d_params,
    ))["cnt"]
    draft_status_rows = await pool.fetch(
        f"SELECT status, COUNT(*) as cnt FROM drafts WHERE true{d_where} GROUP BY status", *d_params,
    )
    draft_by_status = {r["status"]: r["cnt"] for r in draft_status_rows}

    # Replies — detected_at (when our monitor first saw the reply)
    r_params: list = []
    r_where = _date_clause("detected_at", r_params)
    reply_total = (await pool.fetchrow(
        f"SELECT COUNT(*) as cnt FROM replies WHERE direction='INCOMING'{r_where}", *r_params,
    ))["cnt"]
    hot_leads = (await pool.fetchrow(
        f"SELECT COUNT(*) as cnt FROM replies WHERE urgency='high'{r_where}", *r_params,
    ))["cnt"]

    # Opt-outs (no date filter — always all-time)
    opt_outs = (await pool.fetchrow(
        "SELECT COUNT(*) as cnt FROM do_not_contact"
    ))["cnt"]

    # Sends
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date()
    sends_today = (await pool.fetchrow(
        "SELECT COUNT(*) as cnt FROM send_log WHERE date=$1", today,
    ))["cnt"]
    sl_params: list = []
    sl_where = _date_clause("timestamp", sl_params)
    sent_total = (await pool.fetchrow(
        f"SELECT COUNT(*) as cnt FROM drafts WHERE status='sent'{d_where}", *d_params,
    ))["cnt"]

    return {
        "date_range": {
            "after": after or "all-time",
            "before": before or "all-time",
        },
        "contacts": {
            "total": c_total,
            "enriched": c_enriched,
        },
        "submissions": {
            "total": sub_total,
            "by_intent": sub_by_intent,
        },
        "engagements": {
            "total": eng_total,
            "by_status": eng_by_status,
            "by_kind": eng_by_kind,
            "score_distribution": score_dist,
        },
        "drafts": {
            "total": draft_total,
            "by_status": draft_by_status,
        },
        "outreach": {
            "sent": sent_total,
            "sends_today": sends_today,
        },
        "replies": {
            "total": reply_total,
            "hot_leads": hot_leads,
        },
        "opt_outs": opt_outs,
    }


async def get_prospect_domain(
    contact_id: str,
) -> dict[str, Any]:
    """Get likely website domain for a contact from stored data.

    Returns: email_domain, first_url_domain, company name.
    """
    contact = await get_contact(contact_id)

    if not contact:
        return {"contact": None, "email_domain": "", "first_url_domain": "", "company": ""}

    props = contact.get("properties", {})

    email_domain = props.get("email_domain", "")

    first_url_domain = ""
    first_url = props.get("hs_analytics_first_url", "")
    if first_url:
        parsed = urlparse(first_url)
        profile = get_organization_profile()
        if parsed.hostname and not profile.is_first_party_host(parsed.hostname):
            first_url_domain = parsed.netloc

    return {
        "contact": contact,
        "email_domain": email_domain,
        "first_url_domain": first_url_domain,
        "company": props.get("company", ""),
    }
