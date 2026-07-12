"""Edge case tests for all tool functions.

Tests boundary conditions, invalid inputs, state violations,
and sequences that could break the system.
"""
import json
import pytest

from outreach_pipeline.db.contacts import upsert_contact
from outreach_pipeline.db.submissions import create_submission
from outreach_pipeline.db.engagements import (
    create_engagement, add_submission, update_status, get_engagement,
)


async def _seed_contact(contact_id="c1"):
    await upsert_contact(contact_id, {"company": "Acme", "message": "Need pricing for RADIO-A"})


# ===== Invalid IDs =====

class TestInvalidIDs:
    @pytest.mark.asyncio
    async def test_engagement_details_nonexistent(self, db):
        from outreach_pipeline.tools.engagements import get_engagement_details
        r = await get_engagement_details(999999)
        assert r["engagement"] is None

    @pytest.mark.asyncio
    async def test_engagement_details_zero(self, db):
        from outreach_pipeline.tools.engagements import get_engagement_details
        r = await get_engagement_details(0)
        assert r["engagement"] is None

    @pytest.mark.asyncio
    async def test_engagement_details_negative(self, db):
        from outreach_pipeline.tools.engagements import get_engagement_details
        r = await get_engagement_details(-1)
        assert r["engagement"] is None

    @pytest.mark.asyncio
    async def test_contact_history_nonexistent(self, db):
        from outreach_pipeline.tools.engagements import get_contact_history
        r = await get_contact_history("nonexistent_id")
        assert not r["contact"]
        assert r["submissions"] == []
        assert r["engagements"] == []

    @pytest.mark.asyncio
    async def test_score_nonexistent_engagement(self, db):
        from outreach_pipeline.tools.engagements import score_engagement
        r = await score_engagement(999999, 50, "Test")
        assert not r["success"]

    @pytest.mark.asyncio
    async def test_update_status_nonexistent(self, db):
        from outreach_pipeline.tools.engagements import update_engagement_status
        r = await update_engagement_status(999999, "scoring")
        assert not r["success"]

    @pytest.mark.asyncio
    async def test_delete_nonexistent_contact(self, db):
        from outreach_pipeline.tools.contacts import delete_contact
        r = await delete_contact("nonexistent")
        assert not r["success"]

    @pytest.mark.asyncio
    async def test_preview_delete_nonexistent(self, db):
        from outreach_pipeline.tools.contacts import preview_delete_contact
        r = await preview_delete_contact("nonexistent")
        assert not r["found"]

    @pytest.mark.asyncio
    async def test_get_drafts_nonexistent_engagement(self, db):
        from outreach_pipeline.tools.workflow import get_drafts
        r = await get_drafts(engagement_id=999999)
        assert r["count"] == 0

    @pytest.mark.asyncio
    async def test_scoring_context_nonexistent(self, db):
        from outreach_pipeline.tools.workflow import get_scoring_context
        r = await get_scoring_context(999999)
        assert r["engagement"] is None

    @pytest.mark.asyncio
    async def test_drafting_context_nonexistent(self, db):
        from outreach_pipeline.tools.workflow import get_drafting_context
        r = await get_drafting_context(999999)
        assert r["engagement"] is None


# ===== Status transition violations =====

class TestStatusViolations:
    @pytest.mark.asyncio
    async def test_open_to_sent_rejected(self, db):
        from outreach_pipeline.tools.engagements import update_engagement_status
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        r = await update_engagement_status(eid, "sent")
        assert not r["success"]
        assert "Invalid transition" in r["error"]

    @pytest.mark.asyncio
    async def test_closed_to_anything_rejected(self, db):
        from outreach_pipeline.tools.engagements import update_engagement_status
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        await update_status(eid, "closed")
        for status in ["open", "scoring", "drafting", "sent", "blocked"]:
            r = await update_engagement_status(eid, status)
            assert not r["success"], f"Should reject closed -> {status}"

    @pytest.mark.asyncio
    async def test_sent_to_anything_except_closed_rejected(self, db):
        from outreach_pipeline.tools.engagements import update_engagement_status
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        for s in ["scoring", "drafting", "pending_approval", "approved", "sent"]:
            await update_status(eid, s)
        for status in ["open", "scoring", "drafting", "approved"]:
            r = await update_engagement_status(eid, status)
            assert not r["success"], f"Should reject sent -> {status}"

    @pytest.mark.asyncio
    async def test_terminal_blocked_cannot_reopen(self, db):
        from outreach_pipeline.tools.engagements import update_engagement_status
        await _seed_contact()
        eid = await create_engagement("c1", kind="support",
                                      status="blocked", block_terminal=True,
                                      block_code="SUPPORT_REQUEST")
        r = await update_engagement_status(eid, "open")
        assert not r["success"]
        assert "terminal" in r["error"].lower()


