"""Enrichment subagent — deterministic enrichment with LLM summarization only.

Uses a deterministic workflow: domain lookup → fetch page → web search fallback
→ LLM summarization → save. The agent just identifies WHICH contacts to enrich.
"""
from langchain.agents import create_agent
from langchain_core.tools import tool

from outreach_pipeline.graph.agent import _llm, DATE_HANDLING_INSTRUCTIONS, MEMO_INSTRUCTIONS
from outreach_pipeline.graph.middleware import ScopeMiddleware
from outreach_pipeline.graph.state import OutreachState
from outreach_pipeline.graph.tools import search_contacts, get_engagements, update_memo


def _append_meta_suffixes(base: str, props: dict, website_used: str) -> str:
    """Append `|industry:…|region:…|website:…` suffixes to the tool
    result string. Order matters — the UI pops them from the end
    right-to-left (website first), so website MUST be last to keep
    the existing `\\s*\\|website:(\\S+)\\s*$` regex match."""
    industry = (props.get("industry") or "").strip() if props else ""
    region = (props.get("region") or "").strip() if props else ""
    out = base
    if industry and "|industry:" not in out:
        out = f"{out} |industry:{industry}"
    if region and "|region:" not in out:
        out = f"{out} |region:{region}"
    if website_used and "|website:" not in out:
        out = f"{out} |website:{website_used}"
    return out


async def _enrich_one(contact_id: str):
    """Core enrichment logic — called by both the single and batch tools."""
    # Resolve to contact_id if an engagement/submission ID was passed
    from outreach_pipeline.tools.workflow import _resolve_engagement
    from outreach_pipeline.db.contacts import get_contact

    contact = await get_contact(str(contact_id))
    if not contact:
        # Try as engagement/submission ID
        try:
            eng = await _resolve_engagement(int(contact_id))
            if eng:
                contact_id = eng["contact_id"]
                contact = await get_contact(contact_id)
        except (ValueError, TypeError):
            pass
    if not contact:
        # Try as company name
        from outreach_pipeline.db.contacts import search_contacts as _search
        matches = await _search(company=str(contact_id), limit=1)
        if matches:
            contact_id = str(matches[0]["id"])
            contact = await get_contact(contact_id)
    if not contact:
        return f"Contact {contact_id} not found."

    contact_id = str(contact.get("id", contact_id))
    props = contact.get("properties", {})
    company_name = props.get("company", "")
    existing_ctx = contact.get("company_context", "")
    confidence = contact.get("enrichment_confidence", "")
    # If a human previously corrected the prospect's website, prefer
    # that over email_domain — override wins on future re-enrichments.
    website_override = (props.get("website_override") or "").strip()

    if existing_ctx and confidence != "low":
        prefix = f"[{company_name}] " if company_name else ""
        # Fix precedence: override wins regardless of email_domain.
        website_used = website_override or (
            f"https://{props['email_domain']}" if props.get("email_domain") else ""
        )
        base = f"{prefix}Already enriched {contact_id} ({confidence}): {existing_ctx}"
        return _append_meta_suffixes(base, props, website_used)

    # 1. Get domain from email. get_prospect_domain returns the key
    # `email_domain`, not `domain` — a prior version used the wrong
    # key so website_used stayed empty for the fresh-enrichment path
    # and cards rendered "no website detected".
    email_domain = ""
    try:
        from outreach_pipeline.tools.workflow import get_prospect_domain
        domain_result = await get_prospect_domain(contact_id)
        email_domain = domain_result.get("email_domain", "")
    except Exception:
        pass

    # 2. Try fetching homepage — override URL beats email_domain
    page_text = ""
    website_used = website_override or (f"https://{email_domain}" if email_domain else "")
    candidates: list[str] = []
    if website_override:
        candidates = [website_override]
    elif email_domain:
        candidates = [f"https://{email_domain}", f"https://www.{email_domain}"]
    if candidates:
        from outreach_pipeline.tools.enrich_company import afetch_page
        for url in candidates:
            result = await afetch_page(url)
            if result.get("text") and len(result["text"]) > 100:
                page_text = result["text"]
                website_used = url
                break

    # 3. Fall back to web search
    search_text = ""
    if not page_text or len(page_text) < 200:
        from outreach_pipeline.tools.search import aweb_search
        search_result = await aweb_search(f"{company_name} company")
        search_items = search_result.get("results", []) if isinstance(search_result, dict) else []
        if search_items:
            search_text = "\n".join(
                f"{r.get('title', '')}: {r.get('body', '')}"
                for r in search_items[:5]
            )

    # 4. Combine context and summarize via LLM
    context_parts = []
    if page_text:
        context_parts.append(f"Website content:\n{page_text[:1500]}")
    if search_text:
        context_parts.append(f"Web search results:\n{search_text[:1000]}")
    # Include the contact's own message as context
    message = props.get("message", "")
    if message:
        context_parts.append(f"Contact's message:\n{message[:500]}")

    context_text = "\n\n".join(context_parts) if context_parts else company_name

    from outreach_pipeline.tools.enrich_company import asummarize_and_save
    result = await asummarize_and_save(
        contact_id, context_text,
        company_name=company_name, email_domain=email_domain,
    )
    # Prepend company name so UI card can display it. Append the
    # website used as a machine-parseable suffix (`|website:URL`) so
    # the frontend card can show a link and offer correction.
    if company_name and isinstance(result, str) and not result.startswith("["):
        result = f"[{company_name}] {result}"
    if isinstance(result, str):
        result = _append_meta_suffixes(result, props, website_used)
    return result


