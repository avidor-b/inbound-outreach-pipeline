"""Query subagent — retrieves and summarizes pipeline data."""
from langchain.agents import create_agent

from outreach_pipeline.graph.agent import _llm, DATE_HANDLING_INSTRUCTIONS, MEMO_INSTRUCTIONS
from outreach_pipeline.graph.middleware import ScopeMiddleware
from outreach_pipeline.graph.state import OutreachState
from outreach_pipeline.graph.tools import get_agent_tools

query_agent = create_agent(
    model=_llm,
    tools=get_agent_tools("query"),
    middleware=[ScopeMiddleware()],
    state_schema=OutreachState,
    system_prompt=(
        "You are the data retrieval agent. Your job is to query the pipeline database "
        "and return clear, structured results.\n\n"
        + DATE_HANDLING_INSTRUCTIONS + "\n\n"
        "IMPORTANT: The UI renders tool results as visual cards (tables, detail "
        "panels, stat grids) automatically. After calling a tool, DO NOT repeat "
        "the full data in your text response — the user already sees it in the card. "
        "Instead, add only a brief one-sentence observation if relevant.\n\n"
        "CRITICAL: For follow-up queries ('show their submissions', 'score them'), "
        "pass the COMPANY NAME to the tool — not the numeric ID. All tools accept "
        "company names and resolve them automatically. Example: "
        "get_submissions(contact_id='Example Retail') works. This avoids errors from "
        "misremembered numeric IDs.\n\n"
        "CRITICAL: When the user asks for submissions across MULTIPLE companies "
        "(anything of the form 'show submissions for A, B, C'), call "
        "get_submissions_bulk(contacts='A, B, C') — ONE call, comma-separated. "
        "Do NOT call get_submissions in a loop per name — that pattern has "
        "produced duplicate and missing cards in practice. The bulk tool "
        "dedups internally and returns all payloads in a single structured "
        "response.\n\n"
        "CRITICAL: Contact IDs are opaque CRM identifiers. "
        "Engagement IDs are short numbers like 5042. NEVER make up or guess IDs.\n"
        "When the user mentions a company name, ALWAYS use search_contacts(company='...') "
        "first to find the correct contact_id. Then use that contact_id for other tools.\n"
        "If the user asks about 'that contact' or 'show details', look for the "
        "contact_id in the conversation history. If you can't find it, use "
        "get_leads to search by date or company."
        + MEMO_INSTRUCTIONS
    ),
    name="query",
)
