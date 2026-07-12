"""RAG tools — async (for graph) and sync (for CLI).

All graph tool wrappers call the async versions.
"""
from __future__ import annotations

from typing import Any

from outreach_pipeline.adapters.chromadb_adapter import ChromaDBAdapter
from outreach_pipeline.security.url_safety import UnsafeURL, assert_public_http_url

# SSRF guard implementation uses ipaddress plus socket.getaddrinfo and rejects
# is_private, is_loopback, and is_link_local addresses before scraping.


# --- Async (used by graph tools) ---

async def aquery_rag(
    query: str,
    collection: str = "sales_materials",
    top_k: int | None = None,
    db_path: str = "rag/materials_db",
    doc_type: str = "",
    domain: str = "",
) -> dict[str, Any]:
    """Query a RAG collection (async)."""
    adapter = ChromaDBAdapter(db_path=db_path)
    results = await adapter.aquery(query, top_k=top_k, collection_name=collection,
                                   doc_type=doc_type, domain=domain)
    return {
        "results": [r.model_dump() for r in results],
        "count": len(results),
        "collection": collection,
    }


async def aindex_content(
    materials_dir: str,
    collection: str = "sales_materials",
    reset: bool = False,
    db_path: str = "rag/materials_db",
) -> dict[str, Any]:
    """Index files into a RAG collection (async)."""
    adapter = ChromaDBAdapter(db_path=db_path)
    return await adapter.aindex(materials_dir, collection_name=collection, reset=reset)


async def ascrape_and_index(
    url: str,
    collection: str = "",
    prospect: bool = True,
    max_pages: int = 10,
    db_path: str = "rag/materials_db",
) -> dict[str, Any]:
    """Scrape a website URL and index the content (async)."""
    import asyncio
    from urllib.parse import urlparse
    from outreach_pipeline.adapters.web import WebAdapter

    from outreach_pipeline.utils.async_helpers import run_sync
    try:
        assert_public_http_url(url)
    except UnsafeURL as exc:
        return {"success": False, "error": f"Blocked URL: {exc}", "url": url}
    web = WebAdapter(max_pages=max_pages)
    result = await run_sync(web.scrape_raw, url, timeout=60)
    if not result or not result.get("success"):
        return {"success": False, "error": "Scraping failed", "url": url}

    if collection:
        col = collection
    elif prospect:
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        col = netloc.replace(".", "_").replace("-", "_")
    else:
        col = "sales_materials"

    adapter = ChromaDBAdapter(db_path=db_path)
    index_result = await adapter.aindex(result["output_dir"], collection_name=col, reset=True)
    return {"success": True, "url": url, "collection": col, **index_result}


# --- Sync (used by CLI) ---

def query_rag(
    query: str,
    collection: str = "sales_materials",
    top_k: int | None = None,
    db_path: str = "rag/materials_db",
    doc_type: str = "",
    domain: str = "",
) -> dict[str, Any]:
    """Query a RAG collection (sync)."""
    adapter = ChromaDBAdapter(db_path=db_path)
    results = adapter.query(query, top_k=top_k, collection_name=collection,
                            doc_type=doc_type, domain=domain)
    return {
        "results": [r.model_dump() for r in results],
        "count": len(results),
        "collection": collection,
    }


def index_content(
    materials_dir: str,
    collection: str = "sales_materials",
    reset: bool = False,
    db_path: str = "rag/materials_db",
) -> dict[str, Any]:
    """Index files into a RAG collection (sync)."""
    adapter = ChromaDBAdapter(db_path=db_path)
    return adapter.index(materials_dir, collection_name=collection, reset=reset)


def scrape_and_index(
    url: str,
    collection: str = "",
    prospect: bool = True,
    max_pages: int = 10,
    db_path: str = "rag/materials_db",
) -> dict[str, Any]:
    """Scrape a website URL and index the content (sync)."""
    from urllib.parse import urlparse
    from outreach_pipeline.adapters.web import WebAdapter

    try:
        assert_public_http_url(url)
    except UnsafeURL as exc:
        return {"success": False, "error": f"Blocked URL: {exc}", "url": url}
    web = WebAdapter(max_pages=max_pages)
    result = web.scrape_raw(url)
    if not result.get("success"):
        return {"success": False, "error": "Scraping failed", "url": url}

    if collection:
        col = collection
    elif prospect:
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        col = netloc.replace(".", "_").replace("-", "_")
    else:
        col = "sales_materials"

    adapter = ChromaDBAdapter(db_path=db_path)
    index_result = adapter.index(result["output_dir"], collection_name=col, reset=True)
    return {"success": True, "url": url, "collection": col, **index_result}
