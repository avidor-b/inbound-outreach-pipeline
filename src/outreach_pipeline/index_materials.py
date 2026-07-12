#!/usr/bin/env python3
"""Index sales/marketing materials into a ChromaDB vector store.

Uses RecursiveCharacterTextSplitter for intelligent chunking that
respects paragraph, sentence, and word boundaries.

Usage:
  python index_materials.py                          # Index materials/ directory
  python index_materials.py --materials-dir docs/    # Custom directory
  python index_materials.py --reset                  # Clear and re-index
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from langchain_text_splitters import RecursiveCharacterTextSplitter

from outreach_pipeline.adapters.chroma_client import get_chroma_client

DB_PATH = "rag/materials_db"
COLLECTION_NAME = "sales_materials"
SUPPORTED_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".html"}
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200


def _get_rag_config():
    try:
        from outreach_pipeline.config import load_config
        c = load_config()
        return (
            c.get("rag_chunk_size", CHUNK_SIZE),
            c.get("rag_chunk_overlap", CHUNK_OVERLAP),
            c.get("rag_top_k", 8),
        )
    except Exception:
        return CHUNK_SIZE, CHUNK_OVERLAP, 3


def parse_args(args=None):
    p = argparse.ArgumentParser(description="Index sales/marketing materials for RAG")
    p.add_argument("--materials-dir", default="resources/materials", help="Directory containing materials")
    p.add_argument("--db-path", default=DB_PATH, help="ChromaDB storage path")
    p.add_argument("--collection", default=COLLECTION_NAME, help="Collection name")
    p.add_argument("--reset", action="store_true", help="Clear existing index before indexing")
    return p.parse_args(args)


def _detect_document_type(path: Path) -> str:
    """Infer document type from path and extension."""
    name = path.name.lower()
    parts = str(path).lower()
    if "product" in parts or "datasheet" in parts:
        return "product"
    if "blog" in parts or "news" in parts:
        return "blog"
    if "case" in parts or "story" in parts or "success" in parts:
        return "case_study"
    if "faq" in parts or "support" in parts:
        return "support"
    if "landing" in parts or "page" in parts:
        return "landing_page"
    if path.suffix == ".csv":
        return "data"
    return "general"


def _detect_domain(path: Path) -> str:
    """Extract domain from path if a directory looks like a domain name.

    Matches underscore-separated names with 2+ parts where the last part
    is a TLD-like suffix (com, org, net, io, co, etc.).
    e.g. customer_example_com → customer.example.com, example_co_uk → example.co.uk
    """
    import re
    _TLD = {"com", "org", "net", "io", "co", "uk", "de", "fr", "il", "us", "eu", "ai", "dev"}
    parts = str(path).replace("\\", "/").split("/")
    for part in parts:
        if "_" not in part or part.startswith("."):
            continue
        # Skip actual files
        if part.endswith((".txt", ".md", ".html", ".csv", ".json")):
            continue
        segments = part.split("_")
        if len(segments) >= 2 and segments[-1] in _TLD:
            return ".".join(segments)
    return ""


def read_file(path):
    """Read a supported file and return its text content."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def get_collection(db_path=DB_PATH, collection_name=COLLECTION_NAME, reset=False):
    """Get or create the ChromaDB collection.

    Uses HttpClient when CHROMA_HOST is set (cloud / docker compose),
    otherwise a PersistentClient on db_path (local dev).
    """
    client = get_chroma_client(db_path)
    if reset:
        try:
            client.delete_collection(collection_name)
        except Exception:
            pass
    return client.get_or_create_collection(name=collection_name)


def index_materials(materials_dir="resources/materials", db_path=DB_PATH, reset=False, collection_name=COLLECTION_NAME):
    """Index all supported files from materials_dir into ChromaDB."""
    mat_path = Path(materials_dir)
    if not mat_path.exists():
        return {"success": False, "error": f"Directory not found: {materials_dir}"}

    chunk_size, chunk_overlap, _ = _get_rag_config()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    collection = get_collection(db_path, collection_name, reset)
    now = datetime.now(timezone.utc).isoformat()

    files_indexed = 0
    chunks_indexed = 0

    for fpath in sorted(mat_path.rglob("*")):
        if not fpath.is_file() or fpath.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        text = read_file(fpath)
        if not text or not text.strip():
            continue

        rel_path = str(fpath.relative_to(mat_path))
        doc_type = _detect_document_type(fpath)
        domain = _detect_domain(fpath)
        chunks = splitter.split_text(text)

        ids = [f"{rel_path}::chunk{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "source": rel_path,
                "chunk_index": i,
                "document_type": doc_type,
                "domain": domain,
                "indexed_at": now,
            }
            for i in range(len(chunks))
        ]

        collection.upsert(ids=ids, documents=chunks, metadatas=metadatas)
        files_indexed += 1
        chunks_indexed += len(chunks)

    return {
        "success": True,
        "files_indexed": files_indexed,
        "chunks_indexed": chunks_indexed,
        "collection_count": collection.count(),
    }


def main():
    args = parse_args()
    result = index_materials(args.materials_dir, args.db_path, args.reset, args.collection)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
