"""Unit tests for engagement-aware tools."""
import pytest

from outreach_pipeline.db.contacts import upsert_contact
from outreach_pipeline.db.submissions import create_submission
from outreach_pipeline.db.engagements import (
    create_engagement, add_submission, score_engagement,
)


async def _seed(db):
    """Shared setup: contact + submission + scored engagement."""
    await upsert_contact("c1", {
        "company": "Acme", "message": "Need pricing",
        "hs_v2_date_entered_marketingqualifiedlead": "2026-03-28T10:00:00Z",
    })
    sid = await create_submission("c1", message="Need pricing for RADIO-A",
                                 mql_date="2026-03-28T10:00:00Z")
    eid = await create_engagement("c1", kind="sales", primary_product="microwave_radio")
    await add_submission(eid, sid)
    await score_engagement(eid, score=85, score_reasoning="Strong")
    return sid, eid


class TestGetEngagements:
    @pytest.mark.asyncio
    async def test_get_all(self, db):
        from outreach_pipeline.tools.engagements import get_engagements
        _, _ = await _seed(db)
        result = await get_engagements()
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_filter_by_kind(self, db):
        from outreach_pipeline.tools.engagements import get_engagements
        _, _ = await _seed(db)
        await create_engagement("c1", kind="support", status="blocked", block_terminal=True)
        result = await get_engagements(kind="sales")
        assert result["count"] == 1
        assert result["engagements"][0]["kind"] == "sales"

    @pytest.mark.asyncio
    async def test_filter_by_status(self, db):
        from outreach_pipeline.tools.engagements import get_engagements
        _, _ = await _seed(db)
        result = await get_engagements(status="open")
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_filter_by_min_score(self, db):
        from outreach_pipeline.tools.engagements import get_engagements
        _, _ = await _seed(db)
        await create_engagement("c1", kind="sales")  # score=0
        result = await get_engagements(min_score=50)
        assert result["count"] == 1  # only the one with score=85


class TestGetEngagementDetails:
    @pytest.mark.asyncio
    async def test_returns_full_context(self, db):
        from outreach_pipeline.tools.engagements import get_engagement_details
        _, eid = await _seed(db)
        result = await get_engagement_details(eid)
        assert result["engagement"]["id"] == eid
        assert result["contact"]["id"] == "c1"
        assert len(result["submissions"]) == 1
        assert result["submissions"][0]["message"] == "Need pricing for RADIO-A"

    @pytest.mark.asyncio
    async def test_not_found(self, db):
        from outreach_pipeline.tools.engagements import get_engagement_details
        result = await get_engagement_details(9999)
        assert result["engagement"] is None


class TestGetContactHistory:
    @pytest.mark.asyncio
    async def test_full_history(self, db):
        from outreach_pipeline.tools.engagements import get_contact_history
        _, _ = await _seed(db)
        # Add support engagement
        sid2 = await create_submission("c1", message="Need technical support")
        eid2 = await create_engagement("c1", kind="support", status="blocked", block_terminal=True)
        await add_submission(eid2, sid2)

        result = await get_contact_history("c1")
        assert result["contact"]["id"] == "c1"
        assert len(result["submissions"]) == 2
        assert len(result["engagements"]) == 2


class TestGetSubmissions:
    @pytest.mark.asyncio
    async def test_list_submissions(self, db):
        from outreach_pipeline.tools.engagements import get_submissions
        _, _ = await _seed(db)
        result = await get_submissions("c1")
        assert len(result["submissions"]) == 1

    @pytest.mark.asyncio
    async def test_excludes_migration_by_default(self, db):
        from outreach_pipeline.tools.engagements import get_submissions
        _, _ = await _seed(db)
        await create_submission("c1", message="Migrated", source="migration")
        result = await get_submissions("c1")
        assert len(result["submissions"]) == 1  # only the real one


class TestSplitContactList:
    """The bulk tools accept any natural separator the user / LLM
    produces. Previously we only split on commas, so phrasing like
    'Example Technologies and Example Manufacturing' was treated as one company name and
    resolved to zero results."""

    def test_comma(self):
        from outreach_pipeline.graph.tools import _split_contact_list
        assert _split_contact_list("Foo, Bar, Baz") == ["Foo", "Bar", "Baz"]

    def test_and_conjunction(self):
        from outreach_pipeline.graph.tools import _split_contact_list
        assert _split_contact_list("Example Technologies Japan and Example Manufacturing LLC") == [
            "Example Technologies Japan", "Example Manufacturing LLC",
        ]

    def test_semicolon_and_newline(self):
        from outreach_pipeline.graph.tools import _split_contact_list
        assert _split_contact_list("Foo;Bar\nBaz") == ["Foo", "Bar", "Baz"]

    def test_preserves_ampersand_in_name(self):
        from outreach_pipeline.graph.tools import _split_contact_list
        # "&" with no surrounding whitespace is part of the name
        # ("AT&T", "Johnson & Johnson" etc. stay intact as a single
        # token the name-resolver can ILIKE-match).
        assert _split_contact_list("AT&T, Verizon") == ["AT&T", "Verizon"]


