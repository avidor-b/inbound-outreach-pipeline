"""MCP server — thin wrappers over tools/ functions.

All business logic lives in tools/. MCP tools just format results as strings.
When a LangGraph or PydanticAI agent replaces this, it calls the same tools/.
"""
from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("outreach-pipeline")


# ── Engagement tools ──────────────────────────────────────────────────

@mcp.tool()
def get_engagements(kind: str = "", status: str = "", contact_id: str = "",
                    min_score: int = 0, limit: int = 50) -> str:
    """Get engagements (unit of work). Filter by kind, status, score, contact."""
    from outreach_pipeline.tools.engagements import get_engagements as _get
    result = _get(kind=kind, status=status, contact_id=contact_id, min_score=min_score, limit=limit)
    engs = result["engagements"]
    if not engs:
        return "No engagements found."
    lines = [f"Found {result['count']} engagements:\n"]
    for e in engs:
        lines.append(
            f"  #{e['id']}  {e['kind']:18s}  status={e['status']:15s}  "
            f"score={e.get('score', 0):3d}  product={e.get('primary_product', ''):12s}  "
            f"contact={e['contact_id']}"
        )
    return "\n".join(lines)


@mcp.tool()
def get_engagement_details(engagement_id: int) -> str:
    """Full details: contact + submissions + engagement state."""
    from outreach_pipeline.tools.engagements import get_engagement_details as _get
    r = _get(engagement_id)
    if not r["engagement"]:
        return f"Engagement {engagement_id} not found."
    eng = r["engagement"]
    contact = r["contact"] or {}
    props = contact.get("properties", {})
    lines = [
        f"Engagement #{eng['id']}:  {eng['kind']}  status={eng['status']}  score={eng.get('score', 0)}",
        f"  Product: {eng.get('primary_product', '')}",
        f"  Contact: {contact.get('id', '?')} ({props.get('company', '?')})",
        f"  Context: {contact.get('company_context', '')[:200]}",
        f"\n  Submissions ({len(r['submissions'])}):",
    ]
    for s in r["submissions"]:
        msg = (s.get("message", "") or "")[:100]
        lines.append(f"    [{s.get('mql_date', '?')[:10]}] {s.get('form_name', '')}: {msg}")
    if eng.get("block_code"):
        lines.append(f"\n  BLOCKED: {eng['block_code']} — {eng.get('block_reason', '')}")
    return "\n".join(lines)


@mcp.tool()
def get_contact_history(contact_id: str) -> str:
    """Full picture: contact + all submissions + all engagements."""
    from outreach_pipeline.tools.engagements import get_contact_history as _get
    r = _get(contact_id)
    if not r["contact"]:
        return f"Contact {contact_id} not found."
    props = r["contact"].get("properties", {})
    lines = [
        f"Contact {contact_id}: {props.get('company', '?')}",
        f"  Context: {r['contact'].get('company_context', '')[:200]}",
        f"\n  Submissions ({len(r['submissions'])}):",
    ]
    for s in r["submissions"]:
        msg = (s.get("message", "") or "")[:80]
        lines.append(f"    [{s.get('mql_date', '?')[:10]}] {s.get('intent_type', '?'):18s} {msg}")
    lines.append(f"\n  Engagements ({len(r['engagements'])}):")
    for e in r["engagements"]:
        lines.append(f"    #{e['id']}  {e['kind']:18s}  status={e['status']:15s}  score={e.get('score', 0)}")
    return "\n".join(lines)


@mcp.tool()
def get_submissions(contact_id: str, include_migration: bool = False) -> str:
    """Submission history for a contact. Excludes migrated data by default."""
    from outreach_pipeline.tools.engagements import get_submissions as _get
    r = _get(contact_id, include_migration=include_migration)
    if not r["submissions"]:
        return f"No submissions for {contact_id}."
    lines = [f"Found {r['count']} submissions:\n"]
    for s in r["submissions"]:
        msg = (s.get("message", "") or "")[:80]
        lines.append(
            f"  [{s.get('mql_date', '?')[:10]}] {s.get('intent_type', '?'):18s} "
            f"product={s.get('product_family', ''):12s} {msg}"
        )
    return "\n".join(lines)


