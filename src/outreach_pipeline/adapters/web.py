"""Web scraping adapter — wraps scrape_site.py.

Uses subprocess for scraping to avoid async event loop conflicts
when called from MCP server or other async contexts. Playwright
manages its own event loop and locks which conflict with nested
asyncio.run() calls.
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from outreach_pipeline.types import ContentPage


class WebAdapter:
    """Unified interface to web scraping operations."""

    def __init__(self, max_pages: int = 50):
        self.max_pages = max_pages

    def scrape(self, url: str, output_dir: str | None = None) -> list[ContentPage]:
        """Scrape a website and return pages as ContentPage objects."""
        result = self.scrape_raw(url, output_dir)
        if not result.get("success"):
            return []
        return [
            ContentPage(
                url=p["url"],
                title=p["title"],
                text="",
                source_file=p["file"],
            )
            for p in result.get("pages", [])
        ]

    def scrape_raw(self, url: str, output_dir: str | None = None) -> dict[str, Any]:
        """Scrape via subprocess to isolate Playwright's event loop."""
        script = f"""
import asyncio, json, logging, sys
logging.getLogger("crawlee").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
from outreach_pipeline.scrape_site import scrape_site
result = asyncio.run(scrape_site({url!r}, {self.max_pages!r}, {output_dir!r}))
# Output JSON-safe subset (pages list can be large)
safe = {{
    "success": result.get("success", False),
    "domain": result.get("domain", ""),
    "pages_crawled": result.get("pages_crawled", 0),
    "output_dir": result.get("output_dir", ""),
    "pages": result.get("pages", []),
}}
print(json.dumps(safe))
"""
        try:
            proc = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True, text=True, timeout=60,
            )
            if proc.returncode != 0:
                stderr = proc.stderr[:500] if proc.stderr else ""
                return {"success": False, "error": f"Scrape failed: {stderr}"}
            return json.loads(proc.stdout)
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Scrape timed out after 120s"}
        except (json.JSONDecodeError, Exception) as e:
            return {"success": False, "error": str(e)}
