"""Regression tests for the SDR-handoff + pipeline-stats behavior.

These run before every commit via scripts/precommit.sh. Keep them
fast — no network, minimal DB — so the hook stays under the 4-minute
budget.
"""
import inspect
import re

import pytest

from outreach_pipeline.db import engagements as eng_mod
from outreach_pipeline.graph import draft_helpers, middleware
from outreach_pipeline.graph import tools as graph_tools
from outreach_pipeline.tools import workflow


# ---------- static invariants (no DB needed) ---------- #


def test_sdr_handoff_is_a_recognised_status():
    T = eng_mod.VALID_TRANSITIONS
    assert "sdr_handoff" in T
    assert "sdr_handoff" in T["drafting"], "must be reachable from drafting"
    assert "sdr_handoff" in T["pending_approval"], "must be reachable from pending_approval"
    assert T["sdr_handoff"] == {"drafting", "closed"}, \
        "sdr_handoff should only go to closed or back to drafting"


def test_sdr_handoff_cannot_reopen_to_open():
    # The 'open' transition is how blocked engagements come back — sdr_handoff
    # must NOT piggyback on that loophole.
    assert "open" not in eng_mod.VALID_TRANSITIONS["sdr_handoff"]


def test_pipeline_stats_date_clauses_use_real_columns():
    """get_pipeline_stats used to reference columns that don't exist
    (`contacts.created_at`, `submissions.submitted_at`,
    `replies.created_at`). Any date-filtered query silently returned
    zeros. This test locks in the right column names so that bug
    can't regress.
    """
    src = inspect.getsource(workflow.get_pipeline_stats)
    assert '_date_clause("fetched_at"' in src, "contacts uses fetched_at"
    assert '_date_clause("captured_at"' in src, "submissions uses captured_at"
    assert '_date_clause("detected_at"' in src, "replies uses detected_at"
    # The broken names must not reappear — check specifically for the
    # combos that were wrong (drafts.created_at is legitimately correct,
    # so we only forbid the specific bad calls).
    assert '_date_clause("created_at", c_params' not in src
    assert '_date_clause("submitted_at"' not in src


def test_get_drafts_routes_engagement_statuses_via_join():
    """`get_drafts(status='sdr_handoff')` must query by engagement
    status, not by draft status — otherwise the user asks for
    "SDR handoff drafts" and gets nothing.
    """
    src = inspect.getsource(workflow.get_drafts)
    assert "ENG_STATUSES" in src
    assert "sdr_handoff" in src
    assert "JOIN engagements" in src


def test_draft_context_includes_visited_pages():
    src = inspect.getsource(draft_helpers.afetch_draft_context)
    assert "visited_pages" in src, "Draft Review card needs visited pages"
    # Must be deduped by URL — otherwise repeat visits flood the card.
    assert "page_url" in src and "page_title" in src


def test_middleware_accepts_forward_sdr_resume_key():
    """Clicking "Forward to SDR" on the Draft Review card resumes the
    interrupt with {"forward_sdr": true}. The middleware must:
      - parse that key
      - save the draft with status=sdr_handoff
      - transition the engagement to sdr_handoff
    """
    src = inspect.getsource(middleware.DraftReviewMiddleware)
    assert "forward_sdr" in src
    assert 'draft_status="sdr_handoff"' in src
    assert "update_status" in src and "sdr_handoff" in src
    # The resume must NOT short-circuit on "approved" when forward_sdr is set
    assert "forward_sdr" in src.split("if approved")[0] or \
           "approved or forward_sdr" in src


def test_get_engagements_docstring_lists_sdr_handoff():
    """The admin agent picks tool args from the docstring — if
    sdr_handoff isn't documented, the LLM defaults to 'sent' and the
    user gets "no engagements" for every sdr_handoff query.
    """
    # @tool wraps the function; read the module source directly.
    path = graph_tools.__file__
    with open(path) as f:
        src = f.read()
    # Find the get_engagements def + its docstring
    m = re.search(
        r"async def get_engagements\([^)]*\)[^:]*:\s*\"\"\"(.+?)\"\"\"",
        src, flags=re.DOTALL,
    )
    assert m, "get_engagements docstring not found"
    doc = m.group(1)
    assert "sdr_handoff" in doc, \
        "sdr_handoff must appear in get_engagements docstring"


