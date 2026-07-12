"""Async Postgres tests for submissions — CRUD, dedup, classification."""
import hashlib

import pytest

from outreach_pipeline.db.contacts import upsert_contact
from outreach_pipeline.db.submissions import (
    create_submission, get_submission, list_submissions,
    is_duplicate, classify_submission,
)


async def _seed_contacts(db):
    """Seed contacts for FK references."""
    await upsert_contact("c1", {"company": "Acme"})
    await upsert_contact("c2", {"company": "Beta"})


class TestCreateSubmission:
    @pytest.mark.asyncio
    async def test_create_and_get(self, db):
        await _seed_contacts(db)
        sid = await create_submission("c1", message="Need pricing",
                                      form_name="Contact Us", mql_date="2026-03-28T10:00:00Z")
        sub = await get_submission(sid)
        assert sub["contact_id"] == "c1"
        assert sub["message"] == "Need pricing"
        assert sub["form_name"] == "Contact Us"
        assert sub["message_hash"]
        assert sub["captured_at"]

    @pytest.mark.asyncio
    async def test_create_without_message(self, db):
        await _seed_contacts(db)
        sid = await create_submission("c1", form_name="Solution Brief Download",
                                      page_url="https://example.com/resources/mesh-radio",
                                      mql_date="2026-03-20T10:00:00Z")
        sub = await get_submission(sid)
        assert sub["message"] == ""
        assert not sub["message_hash"]  # no message -> no hash (None -> '' via row_to_dict)

    @pytest.mark.asyncio
    async def test_message_hash_computed(self, db):
        await _seed_contacts(db)
        sid = await create_submission("c1", message="  Need PRICING  ")
        sub = await get_submission(sid)
        expected = hashlib.sha256("need pricing".encode()).hexdigest()
        assert sub["message_hash"] == expected

    @pytest.mark.asyncio
    async def test_source_defaults_to_hubspot(self, db):
        await _seed_contacts(db)
        sid = await create_submission("c1", message="Hi")
        assert (await get_submission(sid))["source"] == "hubspot"

    @pytest.mark.asyncio
    async def test_source_migration(self, db):
        await _seed_contacts(db)
        sid = await create_submission("c1", message="Hi", source="migration")
        assert (await get_submission(sid))["source"] == "migration"


class TestListSubmissions:
    @pytest.mark.asyncio
    async def test_list_by_contact(self, db):
        await _seed_contacts(db)
        await create_submission("c1", message="First", mql_date="2026-01-01T10:00:00Z")
        await create_submission("c1", message="Second", mql_date="2026-03-01T10:00:00Z")
        await create_submission("c2", message="Other")
        subs = await list_submissions(contact_id="c1")
        assert len(subs) == 2

    @pytest.mark.asyncio
    async def test_list_excludes_migration_by_default(self, db):
        await _seed_contacts(db)
        await create_submission("c1", message="Real", source="hubspot")
        await create_submission("c1", message="Migrated", source="migration")
        subs = await list_submissions(contact_id="c1")
        assert len(subs) == 1
        assert subs[0]["message"] == "Real"

    @pytest.mark.asyncio
    async def test_list_includes_migration_when_requested(self, db):
        await _seed_contacts(db)
        await create_submission("c1", message="Real", source="hubspot")
        await create_submission("c1", message="Migrated", source="migration")
        subs = await list_submissions(contact_id="c1", include_migration=True)
        assert len(subs) == 2


class TestDedup:
    @pytest.mark.asyncio
    async def test_same_message_same_form_same_day_is_duplicate(self, db):
        await _seed_contacts(db)
        await create_submission("c1", message="Need pricing",
                                form_id="form1", mql_date="2026-03-28T10:00:00Z")
        assert await is_duplicate("c1", message="Need pricing",
                                  form_id="form1", mql_date="2026-03-28T15:00:00Z")

    @pytest.mark.asyncio
    async def test_same_message_same_form_different_day_IS_duplicate(self, db):
        """Regression: a HubSpot re-fetch can assign a new timestamp
        to the same form submission. Before the fix, that bypassed
        dedup and created a second row with identical (contact, form,
        message) but a different date. The same triple must deduplicate
        regardless of date when a message is present."""
        await _seed_contacts(db)
        await create_submission("c1", message="Need pricing",
                                form_id="form1", mql_date="2026-03-28T10:00:00Z")
        assert await is_duplicate("c1", message="Need pricing",
                                  form_id="form1", mql_date="2026-04-15T10:00:00Z")

    @pytest.mark.asyncio
    async def test_different_message_same_form_same_day_is_not_duplicate(self, db):
        await _seed_contacts(db)
        await create_submission("c1", message="Need pricing",
                                form_id="form1", mql_date="2026-03-28T10:00:00Z")
        assert not await is_duplicate("c1", message="Different question",
                                      form_id="form1", mql_date="2026-03-28T15:00:00Z")

    @pytest.mark.asyncio
    async def test_no_message_dedup_on_form_and_date(self, db):
        await _seed_contacts(db)
        await create_submission("c1", form_id="download1",
                                mql_date="2026-03-20T10:00:00Z")
        assert await is_duplicate("c1", form_id="download1",
                                  mql_date="2026-03-20T15:00:00Z")

    @pytest.mark.asyncio
    async def test_no_message_different_day_not_duplicate(self, db):
        await _seed_contacts(db)
        await create_submission("c1", form_id="download1",
                                mql_date="2026-03-20T10:00:00Z")
        assert not await is_duplicate("c1", form_id="download1",
                                      mql_date="2026-03-21T10:00:00Z")


class TestClassifySubmission:
    @pytest.mark.asyncio
    async def test_classify_updates_fields(self, db):
        await _seed_contacts(db)
        sid = await create_submission("c1", message="Need pricing for RADIO-A")
        await classify_submission(sid, intent_type="sales_inquiry",
                                  product_family="microwave_radio", intent_signals=["pricing"])
        sub = await get_submission(sid)
        assert sub["intent_type"] == "sales_inquiry"
        assert sub["product_family"] == "microwave_radio"
        assert sub["classified_at"]

    @pytest.mark.asyncio
    async def test_classify_support(self, db):
        await _seed_contacts(db)
        sid = await create_submission("c1", message="Unit not working")
        await classify_submission(sid, intent_type="support_request",
                                  support_flag=True)
        sub = await get_submission(sid)
        assert sub["support_flag"] is True

    @pytest.mark.asyncio
    async def test_classify_event(self, db):
        await _seed_contacts(db)
        sid = await create_submission("c1", message="Meet at MWC")
        await classify_submission(sid, intent_type="event_meeting",
                                  event_flag=True)
        sub = await get_submission(sid)
        assert sub["event_flag"] is True
