"""Regression: _enrich_one must append `|website:URL` to its return
string whenever a non-empty email_domain (or website_override) exists.

The UI's enrich-result card uses this suffix to render a clickable
link. A prior bug read the wrong dict key (`domain` instead of
`email_domain`) from get_prospect_domain's return, so the
fresh-enrichment path silently produced cards with "no website
detected" for every newly ingested contact.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch, AsyncMock

from outreach_pipeline.db.contacts import upsert_contact


class TestWebsiteSuffix:
    @pytest.mark.asyncio
    async def test_fresh_enrich_appends_website_suffix(self, db):
        """Contact with email_domain but NO prior company_context hits
        the full-enrichment path. Result must end with
        ` |website:https://<email_domain>`."""
        await upsert_contact("c-fresh", {
            "email": "user@telecom.example.net",
            "company": "Example Telecom",
        })

        # Stub the network-bound helpers so the test is hermetic but
        # still exercises the key-name wiring.
        from outreach_pipeline.graph.agents import enrichment as enrich_mod

        async def fake_fetch(url):
            return {"title": "Example Telecom", "text": "Example Telecom is a regional network operator. " * 10}

        async def fake_search(q):
            return {"results": []}

        async def fake_summarize(contact_id, context_text, company_name="", email_domain=""):
            # Mirrors the real return shape — a string the caller
            # prefixes/suffixes.
            return f"Enriched {contact_id} (medium): Example Telecom network operator."

        with patch(
            "outreach_pipeline.tools.enrich_company.afetch_page",
            new=AsyncMock(side_effect=fake_fetch),
        ), patch(
            "outreach_pipeline.tools.search.aweb_search",
            new=AsyncMock(side_effect=fake_search),
        ), patch(
            "outreach_pipeline.tools.enrich_company.asummarize_and_save",
            new=AsyncMock(side_effect=fake_summarize),
        ):
            out = await enrich_mod._enrich_one("c-fresh")

        assert isinstance(out, str), f"expected str, got {type(out)}"
        assert " |website:" in out, \
            f"_enrich_one must append a |website: suffix; got: {out!r}"
        assert "telecom.example.net" in out, \
            f"website must be derived from email_domain; got: {out!r}"
        # Company-name prefix should also be in place.
        assert out.startswith("[Example Telecom]")

    @pytest.mark.asyncio
    async def test_industry_and_region_suffixes(self, db):
        """All three meta suffixes (industry, region, website) must
        appear in the expected order so the UI's right-to-left pop
        parses each without swallowing the next value."""
        await upsert_contact("c-iry", {
            "email": "ops@provider.example.net",
            "company": "Example Networks",
            "industry": "ISP",
            "region": "EMEA",
        })

        from outreach_pipeline.db.contacts import enrich_contact
        await enrich_contact(
            "c-iry",
            company_context="Regional ISP in Iberia.",
            enrichment_confidence="medium",
        )
        from outreach_pipeline.graph.agents.enrichment import _enrich_one
        out = await _enrich_one("c-iry")
        # Suffix order matters for the UI's pop-from-end parser.
        idx_i = out.rfind("|industry:")
        idx_r = out.rfind("|region:")
        idx_w = out.rfind("|website:")
        assert idx_i > 0 and idx_r > 0 and idx_w > 0
        assert idx_i < idx_r < idx_w, (
            f"suffix order must be industry<region<website; got "
            f"i={idx_i} r={idx_r} w={idx_w} in {out!r}"
        )
        assert "|industry:ISP" in out
        assert "|region:EMEA" in out

    @pytest.mark.asyncio
    async def test_already_enriched_also_appends_suffix(self, db):
        """Already-enriched branch uses props['email_domain'] directly.
        Kept here so both code paths stay covered."""
        from outreach_pipeline.db.contacts import enrich_contact
        await upsert_contact("c-prior", {
            "email": "hi@customer.example.com",
            "company": "ExampleCo",
        })
        await enrich_contact(
            "c-prior",
            company_context="ExampleCo builds widgets.",
            enrichment_confidence="medium",
        )
        from outreach_pipeline.graph.agents.enrichment import _enrich_one
        out = await _enrich_one("c-prior")
        assert isinstance(out, str)
        assert "Already enriched" in out
        assert " |website:https://customer.example.com" in out