def test_ui_draft_review_has_contact_said_bold_and_no_rag_sources():
    """Lock the UI invariants for the Draft Review interrupt card."""
    path = "frontend/src/hooks/use-interrupts.tsx"
    with open(path) as f:
        src = f.read()
    # "Contact said" must be bold (font-semibold) not faint
    assert re.search(r'font-semibold[^"]*"[\s>]+.*?Contact said', src, re.DOTALL) \
        or "font-semibold text-text" in src.split('"Contact said"')[0] if "Contact said" in src else False, \
        "'Contact said' should render with font-semibold"
    # The RAG sources block was removed — no "RAG sources" label should remain
    assert "RAG sources" not in src, "RAG sources block must be removed"
    # "Forward to SDR" button must be present
    assert "Forward to SDR" in src
    # Visited pages should render
    assert "visited_pages" in src or "Pages visited" in src


def test_ui_stats_hint_shows_every_non_zero_status():
    """The engagements tile hint was sliced to top-3, which made the
    numbers not add up to the headline. Lock the fix in place.
    """
    path = "frontend/src/hooks/use-outreach-ui.tsx"
    with open(path) as f:
        src = f.read()
    # Find the engStatusParts computation
    block = re.search(r"engStatusParts\s*=\s*Object\.entries[\s\S]+?\.map\(", src)
    assert block, "engStatusParts block not found"
    text = block.group(0)
    assert ".slice(0, 3)" not in text, \
        "hint must NOT slice to top-3 — numbers wouldn't add up to total"


def test_ui_sdr_handoff_status_badge_registered():
    """The engagements table / drafts card status maps must colour
    sdr_handoff — otherwise the badge is invisible grey.
    """
    for path in [
        "frontend/src/components/generative-ui/tool-table.tsx",
        "frontend/src/hooks/use-outreach-ui.tsx",
    ]:
        with open(path) as f:
            src = f.read()
        assert "sdr_handoff" in src, f"{path} missing sdr_handoff badge entry"


# ---------- live DB round-trip (needs outreach_test) ---------- #


@pytest.mark.asyncio
async def test_engagement_transition_round_trip(db):
    """Create drafting → sdr_handoff → closed; verify each write
    survives and that sdr_handoff → open is rejected.
    """
    from outreach_pipeline.db.contacts import upsert_contact
    from outreach_pipeline.db.engagements import (
        create_engagement, update_status, get_engagement,
    )

    await upsert_contact("c-sdr", {"company": "Acme"})
    eid = await create_engagement("c-sdr", kind="sales", status="drafting")

    await update_status(eid, "sdr_handoff")
    assert (await get_engagement(eid))["status"] == "sdr_handoff"

    # The forbidden shortcut: sdr_handoff → open must raise.
    with pytest.raises(ValueError):
        await update_status(eid, "open")

    await update_status(eid, "closed")
    assert (await get_engagement(eid))["status"] == "closed"


@pytest.mark.asyncio
async def test_pipeline_stats_with_date_filter_does_not_error(db):
    """The fixed column names must actually work against a real DB.
    Regression for: `UndefinedColumnError: column "created_at" does
    not exist` on contacts.
    """
    from outreach_pipeline.db.contacts import upsert_contact

    await upsert_contact("c-stats", {"company": "Acme"})
    # Must not raise — this was the bug.
    result = await workflow.get_pipeline_stats(
        after="2026-01-01", before="2026-12-31",
    )
    assert "contacts" in result
    assert "engagements" in result
    # by_status is a dict; the keys can be empty but the shape holds.
    assert isinstance(result["engagements"]["by_status"], dict)


@pytest.mark.asyncio
async def test_get_drafts_sdr_handoff_joins_engagements(db):
    """A draft whose row status lags ("approved") but whose engagement
    is sdr_handoff should still surface when asking for sdr_handoff
    drafts.
    """
    from outreach_pipeline.db.contacts import upsert_contact
    from outreach_pipeline.db.engagements import create_engagement, update_status
    from outreach_pipeline.db.drafts import save_draft

    await upsert_contact("c-drafts", {"company": "Acme"})
    eid = await create_engagement("c-drafts", kind="sales", status="drafting")
    await save_draft(
        "c-drafts", subject="s", body="b",
        engagement_id=eid, status="approved",  # intentionally stale status
    )
    await update_status(eid, "sdr_handoff")

    result = await workflow.get_drafts(status="sdr_handoff")
    ids = [d["engagement_id"] for d in result.get("drafts", [])]
    assert eid in ids, \
        "sdr_handoff filter must find drafts via the engagement JOIN"


# ---------- edge cases ---------- #


