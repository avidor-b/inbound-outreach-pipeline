"""Sendable resource catalog — lexical matching over curated marketing assets.

The catalog is built offline by scripts/build_resource_catalog.py from the
HubSpot landing-pages-ranking exports. It contains solution briefs, case
studies, brochures, whitepapers, ebooks, webinars, product pages and
high-quality blog posts that a sales rep can reasonably send to a prospect.

find_sendable_asset(product, industry, keywords) returns up to N matching
entries. The drafter calls this unconditionally on every draft so the UI
can show the top candidates to the human reviewer, and so the drafter can
optionally cite one in the email.

v1 is lexical (token overlap with asset-type weighting). If recall is
weak, v2 can embed tags+title+summary into a ChromaDB collection.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from outreach_pipeline.organization import get_organization_profile

_CATALOG_PATH = Path(__file__).resolve().parent.parent.parent.parent / "resources" / "catalog.json"

# Asset-type priority: briefs and case studies are the preferred sendables.
# Blog posts rank last at the same score so they only surface when there's
# no better-matched canonical asset.
_TYPE_BOOST = {
    "solution-brief": 4.0,
    "case-study": 4.0,
    "product-brief": 3.5,
    "brochure": 3.0,
    "whitepaper": 3.0,
    "ebook": 3.0,
    "webinar": 2.5,
    "infographic": 2.5,
    "resource-hub": 2.0,
    "product-page": 2.0,
    "blog-post": 1.0,
}

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {
    "a", "an", "and", "as", "at", "be", "by", "for", "from", "in", "is",
    "it", "of", "on", "or", "that", "the", "their", "this", "to", "with",
    "your", "our", "you", "we", "i", "us", "general",
}


def _tokenize(text: str) -> set[str]:
    """Lowercase, split on non-word, drop stopwords and 1-char tokens."""
    return {
        t for t in _WORD.findall(text.lower())
        if t not in _STOP and (len(t) >= 3 or t.isdigit() or t in {"5g", "4g"})
    }


_catalog_cache: list[dict] | None = None


def _load_catalog(path: Path | None = None) -> list[dict]:
    """Load and cache the catalog. Returns [] if the file is missing —
    callers should treat an empty catalog as 'no match' rather than an
    error, so drafting still works before the catalog is built."""
    global _catalog_cache
    if _catalog_cache is not None and path is None:
        return _catalog_cache
    p = path or _CATALOG_PATH
    if not p.exists():
        if path is None:
            _catalog_cache = []
        return []
    with p.open() as f:
        data = json.load(f)
    if path is None:
        _catalog_cache = data
    return data


def _clear_cache() -> None:
    """Test helper — force the next _load_catalog() call to re-read."""
    global _catalog_cache
    _catalog_cache = None


def _score_entry(entry: dict, query_tokens: set[str]) -> float:
    """Score one catalog entry against the query token set.

    Tags are tokenized into a single word-set per entry so over-tagged
    blog posts (which can carry 40+ keyword phrases from the ranking
    script) don't inflate the score linearly. We want to reward "this
    entry mentions the query concept" once per tag-concept, not once
    per tag-phrase.

    Weights:
      tag-concept hit   +3  (unique query-token found in any tag)
      title hit         +2
      summary hit       +1
      asset_type boost  additive
    """
    if not query_tokens:
        return 0.0

    tag_tokens: set[str] = set()
    for tag in entry.get("tags", []):
        tag_tokens.update(_tokenize(tag))

    title_tokens = _tokenize(entry.get("title", ""))
    summary_tokens = _tokenize(entry.get("summary", ""))

    # Cap tag hits at 3. Blog posts often carry 40+ keyword phrases from the
    # SEO ranking script — without the cap, a mildly-related blog beats a
    # directly-named case study by sheer tag surface area.
    tag_hits = min(len(query_tokens & tag_tokens), 3)
    title_hits = len(query_tokens & title_tokens)
    summary_hits = len(query_tokens & summary_tokens)

    raw = (tag_hits * 3.0) + (title_hits * 2.0) + (summary_hits * 1.0)
    if raw == 0.0:
        return 0.0

    boost = _TYPE_BOOST.get(entry.get("asset_type", ""), 1.0)
    return raw + boost


# ---------------------------------------------------------------------------
# Post-draft promise extractor
# ---------------------------------------------------------------------------

# Phrases the drafter might use to offer a resource. Captures the noun
# phrase that follows so we can fuzzy-match it against the retrieved
# catalog entries.
_PROMISE_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"\bI can (?:send|share|provide|put together|pull together) "
        r"(?:the |our |a |an |you )?(?:link to (?:the |our )?)?([^.?\n]{3,120})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:want|would you like) me to send (?:you )?"
        r"(?:the |our |a |an )?(?:link to (?:the |our )?)?([^.?\n]{3,120})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bI(?:'d| would) be happy to (?:send|share|provide) (?:you )?"
        r"(?:the |our |a |an )?([^.?\n]{3,120})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bI(?:'ll| will) send (?:you )?(?:the |our |a |an )?([^.?\n]{3,120})",
        re.IGNORECASE,
    ),
]

# Match any http(s) URL in the body. Catalog ingestion is filtered through the
# configured resource-domain allowlist, and only catalog URLs can become
# referenced_resource_ids.
_URL_PATTERN = re.compile(
    r"https?://[\w.-]+(?:\.[a-z]{2,})+/[\w./?&=#-]*",
    re.IGNORECASE,
)


def _normalize_url(url: str) -> str:
    """Lowercase, strip www./trailing slash, drop query/fragment — so the
    URL used in email copy matches the catalog entry regardless of minor
    variations."""
    u = url.lower().strip()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = u.split("?", 1)[0].split("#", 1)[0]
    return u.rstrip("/")


def extract_promises(
    body: str,
    catalog_matches: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Scan the draft body for resource references and promise phrases.

    Returns (referenced_resource_ids, unverified_promises):
      referenced_resource_ids — ids of catalog_matches whose URL appears
        verbatim in the body. Drives the 'Referenced in email' badge on
        the UI card.
      unverified_promises     — free-text phrases where the drafter
        offered to send something, but the noun-phrase doesn't lexically
        match any of the catalog_matches title/tag words. Drives the
        yellow warning banner.

    When a URL match is found, promise phrases in that draft are trusted
    — the prospect will get a real link, so no warning is raised.
    """
    if not body:
        return [], []

    # URL-based matching — strongest signal
    referenced_ids: list[str] = []
    seen_ids: set[str] = set()
    normalized_body_urls = {
        _normalize_url(m.group(0)) for m in _URL_PATTERN.finditer(body)
    }
    for entry in catalog_matches:
        if _normalize_url(entry.get("url", "")) in normalized_body_urls:
            if entry["id"] not in seen_ids:
                referenced_ids.append(entry["id"])
                seen_ids.add(entry["id"])

    # Promise-phrase detection. If a URL already resolved, skip flagging.
    if referenced_ids:
        return referenced_ids, []

    # Build a bag of all token-concepts across the retrieved matches so
    # we can decide whether a promise noun-phrase is likely about one of
    # them. An email promising a "mining solution brief" when mining
    # assets were retrieved shouldn't be flagged.
    match_tokens: set[str] = set()
    for entry in catalog_matches:
        match_tokens.update(_tokenize(entry.get("title", "")))
        for tag in entry.get("tags", []):
            match_tokens.update(_tokenize(tag))

    unverified: list[str] = []
    seen_phrases: set[str] = set()
    for pat in _PROMISE_PATTERNS:
        for m in pat.finditer(body):
            phrase = m.group(1).strip().rstrip(".,?!")
            # Trim trailing fluff — "over to assist with your BOM", etc.
            phrase = re.split(r"\s+(?:over to|to assist|to help|for your)\b",
                              phrase, maxsplit=1, flags=re.IGNORECASE)[0].strip()
            if not phrase or len(phrase) < 4:
                continue
            key = phrase.lower()
            if key in seen_phrases:
                continue
            seen_phrases.add(key)

            phrase_tokens = _tokenize(phrase)
            if not phrase_tokens:
                continue
            # If the phrase overlaps meaningfully with any retrieved
            # match (>=2 shared tokens, or >=1 if the phrase is short),
            # trust it. Otherwise flag.
            overlap = phrase_tokens & match_tokens
            threshold = 1 if len(phrase_tokens) <= 2 else 2
            if len(overlap) < threshold:
                unverified.append(phrase)

    return referenced_ids, unverified


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def find_sendable_asset(
    product: str = "",
    industry: str = "",
    keywords: list[str] | str = "",
    top_k: int = 3,
    catalog_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return up to `top_k` catalog entries matching the given context.

    Empty query returns []. Entries with zero-overlap score are excluded
    — we never surface a random asset just to fill top_k.

    Args:
        product: primary product match from scoring (e.g. "microwave radio")
        industry: prospect industry from HubSpot (e.g. "Utilities")
        keywords: additional context terms — list or comma/space-separated string
        top_k: maximum number of matches to return
        catalog_path: override for tests
    """
    catalog = _load_catalog(catalog_path)
    if not catalog:
        return []

    # A catalog is data, and may be supplied by an operator or generated by a
    # scraper. Only return links hosted on explicitly configured resource
    # domains so a stale or tampered catalog cannot make drafts recommend an
    # unrelated third-party URL.
    profile = get_organization_profile()
    catalog = [entry for entry in catalog if profile.is_resource_url(entry.get("url", ""))]
    if not catalog:
        return []

    if isinstance(keywords, str):
        kw_list = [k.strip() for k in re.split(r"[,\s]+", keywords) if k.strip()]
    else:
        kw_list = [k.strip() for k in keywords if k and k.strip()]

    query_tokens: set[str] = set()
    for text in [product, industry, *kw_list]:
        query_tokens.update(_tokenize(text))

    if not query_tokens:
        return []

    scored = [(entry, _score_entry(entry, query_tokens)) for entry in catalog]
    scored = [(e, s) for e, s in scored if s > 0]
    scored.sort(key=lambda pair: -pair[1])

    results: list[dict[str, Any]] = []
    for entry, score in scored[:top_k]:
        results.append({
            "id": entry["id"],
            "title": entry["title"],
            "url": entry["url"],
            "asset_type": entry["asset_type"],
            "summary": entry["summary"],
            "match_score": round(score, 2),
        })
    return results
