"""Centralized ChromaDB client construction.

Two modes, picked automatically from env:

  HTTP mode (cloud)  — CHROMA_HOST set. Returns chromadb.HttpClient
                       pointed at the chroma container. CHROMA_PORT
                       defaults to 8000.
  Local mode (dev)   — CHROMA_HOST unset. Returns
                       chromadb.PersistentClient against db_path on
                       the local filesystem. This is the historical
                       behaviour — langgraph dev / pytest / CLI tools
                       keep working without running a separate chroma
                       container.

All callers (adapters.chromadb_adapter, query_rag, index_materials)
route through this so the mode switch is consistent.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def is_http_mode() -> bool:
    """True when CHROMA_HOST is set in the environment."""
    return bool(os.environ.get("CHROMA_HOST"))


def get_chroma_client(db_path: str = "rag/materials_db") -> Any:
    """Return a chromadb client appropriate for the current environment.

    Args:
        db_path: filesystem path for PersistentClient mode. Ignored in
            HTTP mode.

    Raises:
        chromadb's own errors if HTTP connect fails. Callers that
        previously checked `Path(db_path).exists()` should switch to
        catching exceptions from the returned client instead.
    """
    import chromadb

    if is_http_mode():
        host = os.environ["CHROMA_HOST"]
        port = int(os.environ.get("CHROMA_PORT", "8000"))
        return chromadb.HttpClient(host=host, port=port)

    # Local dev — PersistentClient. Create the directory if it doesn't
    # exist so first-time indexing works without a pre-step.
    Path(db_path).mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=db_path)


def collection_path_exists(db_path: str) -> bool:
    """Cheap pre-check: is the underlying store populated?

    In HTTP mode the answer is always 'maybe' — we can't tell without
    calling the server, so return True and let the query fail with an
    empty list if the collection isn't there.
    In local mode, check the directory actually has files.
    """
    if is_http_mode():
        return True
    p = Path(db_path)
    return p.exists() and any(p.iterdir())