class TestEngagementTransitionEdgeCases:
    """Lock down every forbidden path into and out of sdr_handoff."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("from_status", ["open", "scoring", "approved", "sent", "closed", "blocked"])
    async def test_cannot_reach_sdr_handoff_from(self, db, from_status):
        from outreach_pipeline.db.contacts import upsert_contact
        from outreach_pipeline.db.engagements import create_engagement, update_status
        await upsert_contact("c", {"company": "Acme"})
        eid = await create_engagement("c", kind="sales", status=from_status,
                                       block_terminal=(from_status == "blocked"))
        with pytest.raises(ValueError):
            await update_status(eid, "sdr_handoff")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("to_status", ["open", "scoring", "pending_approval", "approved", "sent", "blocked"])
    async def test_sdr_handoff_cannot_jump_to(self, db, to_status):
        """From sdr_handoff, only `drafting` and `closed` are valid."""
        from outreach_pipeline.db.contacts import upsert_contact
        from outreach_pipeline.db.engagements import create_engagement, update_status
        await upsert_contact("c", {"company": "Acme"})
        eid = await create_engagement("c", kind="sales", status="drafting")
        await update_status(eid, "sdr_handoff")
        with pytest.raises(ValueError):
            await update_status(eid, to_status)

    @pytest.mark.asyncio
    async def test_sdr_handoff_back_to_drafting_is_allowed(self, db):
        """Re-drafting after SDR feedback — explicit valid path."""
        from outreach_pipeline.db.contacts import upsert_contact
        from outreach_pipeline.db.engagements import create_engagement, update_status, get_engagement
        await upsert_contact("c", {"company": "Acme"})
        eid = await create_engagement("c", kind="sales", status="pending_approval")
        await update_status(eid, "sdr_handoff")
        await update_status(eid, "drafting")
        assert (await get_engagement(eid))["status"] == "drafting"


class TestPipelineStatsEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_db_returns_zeros_no_errors(self, db):
        r = await workflow.get_pipeline_stats()
        assert r["contacts"]["total"] == 0
        assert r["engagements"]["total"] == 0
        assert r["submissions"]["total"] == 0
        assert r["drafts"]["total"] == 0
        # by_status must be an empty dict, not None / missing.
        assert r["engagements"]["by_status"] == {}

    @pytest.mark.asyncio
    async def test_far_future_range_returns_zeros(self, db):
        from outreach_pipeline.db.contacts import upsert_contact
        await upsert_contact("c", {"company": "Acme"})
        r = await workflow.get_pipeline_stats(after="2099-01-01", before="2099-12-31")
        assert r["contacts"]["total"] == 0

    @pytest.mark.asyncio
    async def test_inverted_range_returns_zeros_without_crashing(self, db):
        """after > before is nonsense but must not throw."""
        r = await workflow.get_pipeline_stats(after="2026-12-31", before="2026-01-01")
        assert r["contacts"]["total"] == 0

    @pytest.mark.asyncio
    async def test_invalid_date_string_is_treated_as_unset(self, db):
        """Garbage 'after' like 'this week' should not crash — parse_date
        returns None and the filter is skipped.
        """
        from outreach_pipeline.db.contacts import upsert_contact
        await upsert_contact("c", {"company": "Acme"})
        r = await workflow.get_pipeline_stats(after="this week", before="")
        # Unparseable date → filter not applied → unfiltered count
        assert r["contacts"]["total"] == 1


class TestGetDraftsEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_status_returns_all_drafts(self, db):
        from outreach_pipeline.db.contacts import upsert_contact
        from outreach_pipeline.db.engagements import create_engagement
        from outreach_pipeline.db.drafts import save_draft
        await upsert_contact("c", {"company": "Acme"})
        eid = await create_engagement("c", kind="sales", status="drafting")
        await save_draft("c", subject="s", body="b",
                         engagement_id=eid, status="approved")
        r = await workflow.get_drafts(status="")
        assert r.get("count", 0) >= 1

    @pytest.mark.asyncio
    async def test_draft_level_status_bypasses_join(self, db):
        """status='approved' is a draft-level status — must NOT go
        through the engagement JOIN (otherwise we'd miss draft rows
        whose engagement isn't 'approved').
        """
        from outreach_pipeline.db.contacts import upsert_contact
        from outreach_pipeline.db.engagements import create_engagement
        from outreach_pipeline.db.drafts import save_draft
        await upsert_contact("c", {"company": "Acme"})
        eid = await create_engagement("c", kind="sales", status="drafting")
        await save_draft("c", subject="s", body="b",
                         engagement_id=eid, status="approved")
        r = await workflow.get_drafts(status="approved")
        ids = [d["engagement_id"] for d in r.get("drafts", [])]
        assert eid in ids

    @pytest.mark.asyncio
    async def test_sdr_handoff_with_no_matches_returns_empty(self, db):
        """No engagement in sdr_handoff → empty result, no crash."""
        r = await workflow.get_drafts(status="sdr_handoff")
        assert r.get("drafts") == [] or r.get("count") == 0

    @pytest.mark.asyncio
    async def test_contact_id_filter_combined_with_engagement_status(self, db):
        """contact_id AND engagement status together — both filters
        must apply (AND, not OR).
        """
        from outreach_pipeline.db.contacts import upsert_contact
        from outreach_pipeline.db.engagements import create_engagement, update_status
        from outreach_pipeline.db.drafts import save_draft
        await upsert_contact("c-a", {"company": "A"})
        await upsert_contact("c-b", {"company": "B"})
        eid_a = await create_engagement("c-a", kind="sales", status="drafting")
        eid_b = await create_engagement("c-b", kind="sales", status="drafting")
        await save_draft("c-a", subject="x", body="x", engagement_id=eid_a, status="approved")
        await save_draft("c-b", subject="y", body="y", engagement_id=eid_b, status="approved")
        await update_status(eid_a, "sdr_handoff")
        await update_status(eid_b, "sdr_handoff")

        r = await workflow.get_drafts(status="sdr_handoff", contact_id="c-a")
        ids = [d["engagement_id"] for d in r.get("drafts", [])]
        assert eid_a in ids
        assert eid_b not in ids


class TestMiddlewareResumeParsing:
    """`parse_resume` must handle the shapes CopilotKit can actually
    send: dict, JSON string, plain string.
    """

    def test_dict_passthrough(self):
        r = draft_helpers.parse_resume({"forward_sdr": True})
        assert r == {"forward_sdr": True}

    def test_json_string(self):
        r = draft_helpers.parse_resume('{"forward_sdr": true, "approved": false}')
        assert r["forward_sdr"] is True

    def test_plain_string_approved_alias(self):
        for word in ("approved", "Approve", "yes"):
            assert draft_helpers.parse_resume(word) == {"approved": True}

    def test_plain_string_falls_back_to_feedback(self):
        r = draft_helpers.parse_resume("please shorten the intro")
        assert r == {"feedback": "please shorten the intro"}

    def test_middleware_prefers_approved_over_forward_sdr_if_both_set(self):
        """If a buggy client sends both flags, approved wins (the
        user clicked Approve; Forward-to-SDR would have been a
        separate click). Locked by checking the middleware source
        orders the checks: `if approved: ... elif forward_sdr:`.
        """
        src = inspect.getsource(middleware.DraftReviewMiddleware)
        # The save/handoff branches must be in `if approved / elif forward_sdr`
        # order — not the other way around.
        approved_idx = src.find("if approved:")
        forward_idx = src.find("elif forward_sdr:")
        assert approved_idx > 0 and forward_idx > approved_idx, \
            "approved branch must come before forward_sdr branch"


class TestApprovalAdvancesEngagement:
    """The Draft Review interrupt must leave both statuses in a
    consistent state: draft=approved AND engagement=approved.
    Before the fix, engagement stuck at pending_approval while the
    draft was approved — which broke Mark-as-sent, undercounted
    approvals in stats, and confused SDRs reading the drafts card.
    """

    def test_middleware_approved_branch_advances_engagement(self):
        src = inspect.getsource(middleware.DraftReviewMiddleware)
        # The approved branch must call update_status with "approved"
        # — locate the block bounded by `if approved:` and `elif forward_sdr:`.
        approved_block = src.split("if approved:")[1].split("elif forward_sdr:")[0]
        assert "update_status" in approved_block, \
            "approved branch must call update_status"
        assert '"approved"' in approved_block, \
            'approved branch must pass "approved" to update_status'

    @pytest.mark.asyncio
    async def test_draft_approved_via_update_status_syncs_engagement_too(self, db):
        """Tool-level update_engagement_status(eid, 'approved') is the
        public path the middleware relies on — it must flip both the
        engagement AND the latest draft together.
        """
        from outreach_pipeline.db.contacts import upsert_contact
        from outreach_pipeline.db.engagements import create_engagement, get_engagement
        from outreach_pipeline.db.drafts import save_draft, get_drafts as _db_drafts
        from outreach_pipeline.tools.engagements import update_engagement_status

        await upsert_contact("c-approve", {"company": "Acme"})
        eid = await create_engagement("c-approve", kind="sales", status="drafting")
        await save_draft(
            "c-approve", subject="s", body="b",
            engagement_id=eid, status="draft",
        )
        from outreach_pipeline.db.engagements import update_status
        await update_status(eid, "pending_approval")

        result = await update_engagement_status(eid, "approved")
        assert result.get("success"), result
        assert (await get_engagement(eid))["status"] == "approved"
        drafts = await _db_drafts(engagement_id=eid, limit=1)
        assert drafts[0]["status"] == "approved", \
            "draft must be auto-synced to approved"


class TestMarkSentEndpoint:
    """POST /engagements/{id}/mark-sent handles two entry states:
    - approved engagement                 → one hop approved → sent
    - pending_approval + draft=approved   → two hops (legacy state)
    """

    @pytest.mark.asyncio
    async def test_mark_sent_from_approved(self, db):
        from outreach_pipeline.db.contacts import upsert_contact
        from outreach_pipeline.db.engagements import (
            create_engagement, update_status, get_engagement,
        )
        from outreach_pipeline.db.drafts import save_draft, get_drafts as _db_drafts
        from outreach_pipeline.tools.engagements import update_engagement_status

        await upsert_contact("c-ms1", {"company": "Acme"})
        eid = await create_engagement("c-ms1", kind="sales", status="drafting")
        await save_draft("c-ms1", subject="s", body="b",
                         engagement_id=eid, status="approved")
        await update_status(eid, "pending_approval")
        await update_status(eid, "approved")

        # Simulate the endpoint's single-hop path
        r = await update_engagement_status(eid, "sent")
        assert r.get("success"), r
        assert (await get_engagement(eid))["status"] == "sent"
        drafts = await _db_drafts(engagement_id=eid, limit=1)
        assert drafts[0]["status"] == "sent"
        assert drafts[0]["sent_at"], "sent_at must be stamped"

    @pytest.mark.asyncio
    async def test_mark_sent_rejects_from_drafting(self, db):
        """drafting → sent is not a valid transition."""
        from outreach_pipeline.db.contacts import upsert_contact
        from outreach_pipeline.db.engagements import create_engagement
        from outreach_pipeline.tools.engagements import update_engagement_status

        await upsert_contact("c-ms2", {"company": "Acme"})
        eid = await create_engagement("c-ms2", kind="sales", status="drafting")
        r = await update_engagement_status(eid, "sent")
        assert not r.get("success"), \
            "update_engagement_status must refuse drafting → sent"


class TestGetDraftsIncludesEngagementStatus:
    """The Mark-as-sent button keys off the draft's status directly,
    but the drafts card also surfaces the engagement status for
    other decisions. Lock in that get_drafts returns both.
    """

    @pytest.mark.asyncio
    async def test_engagement_status_present_on_each_draft(self, db):
        from outreach_pipeline.db.contacts import upsert_contact
        from outreach_pipeline.db.engagements import create_engagement, update_status
        from outreach_pipeline.db.drafts import save_draft

        await upsert_contact("c-es", {"company": "Acme"})
        eid = await create_engagement("c-es", kind="sales", status="drafting")
        await save_draft("c-es", subject="s", body="b",
                         engagement_id=eid, status="draft")
        await update_status(eid, "pending_approval")

        result = await workflow.get_drafts()
        assert result["count"] >= 1
        d = next(d for d in result["drafts"] if d["engagement_id"] == eid)
        assert d["engagement_status"] == "pending_approval"


class TestMockHubSpotWiring:
    """The OUTREACH_MOCK_HUBSPOT=1 flag must swap the real client for
    the mock. Static check — can't easily exercise the runtime swap
    in a pytest (module-level singleton).
    """

    def test_factory_checks_env_flag(self):
        import outreach_pipeline.hubspot_client as hsc
        src = inspect.getsource(hsc.get_async_hubspot)
        assert "OUTREACH_MOCK_HUBSPOT" in src
        assert "MockAsyncHubSpotClient" in src

    def test_mock_implements_pipeline_methods(self):
        from outreach_pipeline.adapters.mock_hubspot import MockAsyncHubSpotClient
        client = MockAsyncHubSpotClient()
        # Every method the ingest pipeline calls must be present.
        assert hasattr(client, "asearch_contacts")
        assert hasattr(client, "aget_contact_profile")
        assert hasattr(client, "aget_contact")
        assert client.config.get("report_timezone"), \
            "config must carry report_timezone (used by build_filters)"


class TestStatusBadgeUIFallback:
    """Unknown statuses must fall back to the neutral grey badge
    (never throw, never render 'undefined').
    """

    def test_unknown_status_has_fallback(self):
        with open("frontend/src/components/generative-ui/tool-table.tsx") as f:
            src = f.read()
        # statusBadge should end with a `return colors[status] || "bg-..."`
        assert re.search(r"return colors\[status\]\s*\|\|", src), \
            "statusBadge must fall back when status key missing"
