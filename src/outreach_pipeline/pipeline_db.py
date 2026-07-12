#!/usr/bin/env python3
"""Human-friendly CLI to inspect pipeline data.

Usage:
  pipeline-db leads                    Show all leads with scores
  pipeline-db leads --min-score 50     Show leads scoring 50+
  pipeline-db lead 123                 Show one lead's full details
  pipeline-db drafts                   Show all email drafts
  pipeline-db replies                  Show replies
  pipeline-db hot-leads                Show hot leads
  pipeline-db opt-outs                 Show do-not-contact list
  pipeline-db blocklist                Show scrape blocklist
  pipeline-db stats                    Pipeline summary
  pipeline-db runs                     Pipeline run history
"""
import argparse
import json
import sys

from outreach_pipeline.db import get_db, get_contact, get_contacts, get_drafts, get_hot_leads, count_contacts


def parse_args(args=None):
    p = argparse.ArgumentParser(description="Inspect pipeline data", formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command")

    ls = sub.add_parser("leads", help="Show leads with scores")
    ls.add_argument("--min-score", type=int, default=0)
    ls.add_argument("--limit", type=int, default=50)
    ls.add_argument("--today", action="store_true", help="Only contacts fetched today")
    ls.add_argument("--unenriched", action="store_true", help="Only contacts not yet enriched")
    ls.add_argument("--unscored", action="store_true", help="Only contacts not yet scored")

    ld = sub.add_parser("lead", help="Show one lead's details")
    ld.add_argument("contact_id")

    sub.add_parser("drafts", help="Show email drafts")
    sub.add_parser("replies", help="Show replies")
    sub.add_parser("hot-leads", help="Show hot leads")
    sub.add_parser("opt-outs", help="Show do-not-contact list")
    sub.add_parser("blocklist", help="Show scrape blocklist")
    sub.add_parser("stats", help="Pipeline summary")
    sub.add_parser("runs", help="Pipeline run history")
    sub.add_parser("metrics", help="Success metrics and performance")

    return p.parse_args(args)


def _trunc(s, n=60):
    s = str(s or "")
    return s[:n] + "..." if len(s) > n else s


def cmd_leads(conn, min_score=0, limit=50, today=False, unenriched=False, unscored=False):
    contacts = get_contacts(conn, min_score=min_score, limit=limit, today_only=today, unenriched=unenriched, unscored=unscored)
    if not contacts:
        print("No leads found.")
        return
    print(f"{'ID':<10} {'Score':>5} {'Intent':<8} {'Company':<25} {'Message':<40}")
    print("-" * 92)
    for c in contacts:
        p = c.get("properties", {})
        print(f"{c['id']:<10} {c['score']:>5} {c.get('buying_intent',''):<8} "
              f"{_trunc(p.get('company',''), 24):<25} {_trunc(p.get('message',''), 39):<40}")
    print(f"\n{len(contacts)} leads shown.")


def cmd_lead(conn, contact_id):
    c = get_contact(conn, contact_id)
    if not c:
        print(f"Contact {contact_id} not found.")
        return
    p = c.get("properties", {})

    fields = [
        ("Company", p.get("company")),
        ("Industry", p.get("industry")),
        ("Job Title", p.get("jobtitle")),
        ("Region", p.get("region")),
        ("Source", p.get("hs_analytics_source")),
        ("Message", p.get("message")),
        ("Pages", f"{p.get('hs_analytics_num_page_views', '')} views" if p.get("hs_analytics_num_page_views") else None),
        ("First URL", p.get("hs_analytics_first_url")),
        ("Last URL", p.get("hs_analytics_last_url")),
    ]

    print(f"Contact: {c['id']}")
    for label, val in fields:
        if val:
            print(f"  {label:<14} {val}")

    enrichment = [
        ("Company Context", c.get("company_context")),
        ("CRM Context", c.get("crm_context")),
    ]
    has_enrichment = any(v for _, v in enrichment)
    if has_enrichment:
        print()
        for label, val in enrichment:
            if val:
                print(f"  {label:<14} {val}")

    if c.get("scored_at"):
        signals = c.get("intent_signals", [])
        if isinstance(signals, list):
            signals = ", ".join(signals)
        print()
        print(f"  Score          {c['score']}")
        print(f"  Intent         {c.get('buying_intent', '')}")
        if signals:
            print(f"  Signals        {signals}")
        if c.get("score_reasoning"):
            print(f"  Reasoning      {c['score_reasoning']}")
        if c.get("product_match"):
            print(f"  Product Match  {c['product_match']}")

    # Show draft if exists
    draft = conn.execute("SELECT * FROM drafts WHERE contact_id=?", (contact_id,)).fetchone()
    if draft:
        print(f"\n  Draft: [{draft['status']}] {draft['subject']}")


def cmd_drafts(conn):
    drafts = get_drafts(conn)
    if not drafts:
        print("No drafts found.")
        return
    print(f"{'Contact':<10} {'Score':>5} {'Status':<8} {'Subject':<50}")
    print("-" * 77)
    for d in drafts:
        print(f"{d['contact_id']:<10} {d.get('score_at_send', 0):>5} {d['status']:<8} {_trunc(d['subject'], 49):<50}")
    print(f"\n{len(drafts)} drafts.")


def cmd_replies(conn):
    rows = conn.execute("SELECT * FROM replies ORDER BY timestamp DESC LIMIT 50").fetchall()
    if not rows:
        print("No replies found.")
        return
    for r in rows:
        print(f"[{r['urgency'] or '?'}] Contact {r['contact_id']}: {_trunc(r['body_preview'], 70)}")
    print(f"\n{len(rows)} replies.")


def cmd_hot_leads(conn):
    leads = get_hot_leads(conn)
    if not leads:
        print("No hot leads.")
        return
    for l in leads:
        print(f"🔥 Contact {l['contact_id']} (score {l['score']}): {_trunc(l['body_preview'], 60)}")
    print(f"\n{len(leads)} hot leads.")


def cmd_opt_outs(conn):
    rows = conn.execute("SELECT * FROM do_not_contact ORDER BY added_at DESC").fetchall()
    if not rows:
        print("No opt-outs.")
        return
    for r in rows:
        print(f"  {r['contact_id']}: {r['reason']} (added {r['added_at'][:10]})")
    print(f"\n{len(rows)} contacts on do-not-contact list.")


def cmd_blocklist(conn):
    rows = conn.execute("SELECT * FROM scrape_blocklist ORDER BY added_at DESC").fetchall()
    if not rows:
        print("No blocked domains.")
        return
    for r in rows:
        print(f"  {r['domain']}: {r['reason']}")
    print(f"\n{len(rows)} blocked domains.")


def cmd_stats(conn):
    counts = count_contacts(conn)
    draft_count = conn.execute("SELECT COUNT(*) as c FROM drafts").fetchone()["c"]
    sent_count = conn.execute("SELECT COUNT(*) as c FROM drafts WHERE status='sent'").fetchone()["c"]
    reply_count = conn.execute("SELECT COUNT(*) as c FROM replies").fetchone()["c"]
    hot_count = conn.execute("SELECT COUNT(*) as c FROM replies WHERE urgency='high'").fetchone()["c"]
    dnc_count = conn.execute("SELECT COUNT(*) as c FROM do_not_contact").fetchone()["c"]

    print("Pipeline Summary")
    print("=" * 30)
    print(f"  Contacts fetched:  {counts['total']}")
    print(f"  Contacts enriched: {counts['enriched']}")
    print(f"  Contacts scored:   {counts['scored']}")
    print(f"  Emails drafted:    {draft_count}")
    print(f"  Emails sent:       {sent_count}")
    print(f"  Replies received:  {reply_count}")
    print(f"  Hot leads:         {hot_count}")
    print(f"  Do-not-contact:    {dnc_count}")


def cmd_runs(conn):
    rows = conn.execute("SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT 10").fetchall()
    if not rows:
        print("No pipeline runs recorded.")
        return
    for r in rows:
        print(f"  Run #{r['id']}: {r['start_date']} to {r['end_date']} | "
              f"{r['contacts_fetched']} fetched, {r['emails_drafted']} drafted, {r['hot_leads']} hot | "
              f"{r['started_at'][:16]}")


def _pct(num, denom):
    return f"{num/denom*100:.1f}%" if denom > 0 else "n/a"


def cmd_metrics(conn):
    total = conn.execute("SELECT COUNT(*) as c FROM contacts").fetchone()["c"]
    enriched = conn.execute("SELECT COUNT(*) as c FROM contacts WHERE enriched_at IS NOT NULL").fetchone()["c"]
    scored = conn.execute("SELECT COUNT(*) as c FROM contacts WHERE scored_at IS NOT NULL").fetchone()["c"]
    drafted = conn.execute("SELECT COUNT(*) as c FROM drafts").fetchone()["c"]
    sent = conn.execute("SELECT COUNT(*) as c FROM drafts WHERE status='sent'").fetchone()["c"]
    total_replies = conn.execute("SELECT COUNT(*) as c FROM replies WHERE direction='INCOMING'").fetchone()["c"]
    positive = conn.execute("SELECT COUNT(*) as c FROM replies WHERE sentiment='positive'").fetchone()["c"]
    hot = conn.execute("SELECT COUNT(*) as c FROM replies WHERE urgency='high'").fetchone()["c"]
    opted_out = conn.execute("SELECT COUNT(*) as c FROM do_not_contact").fetchone()["c"]
    blocked_tone = conn.execute("SELECT COUNT(*) as c FROM replies WHERE sentiment='negative'").fetchone()["c"]

    # Score accuracy: avg score of contacts who got replies vs. those who didn't
    replied_ids = conn.execute("SELECT DISTINCT contact_id FROM replies WHERE direction='INCOMING'").fetchall()
    replied_set = {r["contact_id"] for r in replied_ids}
    scored_contacts = conn.execute("SELECT id, score FROM contacts WHERE scored_at IS NOT NULL").fetchall()
    replied_scores = [r["score"] for r in scored_contacts if r["id"] in replied_set]
    no_reply_scores = [r["score"] for r in scored_contacts if r["id"] not in replied_set and r["id"] in
                       {d["contact_id"] for d in conn.execute("SELECT contact_id FROM drafts").fetchall()}]

    avg_replied = f"{sum(replied_scores)/len(replied_scores):.0f}" if replied_scores else "n/a"
    avg_no_reply = f"{sum(no_reply_scores)/len(no_reply_scores):.0f}" if no_reply_scores else "n/a"

    # Send limit utilization
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_sends = conn.execute("SELECT COUNT(*) as c FROM send_log WHERE date=?", (today,)).fetchone()["c"]

    print("=" * 50)
    print("  OUTREACH PIPELINE METRICS")
    print("=" * 50)

    print("\n📊 Outreach Effectiveness")
    print(f"  Emails sent:          {sent}")
    print(f"  Replies received:     {total_replies}  ({_pct(total_replies, sent)} reply rate)")
    print(f"  Positive replies:     {positive}  ({_pct(positive, sent)} positive rate)")
    print(f"  Hot leads:            {hot}  ({_pct(hot, sent)} hot lead rate)")

    print("\n🎯 Pipeline Quality")
    print(f"  Contacts fetched:     {total}")
    print(f"  Enrichment coverage:  {enriched}/{total}  ({_pct(enriched, total)})")
    print(f"  Scored:               {scored}/{total}  ({_pct(scored, total)})")
    print(f"  Drafted:              {drafted}/{scored}  ({_pct(drafted, scored)} of scored)")
    print(f"  Score accuracy:")
    print(f"    Avg score (replied):    {avg_replied}")
    print(f"    Avg score (no reply):   {avg_no_reply}")

    print("\n⚙️  Operational")
    print(f"  Sends today:          {today_sends}/20")
    print(f"  Opt-outs:             {opted_out}")

    # Score distribution
    if scored > 0:
        high = conn.execute("SELECT COUNT(*) as c FROM contacts WHERE score >= 80").fetchone()["c"]
        med = conn.execute("SELECT COUNT(*) as c FROM contacts WHERE score >= 40 AND score < 80").fetchone()["c"]
        low = conn.execute("SELECT COUNT(*) as c FROM contacts WHERE score >= 1 AND score < 40").fetchone()["c"]
        print(f"\n📈 Score Distribution")
        print(f"  High (80-100):  {high}  ({_pct(high, scored)})")
        print(f"  Medium (40-79): {med}  ({_pct(med, scored)})")
        print(f"  Low (1-39):     {low}  ({_pct(low, scored)})")


def main():
    args = parse_args()
    if not args.command:
        parse_args(["--help"])
        return

    conn = get_db()

    if args.command == "leads":
        cmd_leads(conn, args.min_score, args.limit, args.today, args.unenriched, args.unscored)
    elif args.command == "lead":
        cmd_lead(conn, args.contact_id)
    elif args.command == "drafts":
        cmd_drafts(conn)
    elif args.command == "replies":
        cmd_replies(conn)
    elif args.command == "hot-leads":
        cmd_hot_leads(conn)
    elif args.command == "opt-outs":
        cmd_opt_outs(conn)
    elif args.command == "blocklist":
        cmd_blocklist(conn)
    elif args.command == "stats":
        cmd_stats(conn)
    elif args.command == "runs":
        cmd_runs(conn)
    elif args.command == "metrics":
        cmd_metrics(conn)

    conn.close()


if __name__ == "__main__":
    main()
