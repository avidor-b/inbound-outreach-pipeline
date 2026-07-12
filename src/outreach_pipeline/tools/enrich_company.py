"""Enrichment tools — atomic capabilities for the agentic enrich agent.

The agent decides the strategy (scrape, web search, or LLM knowledge).
These are individual tools it can call, not a hardcoded pipeline.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx
import trafilatura
from pydantic import BaseModel, Field
from typing import Literal

from outreach_pipeline.tools.search import aweb_search
from outreach_pipeline.security.url_safety import UnsafeURL, assert_public_http_url

# SSRF guard implementation uses ipaddress plus socket.getaddrinfo and rejects
# is_private, is_loopback, and is_link_local addresses before outbound fetches.

# Shared HTTP settings
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Outreach/1.0)"}
_TIMEOUT = httpx.Timeout(15.0, connect=10.0)


class EnrichmentConfidence(BaseModel):
    """LLM assessment of enrichment quality."""
    confidence: Literal["high", "medium", "low"] = "low"
    reason: str = Field(description="One sentence explaining the confidence level")
    summary: str = Field(description="2-3 sentence summary of what the company does")


async def afetch_page(url: str) -> dict[str, Any]:
    """Fetch a single URL and extract its text content.

    Returns {url, title, text}. Non-destructive — doesn't index anything.
    Use this to check a company's homepage, about page, or specific URL.
    """
    try:
        assert_public_http_url(url)
        async with httpx.AsyncClient(headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url)
        assert_public_http_url(str(resp.url))
        if resp.status_code != 200:
            return {"url": url, "title": "", "text": "", "error": f"HTTP {resp.status_code}"}

        html = resp.text
        text = trafilatura.extract(
            html, include_comments=False, include_tables=True,
            no_fallback=False, favor_recall=True,
        )

        title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else url

        return {"url": url, "title": title, "text": (text or "").strip()}
    except UnsafeURL as e:
        return {"url": url, "title": "", "text": "", "error": f"Blocked URL: {e}"}
    except Exception as e:
        return {"url": url, "title": "", "text": "", "error": str(e)}


async def asummarize_and_save(
    contact_id: str,
    context_text: str,
    company_name: str = "",
    email_domain: str = "",
) -> dict[str, Any]:
    """Assess enrichment quality via LLM and save to the contact record.

    The agent provides context_text (from any source — scraping, web search,
    its own knowledge, or a combination). This tool:
    1. Runs an LLM confidence assessment (high/medium/low)
    2. Generates a 2-3 sentence summary
    3. Saves the summary to the contact's company_context field

    Returns {success, confidence, summary}.
    """
    try:
        from outreach_pipeline.graph.agent import _llm, INTERNAL_LLM_CONFIG
        from langchain_core.messages import SystemMessage, HumanMessage

        structured = _llm.with_structured_output(EnrichmentConfidence)
        # emit-messages: False prevents ag_ui_langgraph from streaming
        # this nested LLM output as text events (would show raw JSON in chat)
        result = await structured.ainvoke([
            SystemMessage(content=(
                "You are writing a company summary for B2B sales outreach.\n\n"
                "Based on the provided context, write a concise summary.\n\n"
                "Confidence levels:\n"
                "- high: Clear, verified information about the company\n"
                "- medium: Reasonable information but some uncertainty\n"
                "- low: Insufficient or unreliable information\n\n"
                "Write a 2-3 sentence summary focused on:\n"
                "- What the company does (core business)\n"
                "- Industry and market (ISP, telecom, utility, enterprise, etc.)\n"
                "- Size/geography if known\n"
                "- Relevance to wireless/networking if any\n\n"
                "If the content is in a foreign language, translate and summarize in English."
            )),
            HumanMessage(content=(
                f"Company: {company_name}\n"
                f"Domain: {email_domain}\n\n"
                f"Context:\n{context_text[:2000]}"
            )),
        ], config=INTERNAL_LLM_CONFIG)
    except Exception:
        result = EnrichmentConfidence(confidence="low", reason="LLM assessment failed", summary=context_text[:300])

    # Save to DB
    from outreach_pipeline.tools.engagements import save_enrichment
    await save_enrichment(
        contact_id, result.summary,
        enrichment_confidence=result.confidence,
    )

    return f"Enriched {contact_id} ({result.confidence}): {result.summary}"