@mcp.tool()
def update_engagement_status(engagement_id: int, new_status: str,
                              block_code: str = "", block_reason: str = "") -> str:
    """Update engagement status. Valid: open→scoring→drafting→pending_approval→approved→sent→closed.
    Also: drafting or pending_approval → sdr_handoff (flag for human SDR follow-up) → closed."""
    from outreach_pipeline.tools.engagements import update_engagement_status as _update
    r = _update(engagement_id, new_status, block_code, block_reason)
    return f"Engagement #{engagement_id} → {new_status}" if r["success"] else f"Failed: {r['error']}"


@mcp.tool()
def create_retarget_engagement(original_engagement_id: int) -> str:
    """Create retarget for a non-replied sent engagement."""
    from outreach_pipeline.tools.engagements import create_retarget
    r = create_retarget(original_engagement_id)
    return f"Retarget #{r['engagement_id']} created." if r["success"] else f"Failed: {r['error']}"


@mcp.tool()
def reclassify_submission(submission_id: int, intent_type: str,
                           product_family: str = "", support_flag: bool = False,
                           event_flag: bool = False) -> str:
    """Manually reclassify a submission."""
    from outreach_pipeline.tools.engagements import reclassify_submission as _r
    r = _r(submission_id, intent_type, product_family, support_flag, event_flag)
    return f"Submission #{submission_id} → {intent_type}" if r["success"] else "Failed."


# ── Workflow tools ────────────────────────────────────────────────────

@mcp.tool()
def ingest_contacts(start_date: str, end_date: str) -> str:
    """Fetch MQLs from HubSpot, capture form submissions, classify, route to engagements."""
    import asyncio
    from outreach_pipeline.fetch_mqls import fetch_mqls, save_to_db
    from outreach_pipeline.db.connection import get_db
    from outreach_pipeline.services.intake import process_submission

    result = fetch_mqls(start_date, end_date)
    count = asyncio.run(save_to_db(result))

    conn = get_db()
    created = blocked = skipped = subs_found = 0

    for contact in result.get("contacts", []):
        props = contact.get("properties", {})
        r = process_submission(
            conn, contact["id"], message=props.get("message", ""),
            form_name="Contact Us", mql_date=props.get("hs_v2_date_entered_marketingqualifiedlead", ""),
        )
        if r.get("duplicate"): skipped += 1
        elif r.get("engagement_id"): created += 1
        elif r.get("blocked_reason"): blocked += 1

    # Fetch form submission history
    try:
        from outreach_pipeline.config import load_config
        from outreach_pipeline.hubspot_client import HubSpotClient
        from datetime import datetime
        config = load_config()
        client = HubSpotClient(config["hubspot_token"], config)
        for contact in result.get("contacts", []):
            try:
                profile = client.request_with_retry(
                    "GET", f"https://api.hubapi.com/contacts/v1/contact/vid/{contact['id']}/profile")
                for fs in profile.get("form-submissions", []):
                    ts = fs.get("timestamp", 0)
                    sub_date = datetime.fromtimestamp(ts / 1000).isoformat() if ts else ""
                    process_submission(conn, contact["id"], message="",
                                       form_name=fs.get("title", ""), form_id=fs.get("form-id", ""),
                                       page_url=fs.get("page-url", ""), mql_date=sub_date)
                    subs_found += 1
            except Exception:
                pass
    except Exception:
        pass

    conn.close()
    lines = [f"Fetched {result['total']} contacts.", f"Engagements created: {created}"]
    if blocked: lines.append(f"Blocked (opted out): {blocked}")
    if skipped: lines.append(f"Duplicates skipped: {skipped}")
    if subs_found: lines.append(f"Form submissions captured: {subs_found}")
    return "\n".join(lines)


@mcp.tool()
def get_scoring_context(engagement_id: int) -> str:
    """Full context for scoring: contact, submissions, company context, RAG, jobtitle."""
    from outreach_pipeline.tools.workflow import get_scoring_context as _get
    from outreach_pipeline.tools.pii import fetch_jobtitle
    ctx = _get(engagement_id)
    if not ctx["engagement"]:
        return f"Engagement {engagement_id} not found."
    eng = ctx["engagement"]
    contact = ctx["contact"] or {}
    props = contact.get("properties", {})
    jobtitle = fetch_jobtitle(contact.get("id", "")) or "?"
    lines = [
        f"Scoring context for engagement #{eng['id']} ({eng['kind']}):\n",
        f"  Company: {props.get('company', '?')}",
        f"  Job title: {jobtitle}",
        f"  Industry: {props.get('industry', '?')}",
        f"  Page views: {props.get('hs_analytics_num_page_views', '?')}",
        f"  Context: {contact.get('company_context', '') or 'Not enriched'}\n",
        f"  Submissions ({len(ctx['submissions'])}):",
    ]
    for s in ctx["submissions"]:
        msg = (s.get("message", "") or "")[:200]
        lines.append(f"    [{s.get('mql_date', '?')[:10]}] {s.get('form_name', '')}:")
        if msg: lines.append(f"      {msg}")
    if ctx["rag_matches"]:
        lines.append(f"\n  RAG ({len(ctx['rag_matches'])}):")
        for r in ctx["rag_matches"]:
            lines.append(f"    [{r['source']}]: {r['text'][:150]}")
    lines.append(f"\n  Score guide: 80-100=buying intent, 50-79=interest, 25-49=vague, 1-24=unlikely")
    return "\n".join(lines)


