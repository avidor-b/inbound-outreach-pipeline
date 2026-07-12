"""Outreach shared agent infrastructure — model, date context, constants.

Used by all specialist subgraphs. The graph entry point is in
graph/__init__.py which builds a StateGraph with router + specialist nodes.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
load_dotenv()

MODEL = os.environ.get("OUTREACH_MODEL", "openai:gpt-4.1-mini")

_provider, _model_name = MODEL.split(":", 1) if ":" in MODEL else ("google_genai", MODEL)


def _build_llm():
    """Construct the LangChain chat model for OUTREACH_MODEL.

    Kept out of module-import scope so a missing OPENAI_API_KEY /
    GOOGLE_API_KEY doesn't crash the server before /health is
    reachable. First real LLM call builds the client; subsequent
    calls reuse it via _LazyLLM's cache.
    """
    if _provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=_model_name, temperature=0.3, max_tokens=4096)
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(
        model=_model_name, max_output_tokens=4096, temperature=0.3,
    )


class _LazyLLM:
    """Transparent proxy that builds the real LangChain client on first
    attribute access.

    Callers keep using `_llm.invoke(...)` / `_llm.ainvoke(...)` etc.
    exactly as before. The actual ChatOpenAI / ChatGoogleGenerativeAI
    instance is constructed lazily, so missing API keys only fail the
    specific request that needs the LLM — the server still boots,
    /health responds, /ready responds, operators can see what's wrong.
    """

    __slots__ = ("_instance",)

    def __init__(self) -> None:
        # Bypass __setattr__ (we don't override it, but keep the
        # intent explicit: the instance starts unbuilt).
        object.__setattr__(self, "_instance", None)

    def _get(self):
        if self._instance is None:
            object.__setattr__(self, "_instance", _build_llm())
        return self._instance

    def __getattr__(self, name):
        # Only reached when normal lookup fails (i.e. every LangChain
        # method). __slots__ + __init__ keep _instance / _get findable
        # through normal attribute resolution, so no recursion risk.
        return getattr(self._get(), name)

    def __repr__(self) -> str:
        if self._instance is None:
            return f"<_LazyLLM unbuilt model={MODEL!r}>"
        return f"<_LazyLLM {self._instance!r}>"


_llm = _LazyLLM()

# Config for nested LLM calls inside tools — prevents ag_ui_langgraph
# from streaming their output as text events (would show raw JSON in chat).
INTERNAL_LLM_CONFIG = {"metadata": {"emit-messages": False}}


def _build_date_context() -> str:
    """Build date resolution block for specialist prompts."""
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    return (
        f"Today is {now.strftime('%Y-%m-%d')} ({now.strftime('%A')}). "
        f"Yesterday was {yesterday.strftime('%Y-%m-%d')}.\n"
        "Resolve all relative dates to YYYY-MM-DD. "
        "Any day name (\"Friday\", \"Saturday\") = the MOST RECENT past occurrence. "
        "Always look backward, never forward."
    )


DATE_HANDLING_INSTRUCTIONS = (
    "Date handling: keep prompts cache-stable. Do NOT invent concrete dates "
    "from memory. For date-aware tools, pass user relative date phrases "
    "such as 'today', 'yesterday', 'last week', or 'last 7 days' when the "
    "user uses them; the tool resolves them at execution time in the "
    "configured report timezone. If the user already gave YYYY-MM-DD, pass "
    "that value unchanged."
)


MEMO_INSTRUCTIONS = (
    "\n\nMEMO: Call update_memo to save key facts (contact IDs, engagement IDs, scores, "
    "decisions). Include previous memo + new facts. Call update_memo in the SAME batch "
    "as your other tool calls, not as a separate follow-up step. After update_memo, "
    "do NOT repeat your previous response."
)
