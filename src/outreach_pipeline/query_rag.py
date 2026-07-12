#!/usr/bin/env python3
"""Query the sales/marketing materials RAG.

Supports metadata filtering (document_type, domain) and configurable top_k.

Usage:
  python query_rag.py --query "wireless backhaul"
  python query_rag.py --query "RADIO-A" --top-k 5
  python query_rag.py --query "case study" --doc-type case_study
"""
import argparse
import json
import sys
from pathlib import Path

from outreach_pipeline.adapters.chroma_client import (
    collection_path_exists, get_chroma_client, is_http_mode,
)

DB_PATH = "rag/materials_db"
COLLECTION_NAME = "sales_materials"


def _get_default_top_k() -> int:
    try:
        from outreach_pipeline.config import load_config
        return load_config().get("rag_top_k", 8)
    except Exception:
        return 8


def parse_args(args=None):
    p = argparse.ArgumentParser(description="Query sales materials RAG")
    p.add_argument("--query", required=True, help="Search query")
    p.add_argument("--top-k", type=int, default=None, help="Number of results (default from config)")
    p.add_argument("--db-path", default=DB_PATH, help="ChromaDB storage path")
    p.add_argument("--collection", default=COLLECTION_NAME, help="Collection name")
    p.add_argument("--doc-type", default="", help="Filter by document_type metadata")
    p.add_argument("--domain", default="", help="Filter by domain metadata")
    return p.parse_args(args)


def query_materials(
    query,
    top_k=None,
    db_path=DB_PATH,
    collection_name=COLLECTION_NAME,
    doc_type="",
    domain="",
):
    """Query the materials vector store.

    Returns list of {source, text, distance, document_type, domain}.
    Supports metadata filtering via doc_type and domain params.
    """
    if top_k is None:
        top_k = _get_default_top_k()

    if not collection_path_exists(db_path):
        return {"success": False, "error": f"No index found at {db_path}. Run index_materials.py first."}

    try:
        client = get_chroma_client(db_path)
        collection = client.get_collection(name=collection_name)
    except Exception as exc:
        # In HTTP mode, connect failures surface here. Surface the
        # error detail so ops can tell "chroma container down" from
        # "collection doesn't exist".
        return {
            "success": False,
            "error": (
                f"Collection '{collection_name}' not found."
                + (f" ({type(exc).__name__}: {exc})" if is_http_mode() else "")
            ),
        }

    count = collection.count()
    if count == 0:
        return {"success": True, "query": query, "results": [], "total": 0}

    # Build metadata filter
    where = {}
    if doc_type:
        where["document_type"] = doc_type
    if domain:
        where["domain"] = domain

    kwargs = {
        "query_texts": [query],
        "n_results": min(top_k, count),
    }
    if where:
        kwargs["where"] = where

    results = collection.query(**kwargs)

    matches = []
    for i in range(len(results["ids"][0])):
        meta = results["metadatas"][0][i] if results.get("metadatas") else {}
        matches.append({
            "source": meta.get("source", ""),
            "text": results["documents"][0][i],
            "distance": results["distances"][0][i] if results.get("distances") else None,
            "document_type": meta.get("document_type", ""),
            "domain": meta.get("domain", ""),
        })

    return {"success": True, "query": query, "results": matches, "total": len(matches)}


def main():
    args = parse_args()
    result = query_materials(
        args.query, args.top_k, args.db_path, args.collection,
        doc_type=args.doc_type, domain=args.domain,
    )
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
