#!/usr/bin/env python3
"""Export pipeline data to Excel workbook.

Sheets:
1. Engagements — the unit of work (kind, status, score, product, contact)
2. Submissions — evidence history (message, form, date, classification)
3. Enriched MQLs — contact-level view (legacy, still useful)
4. Email Drafts
5. Hot Leads
6. Metrics — pipeline health (contacts, engagements, submissions, drafts, replies)
7. Opt-Outs
"""
import argparse
import asyncio
import json
import math
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from openpyxl.utils import get_column_letter


def parse_args(args=None):
    p = argparse.ArgumentParser(description="Export pipeline data to Excel")
    p.add_argument("--output", default="data/pipeline_output.xlsx", help="Output Excel path")
    return p.parse_args(args)


def _write_header(ws, headers):
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = Font(bold=True)
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"
    ws.freeze_panes = "A2"


def _pct(num, denom):
    return f"{num/denom*100:.1f}%" if denom > 0 else "n/a"


async def _write_engagements_sheet(wb, pool):
    rows = await pool.fetch("""
        SELECT e.*, c.properties, c.company_context,
               (SELECT COUNT(*) FROM engagement_submissions es WHERE es.engagement_id = e.id) as sub_count,
               (SELECT COUNT(*) FROM drafts d WHERE d.contact_id = e.contact_id) as draft_count
        FROM engagements e
        LEFT JOIN contacts c ON e.contact_id = c.id
        ORDER BY e.score DESC, e.opened_at DESC
    """)
    if not rows:
        return
    ws = wb.create_sheet("Engagements")
    headers = ["Eng ID", "Kind", "Status", "Score", "Product", "Priority",
               "Contact ID", "Company", "Industry", "Company Context",
               "Submissions", "Drafts", "Block Code", "Block Reason",
               "Score Reasoning", "Opened", "Closed", "Closed Reason"]
    _write_header(ws, headers)
    for i, r in enumerate(rows, 2):
        props = r["properties"] or {}
        if isinstance(props, str):
            props = json.loads(props)
        ws.cell(row=i, column=1, value=r["id"])
        ws.cell(row=i, column=2, value=r["kind"])
        ws.cell(row=i, column=3, value=r["status"])
        ws.cell(row=i, column=4, value=r["score"])
        ws.cell(row=i, column=5, value=r["primary_product"])
        ws.cell(row=i, column=6, value=r["priority"])
        ws.cell(row=i, column=7, value=r["contact_id"])
        ws.cell(row=i, column=8, value=props.get("company", ""))
        ws.cell(row=i, column=9, value=props.get("industry", ""))
        ws.cell(row=i, column=10, value=r["company_context"] or "")
        ws.cell(row=i, column=11, value=r["sub_count"])
        ws.cell(row=i, column=12, value=r["draft_count"])
        ws.cell(row=i, column=13, value=r["block_code"] or "")
        ws.cell(row=i, column=14, value=r["block_reason"] or "")
        ws.cell(row=i, column=15, value=r["score_reasoning"] or "")
        ws.cell(row=i, column=16, value=str(r["opened_at"] or ""))
        ws.cell(row=i, column=17, value=str(r["closed_at"] or ""))
        ws.cell(row=i, column=18, value=r["closed_reason"] or "")


async def _write_submissions_sheet(wb, pool):
    rows = await pool.fetch("""
        SELECT s.*, c.properties
        FROM submissions s
        LEFT JOIN contacts c ON s.contact_id = c.id
        WHERE s.source != 'migration'
        ORDER BY s.mql_date DESC NULLS LAST, s.id DESC
    """)
    if not rows:
        return
    ws = wb.create_sheet("Submissions")
    headers = ["Sub ID", "Contact ID", "Company", "MQL Date", "Form",
               "Page URL", "Message", "Intent Type", "Product Family",
               "Support", "Event", "Source", "Captured"]
    _write_header(ws, headers)
    for i, r in enumerate(rows, 2):
        props = r["properties"] or {}
        if isinstance(props, str):
            props = json.loads(props)
        ws.cell(row=i, column=1, value=r["id"])
        ws.cell(row=i, column=2, value=r["contact_id"])
        ws.cell(row=i, column=3, value=props.get("company", ""))
        ws.cell(row=i, column=4, value=str(r["mql_date"] or ""))
        ws.cell(row=i, column=5, value=r["form_name"] or "")
        ws.cell(row=i, column=6, value=r["page_url"] or "")
        ws.cell(row=i, column=7, value=r["message"] or "")
        ws.cell(row=i, column=8, value=r["intent_type"] or "")
        ws.cell(row=i, column=9, value=r["product_family"] or "")
        ws.cell(row=i, column=10, value="Yes" if r["support_flag"] else "")
        ws.cell(row=i, column=11, value="Yes" if r["event_flag"] else "")
        ws.cell(row=i, column=12, value=r["source"] or "")
        ws.cell(row=i, column=13, value=str(r["captured_at"] or ""))


