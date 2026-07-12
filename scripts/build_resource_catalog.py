#!/usr/bin/env python3
"""Build a sendable-resource catalog from landing-pages-ranking exports.

Joins combined_ranking.json (priority + keyword data) with
all_landing_pages.json and all_blog_posts.json (descriptions/summaries)
and emits resources/catalog.json — a curated list of assets the drafting
agent may reference in outreach emails.

Inclusion rules:
  - httpStatus == 200 (live URL)
  - priority starts with 'HIGH' (ranking script flagged it as canonical)
  - Asset-type pattern in URL OR category == 'Evergreen Content'
  - Blog posts additionally require kwRelevance >= 30 and score >= 60
    (keeps the catalog focused on sales-usable content)

Usage:
    python scripts/build_resource_catalog.py                  # default paths
    python scripts/build_resource_catalog.py --src <dir>      # custom source dir
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

DEFAULT_SRC = Path(__file__).resolve().parent.parent / "resources" / "hubspot-cms"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "resources" / "catalog.json"

# URL pattern → asset_type. Order matters: most specific first.
# Plain substrings checked before regex patterns.
_ASSET_PATTERNS: list[tuple[str, str]] = [
    ("/resources/solution-brief/", "solution-brief"),
    ("/resources/product-brief/", "product-brief"),
    ("/resources/case-studies/", "case-study"),
    ("/resources/brochures/", "brochure"),
    ("/resources/white-paper", "whitepaper"),
    ("/resources/whitepaper", "whitepaper"),
    ("/resources/ebook/", "ebook"),
    ("/resources/webinar/", "webinar"),
    ("/resources/video/", "webinar"),
    ("/lp/resource-hub/", "resource-hub"),
    ("/products/", "product-page"),
    ("/product/", "product-page"),
    ("/blog/", "blog-post"),
    ("/news/", "blog-post"),
]

# Legacy root-level URLs (AdWords LPs, older campaign pages) that carry
# real sendable content. Matched after the canonical /resources/* paths.
_LEGACY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"/wp-[a-z]"), "whitepaper"),
    (re.compile(r"/white-paper-"), "whitepaper"),
    (re.compile(r"/[a-z0-9][a-z0-9-]*-brochure"), "brochure"),
    (re.compile(r"/[a-z0-9][a-z0-9-]*-infographic"), "infographic"),
]

_TAG_SEP = re.compile(r"\s*[,;|]\s*")

# Common stopwords + marketing filler we don't want polluting the tag index
_STOP = {
    "a", "an", "and", "as", "at", "be", "by", "for", "from", "how", "in",
    "is", "it", "of", "on", "or", "that", "the", "their", "this", "to",
    "with", "your", "our", "you", "we", "i", "us", "company", "resources",
    "resource", "lp", "vod", "ty", "page", "brief", "brochure",
    "case", "study", "studies", "ebook", "webinar", "whitepaper", "white",
    "paper", "product", "solution", "hub", "wp", "blog", "news",
}
_WORD = re.compile(r"[a-z0-9]+")


def _tags_from_text(*values: str) -> list[str]:
    """Extract domain keyword tags from slugs/titles. Lowercase, deduped,
    stopwords stripped, 2-char words dropped unless they're known
    acronyms (e.g. '5g')."""
    tags: list[str] = []
    seen: set[str] = set()
    for v in values:
        if not v:
            continue
        for tok in _WORD.findall(v.lower()):
            if len(tok) < 3 and not tok.isdigit() and tok not in {"5g", "4g", "lt"}:
                continue
            if tok in _STOP:
                continue
            if tok not in seen:
                seen.add(tok)
                tags.append(tok)
    return tags


def _asset_type(url: str, content_type: str) -> str | None:
    """Infer asset_type from URL pattern. Returns None if URL isn't a
    recognised sendable pattern and content_type isn't a blog post."""
    lower = url.lower()
    for pat, kind in _ASSET_PATTERNS:
        if pat in lower:
            return kind
    for rx, kind in _LEGACY_PATTERNS:
        if rx.search(lower):
            return kind
    if content_type == "Blog Post":
        return "blog-post"
    return None


def _split_tags(*values: str) -> list[str]:
    """Split comma/semicolon/pipe-separated keyword strings into a
    deduplicated, lowercased tag list."""
    tags: list[str] = []
    seen: set[str] = set()
    for v in values:
        if not v:
            continue
        for tok in _TAG_SEP.split(v):
            tok = tok.strip().lower()
            if tok and tok not in seen:
                seen.add(tok)
                tags.append(tok)
    return tags


def _load(src: Path) -> tuple[list[dict], dict[str, dict], dict[str, dict]]:
    """Load the three JSON inputs and index the detail sets by id."""
    with (src / "combined_ranking.json").open() as f:
        combined = json.load(f)
    with (src / "all_landing_pages.json").open() as f:
        landing = {str(d.get("id")): d for d in json.load(f)}
    with (src / "all_blog_posts.json").open() as f:
        blog = {str(d.get("id")): d for d in json.load(f)}
    return combined, landing, blog


