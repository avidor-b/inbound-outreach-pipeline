"""Unit tests for services/scoring.py — lead scoring domain flow."""
import pytest

from outreach_pipeline.db.contacts import upsert_contact, get_contact


class TestBuildScoreContext:
    @pytest.fixture(autouse=True)
    async def _seed(self, db):
        await upsert_contact("c1", {
            "company": "Acme", "message": "Need pricing",
            "jobtitle": "VP Engineering", "hs_analytics_num_page_views": "25",
        })

    @pytest.mark.asyncio
    async def test_build_score_context(self, db):
        from outreach_pipeline.services.scoring import build_score_context
        ctx = await build_score_context("c1")
        assert ctx["contact"]["id"] == "c1"
        assert "message" in ctx["contact"]["properties"]

    @pytest.mark.asyncio
    async def test_build_score_context_missing_contact(self, db):
        from outreach_pipeline.services.scoring import build_score_context
        ctx = await build_score_context("missing")
        assert ctx["contact"] == {}


class TestSaveScore:
    @pytest.fixture(autouse=True)
    async def _seed(self, db):
        await upsert_contact("c1", {})

    @pytest.mark.asyncio
    async def test_save_score_persists(self, db):
        from outreach_pipeline.services.scoring import save_score
        await save_score("c1", buying_intent="high",
                         intent_signals=["pricing", "timeline"], score=85,
                         score_reasoning="Strong signals", product_match="RADIO-B100")
        c = await get_contact("c1")
        assert c["score"] == 85
        assert c["buying_intent"] == "high"
        assert c["scored_at"] is not None