def _write_leads_sheet(wb, contacts):
    ws = wb.create_sheet("Enriched MQLs")
    headers = ["Contact ID", "Company", "Industry", "Region", "Source",
               "Message", "First URL", "Page Views",
               "Company Context", "Buying Intent", "Score", "Product Match"]
    _write_header(ws, headers)
    for i, c in enumerate(sorted(contacts, key=lambda x: x.get("score", 0), reverse=True), 2):
        p = c.get("properties", {})
        if isinstance(p, str):
            p = json.loads(p)
        ws.cell(row=i, column=1, value=c.get("id", ""))
        ws.cell(row=i, column=2, value=p.get("company", ""))
        ws.cell(row=i, column=3, value=p.get("industry", ""))
        ws.cell(row=i, column=4, value=p.get("region", ""))
        ws.cell(row=i, column=5, value=p.get("hs_analytics_source", ""))
        ws.cell(row=i, column=6, value=p.get("message", ""))
        ws.cell(row=i, column=7, value=p.get("hs_analytics_first_url", ""))
        ws.cell(row=i, column=8, value=p.get("hs_analytics_num_page_views", ""))
        ws.cell(row=i, column=9, value=c.get("company_context", ""))
        ws.cell(row=i, column=10, value=c.get("buying_intent", ""))
        ws.cell(row=i, column=11, value=c.get("score", ""))
        ws.cell(row=i, column=12, value=c.get("product_match", ""))


def _write_drafts_sheet(wb, drafts):
    ws = wb.create_sheet("Email Drafts")
    headers = ["Contact ID", "Subject", "Score", "Status", "Revisions", "Created", "Sent", "Body"]
    _write_header(ws, headers)
    for i, d in enumerate(drafts, 2):
        ws.cell(row=i, column=1, value=d.get("contact_id", ""))
        ws.cell(row=i, column=2, value=d.get("subject", ""))
        ws.cell(row=i, column=3, value=d.get("score_at_send", ""))
        ws.cell(row=i, column=4, value=d.get("status", ""))
        ws.cell(row=i, column=5, value=d.get("revisions", 0))
        ws.cell(row=i, column=6, value=str(d.get("created_at", "")))
        ws.cell(row=i, column=7, value=str(d.get("sent_at", "")))
        ws.cell(row=i, column=8, value=d.get("body", ""))


def _write_hot_leads_sheet(wb, hot_leads):
    ws = wb.create_sheet("Hot Leads")
    headers = ["Contact ID", "Reply", "Urgency", "Sentiment", "Score", "Response Time (hrs)", "Reply Date"]
    _write_header(ws, headers)
    for i, l in enumerate(hot_leads, 2):
        ws.cell(row=i, column=1, value=l.get("contact_id", ""))
        ws.cell(row=i, column=2, value=l.get("body_preview", ""))
        ws.cell(row=i, column=3, value=l.get("urgency", ""))
        ws.cell(row=i, column=4, value=l.get("sentiment", ""))
        ws.cell(row=i, column=5, value=l.get("score", ""))
        ws.cell(row=i, column=6, value=l.get("response_time_hours", ""))
        ws.cell(row=i, column=7, value=str(l.get("timestamp", "")))


