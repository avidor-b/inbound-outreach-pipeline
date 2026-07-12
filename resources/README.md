# Resource content

This directory contains only fictional sample material created for the repository. It demonstrates the retrieval and sendable-resource formats without distributing organization, customer, CMS, or product data.

## Adding your own content

Add content only when you own it, license it, or have explicit permission for the intended processing and redistribution.

Do not commit:

- raw CRM, marketing-automation, or CMS exports;
- drafts, unpublished pages, private knowledge-base articles, or partner-portal content;
- customer, prospect, employee, or contact information;
- confidential manuals, contracts, proposals, quotes, or support records;
- scraped copies of third-party sites without permission; or
- credentials, tracking parameters, private URLs, or generated vector databases.

A document being publicly reachable does not necessarily make it redistributable.

## Materials

Put approved plain-text, Markdown, CSV, JSON, or HTML files under `materials/`. Prefer concise source text that contains no navigation, tracking markup, or personal data.

Index the directory with:

```bash
index-materials --materials-dir resources/materials --collection sales_materials --reset
```

The index is generated runtime data and belongs outside Git.

## Catalog

`catalog.json` lists public resources that the drafting UI may suggest. Each entry has this shape:

```json
{
  "id": "stable-public-id",
  "title": "Public resource title",
  "url": "https://docs.example.com/resource",
  "asset_type": "product-page",
  "summary": "Short factual description.",
  "tags": ["topic", "use-case"],
  "score": 80
}
```

Use stable synthetic IDs in examples. Real catalogs should contain only reviewed public URLs, summaries you are authorized to publish, and non-sensitive tags. Remove analytics query strings and never include private or authenticated links.

## Review checklist

Before adding or releasing content, verify:

- [ ] ownership or license is documented;
- [ ] publication and model/vector processing are permitted;
- [ ] no personal, customer, employee, or confidential information remains;
- [ ] every URL is intentionally public and controlled by the deployer;
- [ ] summaries make no unsupported claims; and
- [ ] the source and derived index have a retention and deletion path.