class TestGetSubmissionsBulk:
    @pytest.mark.asyncio
    async def test_bulk_with_and_separator(self, db):
        """Regression: the LLM, echoing 'X and Y' user phrasing, used
        to produce zero results because the tool only split on commas."""
        from outreach_pipeline.graph.tools import get_submissions_bulk
        import json
        await upsert_contact("alpha", {"company": "Alpha Corp"})
        await upsert_contact("beta", {"company": "Beta Inc"})
        await create_submission("alpha", message="Pricing for RADIO-A",
                                mql_date="2026-03-28T10:00:00Z")
        await create_submission("beta", message="Need a demo",
                                mql_date="2026-03-28T10:00:00Z")

        raw = await get_submissions_bulk.ainvoke({"contacts": "Alpha Corp and Beta Inc"})
        payload = json.loads(raw)
        results = payload["results"]
        assert len(results) == 2
        by_company = {r["company"]: r for r in results}
        assert by_company["Alpha Corp"]["count"] == 1
        assert by_company["Beta Inc"]["count"] == 1

    @pytest.mark.asyncio
    async def test_bulk_oxford_comma(self, db):
        """The Oxford comma variant 'A, B, and C' must also split into
        three names (commas plus trailing ' and ')."""
        from outreach_pipeline.graph.tools import get_submissions_bulk
        import json
        await upsert_contact("a", {"company": "Example Technologies Japan"})
        await upsert_contact("b", {"company": "Example Manufacturing LLC"})
        await upsert_contact("c", {"company": "Example Networks"})
        for cid in ("a", "b", "c"):
            await create_submission(cid, message="Hi",
                                    mql_date="2026-03-28T10:00:00Z")

        raw = await get_submissions_bulk.ainvoke({
            "contacts": "Example Technologies Japan, Example Manufacturing LLC, and Example Networks",
        })
        payload = json.loads(raw)
        results = payload["results"]
        assert len(results) == 3
        assert all(r["count"] == 1 for r in results)

    @pytest.mark.asyncio
    async def test_single_tool_auto_routes_to_bulk(self, db):
        """Regression: when the LLM passes a multi-name list to the
        single get_submissions tool (instead of get_submissions_bulk),
        we auto-dispatch to bulk so the agent still gets results."""
        from outreach_pipeline.graph.tools import get_submissions
        import json
        await upsert_contact("alpha", {"company": "Alpha Corp"})
        await upsert_contact("beta", {"company": "Beta Inc"})
        await create_submission("alpha", message="A",
                                mql_date="2026-03-28T10:00:00Z")
        await create_submission("beta", message="B",
                                mql_date="2026-03-28T10:00:00Z")

        raw = await get_submissions.ainvoke({
            "contact_id": "Alpha Corp, Beta Inc",
        })
        payload = json.loads(raw)
        # bulk shape: {results: [...], count, total_requested}
        assert "results" in payload
        assert len(payload["results"]) == 2

    @pytest.mark.asyncio
    async def test_bulk_via_tool_invoke_returns_string(self, db):
        """Guard the streaming-adapter path: the public @tool function
        must return a plain JSON string when invoked the way the
        LangGraph runtime invokes it (`.ainvoke`). The prior bug had
        this function recursively calling another @tool's .ainvoke,
        which made the adapter see nested OnToolEnd events with `str`
        outputs and crash the SSE stream. Keep this test: it's the
        thing that passes the output shape check the real runtime does.
        """
        from outreach_pipeline.graph.tools import get_submissions_bulk
        import json
        await upsert_contact("alpha", {"company": "Alpha Corp"})
        await create_submission("alpha", message="Need pricing",
                                mql_date="2026-03-28T10:00:00Z")

        raw = await get_submissions_bulk.ainvoke({"contacts": "Alpha Corp"})
        assert isinstance(raw, str), (
            f"tool must return str for the streaming adapter to wrap it "
            f"in a ToolMessage; got {type(raw).__name__}"
        )
        payload = json.loads(raw)
        assert payload["results"][0]["count"] == 1
