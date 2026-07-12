"""ChromaDB adapter — sync and async interfaces for RAG operations.

Sync methods are used by CLI tools and tests.
Async methods (aquery, aindex, aget_all_chunks) wrap sync calls in
asyncio.to_thread for use in the LangGraph graph.
"""
from __future__ import annotations

import asyncio
from typing import Any

from outreach_pipeline.types import RAGResult


class ChromaDBAdapter:
    """Unified interface to ChromaDB RAG operations."""

    def __init__(self, db_path: str = "rag/materials_db"):
        self.db_path = db_path

    # --- Sync methods ---

    def index(
        self,
        materials_dir: str,
        collection_name: str = "sales_materials",
        reset: bool = False,
    ) -> dict[str, Any]:
        """Index files from a directory into a ChromaDB collection."""
        from outreach_pipeline.index_materials import index_materials
        return index_materials(materials_dir, self.db_path, reset, collection_name)

    def query(
        self,
        query: str,
        top_k: int | None = None,
        collection_name: str = "sales_materials",
        doc_type: str = "",
        domain: str = "",
    ) -> list[RAGResult]:
        """Query a ChromaDB collection. Returns list of RAGResult."""
        from outreach_pipeline.query_rag import query_materials
        result = query_materials(
            query, top_k, self.db_path, collection_name,
            doc_type=doc_type, domain=domain,
        )
        if not result.get("success"):
            return []
        return [
            RAGResult(
                source=m["source"],
                text=m["text"],
                distance=m.get("distance"),
                collection=collection_name,
            )
            for m in result.get("results", [])
        ]

    def get_all_chunks(self, collection_name: str = "sales_materials") -> list[RAGResult]:
        """Retrieve all chunks from a collection (for content grading)."""
        from outreach_pipeline.adapters.chroma_client import (
            collection_path_exists, get_chroma_client,
        )

        if not collection_path_exists(self.db_path):
            return []
        try:
            client = get_chroma_client(self.db_path)
            collection = client.get_collection(name=collection_name)
        except Exception:
            return []

        count = collection.count()
        if count == 0:
            return []

        data = collection.get(include=["documents", "metadatas"])
        results = []
        for i in range(len(data["ids"])):
            meta = data["metadatas"][i] if data["metadatas"] else {}
            results.append(RAGResult(
                source=meta.get("source", "") if isinstance(meta, dict) else "",
                text=data["documents"][i] if data["documents"] else "",
                collection=collection_name,
            ))
        return results

    # --- Async methods (for LangGraph graph nodes) ---

    async def aindex(
        self,
        materials_dir: str,
        collection_name: str = "sales_materials",
        reset: bool = False,
    ) -> dict[str, Any]:
        """Async index with timeout."""
        from outreach_pipeline.utils.async_helpers import run_sync
        return await run_sync(self.index, materials_dir, collection_name, reset, timeout=60)

    async def aquery(
        self,
        query: str,
        top_k: int | None = None,
        collection_name: str = "sales_materials",
        doc_type: str = "",
        domain: str = "",
    ) -> list[RAGResult]:
        """Async query with timeout."""
        from outreach_pipeline.utils.async_helpers import run_sync
        return await run_sync(
            self.query, query, top_k, collection_name, doc_type, domain, timeout=10,
        )

    async def aget_all_chunks(self, collection_name: str = "sales_materials") -> list[RAGResult]:
        """Async get_all_chunks with timeout."""
        from outreach_pipeline.utils.async_helpers import run_sync
        return await run_sync(self.get_all_chunks, collection_name, timeout=30)

    def collection_count(self, collection_name: str = "sales_materials") -> int | None:
        """Get chunk count for a collection. Returns None if not found."""
        from outreach_pipeline.adapters.chroma_client import (
            collection_path_exists, get_chroma_client,
        )
        if not collection_path_exists(self.db_path):
            return None
        try:
            client = get_chroma_client(self.db_path)
            collection = client.get_collection(name=collection_name)
            return collection.count()
        except Exception:
            return None

    async def acollection_count(self, collection_name: str = "sales_materials") -> int | None:
        """Async collection count with timeout."""
        from outreach_pipeline.utils.async_helpers import run_sync
        return await run_sync(self.collection_count, collection_name, timeout=5)
