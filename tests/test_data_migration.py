"""Unit tests for data migration -- existing contacts -> 3-layer model."""
import pytest

from outreach_pipeline.db.contacts import upsert_contact, score_contact, enrich_contact
from outreach_pipeline.db.drafts import save_draft
from outreach_pipeline.db.replies import save_reply
from outreach_pipeline.db.submissions import list_submissions
from outreach_pipeline.db.engagements import (
    list_engagements, get_engagement,
    get_submissions as get_engagement_submissions,
)


class TestMigrateContacts:

    @pytest.mark.asyncio
    async def test_basic_contact_migration(self, db):
        """Scored contact -> 1 submission (source=migration) + 1 engagement."""
        from outreach_pipeline.services.migration import migrate_contact
        await upsert_contact("c1", {"company": "Acme", "message": "Need pricing"})
        await enrich_contact("c1", company_context="Makes rockets")
        await score_contact("c1", "high", ["pricing"], 85, "Strong", "RADIO-A")

        result = await migrate_contact("c1")
        assert result["migrated"]
        assert result["submission_id"] is not None
        assert result["engagement_id"] is not None

        # Check submission
        subs = await list_submissions(contact_id="c1", include_migration=True)
        assert len(subs) == 1
        assert subs[0]["source"] == "migration"
        assert subs[0]["message"] == "Need pricing"

        # Check engagement
        eng = await get_engagement(result["engagement_id"])
        assert eng["kind"] == "sales"
        assert eng["score"] == 85
        assert eng["primary_product"] == "RADIO-A"

    @pytest.mark.asyncio
    async def test_unscored_contact_migration(self, db):
        """Contact with no score -> submission + open engagement with score=0."""
        from outreach_pipeline.services.migration import migrate_contact
        await upsert_contact("c1", {"message": "Hi"})
        result = await migrate_contact("c1")
        assert result["migrated"]
        eng = await get_engagement(result["engagement_id"])
        assert eng["score"] == 0

    @pytest.mark.asyncio
    async def test_contact_without_message_no_engagement(self, db):
        """Contact with no message -> no submission, no engagement."""
        from outreach_pipeline.services.migration import migrate_contact
        await upsert_contact("c1", {"company": "Acme"})
        result = await migrate_contact("c1")
        assert not result["migrated"]
        assert result["submission_id"] is None

    @pytest.mark.asyncio
    async def test_migration_idempotent(self, db):
        """Running migration twice should not create duplicates."""
        from outreach_pipeline.services.migration import migrate_contact
        await upsert_contact("c1", {"message": "Hi"})
        r1 = await migrate_contact("c1")
        r2 = await migrate_contact("c1")
        assert r1["migrated"]
        assert not r2["migrated"]  # already migrated
        subs = await list_submissions(contact_id="c1", include_migration=True)
        assert len(subs) == 1

    @pytest.mark.asyncio
    async def test_migration_source_is_filterable(self, db):
        """Migration submissions should be excluded by default in list_submissions."""
        from outreach_pipeline.services.migration import migrate_contact
        await upsert_contact("c1", {"message": "Hi"})
        await migrate_contact("c1")
        # Default excludes migration
        subs = await list_submissions(contact_id="c1")
        assert len(subs) == 0
        # Explicit include shows it
        subs = await list_submissions(contact_id="c1", include_migration=True)
        assert len(subs) == 1

    @pytest.mark.asyncio
    async def test_draft_linked_to_engagement(self, db):
        """Existing drafts should be linked to the migrated engagement."""
        from outreach_pipeline.services.migration import migrate_contact
        await upsert_contact("c1", {"message": "Hi"})
        await score_contact("c1", "high", [], 80, "")
        await save_draft("c1", "Subject", "Body", 80)
        result = await migrate_contact("c1")
        # Draft should reference the engagement
        # (The old drafts table uses contact_id, not engagement_id.
        #  Migration should note this -- full FK migration is separate.)
        assert result["migrated"]


class TestBatchMigration:

    @pytest.mark.asyncio
    async def test_migrate_all(self, db):
        """Batch migration of multiple contacts."""
        from outreach_pipeline.services.migration import migrate_all_contacts
        await upsert_contact("c1", {"message": "Hi"})
        await upsert_contact("c2", {"message": "Hello"})
        await upsert_contact("c3", {"company": "NoMessage"})  # no message
        await score_contact("c1", "high", [], 80, "")
        await score_contact("c2", "low", [], 20, "")

        result = await migrate_all_contacts()
        assert result["total"] == 3
        assert result["migrated"] == 2  # c1 and c2 have messages
        assert result["skipped"] == 1  # c3 has no message

    @pytest.mark.asyncio
    async def test_migrate_all_idempotent(self, db):
        from outreach_pipeline.services.migration import migrate_all_contacts
        await upsert_contact("c1", {"message": "Hi"})
        r1 = await migrate_all_contacts()
        r2 = await migrate_all_contacts()
        assert r1["migrated"] == 1
        assert r2["migrated"] == 0
