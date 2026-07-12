#!/usr/bin/env python3
"""Save an approved email draft as a markdown file with YAML frontmatter."""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_args(args=None):
    p = argparse.ArgumentParser(description="Save email draft as markdown")
    p.add_argument("--contact-id", required=True, help="HubSpot contact ID")
    p.add_argument("--subject", required=True, help="Email subject line")
    p.add_argument("--body", required=True, help="Email body text")
    p.add_argument("--score", type=int, default=0, help="Lead score (1-100)")
    p.add_argument("--rag-context", default="", help="RAG snippets used for this draft")
    p.add_argument("--output-dir", default="data/drafts", help="Directory for draft files")
    return p.parse_args(args)


def save_draft(contact_id, subject, body, score=0, rag_context="", output_dir="data/drafts"):
    """Write draft markdown file and best-effort save to Postgres."""
    try:
        import asyncio

        async def _save():
            from outreach_pipeline.db.pool import init_pool
            from outreach_pipeline.db.drafts import save_draft as db_save_draft

            await init_pool()
            await db_save_draft(contact_id, subject, body, score, rag_context, status="approved")

        asyncio.run(_save())
    except Exception:
        pass  # DB save is best-effort, file is the primary

    # Save to file
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{contact_id}.md"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    content = f"""---
contact_id: "{contact_id}"
subject: "{subject}"
score: {score}
date: "{now}"
status: approved
---

{body}
"""
    path.write_text(content, encoding="utf-8")
    return str(path)


def main():
    args = parse_args()
    path = save_draft(args.contact_id, args.subject, args.body, args.score, args.rag_context, args.output_dir)
    print(f"Draft saved: {path}")


if __name__ == "__main__":
    main()