async def _write_metrics_sheet(wb, pool):
    total = (await pool.fetchrow("SELECT COUNT(*) as c FROM contacts"))["c"]
    if total == 0:
        return
    enriched = (await pool.fetchrow("SELECT COUNT(*) as c FROM contacts WHERE enriched_at IS NOT NULL"))["c"]
    scored = (await pool.fetchrow("SELECT COUNT(*) as c FROM contacts WHERE scored_at IS NOT NULL"))["c"]
    drafted = (await pool.fetchrow("SELECT COUNT(*) as c FROM drafts"))["c"]
    sent = (await pool.fetchrow("SELECT COUNT(*) as c FROM drafts WHERE status='sent'"))["c"]
    total_replies = (await pool.fetchrow("SELECT COUNT(*) as c FROM replies WHERE direction='INCOMING'"))["c"]
    hot = (await pool.fetchrow("SELECT COUNT(*) as c FROM replies WHERE urgency='high'"))["c"]
    opted_out = (await pool.fetchrow("SELECT COUNT(*) as c FROM do_not_contact"))["c"]
    eng_total = (await pool.fetchrow("SELECT COUNT(*) as c FROM engagements"))["c"]
    eng_sales = (await pool.fetchrow("SELECT COUNT(*) as c FROM engagements WHERE kind='sales'"))["c"]
    eng_support = (await pool.fetchrow("SELECT COUNT(*) as c FROM engagements WHERE kind='support'"))["c"]
    eng_events = (await pool.fetchrow("SELECT COUNT(*) as c FROM engagements WHERE kind='events'"))["c"]
    eng_retarget = (await pool.fetchrow("SELECT COUNT(*) as c FROM engagements WHERE kind='retarget'"))["c"]
    eng_followup = (await pool.fetchrow("SELECT COUNT(*) as c FROM engagements WHERE kind='content_followup'"))["c"]
    eng_open = (await pool.fetchrow("SELECT COUNT(*) as c FROM engagements WHERE status='open'"))["c"]
    eng_blocked = (await pool.fetchrow("SELECT COUNT(*) as c FROM engagements WHERE status='blocked'"))["c"]
    sub_total = (await pool.fetchrow("SELECT COUNT(*) as c FROM submissions WHERE source != 'migration'"))["c"]
    sub_sales = (await pool.fetchrow("SELECT COUNT(*) as c FROM submissions WHERE intent_type='sales_inquiry'"))["c"]
    sub_content = (await pool.fetchrow("SELECT COUNT(*) as c FROM submissions WHERE intent_type='content_download'"))["c"]
    high = (await pool.fetchrow("SELECT COUNT(*) as c FROM engagements WHERE score >= 80"))["c"]
    med = (await pool.fetchrow("SELECT COUNT(*) as c FROM engagements WHERE score >= 40 AND score < 80"))["c"]
    low_e = (await pool.fetchrow("SELECT COUNT(*) as c FROM engagements WHERE score >= 1 AND score < 40"))["c"]
    unscored = (await pool.fetchrow("SELECT COUNT(*) as c FROM engagements WHERE score = 0"))["c"]

    ws = wb.create_sheet("Metrics")
    headers = ["Category", "Metric", "Value", "What It Tells You"]
    _write_header(ws, headers)
    rows = [
        ("Contacts", "Total contacts", total, "MQLs pulled from HubSpot"),
        ("Contacts", "Enriched", f"{enriched}/{total} ({_pct(enriched, total)})", "% with company context"),
        ("", "", "", ""),
        ("Submissions", "Total submissions", sub_total, "Form submissions (excl. migration)"),
        ("Submissions", "Sales inquiries", sub_sales, "Messages with buying intent"),
        ("Submissions", "Content downloads", sub_content, "Gated content access"),
        ("", "", "", ""),
        ("Engagements", "Total engagements", eng_total, "Actionable work items"),
        ("Engagements", "Sales engagements", eng_sales, "Product inquiries"),
        ("Engagements", "Content follow-ups", eng_followup, "Download ��� inquiry progression"),
        ("Engagements", "Support (blocked)", eng_support, "Routed to support"),
        ("Engagements", "Events (blocked)", eng_events, "Routed to events"),
        ("Engagements", "Retargets", eng_retarget, "Re-engagement of non-replied"),
        ("Engagements", "Open", eng_open, "Active"),
        ("Engagements", "Blocked", eng_blocked, "Blocked by policy"),
        ("", "", "", ""),
        ("Engagement Scores", "High (80-100)", high, "Strong buying intent"),
        ("Engagement Scores", "Medium (40-79)", med, "Worth reaching out"),
        ("Engagement Scores", "Low (1-39)", low_e, "Weak signals"),
        ("Engagement Scores", "Unscored", unscored, "Not yet evaluated"),
        ("", "", "", ""),
        ("Outreach", "Emails drafted", drafted, "Drafts created"),
        ("Outreach", "Emails sent", sent, "Delivered"),
        ("Outreach", "Reply rate", _pct(total_replies, sent), "% sent that got a response"),
        ("Outreach", "Hot leads", hot, "High-urgency replies"),
        ("Outreach", "Opt-outs", opted_out, "Do-not-contact list"),
    ]
    for i, (cat, metric, value, desc) in enumerate(rows, 2):
        ws.cell(row=i, column=1, value=cat)
        ws.cell(row=i, column=2, value=metric)
        ws.cell(row=i, column=3, value=str(value))
        ws.cell(row=i, column=4, value=desc)


