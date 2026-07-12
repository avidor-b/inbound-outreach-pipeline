"""Deterministic intent classifier and message trimming for the graph router."""
from __future__ import annotations

import re

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)

# After stripping internal tool messages, keep this many human+response pairs.
MAX_CONVERSATION_PAIRS = 10

# Priority-ordered: more specific intents first, query last (safe default).
# Uses ordered dict to guarantee iteration order.
INTENT_PATTERNS: dict[str, list[str]] = {
    "pipeline": [
        r"(?:run|execute|start)\s+.*pipeline",
        r"(?:fetch|ingest|pull)\s+.*(?:contacts?|leads?|MQLs?)",
        r"(?:check|monitor)\s+(?:for\s+)?replies",
        r"export\s+.*(?:excel|xlsx|pipeline)",
        r"(?:list|show)\s+pipeline\s+runs",
        r"pipeline\s+run\s+#?\d+",
        r"pipeline\s+history",
    ],
    "admin": [
        r"(?:preview\s+)?(?:delete|remove)\s+(?:the\s+|these\s+|all\s+|those\s+)?(?:contacts?|leads?)",
        r"(?:delete|remove)\s+(?:them|march|april|jan|feb)",
        r"opt[\s-]?out",
        r"block\s+(?:the\s+)?(?:domain|contact)",
        r"(?:update|change|set)\s+(?:the\s+)?(?:engagement\s+)?status",
        r"approve\s",
        r"retarget",
        r"reclassify",
        r"index\s+(?:the\s+)?(?:content|files?|materials?)",
        r"grade\s+(?:the\s+)?collection",
    ],
    "enrich": [
        r"enrich\s",
        r"scrap(?:e|ing)\s",
        r"find\s+(?:the\s+)?domain",
        r"(?:company|prospect)\s+(?:context|info|background)",
        r"web\s+search",
        r"search\s+(?:the\s+)?(?:web|online|internet)",
        r"look\s*up\s+(?:the\s+)?(?:domain|website|company|url)",
    ],
    "score": [
        # Verb-form: "score engagement 42", "score them", "score all contacts"
        # NOT noun-form "what's the score" (that's query)
        r"(?:^|\b)score\s+(?:engagement|it|this|them|the|all|remaining|next|\d)",
        r"^(?:.*?\b)?score\s+(?:the\s+|all\s+|remaining\s+)*(?:contacts?|leads?|engagements?)",
        r"evaluate\s+(?:engagement|it|this|them|the|all|contact|lead)",
        r"rate\s+(?:the\s+)?(?:engagement|lead|contact)",
    ],
    "draft": [
        # Verb-form: "draft an email", "draft for next contact", "draft them"
        # Also catches "draft a new email", "continue drafting", "draft the next one"
        # NOT noun-form "show the draft" or "what's the draft" (those are query)
        r"(?:draft|compose)\s+(?:an?\s+)?(?:\w+\s+)*(?:email|message|outreach)",
        r"(?:write|create)\s+(?:an?\s+)?(?:\w+\s+)*(?:email|message|outreach|draft)",
        r"(?:draft|compose)\s+(?:for|the|them|it|next|this|all|remaining|other)",
        r"(?:continue|resume|keep)\s+drafting",
        r"review\s+(?:the\s+)?drafts?",
        r"(?:approve|reject)\s+(?:the\s+)?drafts?",
        r"revise\s+(?:the\s+)?(?:draft|email)",
        r"(?:re-?draft|redraft)\b",
    ],
    "query": [
        r"show\s",
        r"list\s",
        r"get\s+(?:the\s+)?(?:leads?|contacts?|engagements?|stats|details|submissions?|drafts?)",
        r"how\s+many",
        r"what.*(?:status|score|details?|draft)\b",
        r"(?:pipeline\s+)?stats\b",
    ],
}


_VALID_INTENTS = {"pipeline", "admin", "enrich", "score", "draft", "query"}

_LLM_ROUTER_PROMPT = (
    "Classify this user message into EXACTLY ONE intent. "
    "Reply with ONLY the intent word, nothing else.\n\n"
    "Intents:\n"
    "- pipeline: run/fetch/ingest contacts, execute pipeline, check replies, export\n"
    "- admin: delete contacts, opt-out, block domains, change status, reclassify, index content\n"
    "- enrich: research companies, scrape websites, web search, find domains, company context\n"
    "- score: evaluate/score/rate contacts or engagements\n"
    "- draft: write/draft/compose/revise emails or outreach messages\n"
    "- query: view/show/list data, ask questions, check status, get information\n\n"
    "Message: {text}\n\n"
    "Intent:"
)


def _classify_regex(text: str) -> str | None:
    """Try regex patterns first. Returns None if no match."""
    text_lower = text.lower().strip()
    for intent, patterns in INTENT_PATTERNS.items():
        for p in patterns:
            if re.search(p, text_lower):
                return intent
    return None


_LLM_INTENT_ALIASES = {
    "scoring": "score",
    "drafting": "draft",
    "enrichment": "enrich",
    "enriching": "enrich",
    "delete": "admin",
    "fetch": "pipeline",
    "ingest": "pipeline",
}

def _classify_llm(text: str) -> str:
    """LLM fallback for intent classification."""
    try:
        from outreach_pipeline.graph.agent import _llm
        from langchain_core.messages import HumanMessage
        prompt = _LLM_ROUTER_PROMPT.replace("{text}", text)
        result = _llm.invoke([HumanMessage(content=prompt)])
        intent = result.content.strip().lower().split()[0] if result.content else "query"
        intent = _LLM_INTENT_ALIASES.get(intent, intent)
        return intent if intent in _VALID_INTENTS else "query"
    except Exception:
        return "query"