@mcp.tool()
def get_drafting_context(engagement_id: int) -> str:
    """Full context for drafting: contact, submissions, score, RAG, rules."""
    from outreach_pipeline.tools.workflow import get_drafting_context as _get
    ctx = _get(engagement_id)
    if not ctx["engagement"]:
        return f"Engagement {engagement_id} not found."
    if ctx["blocked"]:
        return f"BLOCKED ({ctx.get('block_code', '')}): {ctx.get('block_reason', '')}"
    eng = ctx["engagement"]
    contact = ctx["contact"] or {}
    props = contact.get("properties", {})
    lines = [
        f"Drafting context for engagement #{eng['id']}:  score={eng.get('score', 0)}  product={eng.get('primary_product', '')}\n",
        f"  Company: {props.get('company', '?')}",
        f"  Context: {contact.get('company_context', '') or 'Not enriched'}\n",
        f"  Messages:",
    ]
    for s in ctx["submissions"]:
        msg = (s.get("message", "") or "")[:300]
        if msg: lines.append(f"    [{s.get('mql_date', '?')[:10]}] {msg}")
    if ctx["rag_matches"]:
        lines.append(f"\n  RAG:")
        for r in ctx["rag_matches"]:
            lines.append(f"    [{r['source']}]: {r['text'][:150]}")
    lines.extend([
        f"\n  Rules: 3-4 sentences. Hi [First Name], ... Best, [Sender Name]",
        f"  Score ≥70: qualifying question. Score 50-69: offer a resource.",
        f"  NEVER reference tracking data, downloads, page views.",
    ])
    return "\n".join(lines)


@mcp.tool()
def score_engagement(engagement_id: int, score: int, score_reasoning: str,
                      primary_product: str = "") -> str:
    """Save a score for an engagement."""
    from outreach_pipeline.db.connection import get_db
    from outreach_pipeline.services.scoring import save_engagement_score
    conn = get_db()
    save_engagement_score(conn, engagement_id, score, score_reasoning, primary_product)
    conn.close()
    return f"Engagement #{engagement_id} scored: {score}."


@mcp.tool()
def save_engagement_draft(engagement_id: int, subject: str, body: str) -> str:
    """Validate and save a draft. Validates body against cold_outreach profile."""
    from outreach_pipeline.tools.workflow import save_engagement_draft as _save
    r = _save(engagement_id, subject, body)
    if r["success"]:
        return f"Draft saved for engagement #{engagement_id}: \"{subject}\""
    if "violations" in r:
        return f"Draft rejected: {'; '.join(r['violations'])}. Fix and retry."
    return f"Failed: {r.get('error', '?')}"


@mcp.tool()
def enrich_engagement(engagement_id: int, company_context: str, crm_context: str = "") -> str:
    """Save enrichment for an engagement's contact."""
    from outreach_pipeline.db.connection import get_db
    from outreach_pipeline.db.engagements import get_engagement
    from outreach_pipeline.services.enrichment import save_enrichment
    conn = get_db()
    eng = get_engagement(conn, engagement_id)
    if not eng:
        conn.close()
        return f"Engagement {engagement_id} not found."
    save_enrichment(conn, eng["contact_id"], company_context, crm_context)
    conn.close()
    return f"Engagement #{engagement_id} enriched."


