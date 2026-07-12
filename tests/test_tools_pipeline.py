"""Unit tests for tools/pipeline.py — pipeline inspection tools."""
import pytest

from outreach_pipeline.db.contacts import upsert_contact, score_contact, enrich_contact


class TestGetMetrics:
    @pytest.fixture(autouse=True)
    async def _seed(self, db):
        await upsert_contact("1", {})
        await upsert_contact("2", {})
        await enrich_contact("1", "ctx")
        await score_contact("1", "high", [], 90, "")

    @pytest.mark.asyncio
    async def test_get_stats(self, db):
        from outreach_pipeline.tools.pipeline import get_stats
        result = await get_stats()
        assert result["total"] == 2
        assert result["enriched"] == 1
        assert result["scored"] == 1


class TestGetLeadsForPipeline:
    @pytest.fixture(autouse=True)
    async def _seed(self, db):
        await upsert_contact("1", {"company": "A"})
        await upsert_contact("2", {"company": "B"})
        await score_contact("1", "high", [], 90, "")
        await score_contact("2", "low", [], 20, "")

    @pytest.mark.asyncio
    async def test_get_leads_filtered(self, db):
        from outreach_pipeline.tools.pipeline import get_leads_summary
        result = await get_leads_summary(min_score=50)
        assert result["count"] == 1
