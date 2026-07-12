"""Pipeline subagent — thin LLM wrapper around tools.

The LLM resolves relative dates (today, last week, etc.) using the
date context injected into its system prompt and calls the right
tool — no deterministic regex branches. `run_pipeline` invokes the
pipeline StateGraph subgraph (ingest → enrich + score fan-out →
sequential drafting with HITL).
"""
from __future__ import annotations

from langchain.agents import create_agent
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from outreach_pipeline.graph.agent import _llm, DATE_HANDLING_INSTRUCTIONS, MEMO_INSTRUCTIONS
from outreach_pipeline.graph.middleware import ScopeMiddleware
from outreach_pipeline.graph.state import OutreachState
from outreach_pipeline.graph.tools import get_agent_tools


@tool
async def run_pipeline(
    start_date: str,
    end_date: str = "",
    config: RunnableConfig = None,  # injected by LangChain tool runner
) -> str:
    """Run the full outreach pipeline (ingest → enrich + score → draft w/ HITL).

    start_date: YYYY-MM-DD or a relative expression like today,
        yesterday, last week, this week, or last 7 days. The tool
        resolves relative dates at execution time.
    end_date: YYYY-MM-DD or a relative expression. Optional; defaults to start_date for a
        single-day run.

    Returns a markdown summary of the pipeline results.
    """
    from outreach_pipeline.graph.workflows.full_pipeline import pipeline_graph
    from outreach_pipeline.config import load_config
    from outreach_pipeline.tools.date_ranges import resolve_date_range

    cfg = load_config()
    start, end = resolve_date_range(
        start_date,
        end_date,
        default="yesterday",
        timezone_name=cfg.get("report_timezone", "America/New_York"),
    )
    result = await pipeline_graph.ainvoke(
        {"start_date": start, "end_date": end},
        config or {},
    )
    return _format_pipeline_summary(result)


_pipeline_llm_agent = create_agent(
    model=_llm,
    tools=[run_pipeline, *get_agent_tools("pipeline")],
    middleware=[ScopeMiddleware()],
    state_schema=OutreachState,
    system_prompt=(
        "You are the pipeline operations agent.\n\n"
        + DATE_HANDLING_INSTRUCTIONS + "\n\n"
        "Tools:\n"
        "- run_pipeline(start_date, end_date): Run the FULL pipeline\n"
        "  end-to-end for a date or date range. Use when the user says\n"
        "  'run pipeline', 'run today's pipeline', 'run last week', etc.\n"
        "- ingest_contacts: Fetch NEW MQLs from HubSpot for a given date.\n"
        "- monitor_replies: Check HubSpot for email replies.\n"
        "- export_pipeline: Export to Excel.\n\n"
        "Date handling: if the user uses relative dates, pass those words\n"
        "to pipeline tools. Do not turn them into concrete dates yourself;\n"
        "the tools resolve them at execution time.\n\n"
        "CRITICAL: After ingest_contacts, ALWAYS include the contact_id "
        "and engagement_id for each contact in your response. Format:\n"
        "- Company Name (contact_id: <contact_id>, engagement: #5042)\n"
        "Only use IDs returned by tools — never invent them."
        + MEMO_INSTRUCTIONS
    ),
    name="pipeline",
)




def _format_pipeline_summary(result: dict) -> str:
    """Format structured pipeline log into a readable markdown summary."""
    import json

    log_entries = result.get("log", [])

    # Parse structured log entries
    ingest_info = None
    contacts = []
    drafts_info = None

    for entry in log_entries:
        try:
            data = json.loads(entry)
        except (json.JSONDecodeError, TypeError):
            continue
        if data.get("type") == "ingest":
            ingest_info = data
        elif data.get("type") == "contact":
            contacts.append(data)
        elif data.get("type") == "drafts":
            drafts_info = data

    lines = ["## Pipeline Complete\n"]

    # Ingest section
    if ingest_info:
        date = ingest_info.get("date_range", "")
        fetched = ingest_info.get("fetched", 0)
        to_process = ingest_info.get("to_process", 0)
        lines.append(f"**Date**: {date}")
        lines.append(f"**Contacts**: {fetched} fetched, {to_process} to process\n")

    # Contacts with engagements
    if contacts:
        lines.append("### Contacts\n")
        for c in contacts:
            company = c.get("company") or c.get("contact_id", "?")
            cid = c.get("contact_id", "")
            enriched = "enriched" if c.get("enriched") else "not enriched"
            ready_count = c.get("ready_count", 0)
            engs = c.get("engagements", [])

            lines.append(f"**{company}** (contact: {cid}, {enriched}, {ready_count} ready for drafting)")
            if engs:
                for e in engs:
                    score = e.get("score", 0)
                    status = e.get("status", "?")
                    product = e.get("product", "")
                    kind = e.get("kind", "")
                    eid = e.get("id", "")
                    badge = f" | {product}" if product and product != "general" else ""
                    lines.append(f"- #{eid} {kind} — score **{score}**/100 — _{status}_{badge}")
            else:
                lines.append("- No engagements")
            lines.append("")

    # Drafts section
    if drafts_info:
        dr = drafts_info.get("results", [])
        approved = [d for d in dr if d.get("status") == "approved"]
        rejected = [d for d in dr if d.get("status") == "rejected"]
        failed = [d for d in dr if d.get("status") == "draft_failed"]

        lines.append("### Drafts\n")

        if approved:
            lines.append(f"**{len(approved)} approved**:\n")
            for d in approved:
                company = d.get("company", "")
                subject = d.get("subject", "")
                eid = d.get("engagement_id", "")
                lines.append(f"- **{company}** (#{eid}): _{subject}_")
            lines.append("")

        if rejected:
            lines.append(f"**{len(rejected)} rejected**\n")
        if failed:
            lines.append(f"**{len(failed)} failed** to generate\n")

        if not dr:
            lines.append("No engagements ready for drafting.\n")

    return "\n".join(lines)


async def pipeline_node(state: dict, config: RunnableConfig) -> dict:
    """Pipeline specialist — thin wrapper around the LLM sub-agent.

    All routing and date handling is delegated to the LLM. The
    run_pipeline tool receives the injected RunnableConfig so the
    pipeline subgraph can persist interrupts on the current thread.
    """
    return await _pipeline_llm_agent.ainvoke(state, config)