@tool
async def enrich_contact(contact_id: str) -> dict:
    """Enrich a contact with company context using a deterministic workflow.

    Runs: domain lookup → fetch homepage → web search fallback → LLM summary → save.
    Smart ID resolution: accepts contact_id, engagement_id, submission_id, or company name.

    Returns the enrichment result with summary and confidence.
    """
    return await _enrich_one(contact_id)


@tool
async def enrich_contacts(contacts: str) -> str:
    """Enrich MULTIPLE contacts in one call.

    contacts: comma-separated company names or contact IDs.
    Example: enrich_contacts(contacts="Example Systems, Example Retail")

    Processes each one sequentially. Use this instead of calling
    enrich_contact multiple times — it's more reliable.
    """
    import json as _json
    names = [n.strip() for n in contacts.split(",") if n.strip()]
    if not names:
        return "No contacts specified."

    import json as _json
    results = []
    for name in names:
        try:
            result = await _enrich_one(name)
            text = str(result)
            import re
            # Suffixes are appended in order industry → region →
            # website. Pop them right-to-left so each regex stays
            # anchored at end-of-string and can't swallow the prior
            # suffix's value.
            website_m = re.search(r"\s*\|website:(\S+)\s*$", text)
            website = website_m.group(1) if website_m else ""
            if website_m:
                text = text[: website_m.start()].rstrip()
            region_m = re.search(r"\s*\|region:([^|]+?)\s*$", text)
            region = region_m.group(1).strip() if region_m else ""
            if region_m:
                text = text[: region_m.start()].rstrip()
            industry_m = re.search(r"\s*\|industry:([^|]+?)\s*$", text)
            industry = industry_m.group(1).strip() if industry_m else ""
            if industry_m:
                text = text[: industry_m.start()].rstrip()
            cid_match = re.search(r"[Ee]nriched\s+(\S+)", text)
            conf_match = re.search(r"\((high|medium|low)\)", text, re.IGNORECASE)
            summary_match = re.search(r"\):\s*(.+)", text)
            already = "already enriched" in text.lower()
            results.append({
                "contact": name,
                "contact_id": cid_match.group(1) if cid_match else "",
                "confidence": (conf_match.group(1).lower() if conf_match else "medium"),
                "summary": summary_match.group(1) if summary_match else text,
                "already_enriched": already,
                "website": website,
                "industry": industry,
                "region": region,
                "error": "" if cid_match else text,
            })
        except Exception as e:
            results.append({"contact": name, "error": str(e)})

    count = len([r for r in results if not r.get("error")])
    return _json.dumps({
        "results": results,
        "count": count,
        "total": len(names),
    })


_ENRICH_TOOLS = [enrich_contact, enrich_contacts, search_contacts, get_engagements, update_memo]


enrich_agent = create_agent(
    model=_llm,
    tools=_ENRICH_TOOLS,
    middleware=[ScopeMiddleware()],
    state_schema=OutreachState,
    system_prompt=(
        "You are the enrichment agent. Enrich contacts with company context.\n\n"
        + DATE_HANDLING_INSTRUCTIONS + "\n\n"
        "Workflow:\n"
        "1. Read the [CONTEXT] block in your system message AND the chat\n"
        "   history for ACTUAL company names and contact IDs. These are\n"
        "   real entities the user is working with — do not invent names.\n"
        "2. For MULTIPLE contacts: call enrich_contacts(contacts='<Real Co 1>, <Real Co 2>, ...')\n"
        "   using the REAL company names, verbatim, separated by commas.\n"
        "   NEVER pass placeholder text like 'Company A, Company B' —\n"
        "   those are documentation placeholders, not actual arguments.\n"
        "   Pass company NAMES (not numeric IDs) — the tool resolves them.\n"
        "   This is ONE call that processes all contacts. Do NOT call\n"
        "   enrich_contact multiple times — use the batch tool instead.\n"
        "3. For a SINGLE contact: call enrich_contact(contact_id='<Real Company Name>').\n"
        "4. If the user says bare 're-enrich' or 'enrich them' with no names,\n"
        "   scan the conversation for the most recently listed set of contacts\n"
        "   (e.g. a Leads table) and use THOSE names. Do not fabricate.\n"
        "5. After enrichment is done, say 'Enrichment complete' with a brief count.\n"
        "   Do NOT repeat summaries — the UI already shows visual cards."
        + MEMO_INSTRUCTIONS
    ),
    name="enrichment",
)
