"""Suggester agent — CopilotKit `copilotkitSuggest` provider.

Purpose: when the browser triggers CopilotKit's built-in dynamic
suggestions mechanism, it clones an agent and forces a `tool_choice:
copilotkitSuggest` call. Our main Outreach graph goes through a router
and specialist agents whose tool registries don't include
`copilotkitSuggest`, so the built-in flow wouldn't work against it.

This file defines a tiny, single-node graph that DOES bind
`copilotkitSuggest`. It runs under its own AG-UI endpoint
(`/copilotkit-suggester`) and is registered as a second agent in the
Next.js CopilotRuntime, so `providerAgentId: "suggester"` from the
frontend lands here.

Cost: one LLM call per assistant turn, with the same model as the main
agent (set via OUTREACH_MODEL). Switch to a cheaper model later if
needed by introducing a SUGGESTER_MODEL env var.
"""
from __future__ import annotations

from typing import List

from langchain_core.tools import tool
from langgraph.graph import StateGraph, MessagesState, END
from pydantic import BaseModel, Field

from outreach_pipeline.graph.agent import _llm


class _Suggestion(BaseModel):
    title: str = Field(
        description="Short button label (2-4 words) shown on the suggestion chip."
    )
    message: str = Field(
        description="The full user prompt that will be sent to the assistant when the chip is clicked."
    )


@tool
def copilotkitSuggest(suggestions: List[_Suggestion]) -> str:
    """Provide 2-3 concrete follow-up suggestions the user might want to say next.

    Pass a list of {title, message} objects. Titles are short button
    labels; messages are complete prompts the assistant can act on.
    """
    # The agent never executes this tool — CopilotKit harvests the
    # tool_call args from the AIMessage. Body is a no-op.
    return "ok"


_SUGGESTER_SYSTEM = """\
You generate follow-up suggestions for a sales outreach assistant.

Read the conversation history and return 2-3 concrete, useful next
actions the user is likely to want. Each suggestion MUST be a
complete prompt the user could send the assistant.

Rules:
- Always call the `copilotkitSuggest` tool — never reply with free
  text. This is your only output channel.
- NEVER invent numeric IDs. If a number (draft ID, engagement ID,
  contact ID, etc.) does not appear VERBATIM in the conversation, do
  NOT include it in a suggestion. Prefer company names in every
  suggestion — the backend resolves names to IDs reliably. When you
  MUST reference a numeric ID, copy it character-for-character from
  the conversation; do not shorten, round, or renumber.
- "Draft ID" is almost always wrong to invent — the drafts card only
  shows engagement IDs. If you want to reference a specific draft,
  say "the draft for <Company>", never "draft ID N".
- If the conversation has a batch of entities (multiple contacts or
  engagements), include at least one compound action that covers all
  of them (e.g. "Enrich Acme, Beta, Gamma, then score and draft
  emails for each").
- Skip actions that were clearly just performed — don't suggest
  "score engagement #42" if it was just scored.
- Prefer workflow progression: after enrich → suggest score + draft,
  after score → suggest draft, after draft → suggest approve/send.
- Mix action types across the 2-3 suggestions: one pipeline-progression
  action (enrich/score/draft), one detail/exploration action (show
  submissions, show details, show history), and optionally a zoom-out
  (stats, replies).
- For a listed set of contacts, "Show submission history for <list>"
  is a strong follow-up the user often wants.
- ALWAYS include at least one pipeline-progression action, even when
  the last tool was a detail view (get_lead_details,
  get_engagement_details, get_contact_history, get_submissions). The
  user has just finished exploring — the natural next move is to
  enrich, score, or draft. Don't only suggest more queries.
- ALWAYS include at least one QUERY action in every suggestion set —
  something that shows, lists, or views data WITHOUT mutating it.
  Examples: "show submissions for <Company>", "show their engagements",
  "show full history for <Company>", "show drafts for <Company>",
  "show pipeline stats for this week", "check for new replies".
  Rationale: a reviewer often wants to inspect context before
  committing to an action — never leave them with only progression
  options. Two out of three suggestions may be actions; at least one
  must be a query.
- Titles are 2-4 words. Messages are complete sentences.

The user has this outreach pipeline available:
- fetch / ingest contacts from HubSpot MQLs
- enrich contacts (company context)
- score engagements (fit + intent)
- draft personalized emails
- review + send drafts
- monitor replies, opt out negatives
- run the full pipeline end-to-end
- query leads, engagements, submissions, drafts, pipeline stats
- show submission history for any contact(s)
- show full contact history (submissions + engagements together)
"""


