"""Comprehensive tests for engagement-aware Excel export."""
import json
import tempfile

import pytest
from openpyxl import load_workbook

from outreach_pipeline.db.contacts import upsert_contact, score_contact, enrich_contact
from outreach_pipeline.db.drafts import save_draft
from outreach_pipeline.db.replies import save_reply
from outreach_pipeline.db.guardrails import add_opt_out
from outreach_pipeline.db.submissions import create_submission, classify_submission
from outreach_pipeline.db.engagements import (
    create_engagement, add_submission, score_engagement,
)


class _ExportHelper:
    """Shared helpers for export tests."""

    @staticmethod
    async def _export(output_path):
        from outreach_pipeline.export_pipeline import export_pipeline
        return await export_pipeline(output_path)

    @staticmethod
    def _load(output_path):
        return load_workbook(output_path)

    @staticmethod
    def _get_sheet_data(output_path, sheet_name):
        """Read all rows from a sheet as list of dicts keyed by header."""
        wb = load_workbook(output_path)
        if sheet_name not in wb.sheetnames:
            return []
        ws = wb[sheet_name]
        headers = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]
        rows = []
        for r in range(2, ws.max_row + 1):
            row = {headers[c]: ws.cell(row=r, column=c + 1).value for c in range(len(headers))}
            rows.append(row)
        return rows

    @staticmethod
    def _get_metrics(output_path):
        """Read metrics sheet as dict of metric_name → value."""
        wb = load_workbook(output_path)
        ws = wb["Metrics"]
        metrics = {}
        for row in range(2, ws.max_row + 1):
            metric = ws.cell(row=row, column=2).value
            value = ws.cell(row=row, column=3).value
            if metric:
                metrics[metric] = value
        return metrics


@pytest.fixture
def output_path():
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        return f.name


class TestExportEmpty(_ExportHelper):
    @pytest.mark.asyncio
    async def test_empty_db_exports_without_crash(self, db, output_path):
        await self._export(output_path)
        wb = self._load(output_path)
        assert len(wb.sheetnames) > 0

    @pytest.mark.asyncio
    async def test_empty_db_has_no_engagements_sheet(self, db, output_path):
        await self._export(output_path)
        wb = self._load(output_path)
        assert "Engagements" not in wb.sheetnames

    @pytest.mark.asyncio
    async def test_empty_db_has_no_submissions_sheet(self, db, output_path):
        await self._export(output_path)
        wb = self._load(output_path)
        assert "Submissions" not in wb.sheetnames


class TestEngagementsSheet(_ExportHelper):
    @pytest.mark.asyncio
    async def test_sheet_exists_with_data(self, db, output_path):
        await upsert_contact("c1", {"company": "Acme", "message": "Hi"})
        sid = await create_submission("c1", message="Hi", mql_date="2026-03-28T10:00:00Z")
        eid = await create_engagement("c1", kind="sales")
        await add_submission(eid, sid)
        await self._export(output_path)
        wb = self._load(output_path)
        assert "Engagements" in wb.sheetnames

    @pytest.mark.asyncio
    async def test_headers_present(self, db, output_path):
        await upsert_contact("c1", {"company": "Acme"})
        await create_engagement("c1", kind="sales")
        await self._export(output_path)
        wb = self._load(output_path)
        ws = wb["Engagements"]
        headers = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]
        assert "Eng ID" in headers
        assert "Kind" in headers
        assert "Status" in headers
        assert "Score" in headers
        assert "Product" in headers
        assert "Company" in headers
        assert "Block Code" in headers

    @pytest.mark.asyncio
    async def test_engagement_data_correct(self, db, output_path):
        await upsert_contact("c1", {"company": "Acme", "industry": "Telecom"})
        await enrich_contact("c1", company_context="Makes rockets")
        eid = await create_engagement("c1", kind="sales", primary_product="microwave_radio")
        await score_engagement(eid, score=85, score_reasoning="Strong signals",
                               primary_product="microwave_radio")
        await self._export(output_path)
        rows = self._get_sheet_data(output_path, "Engagements")
        assert len(rows) == 1
        assert rows[0]["Kind"] == "sales"
        assert rows[0]["Score"] == 85
        assert rows[0]["Product"] == "microwave_radio"
        assert rows[0]["Company"] == "Acme"
        assert rows[0]["Company Context"] == "Makes rockets"
        assert rows[0]["Score Reasoning"] == "Strong signals"

    @pytest.mark.asyncio
    async def test_blocked_engagement_shows_block_info(self, db, output_path):
        await upsert_contact("c1", {"company": "Acme"})
        await create_engagement("c1", kind="support",
                                status="blocked", block_code="SUPPORT_REQUEST",
                                block_reason="Route to support", block_terminal=True)
        await self._export(output_path)
        rows = self._get_sheet_data(output_path, "Engagements")
        assert rows[0]["Kind"] == "support"
        assert rows[0]["Status"] == "blocked"
        assert rows[0]["Block Code"] == "SUPPORT_REQUEST"
        assert rows[0]["Block Reason"] == "Route to support"

    @pytest.mark.asyncio
    async def test_multiple_engagements_sorted_by_score(self, db, output_path):
        await upsert_contact("c1", {"company": "Acme"})
        await upsert_contact("c2", {"company": "Beta"})
        eid1 = await create_engagement("c1", kind="sales")
        eid2 = await create_engagement("c2", kind="sales")
        await score_engagement(eid1, score=40)
        await score_engagement(eid2, score=90)
        await self._export(output_path)
        rows = self._get_sheet_data(output_path, "Engagements")
        assert len(rows) == 2
        assert rows[0]["Score"] == 90  # higher score first
        assert rows[1]["Score"] == 40

    @pytest.mark.asyncio
    async def test_submission_count_shown(self, db, output_path):
        await upsert_contact("c1", {"company": "Acme"})
        sid1 = await create_submission("c1", message="First")
        sid2 = await create_submission("c1", message="Second")
        eid = await create_engagement("c1", kind="sales")
        await add_submission(eid, sid1)
        await add_submission(eid, sid2)
        await self._export(output_path)
        rows = self._get_sheet_data(output_path, "Engagements")
        assert rows[0]["Submissions"] == 2


