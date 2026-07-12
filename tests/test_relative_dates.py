from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest


def test_yesterday_uses_report_timezone_not_server_timezone():
    from outreach_pipeline.tools.date_ranges import resolve_date_expr

    # 2026-05-05 in UTC, but still 2026-05-04 in New York.
    now = datetime(2026, 5, 5, 1, 30, tzinfo=timezone.utc)

    assert resolve_date_expr("today", now=now, timezone_name="America/New_York") == "2026-05-04"
    assert resolve_date_expr("yesterday", now=now, timezone_name="America/New_York") == "2026-05-03"


@pytest.mark.asyncio
async def test_ingest_contacts_resolves_relative_dates_at_execution_time(monkeypatch):
    from outreach_pipeline.tools import pipeline

    seen: dict[str, object] = {}

    class FakeHubSpot:
        config = {"report_timezone": "America/New_York"}

        async def asearch_contacts(self, filter_groups, properties):
            seen["filter_groups"] = filter_groups
            return []

    async def fake_upsert_contact(*args, **kwargs):
        raise AssertionError("no contacts should be upserted in this test")

    import outreach_pipeline.db.contacts as contacts_db
    import outreach_pipeline.hubspot_client as hubspot_client

    monkeypatch.setattr(hubspot_client, "get_async_hubspot", lambda: FakeHubSpot())
    monkeypatch.setattr(contacts_db, "upsert_contact", fake_upsert_contact)
    monkeypatch.setattr(
        pipeline,
        "_now",
        lambda: datetime(2026, 5, 5, 1, 30, tzinfo=timezone.utc),
    )

    result = await pipeline.ingest_contacts(start_date="yesterday")

    assert result["date_range"] == "2026-05-03 to 2026-05-03"
    assert "filter_groups" in seen


def test_specialist_prompts_do_not_embed_runtime_date_context():
    agents_dir = Path(__file__).resolve().parents[1] / "src/outreach_pipeline/graph/agents"
    offenders = []
    for path in agents_dir.glob("*.py"):
        if "_build_date_context()" in path.read_text():
            offenders.append(path.name)

    assert offenders == []
