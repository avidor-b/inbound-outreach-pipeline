#!/usr/bin/env python3
"""Pipeline guardrails CLI — thin wrapper around db.py functions.

Usage:
  guardrails check-duplicate --contact-id 123
  guardrails check-send-limit
  guardrails check-opt-out --contact-id 123
  guardrails check-scrape --domain customer.example.com
  guardrails add-opt-out --contact-id 123 --reason "Replied not interested"
  guardrails add-scrape-block --domain competitor.example.com --reason "Competitor"
  guardrails log-send --contact-id 123 --subject "..."
"""
import argparse
import json
import sys

from outreach_pipeline.db import (
    get_db, check_opt_out, check_duplicate, check_send_limit,
    check_scrape_blocklist, check_events_contact, check_support_request,
    add_opt_out, add_scrape_block, log_send,
)

DEFAULT_DAILY_SEND_LIMIT = 20
DEFAULT_COOLDOWN_DAYS = 30


def parse_args(args=None):
    p = argparse.ArgumentParser(description="Pipeline guardrails")
    sub = p.add_subparsers(dest="command", required=True)

    dup = sub.add_parser("check-duplicate")
    dup.add_argument("--contact-id", required=True)
    dup.add_argument("--cooldown-days", type=int, default=DEFAULT_COOLDOWN_DAYS)

    sl = sub.add_parser("check-send-limit")
    sl.add_argument("--daily-limit", type=int, default=DEFAULT_DAILY_SEND_LIMIT)

    oo = sub.add_parser("check-opt-out")
    oo.add_argument("--contact-id", required=True)

    sc = sub.add_parser("check-scrape")
    sc.add_argument("--domain", required=True)

    ao = sub.add_parser("add-opt-out")
    ao.add_argument("--contact-id", required=True)
    ao.add_argument("--reason", default="")

    ab = sub.add_parser("add-scrape-block")
    ab.add_argument("--domain", required=True)
    ab.add_argument("--reason", default="")

    ls = sub.add_parser("log-send")
    ls.add_argument("--contact-id", required=True)
    ls.add_argument("--subject", default="")

    ev = sub.add_parser("check-events")
    ev.add_argument("--contact-id", required=True)

    sp = sub.add_parser("check-support")
    sp.add_argument("--contact-id", required=True)

    return p.parse_args(args)


def main():
    args = parse_args()
    conn = get_db()
    result = {}

    if args.command == "check-duplicate":
        blocked, reason = check_duplicate(conn, args.contact_id, args.cooldown_days)
        result = {"blocked": blocked, "reason": reason}
    elif args.command == "check-send-limit":
        blocked, reason, count = check_send_limit(conn, args.daily_limit)
        result = {"blocked": blocked, "reason": reason, "today_count": count}
    elif args.command == "check-opt-out":
        blocked, reason = check_opt_out(conn, args.contact_id)
        result = {"blocked": blocked, "reason": reason}
    elif args.command == "check-scrape":
        blocked, reason = check_scrape_blocklist(conn, args.domain)
        result = {"blocked": blocked, "reason": reason}
    elif args.command == "add-opt-out":
        ok = add_opt_out(conn, args.contact_id, args.reason)
        result = {"success": ok, "message": "Added" if ok else "Already on list"}
    elif args.command == "add-scrape-block":
        ok = add_scrape_block(conn, args.domain, args.reason)
        result = {"success": ok, "message": "Blocked" if ok else "Already blocked"}
    elif args.command == "log-send":
        log_send(conn, args.contact_id, args.subject)
        result = {"success": True}
    elif args.command == "check-events":
        blocked, reason = check_events_contact(conn, args.contact_id)
        result = {"blocked": blocked, "reason": reason}
    elif args.command == "check-support":
        blocked, reason = check_support_request(conn, args.contact_id)
        result = {"blocked": blocked, "reason": reason}

    conn.close()
    print(json.dumps(result, indent=2))
    if result.get("blocked"):
        sys.exit(1)


if __name__ == "__main__":
    main()