class TestSubmissionsSheet(_ExportHelper):
    @pytest.mark.asyncio
    async def test_sheet_exists_with_data(self, db, output_path):
        await upsert_contact("c1", {"company": "Acme"})
        await create_submission("c1", message="Hi", form_name="Contact Us",
                                mql_date="2026-03-28T10:00:00Z")
        await self._export(output_path)
        wb = self._load(output_path)
        assert "Submissions" in wb.sheetnames

    @pytest.mark.asyncio
    async def test_headers_present(self, db, output_path):
        await upsert_contact("c1", {"company": "Acme"})
        await create_submission("c1", message="Hi")
        await self._export(output_path)
        wb = self._load(output_path)
        ws = wb["Submissions"]
        headers = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]
        assert "Contact ID" in headers
        assert "Message" in headers
        assert "Intent Type" in headers
        assert "Product Family" in headers

    @pytest.mark.asyncio
    async def test_submission_data_correct(self, db, output_path):
        await upsert_contact("c1", {"company": "Acme"})
        sid = await create_submission("c1", message="Need pricing for RADIO-A",
                                      form_name="Contact Us", page_url="https://example.com/products",
                                      mql_date="2026-03-28T10:00:00Z")
        await classify_submission(sid, intent_type="sales_inquiry",
                                  product_family="microwave_radio")
        await self._export(output_path)
        rows = self._get_sheet_data(output_path, "Submissions")
        assert len(rows) == 1
        assert rows[0]["Company"] == "Acme"
        assert rows[0]["Message"] == "Need pricing for RADIO-A"
        assert rows[0]["Form"] == "Contact Us"
        assert rows[0]["Intent Type"] == "sales_inquiry"
        assert rows[0]["Product Family"] == "microwave_radio"

    @pytest.mark.asyncio
    async def test_excludes_migration_submissions(self, db, output_path):
        await upsert_contact("c1", {"company": "Acme"})
        await create_submission("c1", message="Real", source="hubspot")
        await create_submission("c1", message="Migrated", source="migration")
        await self._export(output_path)
        rows = self._get_sheet_data(output_path, "Submissions")
        assert len(rows) == 1
        assert rows[0]["Message"] == "Real"

    @pytest.mark.asyncio
    async def test_support_flag_shown(self, db, output_path):
        await upsert_contact("c1", {"company": "Acme"})
        sid = await create_submission("c1", message="Unit not working")
        await classify_submission(sid, intent_type="support_request",
                                  support_flag=True)
        await self._export(output_path)
        rows = self._get_sheet_data(output_path, "Submissions")
        assert rows[0]["Support"] == "Yes"

    @pytest.mark.asyncio
    async def test_content_download_no_message(self, db, output_path):
        await upsert_contact("c1", {"company": "Acme"})
        await create_submission("c1", message="",
                                form_name="Solution Brief Download",
                                page_url="https://example.com/resources/mesh-radio")
        await self._export(output_path)
        rows = self._get_sheet_data(output_path, "Submissions")
        assert len(rows) == 1
        assert rows[0]["Message"] in ("", None)  # Excel may store empty as None
        assert rows[0]["Form"] == "Solution Brief Download"


