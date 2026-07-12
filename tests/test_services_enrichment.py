"""Unit tests for services/enrichment.py — contact enrichment domain flow."""
import pytest

from outreach_pipeline.db.contacts import upsert_contact, get_contact


class TestSaveEnrichment:
    @pytest.fixture(autouse=True)
    async def _seed(self, db):
        await upsert_contact("c1", {"company": "Acme"})

    @pytest.mark.asyncio
    async def test_save_enrichment_persists(self, db):
        from outreach_pipeline.services.enrichment import save_enrichment
        await save_enrichment("c1", company_context="Makes rockets",
                              crm_context="Customer since 2020")
        c = await get_contact("c1")
        assert c["company_context"] == "Makes rockets"
        assert c["crm_context"] == "Customer since 2020"
        assert c["enriched_at"] is not None