@mcp.tool()
def check_engagement_policy(engagement_id: int) -> str:
    """Check all policies for an engagement."""
    from outreach_pipeline.db.connection import get_db
    from outreach_pipeline.db.engagements import get_engagement
    from outreach_pipeline.policies.engagement import check_cooldown, check_score_threshold
    from outreach_pipeline.db.guardrails import check_opt_out
    conn = get_db()
    eng = get_engagement(conn, engagement_id)
    if not eng:
        conn.close()
        return f"Engagement {engagement_id} not found."
    lines = [f"Policy check #{engagement_id}:"]
    blocked, reason = check_opt_out(conn, eng["contact_id"])
    lines.append(f"  Opt-out: {'BLOCKED — ' + reason if blocked else 'OK'}")
    cd = check_cooldown(conn, engagement_id)
    lines.append(f"  Cooldown: {'BLOCKED — ' + cd.reason if not cd.allowed else 'OK'}")
    st = check_score_threshold(conn, engagement_id)
    lines.append(f"  Score: {'BLOCKED — ' + st.reason if not st.allowed else 'OK'}")
    conn.close()
    return "\n".join(lines)


@mcp.tool()
def get_prospect_domain(contact_id: str) -> str:
    """Get likely website domain from email domain or first URL."""
    from outreach_pipeline.tools.workflow import get_prospect_domain as _get
    r = _get(contact_id)
    if not r["contact"]:
        return f"Contact {contact_id} not found."
    lines = []
    if r["email_domain"]:
        lines.append(f"Email domain: {r['email_domain']} → try https://www.{r['email_domain']}")
    if r["first_url_domain"]:
        lines.append(f"First URL domain: {r['first_url_domain']}")
    if not lines:
        lines.append(f"No domain found. Company: {r['company']}. Try searching.")
    return "\n".join(lines)


@mcp.tool()
def get_email_guidelines() -> str:
    """Get email drafting guidelines."""
    path = Path(__file__).parent.parent.parent / ".claude" / "skills" / "outreach-pipeline" / "email-guidelines.md"
    return path.read_text(encoding="utf-8") if path.exists() else "No guidelines found."


@mcp.tool()
def get_pipeline_stats() -> str:
    """Full pipeline statistics."""
    from outreach_pipeline.tools.workflow import get_pipeline_stats as _get
    s = _get()
    lines = [
        "Pipeline Stats:\n",
        f"  Contacts: {s['contacts']['total']}  (enriched: {s['contacts']['enriched']})",
        f"  Submissions: {s['submissions']['total']}",
    ]
    for intent, count in s["submissions"]["by_intent"].items():
        lines.append(f"    {intent}: {count}")
    lines.append(f"\n  Engagements: {s['engagements']['total']}")
    for kind, count in s["engagements"]["by_kind"].items():
        lines.append(f"    {kind}: {count}")
    lines.append(f"  By status:")
    for status, count in s["engagements"]["by_status"].items():
        lines.append(f"    {status}: {count}")
    sd = s["engagements"]["score_distribution"]
    lines.append(f"\n  Scores: high={sd['high']} med={sd['medium']} low={sd['low']} unscored={sd['unscored']}")
    lines.append(f"\n  Drafts: {s['drafts']['total']}  Sent: {s['outreach']['sent']}  Today: {s['outreach']['sends_today']}")
    lines.append(f"  Replies: {s['replies']['total']}  Hot leads: {s['replies']['hot_leads']}")
    lines.append(f"  Opt-outs: {s['opt_outs']}")
    return "\n".join(lines)


# ── Contact tools ─────────────────────────────────────────────────────

@mcp.tool()
def get_leads(min_score: int = 0, date: str = "", after: str = "", before: str = "",
              no_reply: bool = False, limit: int = 50) -> str:
    """Get leads. Dates use MQL date, not fetch date."""
    from outreach_pipeline.tools.contacts import get_leads as _get
    r = _get(min_score=min_score, date=date, after=after, before=before, no_reply=no_reply, limit=limit)
    if not r["leads"]:
        return "No leads found."
    lines = [f"Found {r['count']} leads:\n"]
    for l in r["leads"]:
        p = l.get("properties", {})
        lines.append(f"  {l['id']}  score={l.get('score', 0):3d}  {p.get('company', '?'):25s}  {(p.get('message', '') or '')[:60]}")
    return "\n".join(lines)


@mcp.tool()
def validate_message(text: str, profile: str = "cold_outreach") -> str:
    """Validate text against a tone profile. Profiles: cold_outreach, retargeting, followup, content_grading."""
    from outreach_pipeline.tools.messaging import validate_message as _v
    r = _v(text, profile=profile)
    if r.passed:
        return f"Passed ({profile})."
    return f"FAILED ({profile}):\n" + "\n".join(f"  - {v['label']}: {v.get('reason', '')}" for v in r.violations)