def _clean_orphans(messages):
    """Make the message list OpenAI-safe before sending to the suggester.

    CopilotKit clones the consumer agent's messages into the suggester.
    The list is built from streamed events, and our server-side filter
    sometimes drops silent-tool results to keep the UI clean. OpenAI
    rejects the list if either half of a tool exchange is missing:

      - orphan tool message      → no preceding tool_call  → dropped
      - orphan assistant.tool_call → no following tool result
                                   → tool_call stripped from that
                                     assistant message (and the
                                     message is dropped entirely if
                                     removing the tool_call leaves
                                     it with no content)

    Both cases would surface as
    `tool_call_ids did not have response messages`.
    """
    from langchain_core.messages import AIMessage, ToolMessage

    def _is_tool(m):
        return isinstance(m, ToolMessage) or (
            isinstance(m, dict) and m.get("role") == "tool"
        )

    def _tool_call_id(m):
        if isinstance(m, ToolMessage):
            return getattr(m, "tool_call_id", None)
        if isinstance(m, dict):
            return m.get("tool_call_id") or m.get("toolCallId")
        return None

    def _tc_id(tc):
        if isinstance(tc, dict):
            return tc.get("id") or (tc.get("function") or {}).get("id")
        return getattr(tc, "id", None)

    def _get_tool_calls(m):
        if hasattr(m, "tool_calls"):
            return getattr(m, "tool_calls", None) or []
        if isinstance(m, dict):
            return m.get("tool_calls") or []
        return []

    def _has_content(m):
        if hasattr(m, "content"):
            c = getattr(m, "content", "")
        elif isinstance(m, dict):
            c = m.get("content", "")
        else:
            c = ""
        return bool(c and (c if isinstance(c, str) else True))

    # Pass 1: walk forward. OpenAI requires assistant.tool_calls BEFORE
    # the tool response, not just "both halves present somewhere". A
    # tool message is valid iff a matching assistant.tool_call appeared
    # earlier in the list (seen_assistant_tc_ids at that point). Both
    # shapes land here:
    #   - orphan tool message (no matching assistant anywhere) → invalid
    #   - out-of-order tool message (assistant comes LATER) → invalid,
    #     OpenAI rejects with "tool role must follow a preceding
    #     tool_calls"
    #   - assistant with unanswered tool_calls (no matching tool after)
    #     → stripped in pass 2 via `answered`
    seen_assistant_tc_ids: set[str] = set()
    valid_tool_indices: set[int] = set()
    answered: set[str] = set()
    for i, m in enumerate(messages):
        if _is_tool(m):
            tid = _tool_call_id(m)
            if tid and tid in seen_assistant_tc_ids:
                valid_tool_indices.add(i)
                answered.add(tid)
            # else: orphan or out-of-order — will be dropped in pass 2
        else:
            for tc in _get_tool_calls(m):
                tcid = _tc_id(tc)
                if tcid:
                    seen_assistant_tc_ids.add(tcid)

    # Pass 2: rebuild. Keep tool messages marked valid. Strip tool_calls
    # from assistants that didn't get an in-order response.
    cleaned = []
    for i, m in enumerate(messages):
        if _is_tool(m):
            if i in valid_tool_indices:
                cleaned.append(m)
            continue

        tcs = _get_tool_calls(m)
        if tcs:
            kept = [tc for tc in tcs if _tc_id(tc) in answered]
            if not kept and not _has_content(m):
                # Tool-call-only assistant with no surviving calls → drop
                continue
            if len(kept) != len(tcs):
                # Rebuild the message without the stripped tool_calls.
                # AIMessage is immutable-ish; use .model_copy with update.
                # CRITICAL: ALSO update additional_kwargs["tool_calls"] to
                # the kept-id subset. langchain_openai's serializer falls
                # back to additional_kwargs when .tool_calls is empty,
                # and even when non-empty it can leak OpenAI-shape
                # entries the LLM sees as unanswered. Rebuild both
                # surfaces from `kept` (indexed by id) so they agree.
                if isinstance(m, AIMessage):
                    kept_ids = {_tc_id(tc) for tc in kept}
                    akw = dict(getattr(m, "additional_kwargs", None) or {})
                    raw_tcs = akw.get("tool_calls") or []
                    akw["tool_calls"] = [
                        rtc for rtc in raw_tcs if _tc_id(rtc) in kept_ids
                    ]
                    if not akw["tool_calls"]:
                        akw.pop("tool_calls", None)
                    m = m.model_copy(
                        update={"tool_calls": kept, "additional_kwargs": akw}
                    )
                elif isinstance(m, dict):
                    kept_ids = {_tc_id(tc) for tc in kept}
                    new_m = {**m, "tool_calls": kept}
                    if "additional_kwargs" in new_m:
                        akw = dict(new_m["additional_kwargs"] or {})
                        raw_tcs = akw.get("tool_calls") or []
                        akw["tool_calls"] = [
                            rtc for rtc in raw_tcs if _tc_id(rtc) in kept_ids
                        ]
                        if not akw["tool_calls"]:
                            akw.pop("tool_calls", None)
                        new_m["additional_kwargs"] = akw
                    m = new_m
        cleaned.append(m)
    return cleaned


def _suggest_node(state: MessagesState) -> dict:
    llm_with_tool = _llm.bind_tools(
        [copilotkitSuggest],
        tool_choice={"type": "function", "function": {"name": "copilotkitSuggest"}},
    )
    messages = _clean_orphans(state["messages"])
    # Prepend system guidance if the caller didn't include one. The
    # built-in prompt from CopilotKit is a user message — leaving it
    # alone keeps their tool-choice plumbing intact.
    from langchain_core.messages import SystemMessage
    has_system = any(getattr(m, "type", None) == "system" for m in messages)
    if not has_system:
        messages = [SystemMessage(content=_SUGGESTER_SYSTEM), *messages]
    response = llm_with_tool.invoke(messages)
    return {"messages": [response]}


def build_suggester_graph():
    """Compile the one-node suggester graph.

    Uses an in-memory checkpointer — suggestion runs are ephemeral
    (fresh thread per request) and never need to survive a restart.
    ag_ui_langgraph requires *some* checkpointer to call aget_state.
    """
    from langgraph.checkpoint.memory import MemorySaver
    g = StateGraph(MessagesState)
    g.add_node("suggest", _suggest_node)
    g.add_edge("__start__", "suggest")
    g.add_edge("suggest", END)
    return g.compile(checkpointer=MemorySaver())
