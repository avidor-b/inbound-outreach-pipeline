#!/usr/bin/env python3
"""Bulk-scrape the configured organization's sitemap and index into ChromaDB.

Usage:
  python scrape_sitemap.py
  python scrape_sitemap.py --dry-run   # just print URLs, don't scrape
"""
import argparse
import asyncio
import os
import re
import sys
import urllib.request
from datetime import timedelta
from pathlib import Path

import trafilatura
from bs4 import BeautifulSoup
from crawlee import ConcurrencySettings
from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext
from outreach_pipeline.organization import get_organization_profile

_PROFILE = get_organization_profile()
_PRIMARY_DOMAIN = _PROFILE.primary_domains[0] if _PROFILE.primary_domains else "example.com"
SITEMAP_URL = os.environ.get(
    "OUTREACH_SITEMAP_URL", f"https://www.{_PRIMARY_DOMAIN}/sitemap.xml",
)
SECTIONS = ("blog", "products", "resources", "news")
OUTPUT_DIR = os.environ.get(
    "OUTREACH_SITEMAP_OUTPUT_DIR", f"resources/materials/{_PRIMARY_DOMAIN}",
)
COLLECTION = "sales_materials"
DB_PATH = "rag/materials_db"

SKIP_PATTERNS = re.compile(
    r'legal|privacy|cookie|terms|career|job|login|signin|sign.?up|register|'
    r'support|ticket|status|sitemap\.xml|\.pdf$|\.zip$|\.png$|\.jpg$',
    re.IGNORECASE,
)


def slugify(text, max_len=80):
    slug = re.sub(r'[^\w\s-]', '', text.lower())
    slug = re.sub(r'[\s_]+', '-', slug).strip('-')
    return slug[:max_len] or "untitled"


def fetch_sitemap_urls(sections):
    print(f"Fetching sitemap: {SITEMAP_URL}")
    with urllib.request.urlopen(SITEMAP_URL, timeout=30) as r:
        xml = r.read().decode("utf-8")
    all_urls = re.findall(r'<loc>([^<]+)</loc>', xml)
    filtered = [
        u for u in all_urls
        if any(f"/{s}/" in u for s in sections)
        and "staging" not in u
    ]
    print(f"Found {len(filtered)} URLs across sections: {', '.join(sections)}")
    by_section = {}
    for s in sections:
        by_section[s] = [u for u in filtered if f"/{s}/" in u]
        print(f"  /{s}/: {len(by_section[s])} pages")
    return filtered


async def crawl(urls, output_dir):
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    pages_saved = 0
    seen_slugs = set()

    crawler = PlaywrightCrawler(
        max_requests_per_crawl=len(urls) + 10,
        max_request_retries=1,
        concurrency_settings=ConcurrencySettings(desired_concurrency=3, max_concurrency=3),
        headless=True,
        navigation_timeout=timedelta(seconds=15),
    )

    @crawler.router.default_handler
    async def handler(context: PlaywrightCrawlingContext):
        nonlocal pages_saved

        url = context.request.url
        if SKIP_PATTERNS.search(url):
            return

        await context.page.wait_for_load_state("domcontentloaded")
        html = await context.page.content()

        text = trafilatura.extract(html, include_comments=False, include_tables=True,
                                   no_fallback=False, favor_recall=True)
        if not text:
            soup = BeautifulSoup(html, "lxml")
            for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "iframe"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)

        soup = BeautifulSoup(html, "lxml")
        title_tag = soup.find("title")
        title = title_tag.string.strip() if title_tag and title_tag.string else url
        text = re.sub(r'\n{3,}', '\n\n', text or "").strip()

        if len(text) < 200:
            return

        slug = slugify(title)
        # Deduplicate slugs
        base_slug = slug
        counter = 1
        while slug in seen_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1
        seen_slugs.add(slug)

        filepath = out / f"{slug}.txt"
        filepath.write_text(f"# {title}\nSource: {url}\n\n{text}", encoding="utf-8")
        pages_saved += 1
        if pages_saved % 25 == 0:
            print(f"  Saved {pages_saved} pages...")

    await crawler.run(urls)
    return pages_saved


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print URLs only, don't scrape")
    args = parser.parse_args()

    urls = fetch_sitemap_urls(SECTIONS)

    if args.dry_run:
        for u in urls:
            print(u)
        return

    print(f"\nScraping {len(urls)} pages into {OUTPUT_DIR} ...")
    import logging
    logging.getLogger("crawlee").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    pages_saved = asyncio.run(crawl(urls, OUTPUT_DIR))
    print(f"Scraped {pages_saved} pages.")

    print(f"\nIndexing into ChromaDB collection '{COLLECTION}' ...")
    from outreach_pipeline.index_materials import index_materials
    result = index_materials(OUTPUT_DIR, db_path=DB_PATH, collection_name=COLLECTION)
    print(f"Done: {result['files_indexed']} files, {result['chunks_indexed']} chunks indexed.")
    print(f"Collection total: {result['collection_count']} chunks")


if __name__ == "__main__":
    main()
