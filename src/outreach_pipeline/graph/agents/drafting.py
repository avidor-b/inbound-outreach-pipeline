"""Drafting subagent — deterministic draft writing with LLM composition only.

Uses the pipeline's awrite_draft + avalidate_with_retry for consistent results.
The agent identifies WHICH engagement to draft, the tool handles everything.
DraftReviewMiddleware after_agent hook handles approval via interrupt().
"""
from langchain.agents import create_agent
from langchain_core.tools import tool

from outreach_pipeline.graph.agent import _llm, DATE_HANDLING_INSTRUCTIONS, MEMO_INSTRUCTIONS
from outreach_pipeline.graph.middleware import DraftReviewMiddleware
from outreach_pipeline.graph.state import OutreachState
from outreach_pipeline.graph.tools import search_contacts, get_engagements, update_memo


@tool
async def draft_email(engagement_id: str) -> str:
    """Write a draft email for an engagement using the pipeline's deterministic logic.

    Runs: context assembly → guidelines → RAG → LLM draft → tone validation → auto-fix.

    Smart ID resolution: accepts
      - an engagement_id (integer like 42)
      - a contact_id (long numeric string)
      - a submission_id
      - a COMPANY NAME (e.g. "Example Systems")
    Prefer passing the company name — numeric IDs are easy to hallucinate.

    Returns the draft in ENGAGEMENT/SUBJECT/body format for the approval flow.
    Multiple drafts can be generated in parallel — the approval middleware
    handles them sequentially (one interrupt per draft).
    """
    from outreach_pipeline.tools.workflow import _resolve_engagement
    from outreach_pipeline.db.contacts import search_contacts as _search_contacts
    from outreach_pipeline.db.engagements import list_engagements as _list_engagements

    raw = str(engagement_id).strip()
    eng = None

    # Numeric → try as ID. Non-numeric → resolve via company name.
    if raw.isdigit():
        eng = await _resolve_engagement(int(raw))
    else:
        matches = await _search_contacts(company=raw, limit=1)
        if matches:
            cid = str(matches[0]["id"])
            engs = await _list_engagements(contact_id=cid)
            for status in ("scoring", "open", "drafting", "pending_approval"):
                for e in engs:
                    if e.get("status") == status:
                        eng = e
                        break
                if eng:
                    break
            if not eng and engs:
                eng = next(
                    (e for e in engs if e.get("status") not in ("closed", "blocked")),
                    engs[0],
                )

    if not eng:
        return f"Could not resolve '{engagement_id}' to an engagement."

    eid = eng["id"]
    status = eng.get("status", "")

    if status in ("closed", "blocked"):
        return f"Engagement #{eid} is {status} — cannot draft."

    from outreach_pipeline.graph.draft_helpers import awrite_draft, avalidate_with_retry, avalidate_cta

    draft = await awrite_draft(eid)
    body = draft.get("body", "")
    subject = draft.get("subject", "")
    rag_context = draft.get("rag_context", "")
    promised_resources = draft.get("promised_resources", []) or []
    referenced_ids = draft.get("referenced_resource_ids", []) or []
    unverified_promises = draft.get("unverified_promises", []) or []

    if not body:
        return f"Failed to generate draft for engagement #{eid}."

    # Validate tone + CTA alignment with score
    body, _passed = await avalidate_with_retry(body)
    # Get contact message for context-specific CTA
    from outreach_pipeline.db.engagements import get_submissions as _get_subs
    cta_subs = await _get_subs(eid)
    cta_msg = next((s.get("message", "") for s in cta_subs if s.get("message")), "")
    body = await avalidate_cta(
        body, eng.get("score", 0),
        contact_message=cta_msg,
        product=eng.get("primary_product", ""),
    )

    # Re-run promise extraction after validators may have rewritten the
    # body. URLs and promise phrases can shift during tone/CTA fixes.
    try:
        from outreach_pipeline.tools.resource_catalog import extract_promises
        referenced_ids, unverified_promises = extract_promises(
            body, promised_resources,
        )
    except Exception:
        pass

    import json as _json
    # Return in the format DraftReviewMiddleware expects. The three new
    # header lines carry JSON so the middleware can parse without
    # ambiguity (titles and promise phrases may contain : and |).
    return (
        f"ENGAGEMENT: {eid}\n"
        f"RAG_CONTEXT: {rag_context}\n"
        f"PROMISED_RESOURCES: {_json.dumps(promised_resources)}\n"
        f"REFERENCED_IDS: {_json.dumps(referenced_ids)}\n"
        f"UNVERIFIED_PROMISES: {_json.dumps(unverified_promises)}\n"
        f"SUBJECT: {subject}\n\n"
        f"{body}"
    )


