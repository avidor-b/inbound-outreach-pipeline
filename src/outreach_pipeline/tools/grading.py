"""Content grading domain tools — grade collections, grade URLs.

Separate from messaging. Uses the same validation layer in audit mode.
"""
from __future__ import annotations

from typing import Any

from outreach_pipeline.services.grading import grade_collection as _grade_collection
from outreach_pipeline.types import GradingReport


def grade_collection(
    collection: str = "sales_materials",
    profile: str = "content_grading",
    db_path: str = "rag/materials_db",
) -> dict[str, Any]:
    """Grade all chunks in a RAG collection."""
    report = _grade_collection(collection, db_path=db_path, profile=profile)
    return report.model_dump()


def grade_url(
    url: str,
    profile: str = "content_grading",
    max_pages: int = 50,
) -> dict[str, Any]:
    """Scrape a URL, index content, and grade it."""
    from outreach_pipeline.adapters.web import WebAdapter
    from outreach_pipeline.adapters.chromadb_adapter import ChromaDBAdapter
    from urllib.parse import urlparse

    # Scrape
    web = WebAdapter(max_pages=max_pages)
    result = web.scrape_raw(url)
    if not result.get("success"):
        return {"success": False, "error": "Scraping failed"}

    # Index
    domain = urlparse(url).netloc.replace(".", "_").replace("-", "_")
    collection_name = f"grading_{domain}"
    rag = ChromaDBAdapter()
    rag.index(result["output_dir"], collection_name=collection_name, reset=True)

    # Grade
    report = _grade_collection(collection_name, profile=profile)
    return {**report.model_dump(), "collection": collection_name, "url": url}