def classify_intent(text: str) -> str:
    """Classify user message into a specialist intent.

    Hybrid approach: regex patterns for obvious cases (instant, free),
    LLM fallback for natural language / ambiguous phrasings.

    Returns one of: pipeline, admin, enrich, score, draft, query.
    """
    if not text or not text.strip():
        return "query"

    # Fast path: regex match
    intent = _classify_regex(text)
    if intent:
        return intent

    # Slow path: LLM classification for unrecognized phrasings
    intent = _classify_llm(text)

    # Safety: normalize and validate — never return an invalid node name
    intent = _LLM_INTENT_ALIASES.get(intent, intent)
    return intent if intent in _VALID_INTENTS else "query"


def last_human_message(messages: list) -> str:
    """Extract the content of the last HumanMessage from a message list."""
    for m in reversed(messages):
        if isinstance(m, HumanMessage) or getattr(m, "type", "") == "human":
            content = m.content
            return content if isinstance(content, str) else str(content)
    return ""


_CONFIRMATION_RE = re.compile(
    r'^(?:yes|no|ok|confirm|cancel|approve|reject|proceed|go ahead|do it|sure|yep|nope|y|n)'
    r'(?:\s+please)?[\s.!,]*$',
    re.IGNORECASE,
)

def _last_specialist(messages: list) -> str | None:
    """Find the specialist that handled the previous turn by checking AIMessage.name.

    Normalises long agent labels to short node keys — the specialist
    subgraphs use `name="enrichment"`/"scoring"/"drafting" metadata but
    the StateGraph nodes are keyed by `enrich`/`score`/`draft`. Without
    this map the confirmation-resume path would KeyError on the
    conditional-edge lookup.
    """
    for m in reversed(messages):
        if isinstance(m, AIMessage) and getattr(m, "name", None):
            raw = m.name
            return _LLM_INTENT_ALIASES.get(raw, raw)
    return None


def route_to_specialist(state: dict) -> str:
    """Routing function for StateGraph conditional edges.

    Bare confirmations (yes/no/ok/confirm) route to the same specialist
    that handled the previous turn, preserving context for multi-step flows.
    """
    messages = state.get("messages", [])
    text = last_human_message(messages)

    # Check if this is a bare confirmation/rejection
    if _CONFIRMATION_RE.match(text.strip()):
        prev = _last_specialist(messages)
        if prev and prev in _VALID_INTENTS:
            return prev

    return classify_intent(text)


async def router_node(state: dict) -> dict:
    """Preprocess: aggressively trim messages to keep state small.

    Also ensures the DB pool is initialized (needed for langgraph dev
    which has no lifespan hook).

    Two-phase trimming to minimize StateSnapshotEvent payload:

    1. Strip internal tool messages from PREVIOUS turns — AIMessages with
       tool_calls and ToolMessages are specialist internals. After a turn
       completes, only the human request + final AI response matter.
       A single specialist turn can add 10+ internal messages; stripping
       them reduces a turn from ~12 messages to ~2.

    2. Cap remaining messages at MAX_CONVERSATION_PAIRS * 2 using
       RemoveMessage to stay within the LangGraph checkpoint.
    """
    from outreach_pipeline.db.pool import init_pool
    await init_pool()  # no-op if already initialized

    messages = state.get("messages", [])
    if not messages:
        return {}

    # --- Phase 1: identify internal tool messages from previous turns ---
    # Find the start of the current turn (last HumanMessage)
    last_human_idx = len(messages)  # default: treat everything as old
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            last_human_idx = i
            break

    removals = []
    # Strip tool internals from PREVIOUS turns only.
    # Current turn's tool calls are needed by the specialist's tool loop.
    # The STATE_SNAPSHOT filter (server.py) handles browser payload separately.
    for m in messages[:last_human_idx]:
        if not hasattr(m, "id") or not m.id:
            continue
        if isinstance(m, ToolMessage):
            removals.append(RemoveMessage(id=m.id))
        elif isinstance(m, AIMessage) and getattr(m, "tool_calls", []):
            removals.append(RemoveMessage(id=m.id))

    # --- Phase 2: cap remaining conversation messages ---
    # After phase 1, estimate how many meaningful messages remain
    remaining_count = sum(
        1 for m in messages[:last_human_idx]
        if isinstance(m, (HumanMessage, AIMessage))
        and not (isinstance(m, AIMessage) and getattr(m, "tool_calls", []))
        and not isinstance(m, SystemMessage)
    )
    # Add current turn messages
    remaining_count += len(messages) - last_human_idx

    max_keep = MAX_CONVERSATION_PAIRS * 2
    if remaining_count > max_keep:
        # Remove oldest human+response pairs beyond the cap
        kept = 0
        for m in messages[:last_human_idx]:
            if not hasattr(m, "id") or not m.id:
                continue
            if isinstance(m, SystemMessage):
                continue
            # Already marked for removal by phase 1
            if isinstance(m, ToolMessage):
                continue
            if isinstance(m, AIMessage) and getattr(m, "tool_calls", []):
                continue
            # This is a kept message (HumanMessage or final AIMessage)
            kept += 1
            overflow = remaining_count - max_keep
            if kept <= overflow:
                removals.append(RemoveMessage(id=m.id))

    return {"messages": removals} if removals else {}
