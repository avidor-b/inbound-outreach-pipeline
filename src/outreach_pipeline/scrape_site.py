#!/usr/bin/env python3
"""Scrape a website and save content as text files for RAG indexing.

Uses Crawlee PlaywrightCrawler for full JS rendering — works with React, Vue,
HubSpot, and other JS-heavy sites. Works for both your own site and prospects.

Usage:
  python scrape_site.py --url https://example.com --max-pages 50
  python scrape_site.py --url https://example.com --output-dir materials/my-site
  python scrape_site.py --url https://prospect.example.com --output-dir materials/prospects/prospect-example-com
"""
import argparse
import asyncio
import json
import re
import sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

import trafilatura
from bs4 import BeautifulSoup
from crawlee import ConcurrencySettings
from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext
from outreach_pipeline.security.url_safety import assert_public_http_url

# SSRF guard implementation uses ipaddress plus socket.getaddrinfo and rejects
# is_private, is_loopback, and is_link_local addresses before browser crawling.


def parse_args(args=None):
    try:
        from outreach_pipeline.config import load_config
        cfg = load_config()
        default_max_pages = cfg.get("scrape_max_pages", 50)
    except Exception:
        default_max_pages = 50

    p = argparse.ArgumentParser(description="Scrape website for RAG indexing")
    p.add_argument("--url", required=True, help="Starting URL to crawl")
    p.add_argument("--max-pages", type=int, default=default_max_pages, help="Max pages to crawl")
    p.add_argument("--output-dir", help="Output directory (default: materials/<domain>)")
    p.add_argument("--index", action="store_true", help="Auto-index into ChromaDB after scraping")
    p.add_argument("--collection", help="ChromaDB collection name (default: domain-based, e.g. customer_example_com)")
    return p.parse_args(args)


def extract_text(html):
    """Extract main article content from rendered HTML using trafilatura."""
    # trafilatura strips nav/ads/footers using Reader-View-style heuristics
    text = trafilatura.extract(html, include_comments=False, include_tables=True,
                               no_fallback=False, favor_recall=True)
    if not text:
        # Fallback: basic BeautifulSoup stripping
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "iframe"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)

    soup = BeautifulSoup(html, "lxml")  # for title extraction
    text = re.sub(r'\n{3,}', '\n\n', text or "")
    return soup, text.strip()


def slugify(text, max_len=80):
    """Convert text to a safe filename."""
    slug = re.sub(r'[^\w\s-]', '', text.lower())
    slug = re.sub(r'[\s_]+', '-', slug).strip('-')
    return slug[:max_len] or "untitled"


PRIORITY_PATTERNS = re.compile(
    r'about|product|service|solution|platform|feature|case.?stud|customer|'
    r'blog|news|press|resource|pricing|industr|partner|integrat|why.?us|overview',
    re.IGNORECASE,
)

SKIP_PATTERNS = re.compile(
    r'legal|privacy|cookie|terms|career|job|login|signin|sign.?up|register|'
    r'support|ticket|status|sitemap\.xml|\.pdf$|\.zip$|\.png$|\.jpg$',
    re.IGNORECASE,
)


def _url_priority(url):
    """Score a URL for relevance. Higher = more useful for enrichment."""
    path = urlparse(url).path.lower()
    if SKIP_PATTERNS.search(path):
        return -1
    if PRIORITY_PATTERNS.search(path):
        return 10
    if path in ('/', ''):
        return 5  # homepage is useful
    return 1


async def scrape_site(url, max_pages=50, output_dir=None):
    """Crawl a website using a headless browser and save pages as text files."""
    assert_public_http_url(url)
    domain = urlparse(url).netloc
    out = Path(output_dir or f"resources/materials/{domain}")
    out.mkdir(parents=True, exist_ok=True)

    pages_saved = 0
    results = []

    crawler = PlaywrightCrawler(
        max_requests_per_crawl=max_pages,
        max_request_retries=1,
        concurrency_settings=ConcurrencySettings(desired_concurrency=3, max_concurrency=3),
        headless=True,
        navigation_timeout=timedelta(seconds=15),
    )

    @crawler.router.default_handler
    async def handler(context: PlaywrightCrawlingContext):
        nonlocal pages_saved

        # Skip low-value pages
        assert_public_http_url(context.request.url)
        if _url_priority(context.request.url) < 0:
            await context.enqueue_links(strategy="same-domain")
            return

        # Wait for main content to render
        await context.page.wait_for_load_state("domcontentloaded")

        html = await context.page.content()
        soup, text = extract_text(html)

        title_tag = soup.find("title")
        title = title_tag.string.strip() if title_tag and title_tag.string else context.request.url

        if len(text) < 100:
            await context.enqueue_links(strategy="same-domain")
            return

        slug = slugify(title)
        filepath = out / f"{slug}.txt"
        content = f"# {title}\nSource: {context.request.url}\n\n{text}"
        filepath.write_text(content, encoding="utf-8")
        pages_saved += 1
        results.append({"url": context.request.url, "title": title, "file": str(filepath)})

        await context.enqueue_links(strategy="same-domain")

    await crawler.run([url])

    return {
        "success": True,
        "domain": domain,
        "pages_crawled": pages_saved,
        "output_dir": str(out),
        "pages": results,
    }


def main():
    args = parse_args()

    import logging
    logging.getLogger("crawlee").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    result = asyncio.run(scrape_site(args.url, args.max_pages, args.output_dir))
    print(json.dumps({k: v for k, v in result.items() if k != "pages"}, indent=2))
    print(f"\nPages saved: {len(result['pages'])}")
    for p in result["pages"][:5]:
        print(f"  {p['title'][:60]}")
    if len(result["pages"]) > 5:
        print(f"  ... and {len(result['pages']) - 5} more")

    if args.index and result.get("success"):
        from outreach_pipeline.index_materials import index_materials
        collection = args.collection or result["domain"].replace(".", "_").replace("-", "_")
        idx = index_materials(result["output_dir"], collection_name=collection)
        print(f"\nIndexed: {idx['chunks_indexed']} chunks from {idx['files_indexed']} files into collection '{collection}'")


if __name__ == "__main__":
    main()
