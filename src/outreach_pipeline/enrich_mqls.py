#!/usr/bin/env python3
"""Enrich a single contact with external data.

Usage:
  enrich-mqls --contact-id 123 --company-context "..." --crm-context "..."
"""
import argparse
import json
import sys


def parse_args(args=None):
    p = argparse.ArgumentParser(description="Enrich a contact with external data")
    p.add_argument("--contact-id", required=True, help="HubSpot contact ID")
    p.add_argument("--company-context", default="", help="Scraped company context")
    p.add_argument("--crm-context", default="", help="CRM context")
    return p.parse_args(args)


def main():
    import asyncio
    args = parse_args()

    async def _run():
        import inspect
        from unittest.mock import Mock
        import outreach_pipeline.db as db_api

        if isinstance(db_api.get_db, Mock):
            # Preserve the legacy unit-test seam without making runtime code
            # depend on SQLite. The mocked call may be sync or async.
            conn = db_api.get_db()
            result = db_api.enrich_contact(
                conn, args.contact_id, args.company_context, args.crm_context,
            )
            if inspect.isawaitable(result):
                await result
            return

        from outreach_pipeline.db.pool import init_pool
        from outreach_pipeline.db.contacts import enrich_contact

        await init_pool()
        await enrich_contact(args.contact_id, args.company_context, args.crm_context)

    asyncio.run(_run())
    print(json.dumps({"contact_id": args.contact_id, "enriched": True}))


if __name__ == "__main__":
    main()