async def export_pipeline(output_path="data/pipeline_output.xlsx"):
    from outreach_pipeline.db.pool import get_pool
    from outreach_pipeline.db.contacts import get_contacts
    from outreach_pipeline.db.drafts import get_drafts
    from outreach_pipeline.db.replies import get_hot_leads

    pool = get_pool()
    wb = Workbook()
    wb.remove(wb.active)

    contacts = await get_contacts(limit=10000)
    drafts = await get_drafts(limit=100)
    hot_leads = await get_hot_leads()

    await _write_engagements_sheet(wb, pool)
    await _write_submissions_sheet(wb, pool)
    if contacts:
        _write_leads_sheet(wb, contacts)
    if drafts:
        _write_drafts_sheet(wb, drafts)
    if hot_leads:
        _write_hot_leads_sheet(wb, hot_leads)
    await _write_metrics_sheet(wb, pool)

    dnc = await pool.fetch("SELECT * FROM do_not_contact ORDER BY added_at DESC NULLS LAST")
    if dnc:
        ws = wb.create_sheet("Opt-Outs")
        _write_header(ws, ["Contact ID", "Reason", "Added"])
        for i, r in enumerate(dnc, 2):
            ws.cell(row=i, column=1, value=r["contact_id"])
            ws.cell(row=i, column=2, value=r["reason"])
            ws.cell(row=i, column=3, value=str(r["added_at"] or ""))

    if not wb.sheetnames:
        wb.create_sheet("Pipeline Output")
        wb["Pipeline Output"].cell(row=1, column=1, value="No pipeline data found")

    wrap = Alignment(wrap_text=True, vertical="top")
    for ws in wb:
        col_widths = {}
        for col_idx in range(1, ws.max_column + 1):
            col_letter = get_column_letter(col_idx)
            max_len = 0
            for row in range(1, ws.max_row + 1):
                val = str(ws.cell(row=row, column=col_idx).value or "")
                max_len = max(max_len, min(max(len(l) for l in val.split("\n")), 60))
            ws.column_dimensions[col_letter].width = max(max_len + 2, 12)
            col_widths[col_idx] = max(max_len + 2, 12)
        for row in range(2, ws.max_row + 1):
            max_lines = 1
            for col_idx in range(1, ws.max_column + 1):
                cell = ws.cell(row=row, column=col_idx)
                cell.alignment = wrap
                val = str(cell.value or "")
                col_w = col_widths.get(col_idx, 12)
                lines = sum(max(1, math.ceil(len(para) / col_w)) for para in val.split("\n"))
                max_lines = max(max_lines, lines)
            ws.row_dimensions[row].height = max_lines * 15 + 4

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(out))
    return str(out)


def main():
    args = parse_args()

    async def _run():
        from outreach_pipeline.db.pool import init_pool, close_pool
        await init_pool()
        try:
            path = await export_pipeline(args.output)
            print(f"Pipeline exported to {path}")
        finally:
            await close_pool()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
