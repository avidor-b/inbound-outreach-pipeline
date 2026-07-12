#!/usr/bin/env python3
"""Ingest deployment-owned HubSpot CMS content (landing pages + blog posts) into
the `sales_materials` ChromaDB collection.

Input: `resources/hubspot-cms/` (or --src) with
  - combined_ranking.json   (priority + httpStatus per page)
  - all_landing_pages.json  (raw HubSpot LP export, metadata only)
  - all_blog_posts.json     (raw HubSpot blog export, includes postBody)

Selection rules:
  - httpStatus == 200 (live URL)
  - priority starts with 'HIGH' or 'MEDIUM' (the ranking script's keeps)
  - updatedAt within the last `--max-age-days` (default 1095 = 3yr)
  - Landing pages: URL path must contain /resources/ or /products/
    (product-briefs, solution-briefs, case-studies, webinars, etc.).
    Other LPs — campaign pages, forms, thank-yous — are excluded.
  - Blog posts: no URL filter.

Content extracted:
  - Blog posts: `postBody` with HTML tags stripped. Chunked with the
    same RecursiveCharacterTextSplitter that index_materials.py uses
    so chunk boundaries are consistent across the collection.
  - Landing pages: `htmlTitle` + `metaDescription` only — raw HubSpot
    LP exports do not include the rendered body HTML, and we explicitly
    chose not to HTTP-fetch each URL for this pass. Titles + meta
    descriptions still give the retriever enough signal to surface the
    right URL, which is the main use case (drafting agent linking to
    the right resource).

IDs are `hubspot_cms::<type>::<hubspot_id>::chunk<n>` so re-runs upsert
cleanly without duplicating rows.

Usage:
    python -m scripts.ingest_hubspot_cms                     # defaults
    python -m scripts.ingest_hubspot_cms --reset             # wipe sales_materials first
    python -m scripts.ingest_hubspot_cms --src <dir>         # custom input dir
    python -m scripts.ingest_hubspot_cms --max-age-days 730  # 2 years
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter

from outreach_pipeline.adapters.chroma_client import get_chroma_client
from outreach_pipeline.index_materials import COLLECTION_NAME, DB_PATH, _get_rag_config


_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = _REPO_ROOT / "resources" / "hubspot-cms"
# URL path fragments that qualify a landing page as "content we'd link
# to in outreach." Everything else (thank-you pages, forms, campaign
# LPs) is skipped.
_LP_PATH_ALLOW = ("/resources/", "/products/", "/product/")
_DEFAULT_MAX_AGE_DAYS = 3 * 365


def _parse_date(val) -> datetime | None:
    """Parse either ISO 8601 strings or epoch-ms ints to UTC datetime.

    HubSpot uses both forms inconsistently across payloads — LPs are
    ISO, blogs mix ISO with epoch-ms — so we accept either.
    """
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        # Heuristic: >1e11 is ms, otherwise seconds.
        ts = val / 1000 if val > 1e11 else val
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, ValueError, OSError):
            return None
    try:
        s = str(val).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _strip_html(html: str) -> str:
    """Conservative HTML strip — we're not rendering, we're retrieving."""
    if not html:
        return ""
    s = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"<style[^>]*>.*?</style>", " ", s, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"<[^>]+>", " ", s)
    # HubSpot embeds like {{cta('...')}} — strip them so chunks read clean.
    s = re.sub(r"\{\{[^}]+\}\}", " ", s)
    s = re.sub(r"&nbsp;", " ", s)
    s = re.sub(r"&amp;", "&", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _url_qualifies_lp(url: str) -> bool:
    if not url:
        return False
    path = url.split("://", 1)[-1].split("/", 1)[-1].lower()
    path = "/" + path
    return any(frag in path for frag in _LP_PATH_ALLOW)


def load_inputs(src: Path) -> tuple[list[dict], dict, dict]:
    with (src / "combined_ranking.json").open() as f:
        combined = json.load(f)
    with (src / "all_landing_pages.json").open() as f:
        lp = {str(d.get("id")): d for d in json.load(f)}
    with (src / "all_blog_posts.json").open() as f:
        bp = {str(d.get("id")): d for d in json.load(f)}
    return combined, lp, bp


def select(combined: list[dict], lp: dict, bp: dict, max_age_days: int) -> list[dict]:
    """Apply priority + age + URL filters, return the documents we'll index."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    docs: list[dict] = []

    for entry in combined:
        if entry.get("httpStatus") != 200:
            continue
        pri = (entry.get("priority") or "").upper()
        if not (pri.startswith("HIGH") or pri.startswith("MEDIUM")):
            continue

        hid = str(entry.get("id"))
        url = entry.get("url") or ""

        lp_rec = lp.get(hid)
        bp_rec = bp.get(hid)
        if bp_rec:
            doc_type = "blog"
            updated = _parse_date(bp_rec.get("updated") or bp_rec.get("publishDate"))
            title = (bp_rec.get("htmlTitle") or bp_rec.get("name") or "").strip()
            body = _strip_html(bp_rec.get("postBody") or "")
            text = f"{title}\n\n{body}".strip() if title else body
        elif lp_rec:
            if not _url_qualifies_lp(url):
                continue
            doc_type = "landing_page"
            updated = _parse_date(lp_rec.get("updatedAt") or lp_rec.get("publishDate"))
            title = (lp_rec.get("htmlTitle") or lp_rec.get("name") or "").strip()
            meta = _strip_html(lp_rec.get("metaDescription") or "")
            text = f"{title}\n\n{meta}".strip() if meta or title else ""
        else:
            # Ranked but no detail record — skip.
            continue

        if updated is None or updated < cutoff:
            continue
        if not text or len(text) < 40:
            continue

        docs.append(
            {
                "hid": hid,
                "url": url,
                "priority": entry.get("priority") or "",
                "category": entry.get("category") or "",
                "doc_type": doc_type,
                "updated_at": updated.isoformat(),
                "title": title,
                "text": text,
            }
        )
    return docs