class TestMetricsSheet(_ExportHelper):
    @pytest.mark.asyncio
    async def test_metrics_has_all_categories(self, db, output_path):
        await upsert_contact("c1", {"company": "A"})
        await score_contact("c1", "high", [], 80, "")
        await create_submission("c1", message="Hi")
        await create_engagement("c1", kind="sales")
        await self._export(output_path)
        metrics = self._get_metrics(output_path)
        assert "Total contacts" in metrics
        assert "Total submissions" in metrics
        assert "Total engagements" in metrics
        assert "Sales engagements" in metrics
        assert "Emails drafted" in metrics
        assert "Opt-outs" in metrics

    @pytest.mark.asyncio
    async def test_metrics_engagement_score_distribution(self, db, output_path):
        await upsert_contact("c1", {"company": "A"})
        await upsert_contact("c2", {"company": "B"})
        await upsert_contact("c3", {"company": "C"})
        eid1 = await create_engagement("c1", kind="sales")
        eid2 = await create_engagement("c2", kind="sales")
        eid3 = await create_engagement("c3", kind="sales")
        await score_engagement(eid1, score=90)
        await score_engagement(eid2, score=60)
        await score_engagement(eid3, score=20)
        await self._export(output_path)
        metrics = self._get_metrics(output_path)
        assert metrics["High (80-100)"] == "1"
        assert metrics["Medium (40-79)"] == "1"
        assert metrics["Low (1-39)"] == "1"

    @pytest.mark.asyncio
    async def test_metrics_support_and_events_counted(self, db, output_path):
        await upsert_contact("c1", {"company": "A"})
        await upsert_contact("c2", {"company": "B"})
        await create_engagement("c1", kind="support",
                                status="blocked", block_terminal=True)
        await create_engagement("c2", kind="events",
                                status="blocked", block_terminal=True)
        await self._export(output_path)
        metrics = self._get_metrics(output_path)
        assert metrics["Support (blocked)"] == "1"
        assert metrics["Events (blocked)"] == "1"

    @pytest.mark.asyncio
    async def test_metrics_submission_counts(self, db, output_path):
        await upsert_contact("c1", {"company": "A"})
        sid1 = await create_submission("c1", message="Pricing")
        sid2 = await create_submission("c1", message="",
                                        form_name="Download")
        await classify_submission(sid1, intent_type="sales_inquiry")
        await classify_submission(sid2, intent_type="content_download")
        await self._export(output_path)
        metrics = self._get_metrics(output_path)
        assert metrics["Total submissions"] == "2"
        assert metrics["Sales inquiries"] == "1"
        assert metrics["Content downloads"] == "1"

    @pytest.mark.asyncio
    async def test_metrics_opt_outs(self, db, output_path):
        await upsert_contact("c1", {"company": "A"})
        await add_opt_out("c1", "Not interested")
        await self._export(output_path)
        metrics = self._get_metrics(output_path)
        assert metrics["Opt-outs"] == "1"

    @pytest.mark.asyncio
    async def test_metrics_drafts_and_sends(self, db, output_path):
        await upsert_contact("c1", {"company": "A"})
        await save_draft("c1", "Subject", "Body", 80)
        await self._export(output_path)
        metrics = self._get_metrics(output_path)
        assert metrics["Emails drafted"] == "1"
        assert metrics["Emails sent"] == "0"


class TestAllSheetsPresent(_ExportHelper):
    @pytest.mark.asyncio
    async def test_full_export_has_all_sheets(self, db, output_path):
        """Full pipeline data should produce all 7 sheets."""
        await upsert_contact("c1", {"company": "Acme", "message": "Pricing"})
        await enrich_contact("c1", "Makes rockets")
        await score_contact("c1", "high", ["pricing"], 85, "Strong")
        await save_draft("c1", "Subject", "Body", 85)
        await save_reply("c1", "Re:", "Yes!", "INCOMING", "2026-03-29T00:00:00+00:00", urgency="high")
        await add_opt_out("c2", "Spam")

        sid = await create_submission("c1", message="Pricing",
                                       mql_date="2026-03-28T10:00:00Z")
        eid = await create_engagement("c1", kind="sales")
        await add_submission(eid, sid)
        await score_engagement(eid, score=85)

        await self._export(output_path)
        wb = self._load(output_path)
        assert "Engagements" in wb.sheetnames
        assert "Submissions" in wb.sheetnames
        assert "Enriched MQLs" in wb.sheetnames
        assert "Email Drafts" in wb.sheetnames
        assert "Hot Leads" in wb.sheetnames
        assert "Metrics" in wb.sheetnames
        assert "Opt-Outs" in wb.sheetnames
        assert len(wb.sheetnames) == 7