# ===== Score boundary values =====

class TestScoreBoundaries:
    @pytest.mark.asyncio
    async def test_score_zero_closes(self, db):
        from outreach_pipeline.tools.engagements import score_engagement
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        r = await score_engagement(eid, 0, "No fit at all")
        assert r["success"]
        assert r["status"] == "closed"

    @pytest.mark.asyncio
    async def test_score_25_closes(self, db):
        from outreach_pipeline.tools.engagements import score_engagement
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        r = await score_engagement(eid, 25, "Events contact")
        assert r["success"]
        assert r["status"] == "closed"

    @pytest.mark.asyncio
    async def test_score_26_stays_scoring(self, db):
        from outreach_pipeline.tools.engagements import score_engagement
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        r = await score_engagement(eid, 26, "Borderline")
        assert r["success"]
        assert r["status"] == "scoring"

    @pytest.mark.asyncio
    async def test_score_39_stays_scoring(self, db):
        """Drafting threshold is 40. 26-39 stays in scoring."""
        from outreach_pipeline.tools.engagements import score_engagement
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        r = await score_engagement(eid, 39, "Not quite there")
        assert r["status"] == "scoring"

    @pytest.mark.asyncio
    async def test_score_40_advances_to_drafting(self, db):
        from outreach_pipeline.tools.engagements import score_engagement
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        r = await score_engagement(eid, 40, "Meets threshold")
        assert r["status"] == "drafting"

    @pytest.mark.asyncio
    async def test_score_100(self, db):
        from outreach_pipeline.tools.engagements import score_engagement
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        r = await score_engagement(eid, 100, "Perfect lead")
        assert r["success"]
        assert r["status"] == "drafting"


# ===== Draft tone violations =====

class TestDraftViolations:
    @pytest.mark.asyncio
    async def test_draft_with_tracking_data(self, db):
        from outreach_pipeline.tools.workflow import save_engagement_draft
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        r = await save_engagement_draft(eid,
            "Follow up",
            "Hi [First Name],\n\nI noticed you downloaded our whitepaper and visited our pricing page 3 times.\n\nBest,\n[Sender Name]")
        assert not r["success"]
        assert "TRACKING_DATA" in r["violations"]

    @pytest.mark.asyncio
    async def test_draft_with_call_request(self, db):
        from outreach_pipeline.tools.workflow import save_engagement_draft
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        r = await save_engagement_draft(eid,
            "Quick chat",
            "Hi [First Name],\n\nLet's hop on a call this week to discuss your needs.\n\nBest,\n[Sender Name]")
        assert not r["success"]
        assert "CALL_REQUEST" in r["violations"]

    @pytest.mark.asyncio
    async def test_draft_missing_greeting(self, db):
        from outreach_pipeline.tools.workflow import save_engagement_draft
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        r = await save_engagement_draft(eid,
            "Info",
            "The RADIO-A is a great product for your use case.\n\nBest,\n[Sender Name]")
        assert not r["success"]
        assert "MISSING_GREETING" in r["violations"]

    @pytest.mark.asyncio
    async def test_draft_missing_signoff(self, db):
        from outreach_pipeline.tools.workflow import save_engagement_draft
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        r = await save_engagement_draft(eid,
            "Info",
            "Hi [First Name],\n\nThe RADIO-A fits your use case. Want the spec sheet?")
        assert not r["success"]
        assert "MISSING_SIGNOFF" in r["violations"]

    @pytest.mark.asyncio
    async def test_valid_draft_passes(self, db):
        from outreach_pipeline.tools.workflow import save_engagement_draft
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        r = await save_engagement_draft(eid,
            "Quick question about your inquiry",
            "Hi [First Name],\n\nThe RADIO-A platform supports your use case. Want me to send the configuration guide?\n\nBest,\n[Sender Name]",
            rag_context="RADIO-A config guide")
        assert r["success"]


