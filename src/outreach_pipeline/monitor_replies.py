#!/usr/bin/env python3
"""Fetch email replies for pipeline contacts from HubSpot (read-only)."""
import argparse
import json
import sys
from datetime import datetime

from outreach_pipeline.hubspot_client import HubSpotClient
from outreach_pipeline.config import load_config


def parse_args(args=None):
    p = argparse.ArgumentParser(description="Monitor replies for pipeline contacts")
    p.add_argument("--since", required=True, help="Only include replies after this date (YYYY-MM-DD)")
    return p.parse_args(args)


def filter_replies(emails, since_date):
    """Filter for incoming emails after since_date."""
    cutoff = datetime.strptime(since_date, "%Y-%m-%d")
    replies = []
    for e in emails:
        if e.get("direction") != "INCOMING":
            continue
        ts = e.get("timestamp", "")
        if not ts:
            continue
        try:
            email_dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
            if email_dt >= cutoff:
                replies.append(e)
        except (ValueError, TypeError):
            continue
    return replies


def fetch_replies(since_date):
    """Fetch replies for contacts that have been emailed."""
    config = load_config()
    client = HubSpotClient(config["hubspot_token"], config)

    from outreach_pipeline.db import get_db, get_drafts, save_reply
    conn = get_db()
    drafts = get_drafts(conn, status="sent")

    results = []
    for draft in drafts:
        cid = draft["contact_id"]
        try:
            emails = client.get_contact_emails(cid)
        except Exception as exc:
            print(f"WARNING: Failed to fetch emails for {cid}: {exc}", file=sys.stderr)
            continue
        replies = filter_replies(emails, since_date)
        for reply in replies:
            save_reply(conn, cid, reply.get("subject", ""), reply.get("body_preview", ""),
                       reply.get("direction", ""), reply.get("timestamp", ""))
            results.append({"contact_id": cid, "reply": reply})

    conn.close()
    return {"replies": results, "total": len(results), "since": since_date}


def main():
    args = parse_args()
    result = fetch_replies(args.since)
    print(json.dumps({"total_replies": result["total"], "since": result["since"]}, indent=2))


if __name__ == "__main__":
    main()
