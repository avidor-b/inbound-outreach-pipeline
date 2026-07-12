"""Scrape policies — preconditions for web scraping.

Facade over db/guardrails.py functions.
"""
from __future__ import annotations

from outreach_pipeline.policies.codes import Codes
from outreach_pipeline.policies.decisions import PolicyDecision


async def check_scrape_policies(domain: str) -> PolicyDecision:
    """Check if domain is allowed for scraping."""
    from outreach_pipeline.db.guardrails import check_scrape_blocklist

    blocked, reason = await check_scrape_blocklist(domain)
    if blocked:
        return PolicyDecision.deny(Codes.SCRAPE_BLOCKLIST, reason)

    return PolicyDecision.allow()
