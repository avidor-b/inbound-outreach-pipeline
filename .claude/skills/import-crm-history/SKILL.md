---
name: import-crm-history
description: Sanitize and index an authorized historical CRM export for aggregate outreach-pattern retrieval.
argument-hint: "[private-file-path]"
---

# Import CRM history

Import only a file the user is authorized to process. Historical CRM exports are sensitive and must remain outside the repository.

## 1. Confirm scope

Ask the user to identify the private file and confirm that its intended use, retention, and model/vector providers are approved. Do not search the repository for exports and do not upload the source file anywhere.

## 2. Inspect structure

Read headers and a minimal sample. Propose a mapping for organization, industry, outcome, stage, date, and generalized notes. Do not display names, emails, phone numbers, message bodies, monetary values, or internal identifiers.

Wait for confirmation before transforming or indexing.

## 3. Sanitize

Remove or generalize:

- personal and employee identities;
- email addresses, phone numbers, signatures, and free-form message threads;
- account, contact, opportunity, contract, and ticket identifiers;
- prices, margins, contract values, and confidential commercial terms;
- credentials, private URLs, and attachment paths; and
- rare details that could re-identify a customer.

Prefer aggregate patterns such as industry, generalized outcome, and broad engagement approach. Show no more than three fully synthetic examples for approval.

## 4. Store privately and index

Write sanitized documents to a user-configured private directory outside the checkout, then index that directory:

```bash
index-materials --materials-dir "$OUTREACH_PRIVATE_DATA_DIR/crm_history" --collection outreach_history
```

Do not reset an existing collection unless the user explicitly requests it and understands the deletion impact.

## 5. Report

Report aggregate row counts, skipped rows, removed field classes, and the target collection. Do not list organizations or people.

## Safety rules

- Never commit source or processed CRM history.
- Never rely on automatic redaction as the only control.
- Never index raw notes, full emails, personal data, deal values, or confidential attachments.
- Stop when authorization or data ownership is unclear.
- Provide a deletion path for both source transformations and embeddings.
