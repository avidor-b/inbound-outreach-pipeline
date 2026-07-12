"""Web search tools — sync (ddgs) and async (httpx + DuckDuckGo lite).

The async version is used by the graph. The sync version is kept for CLI.
"""
from __future__ import annotations

import re
from typing import Any

import httpx


async def aweb_search(
    query: str,
    max_results: int = 5,
) -> dict[str, Any]:
    """Async web search via DuckDuckGo lite HTML endpoint.

    No API key needed. Returns titles, URLs, and snippets.
    """
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(
                "https://lite.duckduckgo.com/lite/",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            html = resp.text

        # Parse results from DuckDuckGo lite HTML
        results = _parse_ddg_lite(html, max_results)
        return {
            "success": True,
            "query": query,
            "results": results,
            "count": len(results),
        }
    except Exception as e:
        return {"success": False, "query": query, "error": str(e), "results": []}


def _parse_ddg_lite(html: str, max_results: int) -> list[dict[str, str]]:
    """Parse DuckDuckGo lite HTML into structured results."""
    results = []

    # DDG lite uses single-quoted class attributes
    link_pattern = re.compile(r'href="([^"]+)"[^>]*class=[\'"]result-link[\'"][^>]*>([^<]+)</a>')
    snippet_pattern = re.compile(r'class=[\'"]result-snippet[\'"][^>]*>(.*?)</td>', re.DOTALL)

    links = link_pattern.findall(html)
    snippets = snippet_pattern.findall(html)

    from urllib.parse import unquote, parse_qs, urlparse

    for i, (raw_url, title) in enumerate(links):
        if i >= max_results:
            break
        # Extract actual URL from DDG redirect
        url = raw_url.strip()
        parsed = urlparse(url)
        if "uddg" in parse_qs(parsed.query):
            url = unquote(parse_qs(parsed.query)["uddg"][0])

        snippet = ""
        if i < len(snippets):
            snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip()
        results.append({
            "title": title.strip(),
            "url": url,
            "snippet": snippet,
        })

    return results


# --- Sync version (for CLI, uses ddgs library) ---

def web_search(
    query: str,
    max_results: int = 5,
) -> dict[str, Any]:
    """Search the web for information (sync). Returns titles, URLs, and snippets."""
    try:
        from ddgs import DDGS
        results = DDGS().text(query, max_results=max_results)
        return {
            "success": True,
            "query": query,
            "results": [
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                }
                for r in results
            ],
            "count": len(results),
        }
    except Exception as e:
        return {"success": False, "query": query, "error": str(e), "results": []}