# ===== Opt-out then action =====

class TestOptOutBlocking:
    @pytest.mark.asyncio
    async def test_opt_out_blocks_new_engagement(self, db):
        from outreach_pipeline.tools.engagements import add_opt_out
        from outreach_pipeline.services.intake import process_submission
        await _seed_contact()
        await add_opt_out("c1", "Unsubscribed")
        r = await process_submission("c1", message="I want pricing",
                                     form_name="Contact Us", mql_date="2026-03-30")
        assert r["engagement_id"] is None
        assert r["blocked_reason"] == "opted_out"

    @pytest.mark.asyncio
    async def test_opt_out_detected_by_policy(self, db):
        from outreach_pipeline.tools.engagements import add_opt_out, check_engagement_policy
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        await add_opt_out("c1", "Unsubscribed")
        r = await check_engagement_policy(eid)
        assert not r["allowed"]
        assert r["code"] == "OPT_OUT"


# ===== Retarget edge cases =====

class TestRetargetEdgeCases:
    @pytest.mark.asyncio
    async def test_retarget_on_open_engagement_fails(self, db):
        from outreach_pipeline.tools.engagements import create_retarget
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        r = await create_retarget(eid)
        assert not r["success"]

    @pytest.mark.asyncio
    async def test_retarget_on_blocked_engagement_fails(self, db):
        from outreach_pipeline.tools.engagements import create_retarget
        await _seed_contact()
        eid = await create_engagement("c1", kind="support", status="blocked")
        r = await create_retarget(eid)
        assert not r["success"]

    @pytest.mark.asyncio
    async def test_double_retarget_fails(self, db):
        from outreach_pipeline.tools.engagements import create_retarget
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        for s in ["scoring", "drafting", "pending_approval", "approved", "sent"]:
            await update_status(eid, s)
        r1 = await create_retarget(eid)
        assert r1["success"]
        r2 = await create_retarget(eid)
        assert not r2["success"]


# ===== Delete then reference =====

class TestDeleteThenReference:
    @pytest.mark.asyncio
    async def test_delete_then_get_details(self, db):
        from outreach_pipeline.tools.contacts import delete_contact, get_lead_details
        await _seed_contact()
        await create_engagement("c1", kind="sales")
        await delete_contact("c1")
        r = await get_lead_details("c1")
        assert not r["contact"]

    @pytest.mark.asyncio
    async def test_delete_then_get_submissions(self, db):
        from outreach_pipeline.tools.contacts import delete_contact
        from outreach_pipeline.tools.engagements import get_submissions
        await _seed_contact()
        await create_submission("c1", message="Test")
        await delete_contact("c1")
        r = await get_submissions("c1")
        assert r["count"] == 0

    @pytest.mark.asyncio
    async def test_delete_then_get_engagements(self, db):
        from outreach_pipeline.tools.contacts import delete_contact
        from outreach_pipeline.tools.engagements import get_engagements
        await _seed_contact()
        await create_engagement("c1", kind="sales")
        await delete_contact("c1")
        r = await get_engagements(contact_id="c1")
        assert r["count"] == 0


# ===== Empty/null inputs =====

class TestEmptyInputs:
    @pytest.mark.asyncio
    async def test_get_leads_no_filters(self, db):
        from outreach_pipeline.tools.contacts import get_leads
        r = await get_leads()
        assert "leads" in r

    @pytest.mark.asyncio
    async def test_get_engagements_no_filters(self, db):
        from outreach_pipeline.tools.engagements import get_engagements
        r = await get_engagements()
        assert "engagements" in r

    @pytest.mark.asyncio
    async def test_validate_empty_message(self, db):
        from outreach_pipeline.tools.messaging import validate_message
        r = validate_message("", "cold_outreach")
        # Empty message should fail validation (missing greeting, signoff)
        assert not r.passed

    @pytest.mark.asyncio
    async def test_save_draft_empty_body(self, db):
        from outreach_pipeline.tools.workflow import save_engagement_draft
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        r = await save_engagement_draft(eid, "Subject", "")
        assert not r["success"]

    @pytest.mark.asyncio
    async def test_save_draft_empty_subject(self, db):
        from outreach_pipeline.tools.workflow import save_engagement_draft
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        r = await save_engagement_draft(eid, "",
            "Hi [First Name],\n\nTest.\n\nBest,\n[Sender Name]")
        # Empty subject — should still save if body validates
        # (subject validation is not in tone_guard)
        assert "success" in r