@tool
async def review_pending_drafts() -> str:
    """Review all pending drafts saved by the pipeline.

    Fetches drafts with 'draft' status and returns the FIRST one in
    ENGAGEMENT/SUBJECT/body format for the approval flow.
    After approval/rejection, call this again for the next one.
    """
    import json as _json
    from outreach_pipeline.db.drafts import get_drafts
    # db.drafts.get_drafts returns a list directly, not a {"drafts": [...]}
    # dict. Use the list as-is — older code mistakenly treated it as a dict.
    drafts = await get_drafts(status="draft")

    if not drafts:
        return "No pending drafts to review."

    d = drafts[0]
    eid = d.get("engagement_id", 0)
    subject = d.get("subject", "")
    body = d.get("body", "")
    rag_context = d.get("rag_context", "")
    # Resource fields were added in migration 002 — default to [] for
    # older drafts that predate the catalog feature.
    promised_resources = d.get("promised_resources") or []
    referenced_ids = d.get("referenced_resource_ids") or []
    unverified_promises = d.get("unverified_promises") or []
    remaining = len(drafts) - 1

    return (
        f"ENGAGEMENT: {eid}\n"
        f"RAG_CONTEXT: {rag_context}\n"
        f"PROMISED_RESOURCES: {_json.dumps(promised_resources)}\n"
        f"REFERENCED_IDS: {_json.dumps(referenced_ids)}\n"
        f"UNVERIFIED_PROMISES: {_json.dumps(unverified_promises)}\n"
        f"SUBJECT: {subject}\n\n"
        f"{body}\n\n"
        f"[{remaining} more pending draft{'s' if remaining != 1 else ''} after this one]"
    )


_DRAFT_TOOLS = [draft_email, review_pending_drafts, search_contacts, get_engagements, update_memo]


draft_agent = create_agent(
    model=_llm,
    tools=_DRAFT_TOOLS,
    middleware=[DraftReviewMiddleware()],
    state_schema=OutreachState,
    system_prompt=(
        "You are the email drafting agent. Draft or review ONE email per turn.\n\n"
        + DATE_HANDLING_INSTRUCTIONS + "\n\n"
        "Workflow:\n"
        "1. If the user says 'review drafts', call review_pending_drafts.\n"
        "   The system will show a Draft Review card for approval.\n"
        "   After approval/rejection, call review_pending_drafts again for the next.\n"
        "2. If the user wants a new draft:\n"
        "   - PREFER calling draft_email(engagement_id='Company Name') directly.\n"
        "     The tool resolves the company name to the right engagement.\n"
        "   - Only fall back to search_contacts + get_engagements if the\n"
        "     company name is ambiguous or the user gave you an ID already.\n"
        "3. The draft_email tool handles everything:\n"
        "   context, guidelines, RAG, LLM drafting, tone validation.\n"
        "4. The system will show a Draft Review card for user approval.\n\n"
        "CRITICAL:\n"
        "- Call draft_email for ONE engagement only per turn.\n"
        "- NEVER invent numeric engagement IDs. Use the COMPANY NAME\n"
        "  (from the conversation or memo) instead — the tool resolves it.\n"
        "  If you don't have a company name either, call search_contacts /\n"
        "  get_engagements first; do NOT guess digits.\n"
        "- Do NOT write emails yourself — always use the draft_email tool."
        + MEMO_INSTRUCTIONS
    ),
    name="drafting",
)