def _summary_for(entry: dict, landing: dict, blog: dict) -> str:
    """Pull a short description: prefer metaDescription, fall back to
    postSummary, strip HTML, truncate to ~300 chars."""
    details = landing.get(str(entry["id"])) or blog.get(str(entry["id"])) or {}
    raw = (details.get("metaDescription") or "").strip()
    if not raw:
        raw = (details.get("postSummary") or "").strip()
    # Strip any HTML tags conservatively — postSummary can be HTML
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw[:300]


def build(src: Path = DEFAULT_SRC, out: Path = DEFAULT_OUT) -> dict:
    """Build catalog.json from source exports. Returns summary stats."""
    combined, landing, blog = _load(src)

    catalog: list[dict] = []
    skipped_low_quality_blogs = 0

    for entry in combined:
        if entry.get("httpStatus") != 200:
            continue
        if not (entry.get("priority") or "").startswith("HIGH"):
            continue

        url = entry.get("url", "")
        content_type = entry.get("contentType", "")
        asset_type = _asset_type(url, content_type)
        if not asset_type:
            continue

        # Drop obvious duplicates / Thank You variants that share a URL
        # root with a canonical page. Patterns seen: '-ty' suffix, '_old',
        # '/enhancing-connectivity-and-c' duplicated with '-vod-ty'.
        name_lower = entry.get("name", "").lower()
        if name_lower.endswith("_old") or " ty" in name_lower or name_lower.endswith(" ty"):
            continue
        if url.endswith("-ty") or url.rstrip("/").endswith("-ty"):
            continue

        # For blog posts and product pages, require the 'Evergreen Content'
        # signal to avoid sweeping in events or thank-you pages. Sendable
        # resource-type URLs (solution-brief, case-study, brochure, etc.)
        # are trusted by their URL pattern regardless of ranking category
        # — the ranking script misclassifies many of them as 'Other' or
        # 'Thank You Page'.
        category = entry.get("category", "")
        _trust_url = {
            "solution-brief", "product-brief", "case-study", "brochure",
            "whitepaper", "ebook", "webinar", "infographic",
            "resource-hub", "product-page",
        }
        if asset_type not in _trust_url and content_type == "Landing Page":
            if category != "Evergreen Content":
                continue

        # Blog posts need some keyword-level relevance to be worth
        # including. Thresholds chosen empirically — 20/50 recovers
        # topical posts that aren't top-tier for SEO but are still
        # citeable in outreach. Aggressive filtering (30/60) misses
        # things like "Broadband Goes Rural" in some catalogs.
        if asset_type == "blog-post":
            try:
                relevance = float(entry.get("kwRelevance") or 0)
            except (TypeError, ValueError):
                relevance = 0.0
            if relevance < 20 or (entry.get("score") or 0) < 50:
                skipped_low_quality_blogs += 1
                continue

        # Primary tags come from the keyword data when present (blog posts).
        # For landing pages kwAdGroups/kwTopKeywords are usually empty, so
        # fall back to title + slug tokens to ensure every entry has
        # something to match against.
        tags = _split_tags(
            entry.get("kwAdGroups") or "",
            entry.get("kwTopKeywords") or "",
        )
        if not tags:
            slug = entry.get("slug", "").split("/")[-1]  # last slug segment
            tags = _tags_from_text(entry.get("name", ""), slug)

        catalog.append({
            "id": str(entry["id"]),
            "title": entry.get("name", "").strip(),
            "url": url,
            "asset_type": asset_type,
            "summary": _summary_for(entry, landing, blog),
            "tags": tags,
            "score": entry.get("score", 0),
        })

    # Sort by asset_type primacy then score (descending). Sendable
    # briefs/case studies rank above blog posts at the same score.
    _TYPE_RANK = {
        "solution-brief": 0, "case-study": 1, "product-brief": 2,
        "brochure": 3, "whitepaper": 4, "ebook": 5, "webinar": 6,
        "infographic": 7, "resource-hub": 8, "product-page": 9,
        "blog-post": 10,
    }
    catalog.sort(key=lambda e: (_TYPE_RANK.get(e["asset_type"], 99), -e["score"]))

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        json.dump(catalog, f, indent=2)

    # Stats
    counts: dict[str, int] = {}
    for e in catalog:
        counts[e["asset_type"]] = counts.get(e["asset_type"], 0) + 1

    return {
        "total": len(catalog),
        "by_asset_type": counts,
        "skipped_low_quality_blogs": skipped_low_quality_blogs,
        "output": str(out),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Build the sendable resource catalog.")
    p.add_argument("--src", type=Path, default=DEFAULT_SRC, help="Source directory.")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output catalog path.")
    args = p.parse_args()

    stats = build(args.src, args.out)
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
