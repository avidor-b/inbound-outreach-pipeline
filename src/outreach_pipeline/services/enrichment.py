"""Enrichment domain service — contact enrichment persistence."""
from __future__ import annotations

from outreach_pipeline.db.contacts import enrich_contact


async def save_enrichment(
    contact_id: str,
    company_context: str = "",
    crm_context: str = "",
    enrichment_confidence: str = "",
) -> None:
    """Persist enrichment data to the database."""
    await enrich_contact(contact_id, company_context, crm_context, enrichment_confidence)