# ===== Classification edge cases =====

class TestClassificationEdgeCases:
    @pytest.mark.asyncio
    async def test_po_with_redacted_number(self, db):
        from outreach_pipeline.policies.submission import classify_intent
        r = classify_intent("Status of PO# [phone redacted] please")
        assert r["support_flag"]
        assert r["intent_type"] == "support_request"

    @pytest.mark.asyncio
    async def test_contact_us_empty_message(self, db):
        from outreach_pipeline.policies.submission import classify_intent
        r = classify_intent("", form_name="Contact Us")
        assert r["intent_type"] == "sales_inquiry"

    @pytest.mark.asyncio
    async def test_download_form_empty_message(self, db):
        from outreach_pipeline.policies.submission import classify_intent
        r = classify_intent("", form_name="Download Whitepaper")
        assert r["intent_type"] == "content_download"

    @pytest.mark.asyncio
    async def test_event_in_url(self, db):
        from outreach_pipeline.policies.submission import classify_intent
        r = classify_intent("I want info", page_url="https://example.com/events/mwc")
        assert r["event_flag"]
        assert r["intent_type"] == "event_meeting"

    @pytest.mark.asyncio
    async def test_mwc_in_message(self, db):
        from outreach_pipeline.policies.submission import classify_intent
        r = classify_intent("Can we meet at MWC?")
        assert r["event_flag"]

    @pytest.mark.asyncio
    async def test_non_english_support(self, db):
        from outreach_pipeline.policies.submission import classify_intent
        # Arabic for "reset" — should still be classified if it matches patterns
        r = classify_intent("\u062a\u0635\u0641\u064a\u0631 \u0627\u0644\u062c\u0647\u0627\u0632")
        # The Arabic word for reset is in the _STRONG_SUPPORT pattern
        assert r["support_flag"]


# ===== Dedup edge cases =====

class TestDedupEdgeCases:
    @pytest.mark.asyncio
    async def test_same_message_same_day_is_dup(self, db):
        from outreach_pipeline.db.submissions import is_duplicate
        await _seed_contact()
        await create_submission("c1", message="Need pricing",
                                form_id="f1", mql_date="2026-03-27")
        assert await is_duplicate("c1", message="Need pricing",
                                  form_id="f1", mql_date="2026-03-27")

    @pytest.mark.asyncio
    async def test_same_message_different_day_IS_dup(self, db):
        """Regression: same (contact, form, message) is a dup even on a
        different date. HubSpot re-fetches assign fresh timestamps to
        the same form submission — date-sensitive dedup was letting
        them through as "new" rows."""
        from outreach_pipeline.db.submissions import is_duplicate
        await _seed_contact()
        await create_submission("c1", message="Need pricing",
                                form_id="f1", mql_date="2026-03-27")
        assert await is_duplicate("c1", message="Need pricing",
                                  form_id="f1", mql_date="2026-03-28")

    @pytest.mark.asyncio
    async def test_empty_message_empty_form_never_dup(self, db):
        from outreach_pipeline.db.submissions import is_duplicate
        await _seed_contact()
        await create_submission("c1", message="", form_id="", mql_date="2026-03-27")
        # Empty message + empty form_id = not enough to dedup
        assert not await is_duplicate("c1", message="",
                                      form_id="", mql_date="2026-03-27")

    @pytest.mark.asyncio
    async def test_empty_message_with_form_is_dup(self, db):
        from outreach_pipeline.db.submissions import is_duplicate
        await _seed_contact()
        await create_submission("c1", message="", form_id="f1", mql_date="2026-03-27")
        assert await is_duplicate("c1", message="",
                                  form_id="f1", mql_date="2026-03-27")


# ===== Send safety (NEVER actually sends) =====