def ingest(
    src: Path = DEFAULT_SRC,
    db_path: str = DB_PATH,
    collection_name: str = COLLECTION_NAME,
    max_age_days: int = _DEFAULT_MAX_AGE_DAYS,
    reset: bool = False,
) -> dict:
    combined, lp, bp = load_inputs(src)
    docs = select(combined, lp, bp, max_age_days=max_age_days)

    if not docs:
        return {"success": True, "docs_selected": 0, "chunks_indexed": 0}

    chunk_size, chunk_overlap, _ = _get_rag_config()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    client = get_chroma_client(db_path)
    if reset:
        try:
            client.delete_collection(collection_name)
        except Exception:
            pass
    collection = client.get_or_create_collection(name=collection_name)

    now = datetime.now(timezone.utc).isoformat()
    chunks_indexed = 0
    docs_indexed = 0

    # Chunk by blog_post, upsert in batches to avoid huge single calls.
    batch_ids: list[str] = []
    batch_docs: list[str] = []
    batch_meta: list[dict] = []
    BATCH = 256

    def flush():
        nonlocal batch_ids, batch_docs, batch_meta, chunks_indexed
        if not batch_ids:
            return
        collection.upsert(ids=batch_ids, documents=batch_docs, metadatas=batch_meta)
        chunks_indexed += len(batch_ids)
        batch_ids, batch_docs, batch_meta = [], [], []

    for d in docs:
        chunks = splitter.split_text(d["text"])
        if not chunks:
            continue
        docs_indexed += 1
        for i, chunk in enumerate(chunks):
            batch_ids.append(f"hubspot_cms::{d['doc_type']}::{d['hid']}::chunk{i}")
            batch_docs.append(chunk)
            batch_meta.append(
                {
                    "source": f"hubspot_cms/{d['doc_type']}/{d['hid']}",
                    "source_type": "hubspot_cms",
                    "document_type": d["doc_type"],
                    "url": d["url"],
                    "title": d["title"][:200],
                    "priority": d["priority"],
                    "category": d["category"],
                    "updated_at": d["updated_at"],
                    "indexed_at": now,
                    "chunk_index": i,
                }
            )
            if len(batch_ids) >= BATCH:
                flush()
    flush()

    return {
        "success": True,
        "docs_selected": len(docs),
        "docs_indexed": docs_indexed,
        "chunks_indexed": chunks_indexed,
        "collection_count": collection.count(),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--src", default=str(DEFAULT_SRC), help="Dir containing the 3 JSON inputs")
    p.add_argument("--collection", default=COLLECTION_NAME)
    p.add_argument("--db-path", default=DB_PATH)
    p.add_argument("--max-age-days", type=int, default=_DEFAULT_MAX_AGE_DAYS)
    p.add_argument("--reset", action="store_true", help="Wipe the collection before indexing")
    args = p.parse_args(argv)

    result = ingest(
        src=Path(args.src),
        db_path=args.db_path,
        collection_name=args.collection,
        max_age_days=args.max_age_days,
        reset=args.reset,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
