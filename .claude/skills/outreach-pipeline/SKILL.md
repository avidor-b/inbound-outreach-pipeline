---
name: outreach-pipeline
description: Run the human-reviewed inbound outreach workflow for an explicit date range.
argument-hint: "[start-date] [end-date]"
---

# Outreach pipeline

Run only against a configured environment that the user is authorized to access. Default to the synthetic local stack. Never infer permission to use a real CRM, mailbox, or recipient list.

## 1. Confirm environment and range

Confirm the start date, end date, CRM adapter, database, sending state, and whether retrieval content is approved. Stop if the target environment is ambiguous.

## 2. Fetch

```bash
fetch-mqls --start-date START_DATE --end-date END_DATE > data/mqls_raw.json
```

Treat generated data as sensitive even when ignored by Git. Report counts, not identities.

## 3. Enrich

Delegate eligible contacts to the `mql-enricher` agent. Enrichment must use permitted organization-level sources and must finish before scoring.

## 4. Score

Delegate enriched contacts to the `lead-scorer` agent. Policy-blocked contacts do not advance, regardless of score.

## 5. Draft and review

Delegate eligible contacts to the `email-drafter` agent in interactive mode. Every real draft requires an authorized reviewer to approve, revise, or discard it. Do not use a background auto-approval path for real recipients.

## 6. Send

Sending is a separate, explicit action. Verify sender configuration, authorization, opt-out, duplicate, cooldown, rate, and recipient checks immediately before each send. If sending is disabled, leave approved drafts unsent.

## 7. Monitor replies

Delegate to `reply-monitor` for an explicit date range. Apply opt-out requests before any other reply handling and retain only a redacted summary.

## 8. Optional export

```bash
export-pipeline --data-dir data --output data/pipeline_output.xlsx
```

Exports may contain sensitive information. Keep them local, encrypted where appropriate, out of Git, and within the configured retention period.

## 9. Summary

Use `pipeline-db metrics` and report aggregate fetched, enriched, blocked, scored, drafted, approved, sent, replied, and opted-out counts. Do not print contact identities or message excerpts.

## Guidance

- [Email drafting guidelines](email-guidelines.md)
- [Reply classification guidelines](reply-guidelines.md)
