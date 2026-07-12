"""Unit tests for export_pipeline.py"""
import os
import tempfile

import pytest
from openpyxl import load_workbook

from outreach_pipeline.db.contacts import upsert_contact, enrich_contact, score_contact
from outreach_pipeline.db.drafts import save_draft, mark_sent, log_send
from outreach_pipeline.db.replies import save_reply
from outreach_pipeline.export_pipeline import export_pipeline


@pytest.fixture
async def seeded_db(db):
    """Seed the pool with one fully-processed contact."""
    await upsert_contact("101", {"company": "Acme", "industry": "Tech", "message": "Need pricing",
                                  "hs_analytics_source": "ORGANIC_SEARCH"})
    await enrich_contact("101", "Makes industrial robots")
    await score_contact("101", "high", ["pricing"], 90, "Strong signals", "Enterprise tier")
    draft_id = await save_draft("101", "Following up", "Hi there", 90)
    await mark_sent(draft_id)
    await log_send("101", "Following up")
    await save_reply("101", "Re: Following up", "Yes lets talk", "INCOMING", "2026-02-20T00:00:00Z", urgency="high", sentiment="positive")
    return db


class TestExportPipeline:
    @pytest.mark.asyncio
    async def test_creates_excel_with_all_sheets(self, seeded_db):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = os.path.join(tmpdir, "out.xlsx")
            await export_pipeline(out)
            wb = load_workbook(out)
            assert "Enriched MQLs" in wb.sheetnames
            assert "Email Drafts" in wb.sheetnames
            assert "Hot Leads" in wb.sheetnames
            assert "Metrics" in wb.sheetnames

    @pytest.mark.asyncio
    async def test_leads_sheet_has_data(self, seeded_db):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = os.path.join(tmpdir, "out.xlsx")
            await export_pipeline(out)
            wb = load_workbook(out)
            ws = wb["Enriched MQLs"]
            assert ws.cell(row=1, column=1).value == "Contact ID"
            assert ws.cell(row=2, column=1).value == "101"
            assert ws.cell(row=2, column=2).value == "Acme"
            assert ws.cell(row=2, column=10).value == "high"  # Buying Intent column

    @pytest.mark.asyncio
    async def test_drafts_sheet_has_data(self, seeded_db):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = os.path.join(tmpdir, "out.xlsx")
            await export_pipeline(out)
            wb = load_workbook(out)
            ws = wb["Email Drafts"]
            assert ws.cell(row=2, column=1).value == "101"
            assert "Following up" in ws.cell(row=2, column=2).value

    @pytest.mark.asyncio
    async def test_metrics_sheet_has_data(self, seeded_db):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = os.path.join(tmpdir, "out.xlsx")
            await export_pipeline(out)
            wb = load_workbook(out)
            ws = wb["Metrics"]
            assert ws.cell(row=1, column=1).value == "Category"
            # Check reply rate exists
            values = [ws.cell(row=r, column=2).value for r in range(2, ws.max_row + 1)]
            assert "Reply rate" in values

    @pytest.mark.asyncio
    async def test_empty_db(self, db):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = os.path.join(tmpdir, "empty.xlsx")
            await export_pipeline(out)
            wb = load_workbook(out)
            assert len(wb.sheetnames) >= 1
