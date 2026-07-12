---
name: index-content
description: Index approved local material or a permitted public site into the retrieval store.
disable-model-invocation: true
argument-hint: "[path-or-url] [--prospect]"
allowed-tools: Bash(scrape-site *), Bash(index-materials *), Bash(query-rag *), Read
---

# Index approved content

Index content only when the user owns it, licenses it, or has confirmed permission for the intended processing and storage.

## Local material

With no arguments, index the repository's fictional sample:

```bash
index-materials --materials-dir resources/materials --collection sales_materials
```

For a supplied directory, verify that it is intended for indexing and contains no CRM exports, credentials, private messages, customer documents, or confidential manuals before running `index-materials`.

## Public site

Before using `scrape-site`, confirm the exact domain, collection, page limit, site terms, robots policy, and authorization. Never crawl authenticated, gated, private, link-local, loopback, or cloud-metadata targets.

```bash
scrape-site --url https://docs.example.com --max-pages 5 --index --collection sales_materials
```

Use `--prospect` only for a permitted organization-level enrichment collection. Do not collect employee pages or personal profiles.

## Verification

Report file or page count, chunk count, collection name, and any skipped content. Test retrieval with a non-sensitive query:

```bash
query-rag --query "sample product overview" --collection sales_materials
```

Do not paste indexed content into the report. Remind the user that embeddings and metadata require the same retention and deletion controls as the source.
