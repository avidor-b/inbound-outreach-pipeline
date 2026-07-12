"""LangGraph @tool wrappers — thin calls to the framework-agnostic tools/ layer.

Every function here is a 1-3 line wrapper. All business logic lives in tools/*.
Tools are assigned to agent groups via the @agent_tool decorator.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from langchain.tools import tool, ToolRuntime


def _report_timezone() -> str:
    from outreach_pipeline.config import load_config
    return load_config().get("report_timezone", "America/New_York")


def _resolve_date(value: str) -> str:
    from outreach_pipeline.tools.date_ranges import resolve_date_expr
    return resolve_date_expr(value, timezone_name=_report_timezone())


def _resolve_range(start: str, end: str = "", default: str = "") -> tuple[str, str]:
    from outreach_pipeline.tools.date_ranges import resolve_date_range
    return resolve_date_range(
        start,
        end,
        default=default,
        timezone_name=_report_timezone(),
    )


# ---------------------------------------------------------------------------
# Dynamic tool registration
# ---------------------------------------------------------------------------

_TOOL_REGISTRY: dict[str, list] = {
    "query": [],
    "enrich": [],
    "scoring": [],
    "drafting": [],
    "pipeline": [],
    "admin": [],
}

def agent_tool(group: str):
    """Decorator that registers a @tool function with an agent group.

    Usage:
        @agent_tool("query")
        @tool
        def get_leads(...): ...

    The tool is added to _TOOL_REGISTRY[group] and can be retrieved
    via get_agent_tools("query").
    """
    def decorator(fn: Callable) -> Callable:
        if group not in _TOOL_REGISTRY:
            _TOOL_REGISTRY[group] = []
        _TOOL_REGISTRY[group].append(fn)
        return fn
    return decorator

def get_agent_tools(group: str) -> list:
    """Get all tools registered for an agent group."""
    return list(_TOOL_REGISTRY.get(group, []))

from langgraph.types import Command
from langchain.messages import ToolMessage

@tool
def update_memo(memo: str, runtime: ToolRuntime) -> Command:
    """Update the working memo with key facts to remember across turns.

    The memo persists in state even when old messages are trimmed. Write
    contact IDs, engagement IDs, scores, decisions, and context here.
    Overwrite the entire memo each time — include everything important.

    Example: "Working on contact contact-example-001 (Example Distributor). Engagement #24 scored 40."
    """
    return Command(update={
        "memo": memo,
        "messages": [ToolMessage(
            content="Memo saved. Do not repeat your previous response.",
            tool_call_id=runtime.tool_call_id,
        )],
    })



def _slim_engagement(e: dict, contact_info: dict | None = None) -> dict:
    """Reduce engagement dict to essential fields + contact context.

    contact_info: optional {company, country, region} pulled once per
    contact from the batch lookup so the UI table can show something
    more informative than a 12-digit contact_id.
    """
    base = {k: e.get(k, "") for k in ("id", "contact_id", "kind", "status", "score", "primary_product")}
    if contact_info:
        base.update({
            "company": contact_info.get("company", ""),
            "country": contact_info.get("country", ""),
            "region": contact_info.get("region", ""),
        })
    return base


_contact_scores_cache: dict[str, int] = {}


async def _fetch_contact_score(contact_id: str) -> int:
    """Get the highest engagement score for a contact."""
    if contact_id in _contact_scores_cache:
        return _contact_scores_cache[contact_id]
    try:
        from outreach_pipeline.db.pool import get_pool
        pool = get_pool()
        row = await pool.fetchrow(
            "SELECT COALESCE(MAX(score), 0) as max_score FROM engagements WHERE contact_id=$1",
            contact_id,
        )
        score = row["max_score"] if row else 0
        _contact_scores_cache[contact_id] = score
        return score
    except Exception:
        return 0


def _slim_contact(
    c: dict,
    engagement_score: int | None = None,
    country: str = "",
) -> dict:
    """Reduce contact dict to essential fields.

    engagement_score: if provided, uses this instead of c.score (which
    is often 0 because scoring writes to engagements, not contacts).
    country: PII-on-demand; pass from the batch HubSpot fetch since
    it isn't persisted locally.
    """
    if not c:
        return {}
    props = c.get("properties", {})
    score = engagement_score if engagement_score is not None else c.get("score", 0)
    return {
        "id": c.get("id", ""),
        "company": props.get("company", ""),
        "score": score,
        "enriched": bool(c.get("enriched_at") or c.get("company_context") or c.get("enrichment_confidence")),
        "email_domain": props.get("email_domain", ""),
        "industry": props.get("industry", ""),
        "region": props.get("region", ""),
        "country": country,
    }


def _to_json(obj, id_summary: str = "") -> str:
    """Sanitize + JSON-serialize a tool result.

    id_summary: optional human-readable prefix prepended before the JSON
    so the LLM reliably reads entity IDs without parsing the full JSON.
    The frontend's parseResult finds the first '{' and parses from there.
    """
    import json
    payload = json.dumps(_sanitize(obj), default=str)
    return f"{id_summary}\n{payload}" if id_summary else payload


async def _resolve_contact_id(contact_id: str) -> str:
    """Resolve a contact_id or company name to an actual contact ID."""
    if contact_id.isdigit():
        return contact_id
    from outreach_pipeline.db.contacts import search_contacts as _search
    matches = await _search(company=contact_id, limit=1)
    return str(matches[0]["id"]) if matches else ""


def _ids_line(items: list, id_key: str = "id", label_key: str = "company") -> str:
    """Build 'IDs: 123 (Acme), 456 (Beta)' from a result list."""
    parts = []
    for item in items:
        eid = item.get(id_key, item.get("contact_id", item.get("engagement_id", "")))
        label = item.get(label_key, item.get("name", ""))
        parts.append(f"{eid} ({label})" if label else str(eid))
    return "IDs: " + ", ".join(parts) if parts else ""


def _sanitize(obj):
    """Recursively replace None with '' in dicts/lists for LLM tool responses.

    Gemini tool-calling can error on null values in tool results.
    """
    if obj is None:
        return ""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def _split_contact_list(text: str) -> list[str]:
    """Split a free-form list of contact names/IDs on any common separator.

    Accepts comma, semicolon, newline, or the conjunction " and "/" & "
    between names (word-bounded, so "AT&T" and "Johnson & Johnson"
    survive). Earlier this only split on commas, and the LLM — echoing
    the user's "X and Y" phrasing — passed a single concatenated string
    that then failed search_contacts.
    """
    import re
    # Replace each separator with a sentinel, then split.
    # " and " / " AND " with whitespace on both sides is safe (no
    # legitimate company name has that form). " & " with whitespace on
    # both sides matches "Foo & Bar" as a separator but leaves "AT&T"
    # and "Johnson & Johnson" alone (the latter's first "&" has space
    # around it too — we accept it because the LLM passing the whole
    # company as one token is fine; it still resolves via ILIKE).
    parts = re.split(r"[,;\n]| +(?:and|AND|And)(?= +)", text)
    out = [p.strip() for p in parts if p and p.strip()]
    return out

# ---------------------------------------------------------------------------
# Query tools (read-only)
# ---------------------------------------------------------------------------

@agent_tool("query")
@tool
async def get_leads(
    min_score: int = 0,
    date: str = "",
    after: str = "",
    before: str = "",
    today_only: bool = False,
    unenriched: bool = False,
    unscored: bool = False,
    no_reply: bool = False,
    limit: int = 50,
) -> dict:
    """Show/list leads already in the local pipeline database. Does NOT fetch from HubSpot.

    Use this when the user says "show leads", "list contacts", "what leads do we have".
    Use date filters (date, after, before) for MQL date ranges. These accept
    YYYY-MM-DD or relative expressions like today/yesterday/last Friday.
    Use today_only=True for leads ingested today.
    Use unenriched/unscored to find leads needing processing.
    To pull NEW leads from HubSpot, use ingest_contacts instead.

    Results: Show each lead's id, company (from properties), score, and enrichment status.
    IMPORTANT: Remember the returned contact IDs. If the user asks about "that contact" or
    "show details", use the ID from this result — do NOT ask the user for it.
    """
    from outreach_pipeline.tools.contacts import get_leads as _fn
    date = _resolve_date(date)
    after = _resolve_date(after)
    before = _resolve_date(before)
    result = await _fn(min_score=min_score, date=date, after=after, before=before,
                 today_only=today_only, unenriched=unenriched, unscored=unscored,
                 no_reply=no_reply, limit=limit)
    leads = result.get("leads", [])

    # Batch PII-on-demand fetch for country (not stored locally). One
    # concurrent HubSpot call per contact; capped via the limit arg.
    import asyncio as _asyncio
    from outreach_pipeline.tools.pii import afetch_contact_pii as _afetch_pii
    async def _country(cid: str) -> str:
        try:
            pii = await _afetch_pii(str(cid), ["country"])
            return (pii.get("country") or "").strip()
        except Exception:
            return ""
    countries = await _asyncio.gather(
        *[_country(c.get("id", "")) for c in leads]
    ) if leads else []

    # Fetch engagement scores for each contact (contacts.score is often 0)
    _contact_scores_cache.clear()
    slim_leads = []
    for i, c in enumerate(leads):
        cid = c.get("id", "")
        eng_score = await _fetch_contact_score(cid) if cid else 0
        slim_leads.append(_slim_contact(
            c, engagement_score=eng_score,
            country=countries[i] if i < len(countries) else "",
        ))
    result["leads"] = slim_leads
    return _to_json(result, _ids_line(result["leads"]))

@agent_tool("query")
@tool
async def search_contacts(company: str = "", limit: int = 10) -> str:
    """Search contacts by company name. Use this when the user mentions a company
    name and you need to find the contact_id.

    Returns a list of matching contacts with their IDs, companies, and scores.
    Example: search_contacts(company="Example Integrator") → [{id: "contact-123", company: "Example Integrator", ...}]
    """
    from outreach_pipeline.db.contacts import search_contacts as _fn
    results = await _fn(company=company, limit=limit)
    slim = [_slim_contact(c) for c in results]
    return _to_json({"contacts": slim, "count": len(slim)}, _ids_line(slim))


@agent_tool("query")
@tool
async def get_lead_details(contact_id: str) -> str:
    """Get full details for a single contact.

    contact_id accepts a contact ID OR a company name. Company names are
    resolved automatically so you don't need to reproduce long numeric IDs.
    """
    actual_cid = await _resolve_contact_id(contact_id)
    if not actual_cid:
        return _to_json({"error": f"No contact found for '{contact_id}'"})
    from outreach_pipeline.tools.contacts import get_lead_details as _fn
    return _to_json(await _fn(actual_cid))

@agent_tool("query")
@tool
async def get_engagements(
    kind: str = "",
    status: str = "",
    contact_id: str = "",
    min_score: int = 0,
    limit: int = 50,
) -> dict:
    """Get engagements with optional filters.

    contact_id: a contact ID or company name. If a company name is passed,
    it's automatically resolved to the contact ID via search_contacts.

    Kinds: sales, retarget, support, events, content_followup.
    Statuses: open, scoring, drafting, pending_approval, approved, sent, sdr_handoff, blocked, closed.
    Use status='sdr_handoff' for engagements flagged for human SDR follow-up.
    """
    from outreach_pipeline.tools.engagements import get_engagements as _fn

    # Auto-resolve company names to contact IDs
    if contact_id and not contact_id.isdigit():
        from outreach_pipeline.db.contacts import search_contacts as _search
        matches = await _search(company=contact_id, limit=1)
        if matches:
            contact_id = str(matches[0]["id"])
        else:
            return {"engagements": [], "count": 0, "note": f"No contact found for '{contact_id}'"}

    result = await _fn(kind=kind, status=status, contact_id=contact_id, min_score=min_score, limit=limit)
    engs = result.get("engagements", [])

    # Batch-load company + region from the DB and country via HubSpot PII.
    # One roundtrip per contact is fine at typical page sizes (≤50).
    from outreach_pipeline.db.pool import get_pool
    from outreach_pipeline.tools.pii import afetch_contact_pii as _afetch_pii
    import asyncio as _asyncio

    uniq_cids = list({str(e.get("contact_id", "")) for e in engs if e.get("contact_id")})
    info_by_cid: dict[str, dict] = {}
    if uniq_cids:
        pool = get_pool()
        rows = await pool.fetch(
            "SELECT id, properties FROM contacts WHERE id = ANY($1::text[])",
            uniq_cids,
        )
        import json as _json
        for r in rows:
            props = r["properties"] or {}
            if isinstance(props, str):
                try:
                    props = _json.loads(props)
                except Exception:
                    props = {}
            info_by_cid[r["id"]] = {
                "company": props.get("company", ""),
                "region": props.get("region", ""),
                "country": "",
            }
        # Fetch country (PII-on-demand) for all contacts in parallel.
        async def _country(cid: str) -> str:
            try:
                pii = await _afetch_pii(cid, ["country"])
                return (pii.get("country") or "").strip()
            except Exception:
                return ""
        countries = await _asyncio.gather(*[_country(cid) for cid in uniq_cids])
        for cid, country in zip(uniq_cids, countries):
            info_by_cid.setdefault(cid, {})["country"] = country

    result["engagements"] = [
        _slim_engagement(e, info_by_cid.get(str(e.get("contact_id", "")), {}))
        for e in engs
    ]
    return _to_json(result, _ids_line(result["engagements"], label_key="status"))

@agent_tool("query")
@tool
async def get_engagement_details(engagement_id: int) -> dict:
    """Get full context for a single engagement by its ENGAGEMENT ID (short number like 5042).

    Returns: engagement record + linked contact + linked submissions.
    DO NOT pass a contact ID here — contact IDs are opaque CRM identifiers.

    For all engagements of a contact, use get_engagements(contact_id='...') or
    get_contact_history(contact_id='...').
    """
    from outreach_pipeline.tools.engagements import get_engagement_details as _fn
    return _to_json(await _fn(engagement_id))

@agent_tool("query")
@tool
async def get_contact_history(contact_id: str) -> str:
    """Full picture of a contact: contact info + ALL submissions + ALL engagements.

    contact_id accepts a contact ID OR a company name.
    """
    actual_cid = await _resolve_contact_id(contact_id)
    if not actual_cid:
        return _to_json({"error": f"No contact found for '{contact_id}'"})
    from outreach_pipeline.tools.engagements import get_contact_history as _fn
    return _to_json(await _fn(actual_cid))

async def _get_submissions_one(contact_id: str, include_migration: bool = False) -> dict:
    """Core logic for a single-contact submission lookup. Returns the
    dict payload (not JSON). Split out so get_submissions_bulk can call
    it without re-entering the @tool/@agent_tool wrappers — if bulk
    called `get_submissions.ainvoke(...)` instead, each inner call
    would emit its own LangGraph OnToolEnd event whose `output` is a
    raw str, which ag_ui_langgraph's adapter can't turn into a
    ToolCallResult (it expects a ToolMessage with .tool_call_id) —
    crashing the stream with AttributeError and leaving the chat with
    a bogus "missing_terminal_event" error.
    """
    from outreach_pipeline.tools.engagements import get_submissions as _fn

    actual_cid = contact_id
    if not contact_id.isdigit():
        from outreach_pipeline.db.contacts import search_contacts as _search
        matches = await _search(company=contact_id, limit=1)
        if matches:
            actual_cid = str(matches[0]["id"])
        else:
            return {"submissions": [], "count": 0,
                    "contact_id": contact_id,
                    "error": f"No contact found for '{contact_id}'"}

    result = await _fn(actual_cid, include_migration=include_migration)

    # Engagement-ID fallback: if nothing matches and the input was a
    # numeric ID, the LLM may have handed us an engagement id rather
    # than a contact id — resolve and retry.
    if result.get("count", 0) == 0:
        try:
            from outreach_pipeline.tools.workflow import _resolve_engagement
            eng = await _resolve_engagement(int(actual_cid))
            if eng and eng.get("contact_id") != actual_cid:
                resolved = await _fn(eng["contact_id"], include_migration=include_migration)
                if resolved.get("count", 0) > 0:
                    result = resolved
                    actual_cid = eng["contact_id"]
        except (ValueError, TypeError):
            pass

    subs = result.get("submissions", [])
    if not subs:
        return {"submissions": [], "count": 0, "contact_id": contact_id}

    company = ""
    try:
        from outreach_pipeline.db.contacts import get_contact
        contact = await get_contact(actual_cid)
        if contact:
            company = (contact.get("properties") or {}).get("company", "")
    except Exception:
        pass

    slim = []
    for s in subs:
        slim.append({
            "id": s.get("id", ""),
            "date": str(s.get("mql_date", ""))[:10],
            "intent_type": s.get("intent_type", ""),
            "product_family": s.get("product_family", ""),
            "form_name": s.get("form_name", ""),
            "page_url": s.get("page_url", ""),
            "message": (s.get("message") or "").strip()[:500],
            "message_translation": (s.get("message_translation") or "").strip()[:500],
        })
    return {"submissions": slim, "count": len(slim), "contact_id": actual_cid, "company": company}


@agent_tool("query")
@tool
async def get_submissions(contact_id: str, include_migration: bool = False) -> str:
    """Get submission history for a contact.

    contact_id accepts: a contact ID, engagement ID, submission ID,
    OR a company name. Company names are resolved via search_contacts
    so the agent doesn't need to reproduce long numeric IDs exactly.

    If the input looks like a list of multiple names (commas, " and ",
    etc.), this tool auto-dispatches to get_submissions_bulk so the
    agent still gets useful results even when it picks the wrong tool.
    """
    # Auto-route to bulk if the agent passed multiple names in one go
    # (happens when the LLM mirrors the user's "A, B, and C" phrasing
    # and picks this tool instead of get_submissions_bulk).
    if not contact_id.isdigit():
        parts = _split_contact_list(contact_id)
        if len(parts) > 1:
            payload = await _get_submissions_bulk_payload(contact_id, include_migration)
            return _to_json(payload)

    payload = await _get_submissions_one(contact_id, include_migration)
    return _to_json(payload)


async def _get_submissions_bulk_payload(
    contacts: str, include_migration: bool = False,
) -> dict:
    """Shared helper so both the public tool and the single-tool's
    auto-route path can produce a bulk payload without re-entering
    the @tool wrapper (which would emit a nested OnToolEnd that
    ag_ui_langgraph can't handle — see _get_submissions_one)."""
    names = _split_contact_list(contacts)
    seen_cids: set[str] = set()
    results: list[dict] = []
    for name in names:
        payload = await _get_submissions_one(name, include_migration)
        cid = str(payload.get("contact_id") or name)
        if cid in seen_cids:
            continue
        seen_cids.add(cid)
        results.append(payload)
    return {
        "results": results,
        "count": len(results),
        "total_requested": len(names),
    }


@agent_tool("query")
@tool
async def get_submissions_bulk(contacts: str, include_migration: bool = False) -> str:
    """Get submission history for MULTIPLE contacts in a SINGLE call.

    `contacts` is a list of contact IDs and/or company names. Any
    natural separator works: commas, semicolons, newlines, or the word
    " and " between names. Examples:
      - "Example Telecoms, Example Scientific & Electronics Co."
      - "Example Technologies Japan and Example Manufacturing LLC"
      - "Foo; Bar; Baz"

    Use this whenever the user asks about submissions for two or more
    companies — do NOT call get_submissions in a loop. Looping is
    error-prone: the LLM has been observed repeating the same name
    instead of iterating. This tool dedups by resolved contact_id, so
    accidental duplicates in the input are collapsed.

    Returns: {results: [...per-contact payload], count, total}.
    Each per-contact payload has the same shape get_submissions returns
    (submissions, count, contact_id, company).
    """
    payload = await _get_submissions_bulk_payload(contacts, include_migration)
    return _to_json(payload)

@agent_tool("query")
@tool
async def get_drafts(engagement_id: int = 0, contact_id: str = "", status: str = "") -> dict:
    """Get saved email drafts from the database.

    Use when the user asks to see a draft, review an email, or check what's been written.
    Filter by engagement_id (preferred), contact_id (ID or company name), or status.

    contact_id accepts a contact ID OR a company name — company names are
    auto-resolved so you don't need to reproduce long numeric IDs.

    Results: Show each draft's subject, body (full text), score, and rag_context.
    IMPORTANT: Report the exact 'status' field from the data — draft, approved, or sent.
    Do NOT say "draft" if the status is "approved". Read the field accurately.
    """
    from outreach_pipeline.tools.workflow import get_drafts as _fn

    if contact_id:
        if str(contact_id).isdigit():
            contact_id = str(contact_id)
        else:
            from outreach_pipeline.db.contacts import search_contacts as _search
            matches = await _search(company=str(contact_id), limit=20)
            if not matches:
                return _to_json({"drafts": [], "count": 0, "note": f"No contact found for '{contact_id}'"})
            if len(matches) == 1:
                contact_id = str(matches[0]["id"])
            else:
                drafts: list[dict] = []
                matched_ids = [str(m["id"]) for m in matches]
                for cid in matched_ids:
                    result = await _fn(
                        engagement_id=engagement_id,
                        contact_id=cid,
                        status=status,
                    )
                    drafts.extend(result.get("drafts", []))
                if drafts:
                    drafts.sort(key=lambda d: str(d.get("created_at", "")), reverse=True)
                    return _to_json({
                        "drafts": drafts,
                        "count": len(drafts),
                        "filter": {
                            "status": status,
                            "engagement_id": engagement_id,
                            "contact_id": contact_id,
                        },
                        "matched_contact_ids": matched_ids,
                    })

                from outreach_pipeline.db.pool import get_pool
                pool = get_pool()
                rows = await pool.fetch(
                    """SELECT status, COUNT(*) AS c
                       FROM drafts
                       WHERE contact_id = ANY($1::text[])
                       GROUP BY status
                       ORDER BY c DESC""",
                    matched_ids,
                )
                breakdown = {r["status"]: r["c"] for r in rows}
                return _to_json({
                    "drafts": [],
                    "count": 0,
                    "filter": {
                        "status": status,
                        "engagement_id": engagement_id,
                        "contact_id": contact_id,
                    },
                    "matched_contact_ids": matched_ids,
                    "total_drafts": sum(breakdown.values()),
                    "by_status": breakdown,
                })

    return _to_json(await _fn(engagement_id=engagement_id, contact_id=contact_id, status=status))

@agent_tool("query")
@tool
async def get_pipeline_stats(after: str = "", before: str = "") -> str:
    """Get pipeline statistics: contacts, submissions, engagements, drafts, replies, sends.

    Use after/before to scope to a date range. These accept YYYY-MM-DD or
    relative expressions like today/yesterday/last Friday.
    Empty = all-time.
    """
    from outreach_pipeline.tools.workflow import get_pipeline_stats as _fn
    after = _resolve_date(after)
    before = _resolve_date(before)
    return _to_json(await _fn(after=after, before=before))

@agent_tool("query")
@tool
async def get_leads_summary(
    min_score: int = 0,
    date: str = "",
    after: str = "",
    before: str = "",
    no_reply: bool = False,
    limit: int = 50,
) -> dict:
    """Get a filtered summary of leads already in the local DB, using MQL dates. Lighter than get_leads."""
    from outreach_pipeline.tools.pipeline import get_leads_summary as _fn
    date = _resolve_date(date)
    after = _resolve_date(after)
    before = _resolve_date(before)
    return await _fn(min_score=min_score, date=date, after=after, before=before, no_reply=no_reply, limit=limit)

@agent_tool("enrich")
@tool
async def query_rag(query: str, collection: str = "sales_materials", top_k: int = 8) -> dict:
    """Query a RAG collection for relevant content. Use for sales materials, prospect info, etc."""
    from outreach_pipeline.tools.rag import aquery_rag as _fn
    return _to_json(await _fn(query, collection=collection, top_k=top_k))

@agent_tool("drafting")
@tool
async def get_email_guidelines() -> str:
    """Get the email drafting guidelines. Call this before drafting any email."""
    path = Path(__file__).parent.parent.parent.parent / ".claude" / "skills" / "outreach-pipeline" / "email-guidelines.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "Draft a concise 3-4 sentence email. Start with 'Hi [First Name],' and end with 'Best,\\n[Sender Name]'."

@agent_tool("scoring")
@tool
async def check_engagement_policy(engagement_id: int) -> dict:
    """Check contact-level policies for an engagement.

    Smart ID resolution: if the ID isn't a valid engagement, it will
    be tried as a contact_id or submission_id automatically.
    """
    from outreach_pipeline.tools.workflow import _resolve_engagement
    eng = await _resolve_engagement(engagement_id)
    if not eng:
        return {"error": f"ID {engagement_id} not found as engagement, contact, or submission."}
    from outreach_pipeline.tools.engagements import check_engagement_policy as _fn
    return _to_json(await _fn(eng["id"]))

# ---------------------------------------------------------------------------
# Enrichment tools
# ---------------------------------------------------------------------------

@agent_tool("enrich")
@tool
async def web_search(query: str, max_results: int = 5) -> dict:
    """Search the web for information. Returns titles, URLs, and snippets.

    Use for: finding company websites when get_prospect_domain returns no domain,
    looking up company context when scraping fails, verifying domains, general research.
    For enrichment: search for the company name to find their website, then scrape_and_index it.
    """
    from outreach_pipeline.tools.search import aweb_search as _fn
    return _to_json(await _fn(query, max_results=max_results))

@agent_tool("enrich")
@tool
async def get_prospect_domain(contact_id: str) -> dict:
    """Get likely website domain for a contact from email domain or first URL.

    Use this as the first step in enrichment to find the company website.
    """
    from outreach_pipeline.tools.workflow import get_prospect_domain as _fn
    return await _fn(contact_id)

@agent_tool("enrich")
@tool
async def scrape_and_index(url: str, collection: str = "", prospect: bool = True, max_pages: int = 10) -> dict:
    """Scrape a website URL and index the content into a RAG collection.

    Use this after get_prospect_domain to scrape the company website.
    Results can then be queried with query_rag using the returned collection name.

    prospect=True (default): creates a domain-specific collection (e.g. 'acme_com') for
        prospect enrichment. Use this when scraping a prospect's website.
    prospect=False: indexes into 'sales_materials' collection. Use for our own content.
    collection: override the auto-derived collection name.
    """
    from outreach_pipeline.tools.rag import ascrape_and_index as _fn
    return await _fn(url, collection=collection, prospect=prospect, max_pages=max_pages)

@agent_tool("enrich")
@tool
async def fetch_page(url: str) -> dict:
    """Fetch a single URL and extract its text content.

    Returns {url, title, text}. Use to check a company's homepage, about page,
    or any specific URL. Non-destructive — doesn't index anything.

    For full site scraping + RAG indexing, use scrape_and_index instead.
    """
    from outreach_pipeline.tools.enrich_company import afetch_page as _fn
    return await _fn(url)


@agent_tool("enrich")
@tool
async def summarize_and_save(
    contact_id: str,
    context_text: str,
    company_name: str = "",
    email_domain: str = "",
) -> dict:
    """Assess enrichment quality and save a company summary for a contact.

    Provide context_text gathered from ANY source (fetch_page, web_search,
    scrape_and_index, query_rag, or your own knowledge). This tool:
    1. Runs an LLM confidence assessment (high/medium/low)
    2. Generates a 2-3 sentence company summary
    3. Saves the summary to the contact record

    contact_id: opaque contact ID returned by the CRM
    context_text: the raw information you gathered about the company
    company_name: the company name (for verification)
    email_domain: the company's email domain (for verification)
    """
    from outreach_pipeline.tools.enrich_company import asummarize_and_save as _fn
    return await _fn(contact_id, context_text, company_name, email_domain)


@agent_tool("enrich")
@tool
async def save_enrichment(contact_id: str, company_context: str, crm_context: str = "") -> dict:
    """Save enrichment data (company context) for a contact.

    Call this after scraping and summarizing the company website.
    """
    from outreach_pipeline.tools.engagements import save_enrichment as _fn
    return await _fn(contact_id, company_context, crm_context)

# ---------------------------------------------------------------------------
# Scoring tools
# ---------------------------------------------------------------------------

@agent_tool("scoring")
@tool
async def get_scoring_context(engagement_id: int) -> dict:
    """Assemble full context for scoring an engagement.

    Smart ID resolution: if the ID isn't a valid engagement, it will
    be tried as a contact_id or submission_id automatically.
    """
    from outreach_pipeline.tools.workflow import get_scoring_context as _fn
    return await _fn(engagement_id)

@agent_tool("scoring")
@tool
async def save_engagement_score(
    engagement_id: int,
    score: int,
    score_reasoning: str,
    buying_intent: str = "",
    intent_signals: list[str] | None = None,
    primary_product: str = "",
) -> dict:
    """Save a score (1-100) on an engagement.

    Smart ID resolution: if the ID isn't a valid engagement, it will
    be tried as a contact_id or submission_id automatically.

    ALL fields should be provided:
    - score: 1-100. 80-100=clear buying intent, 50-79=genuine interest, 25-49=vague, 1-24=unlikely.
    - score_reasoning: one sentence explaining the score.
    - buying_intent: 'low', 'medium', or 'high'.
    - intent_signals: list of observed signals.
    - primary_product: microwave_radio, millimeter_wave_radio, point_to_point_radio, split_mount_radio, mesh_radio, outdoor_radio, or general.

    Auto-advances: open→scoring, scoring→drafting (≥50), scoring→closed (≤25).
    """
    from outreach_pipeline.tools.workflow import _resolve_engagement
    eng = await _resolve_engagement(engagement_id)
    if not eng:
        return {"error": f"ID {engagement_id} not found as engagement, contact, or submission."}
    from outreach_pipeline.tools.engagements import score_engagement as _fn
    return await _fn(eng["id"], score, score_reasoning, primary_product,
               buying_intent=buying_intent, intent_signals=intent_signals or [])

# ---------------------------------------------------------------------------
# Drafting tools
# ---------------------------------------------------------------------------

@agent_tool("drafting")
@tool
async def get_drafting_context(engagement_id: int) -> dict:
    """Assemble full context for drafting an engagement.

    Smart ID resolution: if the ID isn't a valid engagement, it will
    be tried as a contact_id or submission_id automatically.
    """
    from outreach_pipeline.tools.workflow import get_drafting_context as _fn
    return await _fn(engagement_id)

@agent_tool("drafting")
@tool
async def validate_message(text: str, profile: str = "cold_outreach") -> dict:
    """Validate message text against a tone profile.

    Profiles: cold_outreach, retargeting, followup, content_grading.
    Returns violations list and passed boolean.
    """
    from outreach_pipeline.utils.async_helpers import run_sync
    from outreach_pipeline.tools.messaging import validate_message as _fn
    result = await run_sync(_fn, text, profile, timeout=5)
    return result.model_dump() if result else {"passed": False, "violations": []}

@agent_tool("drafting")
@tool
async def save_engagement_draft(
    engagement_id: int, subject: str, body: str,
    rag_context: str = "", validate_only: bool = False,
) -> dict:
    """Validate and save a draft for an engagement.

    Runs tone validation first. Returns violations if validation fails.
    Set validate_only=True to check without saving (dry run).

    rag_context: comma-separated summary of RAG sources used when drafting.
    ALWAYS provide this when drafting — it tracks which materials informed the email.

    NOTE: In the interactive graph, the draft_review node handles validation
    and approval AFTER the drafting agent runs. This tool is used by the
    pipeline's batch flow where no human is in the loop.
    """
    from outreach_pipeline.tools.messaging import validate_message as _validate
    validation = _validate(body, profile="cold_outreach")
    if not validation.passed:
        return {
            "success": False,
            "validate_only": validate_only,
            "violations": [v["label"] for v in validation.violations],
            "engagement_id": engagement_id,
        }
    if validate_only:
        return {"success": True, "validate_only": True, "passed": True, "engagement_id": engagement_id}
    from outreach_pipeline.tools.workflow import save_engagement_draft as _fn
    return await _fn(engagement_id, subject, body, rag_context=rag_context)

# ---------------------------------------------------------------------------
# Action tools (state changes)
# ---------------------------------------------------------------------------

@agent_tool("admin")
@tool
async def update_engagement_status(
    engagement_id: int,
    new_status: str,
    block_code: str = "",
    block_reason: str = "",
) -> dict:
    """Update engagement status with transition validation.

    Valid transitions: open->scoring/blocked/closed, scoring->drafting/blocked/closed,
    drafting->pending_approval/sdr_handoff/blocked/closed,
    pending_approval->approved/drafting/sdr_handoff/closed,
    approved->sent/closed, sent->closed, blocked->open/closed,
    sdr_handoff->drafting/closed.
    """
    from outreach_pipeline.tools.engagements import update_engagement_status as _fn
    return await _fn(engagement_id, new_status, block_code=block_code, block_reason=block_reason)

@agent_tool("admin")
@tool
async def create_retarget(original_engagement_id: int) -> dict:
    """Create a retarget engagement for a non-replied sent engagement.

    Original must be in 'sent' or 'closed' status. Only one retarget per original.
    """
    from outreach_pipeline.tools.engagements import create_retarget as _fn
    return await _fn(original_engagement_id)

@agent_tool("admin")
@tool
async def reclassify_submission(
    submission_id: int,
    intent_type: str = "",
    product_family: str = "",
    support_flag: bool = False,
    event_flag: bool = False,
) -> dict:
    """Manually reclassify a submission's intent type and product family.

    Smart ID resolution: if submission_id is a contact_id (large number),
    reclassifies EVERY submission-with-a-message belonging to that
    contact. Users typically mean 'this company's submissions are
    misclassified', not 'some random one of them'.
    """
    from outreach_pipeline.tools.engagements import reclassify_submission as _fn

    # Contact IDs overflow int32; real submission IDs are SERIAL and
    # stay well under 2B for the foreseeable future.
    if submission_id > 2_000_000_000:
        from outreach_pipeline.db.submissions import list_submissions
        subs = await list_submissions(contact_id=str(submission_id))
        # Reverse so we report the most recent first — callers usually
        # care about the latest submission's state.
        msg_subs = [s for s in reversed(subs) if (s.get("message") or "").strip()]
        if not msg_subs:
            return {"error": f"No submissions with messages found for contact {submission_id}."}
        updated: list[int] = []
        failed: list[int] = []
        for s in msg_subs:
            r = await _fn(
                s["id"], intent_type=intent_type, product_family=product_family,
                support_flag=support_flag, event_flag=event_flag,
            )
            (updated if r.get("success") else failed).append(s["id"])
        return {
            "success": bool(updated),
            "updated_submission_ids": updated,
            "failed_submission_ids": failed,
            "count": len(updated),
        }

    return await _fn(submission_id, intent_type=intent_type, product_family=product_family,
                     support_flag=support_flag, event_flag=event_flag)

@agent_tool("admin")
@tool
async def add_opt_out(contact_id: str, reason: str = "") -> dict:
    """Add a contact to the do-not-contact list."""
    from outreach_pipeline.tools.engagements import add_opt_out as _fn
    return await _fn(contact_id, reason or "Requested removal")

@agent_tool("scoring")
@tool
async def fetch_jobtitle(contact_id: str) -> str:
    """Fetch the job title for a contact from HubSpot (on-demand, not stored in DB).

    Use during scoring to assess authority level (VP/Director/CTO = decision-maker).
    This is PII-adjacent data fetched live from HubSpot, never persisted locally.
    """
    from outreach_pipeline.tools.pii import afetch_jobtitle as _fn
    return await _fn(contact_id) or "Unknown"

@agent_tool("admin")
@tool
async def add_scrape_block(domain: str, reason: str = "") -> dict:
    """Add a domain to the scrape blocklist.

    Use when a prospect turns out to be a competitor, vendor, or otherwise
    should not be scraped. Also consider adding them to opt-out.
    """
    from outreach_pipeline.tools.engagements import add_scrape_block as _fn
    return await _fn(domain, reason)

@agent_tool("admin")
@tool
async def preview_delete_contact(contact_id: str) -> dict:
    """Preview what would be deleted for a contact. Does NOT delete anything.

    ALWAYS call this first when the user asks to delete a contact.
    Shows the contact info and all associated data (submissions, engagements, drafts)
    that would be removed. Present this clearly to the user and ask for confirmation
    before calling delete_contact.
    """
    from outreach_pipeline.tools.contacts import preview_delete_contact as _fn
    return await _fn(contact_id)

@agent_tool("admin")
@tool
async def delete_contact(contact_id: str) -> dict:
    """Delete a contact and ALL associated data. Destructive, cannot be undone.

    contact_id accepts a contact ID OR a company name — company names
    are resolved via search_contacts so the agent doesn't need to
    reproduce long numeric IDs exactly.

    The ReviewMiddleware wrap_tool_call hook automatically shows a preview
    and interrupts for human confirmation before this executes.
    """
    # Resolve company name → contact ID before the middleware sees the
    # request, so the preview card and the final delete both target
    # the right row. Numeric IDs and existing rows pass through
    # unchanged.
    from outreach_pipeline.db.contacts import get_contact as _get, search_contacts as _search
    existing = await _get(str(contact_id))
    if not existing and contact_id and not str(contact_id).isdigit():
        matches = await _search(company=str(contact_id), limit=1)
        if matches:
            contact_id = str(matches[0]["id"])
    from outreach_pipeline.tools.contacts import delete_contact as _fn
    return await _fn(contact_id)

@agent_tool("admin")
@tool
async def send_message(artifact_id: int, dry_run: bool = False) -> dict:
    """Email sending is DISABLED. This tool always returns an error.

    Sending is not available in this version. Drafts can be approved
    but must be sent manually outside this system.
    """
    return {"success": False, "error": "Email sending is disabled. Drafts must be sent manually outside this system."}

# ---------------------------------------------------------------------------
# Pipeline tools (batch operations)
# ---------------------------------------------------------------------------

@agent_tool("pipeline")
@tool
async def ingest_contacts(start_date: str = "", end_date: str = "") -> dict:
    """Fetch/pull NEW MQL contacts from HubSpot and ingest them into the local pipeline.

    Use this when the user says "fetch contacts", "pull leads", "ingest MQLs", "get new leads from HubSpot".
    Dates accept YYYY-MM-DD or relative expressions like today, yesterday,
    last week, this week, or last 7 days. Defaults to yesterday if empty.
    To just view leads already in the DB, use get_leads instead.

    Results include a 'contacts' list. Each entry has:
    - contact_id: the CONTACT ID returned by the CRM — use this for get_contact_history, get_lead_details
    - engagement_id: the ENGAGEMENT ID (short number like 5042) — use this for get_engagement_details
    - company: company name

    CRITICAL: contact_id and engagement_id are DIFFERENT numbers. Do not confuse them.
    When the user asks for details, use get_contact_history(contact_id) for the full picture,
    or get_engagement_details(engagement_id) for a specific engagement.
    """
    from outreach_pipeline.tools.pipeline import ingest_contacts as _fn
    start_date, end_date = _resolve_range(start_date, end_date, default="yesterday")
    result = await _fn(start_date=start_date, end_date=end_date)
    contacts = result.get("contacts", [])
    return _to_json(result, _ids_line(contacts, id_key="contact_id"))

@agent_tool("pipeline")
@tool
async def monitor_replies(since_date: str = "") -> str:
    """Check HubSpot for email replies since a given date (YYYY-MM-DD).

    Use when user says "check replies", "any responses", "monitor replies".
    Returns reply count, sentiment, and urgency flags.
    """
    from outreach_pipeline.services.monitoring import fetch_replies
    if not since_date:
        from datetime import datetime, timedelta, timezone
        since_date = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    else:
        since_date = _resolve_date(since_date)
    return _to_json(fetch_replies(since_date))

@agent_tool("pipeline")
@tool
async def export_pipeline(output_path: str = "data/pipeline_output.xlsx") -> dict:
    """Export the full pipeline to an Excel file. Overwrites if file exists."""
    import os
    if os.path.isfile(output_path):
        os.remove(output_path)
    from outreach_pipeline.adapters.excel import ExcelAdapter
    ExcelAdapter().export(output_path)
    return {"success": True, "path": output_path}

@agent_tool("pipeline")
@tool
async def get_pipeline_runs(limit: int = 10) -> dict:
    """List past pipeline runs with dates, metrics, and run IDs.

    Use when the user says "list pipeline runs", "show pipeline history",
    "past pipeline runs".
    """
    from outreach_pipeline.db.pool import get_pool, rows_to_dicts
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT id, start_date, end_date, contacts_fetched, emails_drafted, "
        "started_at, completed_at FROM pipeline_runs ORDER BY id DESC LIMIT $1",
        limit,
    )
    return _to_json({"runs": rows_to_dicts(rows), "count": len(rows)})


@agent_tool("pipeline")
@tool
async def get_pipeline_run(run_id: int) -> dict:
    """Get full details and summary for a specific pipeline run.

    Use when the user says "show pipeline run #3", "pipeline run details".
    Returns the formatted summary that was shown at the end of the run.
    """
    from outreach_pipeline.db.pool import get_pool, row_to_dict
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM pipeline_runs WHERE id=$1", run_id)
    if not row:
        return {"error": f"Pipeline run #{run_id} not found"}
    return row_to_dict(row)


@agent_tool("admin")
@tool
async def index_content(
    materials_dir: str = "resources/materials",
    collection: str = "sales_materials",
    reset: bool = False,
) -> dict:
    """Index files from a directory into a RAG collection."""
    from outreach_pipeline.tools.rag import aindex_content as _fn
    return await _fn(materials_dir, collection=collection, reset=reset)

@agent_tool("admin")
@tool
async def grade_collection(collection: str = "sales_materials", profile: str = "content_grading") -> dict:
    """Grade all chunks in a RAG collection against a validation profile."""
    from outreach_pipeline.utils.async_helpers import run_sync
    from outreach_pipeline.tools.grading import grade_collection as _fn
    return await run_sync(_fn, collection, profile, timeout=30)

# ---------------------------------------------------------------------------
# Per-agent tool groups (populated by @agent_tool decorators above)
# Shared tools (query_rag) are registered once but added to multiple groups below.
# ---------------------------------------------------------------------------

# Register shared tools in additional groups BEFORE any get_agent_tools() calls.
# query_rag is needed in scoring and drafting (primary home is enrich).
# get_drafts is needed in drafting (primary home is query).
# search_contacts is needed everywhere so agents can resolve company names to IDs.
if query_rag not in _TOOL_REGISTRY["scoring"]:
    _TOOL_REGISTRY["scoring"].append(query_rag)
if query_rag not in _TOOL_REGISTRY["drafting"]:
    _TOOL_REGISTRY["drafting"].append(query_rag)
if get_drafts not in _TOOL_REGISTRY["drafting"]:
    _TOOL_REGISTRY["drafting"].append(get_drafts)
if get_engagements not in _TOOL_REGISTRY["drafting"]:
    _TOOL_REGISTRY["drafting"].append(get_engagements)
if get_engagements not in _TOOL_REGISTRY["scoring"]:
    _TOOL_REGISTRY["scoring"].append(get_engagements)
# web_search useful for query agent too (general research)
if web_search not in _TOOL_REGISTRY["query"]:
    _TOOL_REGISTRY["query"].append(web_search)
for _group in _TOOL_REGISTRY:
    if search_contacts not in _TOOL_REGISTRY[_group]:
        _TOOL_REGISTRY[_group].append(search_contacts)

# update_memo is needed in every specialist so each can persist context.
for _group in _TOOL_REGISTRY:
    if update_memo not in _TOOL_REGISTRY[_group]:
        _TOOL_REGISTRY[_group].append(update_memo)

# ---------------------------------------------------------------------------
# Long-term memory tools (cross-thread, via LangGraph Store)
# ---------------------------------------------------------------------------

@agent_tool("query")
@tool
async def save_preference(key: str, value: str) -> dict:
    """Save a user preference that persists across all threads.

    Examples: 'always show confidence scores', 'default date range is 7 days'.
    key: short identifier (e.g. 'date_range', 'verbosity')
    value: the preference value
    """
    from outreach_pipeline.tools.memory import save_preference as _save
    return await _save(key, value)


@agent_tool("query")
@tool
async def get_preference(key: str) -> dict:
    """Get a saved user preference. Returns empty value if not set."""
    from outreach_pipeline.tools.memory import get_preference as _get
    return await _get(key)


@agent_tool("query")
@tool
async def list_preferences() -> dict:
    """List all saved user preferences."""
    from outreach_pipeline.tools.memory import list_preferences as _list
    return await _list()


@agent_tool("query")
@tool
async def save_correction(key: str, value: str) -> dict:
    """Save a user-corrected fact (e.g. 'Example Networks is in Mexico, not Example Networks').

    key: what's being corrected
    value: the correct information
    """
    from outreach_pipeline.tools.memory import save_correction as _save
    return await _save(key, value)


@agent_tool("query")
@tool
async def get_corrections() -> dict:
    """Get all saved user corrections."""
    from outreach_pipeline.tools.memory import get_corrections as _get
    return await _get()


@agent_tool("query")
@tool
async def save_contact_note(contact_id: str, note: str) -> dict:
    """Save a note about a contact that persists across threads.

    Examples: 'Key decision maker', 'Already has configured radio units'.
    """
    from outreach_pipeline.tools.memory import save_contact_note as _save
    return await _save(contact_id, note)


@agent_tool("query")
@tool
async def get_contact_note(contact_id: str) -> dict:
    """Get saved notes about a contact."""
    from outreach_pipeline.tools.memory import get_contact_note as _get
    return await _get(contact_id)


# Make read-only memory tools available to all specialists
for _fn in (get_preference, list_preferences, get_corrections, get_contact_note):
    for _group in _TOOL_REGISTRY:
        if _fn not in _TOOL_REGISTRY[_group]:
            _TOOL_REGISTRY[_group].append(_fn)

QUERY_TOOLS = get_agent_tools("query")
ENRICH_TOOLS = get_agent_tools("enrich")
SCORING_TOOLS = get_agent_tools("scoring")
DRAFTING_TOOLS = get_agent_tools("drafting")
PIPELINE_TOOLS = get_agent_tools("pipeline")
ADMIN_TOOLS = get_agent_tools("admin")

# ---------------------------------------------------------------------------
# All tools — generated from registry (deduplicated, stable order)
# ---------------------------------------------------------------------------

def _build_all_tools() -> list:
    """Build ALL_TOOLS from registry, deduplicating shared tools."""
    seen = set()
    tools = []
    for group in ["query", "enrich", "scoring", "drafting", "pipeline", "admin"]:
        for t in get_agent_tools(group):
            if t.name not in seen:
                seen.add(t.name)
                tools.append(t)
    return tools

ALL_TOOLS = _build_all_tools()