class TestSendSafety:
    @pytest.mark.asyncio
    async def test_send_unapproved_rejected(self, db):
        from outreach_pipeline.tools.sending import send_message
        from outreach_pipeline.db.drafts import save_draft
        from outreach_pipeline.db.threads import create_user, create_thread, create_artifact
        await _seed_contact()
        # Create artifact in draft status (not approved)
        await create_user("user1", "user1@example.com", "User One")
        tid = await create_thread("Test", "user1")
        aid = await create_artifact(tid, "email", "c1",
                                    json.dumps({"subject": "Test", "body": "Test"}))
        r = await send_message(aid)
        assert not r["success"]

    @pytest.mark.asyncio
    async def test_send_nonexistent_artifact(self, db):
        from outreach_pipeline.tools.sending import send_message
        r = await send_message(999999)
        assert not r["success"]


# ===== Scrape block then scrape =====

class TestScrapeBlockEnforcement:
    @pytest.mark.asyncio
    async def test_add_scrape_block(self, db):
        from outreach_pipeline.tools.engagements import add_scrape_block
        r = await add_scrape_block("competitor.example.com", "Competitor")
        assert r["success"]
        from outreach_pipeline.db.guardrails import check_scrape_blocklist
        blocked, _ = await check_scrape_blocklist("competitor.example.com")
        assert blocked


# ===== Reclassify edge cases =====

class TestReclassifyEdgeCases:
    @pytest.mark.asyncio
    async def test_reclassify_nonexistent_submission(self, db):
        from outreach_pipeline.tools.engagements import reclassify_submission
        # Zero rows affected is reported as a failure so callers don't
        # believe a reclassify landed when it didn't — this matters
        # when the admin agent reports "done" to the user.
        r = await reclassify_submission(999999, intent_type="sales_inquiry")
        assert not r["success"]
        assert "not found" in r["error"].lower()

    @pytest.mark.asyncio
    async def test_reclassify_preserves_unrelated_fields(self, db):
        """Changing intent_type must NOT wipe product_match / intent_signals
        / classification_reasoning set by the original classifier."""
        from outreach_pipeline.db.submissions import (
            create_submission, classify_submission, get_submission,
        )
        from outreach_pipeline.tools.engagements import reclassify_submission
        from tests.test_tools_engagements_extended import _seed_contact

        await _seed_contact(db)
        sid = await create_submission("c1", message="Need pricing")
        # Original classification: intent_type + the richer fields
        # the LLM populated.
        await classify_submission(
            sid,
            intent_type="off_topic",
            product_family="microwave_radio",
            product_match="high",
            intent_signals=["pricing_inquiry"],
            classification_reasoning="Customer asked about pricing",
        )

        r = await reclassify_submission(sid, intent_type="sales_inquiry")
        assert r["success"]

        sub = await get_submission(sid)
        assert sub["intent_type"] == "sales_inquiry"
        # Unrelated fields must survive.
        assert sub["product_family"] == "microwave_radio"
        assert sub["product_match"] == "high"
        assert sub["intent_signals"] == ["pricing_inquiry"]
        assert sub["classification_reasoning"] == "Customer asked about pricing"


# ===== Draft status sync edge cases =====

class TestDraftStatusSyncEdgeCases:
    @pytest.mark.asyncio
    async def test_approve_with_upserted_draft(self, db):
        """Draft upserts in place — approve marks the single draft as approved."""
        from outreach_pipeline.db.drafts import save_draft, get_drafts
        from outreach_pipeline.tools.engagements import update_engagement_status
        await _seed_contact()
        eid = await create_engagement("c1", kind="sales")
        await save_draft("c1", "V1", "Body v1", engagement_id=eid)
        await save_draft("c1", "V2", "Body v2", engagement_id=eid)
        await save_draft("c1", "V3", "Body v3", engagement_id=eid)
        drafts = await get_drafts(engagement_id=eid)
        assert len(drafts) == 1  # upsert
        assert drafts[0]["subject"] == "V3"
        for s in ["scoring", "drafting", "pending_approval"]:
            await update_status(eid, s)
        await update_engagement_status(eid, "approved")
        drafts = await get_drafts(engagement_id=eid)
        assert drafts[0]["status"] == "approved"