# ── RAG tools ─────────────────────────────────────────────────────────

@mcp.tool()
def query_rag(query: str, collection: str = "sales_materials", top_k: int = 3) -> str:
    """Search RAG collection."""
    from outreach_pipeline.tools.rag import query_rag as _q
    r = _q(query, collection=collection, top_k=top_k)
    if not r["results"]:
        return f"No results in '{collection}'."
    lines = [f"Top {r['count']} from '{collection}':"]
    for m in r["results"]:
        lines.append(f"\n  [{m['source']}]: {m['text'][:300]}")
    return "\n".join(lines)


@mcp.tool()
def index_content(materials_dir: str, collection: str = "sales_materials", reset: bool = False) -> str:
    """Index files into RAG."""
    from outreach_pipeline.tools.rag import index_content as _i
    r = _i(materials_dir, collection=collection, reset=reset)
    return f"Indexed {r['files_indexed']} files ({r['chunks_indexed']} chunks) into '{collection}'." if r.get("success") else f"Failed: {r.get('error')}"


@mcp.tool()
def scrape_and_index(url: str, collection: str = "", max_pages: int = 10) -> str:
    """Scrape website and index into RAG."""
    from outreach_pipeline.adapters.web import WebAdapter
    from outreach_pipeline.adapters.chromadb_adapter import ChromaDBAdapter
    from urllib.parse import urlparse
    web = WebAdapter(max_pages=max_pages)
    r = web.scrape_raw(url)
    if not r.get("success"):
        return f"Scraping failed for {url}."
    domain = urlparse(url).netloc.replace(".", "_").replace("-", "_")
    col = collection or domain
    idx = ChromaDBAdapter().index(r["output_dir"], collection_name=col, reset=True)
    return f"Scraped {r.get('pages_crawled', 0)} pages, indexed {idx.get('chunks_indexed', 0)} chunks into '{col}'."


# ── Pipeline tools ────────────────────────────────────────────────────

@mcp.tool()
def export_pipeline(output_path: str = "data/pipeline_output.xlsx") -> str:
    """Export to Excel."""
    from outreach_pipeline.adapters.excel import ExcelAdapter
    ExcelAdapter().export(output_path=output_path)
    return f"Exported to {output_path}"


@mcp.tool()
def monitor_replies(since_date: str) -> str:
    """Check HubSpot for replies since a date."""
    from outreach_pipeline.services.monitoring import fetch_replies
    r = fetch_replies(since_date)
    replies = r["replies"]
    if not replies:
        return f"No replies since {since_date}."
    lines = [f"Found {len(replies)} replies:"]
    for rep in replies:
        lines.append(f"  {rep.get('contact_id', '?')}: {rep.get('subject', '?')[:60]}")
    return "\n".join(lines)


@mcp.tool()
def grade_collection(collection: str = "sales_materials") -> str:
    """Grade content quality."""
    from outreach_pipeline.tools.grading import grade_collection as _g
    r = _g(collection=collection)
    if r["total_chunks"] == 0:
        return f"No chunks in '{collection}'."
    lines = [f"Grading '{collection}': {r['total_chunks']} chunks, score={r['overall_score']:.1f}/100"]
    if r["violations_by_type"]:
        for label, count in sorted(r["violations_by_type"].items(), key=lambda x: -x[1]):
            lines.append(f"  {label}: {count}")
    return "\n".join(lines)


# ── Guardrails ────────────────────────────────────────────────────────

@mcp.tool()
def add_opt_out(contact_id: str, reason: str = "Requested removal") -> str:
    """Add to do-not-contact list."""
    from outreach_pipeline.db.connection import get_db
    from outreach_pipeline.db.guardrails import add_opt_out as _add
    conn = get_db()
    ok = _add(conn, contact_id, reason)
    conn.close()
    return f"Added {contact_id}." if ok else f"{contact_id} already on list."


# ── Resources ─────────────────────────────────────────────────────────

@mcp.resource("pipeline://stats")
def resource_stats() -> str:
    from outreach_pipeline.tools.workflow import get_pipeline_stats as _get
    return json.dumps(_get(), default=str)


# ── Entry point ───────────────────────────────────────────────────────

def main():
    mcp.run(transport="stdio")

if __name__ == "__main__":
    main()
