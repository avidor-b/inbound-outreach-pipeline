"""Admin subagent — status changes, deletions, opt-outs, content management."""
from langchain.agents import create_agent

from outreach_pipeline.graph.agent import _llm, DATE_HANDLING_INSTRUCTIONS, MEMO_INSTRUCTIONS
from outreach_pipeline.graph.middleware import ReviewMiddleware
from outreach_pipeline.graph.state import OutreachState
from outreach_pipeline.graph.tools import get_agent_tools

admin_agent = create_agent(
    model=_llm,
    tools=get_agent_tools("admin"),
    middleware=[ReviewMiddleware()],
    state_schema=OutreachState,
    system_prompt=(
        "You are the admin agent. Execute admin operations immediately when asked.\n\n"
        + DATE_HANDLING_INSTRUCTIONS + "\n\n"
        "Operations:\n"
        "- update_engagement_status: Change engagement status\n"
        "- create_retarget: Create retarget for sent/closed engagements\n"
        "- reclassify_submission: Change submission intent/product\n"
        "- add_opt_out: Add contact to do-not-contact list\n"
        "- add_scrape_block: Block domain from scraping\n"
        "- preview_delete_contact: Show what WOULD be deleted (no action)\n"
        "- delete_contact: ACTUALLY delete the contact and all data\n"
        "- index_content: Index files into RAG collection\n"
        "- grade_collection: Grade RAG collection quality\n\n"
        "CRITICAL — deletion rules:\n"
        "- Call delete_contact directly — it shows a confirmation dialog automatically.\n"
        "- Do NOT call preview_delete_contact before delete_contact.\n"
        "- When deleting MULTIPLE contacts, call delete_contact for ONE contact at a time.\n"
        "  NEVER make multiple parallel delete_contact calls in one response.\n"
        "- If the result says 'cancelled': the user cancelled. STOP. Do NOT retry.\n"
        "  Just say 'Delete cancelled' and wait for further instructions.\n\n"
        "Email sending is DISABLED."
        + MEMO_INSTRUCTIONS
    ),
    name="admin",
)
