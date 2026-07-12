"""Unit tests for DraftReviewMiddleware._parse_draft.

Covers the tool-output format the drafting agent emits and the
middleware's parsing of subject, body, RAG context, and the three new
resource fields (promised_resources, referenced_resource_ids,
unverified_promises).
"""
from __future__ import annotations

import json

import pytest

from outreach_pipeline.graph.middleware import DraftReviewMiddleware


def _parse(text: str) -> dict:
    return DraftReviewMiddleware._parse_draft(text)


def test_parses_strict_format_without_resources():
    text = (
        "ENGAGEMENT: 42\n"
        "RAG_CONTEXT: source1.pdf, source2.pdf\n"
        "SUBJECT: Quick question\n"
        "\n"
        "Hi [First Name],\n"
        "Short body.\n"
        "Best,\n[Sender Name]"
    )
    d = _parse(text)
    assert d["engagement_id"] == 42
    assert d["subject"] == "Quick question"
    assert d["rag_context"] == "source1.pdf, source2.pdf"
    assert "Hi [First Name]" in d["body"]
    # Absent fields default to empty list
    assert d["promised_resources"] == []
    assert d["referenced_resource_ids"] == []
    assert d["unverified_promises"] == []


def test_parses_all_resource_fields():
    resources = [
        {"id": "sb1", "title": "Utilities Brief",
         "url": "https://example.com/sb", "asset_type": "solution-brief",
         "summary": "Short", "match_score": 14.0},
    ]
    text = (
        "ENGAGEMENT: 100\n"
        "RAG_CONTEXT: rag.pdf\n"
        f"PROMISED_RESOURCES: {json.dumps(resources)}\n"
        f"REFERENCED_IDS: {json.dumps(['sb1'])}\n"
        f"UNVERIFIED_PROMISES: {json.dumps(['solar brief'])}\n"
        "SUBJECT: Re: your RFQ\n\n"
        "Hi [First Name],\n"
        "Body.\n"
        "Best,\n[Sender Name]"
    )
    d = _parse(text)
    assert d["engagement_id"] == 100
    assert len(d["promised_resources"]) == 1
    assert d["promised_resources"][0]["id"] == "sb1"
    assert d["promised_resources"][0]["match_score"] == 14.0
    assert d["referenced_resource_ids"] == ["sb1"]
    assert d["unverified_promises"] == ["solar brief"]


def test_malformed_resource_json_falls_back_to_empty_list():
    text = (
        "ENGAGEMENT: 5\n"
        "PROMISED_RESOURCES: [not valid json\n"
        "SUBJECT: S\n\n"
        "Hi [First Name],\nBody.\nBest,\n[Sender Name]"
    )
    d = _parse(text)
    # Bad JSON decays to [] rather than raising
    assert d["promised_resources"] == []


def test_empty_resource_arrays_roundtrip():
    text = (
        "ENGAGEMENT: 7\n"
        "PROMISED_RESOURCES: []\n"
        "REFERENCED_IDS: []\n"
        "UNVERIFIED_PROMISES: []\n"
        "SUBJECT: Subj\n\n"
        "Hi [First Name],\nBody.\nBest,\n[Sender Name]"
    )
    d = _parse(text)
    assert d["promised_resources"] == []
    assert d["referenced_resource_ids"] == []
    assert d["unverified_promises"] == []


class TestReviewPendingDrafts:
    """review_pending_drafts calls db.drafts.get_drafts which returns a
    list, not a dict. Prior code called .get() on it and crashed in
    production. These tests lock in the correct contract."""

    @pytest.mark.asyncio
    async def test_returns_first_pending_with_all_fields(self, db):
        from outreach_pipeline.db.contacts import upsert_contact
        from outreach_pipeline.db.engagements import create_engagement
        from outreach_pipeline.db.drafts import save_draft
        from outreach_pipeline.graph.agents.drafting import review_pending_drafts

        await upsert_contact("c1", {})
        eid = await create_engagement("c1", kind="inbound_mql")
        await save_draft(
            "c1", "Subj", "Body text", engagement_id=eid,
            promised_resources=[{"id": "sb1", "title": "Utilities",
                                 "url": "https://example.com",
                                 "asset_type": "solution-brief"}],
            referenced_resource_ids=["sb1"],
            unverified_promises=["random phrase"],
        )
        result = await review_pending_drafts.ainvoke({})
        assert "ENGAGEMENT:" in result
        assert "PROMISED_RESOURCES:" in result
        assert "REFERENCED_IDS:" in result
        assert "UNVERIFIED_PROMISES:" in result
        assert "Body text" in result
        # Sanity check the JSON round-trips correctly
        assert '"sb1"' in result
        assert '"random phrase"' in result

    @pytest.mark.asyncio
    async def test_returns_friendly_message_when_empty(self, db):
        from outreach_pipeline.graph.agents.drafting import review_pending_drafts
        result = await review_pending_drafts.ainvoke({})
        assert result == "No pending drafts to review."


def test_loose_format_still_parses_body():
    # Agent occasionally writes a draft without the ENGAGEMENT header.
    # The parser's loose-mode behaviour should survive the addition of
    # the new structured fields — no hard dependency on any of them.
    text = (
        "Here's a draft for engagement #99:\n\n"
        "Subject: Hi there\n\n"
        "Hi [First Name],\nLoose body.\nBest,\n[Sender Name]"
    )
    d = _parse(text)
    assert d["engagement_id"] == 99
    assert d["subject"] == "Hi there"
    assert "Loose body" in d["body"]
    assert d["promised_resources"] == []
