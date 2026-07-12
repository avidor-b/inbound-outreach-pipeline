"""Draft and send log operations — async Postgres (asyncpg).

Drafts are keyed by engagement_id (unit of work), not contact_id.
A contact can have multiple engagements, each with its own draft history.
"""
from datetime import datetime, timezone

from outreach_pipeline.db.pool import get_pool, now, row_to_dict, rows_to_dicts


async def save_draft(contact_id, subject, body, score=0, rag_context="",
                     engagement_id=None, status="draft",
                     promised_resources=None, referenced_resource_ids=None,
                     unverified_promises=None):
    """Save or update an email draft.

    If a draft already exists for the engagement, updates it (upsert).
    Otherwise creates a new row.

    promised_resources: list of catalog entries surfaced during drafting
      (top-N find_sendable_asset results). Shape:
      [{id, title, url, asset_type, summary, match_score}]
    referenced_resource_ids: subset of promised_resources ids actually
      cited in the email body.
    unverified_promises: free-text phrases in the body that look like
      resource offers but don't match the catalog — shown as a warning
      on the Draft Review card.
    """
    import json as _json

    promised_json = _json.dumps(promised_resources or [])
    referenced_json = _json.dumps(referenced_resource_ids or [])
    unverified_json = _json.dumps(unverified_promises or [])

    pool = get_pool()
    if engagement_id:
        existing = await pool.fetchrow(
            "SELECT id FROM drafts WHERE engagement_id = $1 ORDER BY id DESC LIMIT 1",
            engagement_id,
        )
        if existing:
            await pool.execute(
                """UPDATE drafts SET subject=$1, body=$2, rag_context=$3, status=$4,
                   created_at=$5, promised_resources=$6::jsonb,
                   referenced_resource_ids=$7::jsonb, unverified_promises=$8::jsonb
                   WHERE id=$9""",
                subject, body, rag_context, status, now(),
                promised_json, referenced_json, unverified_json, existing["id"],
            )
            return existing["id"]

    row = await pool.fetchrow(
        """INSERT INTO drafts (engagement_id, contact_id, subject, body,
           score_at_send, rag_context, status, created_at,
           promised_resources, referenced_resource_ids, unverified_promises)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8,
                   $9::jsonb, $10::jsonb, $11::jsonb)
           RETURNING id""",
        engagement_id, str(contact_id), subject, body, int(score),
        rag_context, status, now(),
        promised_json, referenced_json, unverified_json,
    )
    return row["id"]


async def get_drafts(engagement_id=None, contact_id=None, status=None, limit=100):
    """Get drafts with optional filters."""
    pool = get_pool()
    conditions = []
    params = []
    idx = 1
    if engagement_id:
        conditions.append(f"engagement_id = ${idx}")
        params.append(engagement_id)
        idx += 1
    if contact_id:
        conditions.append(f"contact_id = ${idx}")
        params.append(str(contact_id))
        idx += 1
    if status:
        conditions.append(f"status = ${idx}")
        params.append(status)
        idx += 1
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    params.append(limit)
    rows = await pool.fetch(
        f"SELECT * FROM drafts {where} ORDER BY created_at DESC LIMIT ${idx}",
        *params,
    )
    return rows_to_dicts(rows)


async def mark_sent(draft_id):
    """Mark a draft as sent by its ID."""
    pool = get_pool()
    await pool.execute("UPDATE drafts SET status='sent', sent_at=$1 WHERE id=$2",
                       now(), draft_id)


async def log_send(contact_id, subject):
    """Record a send."""
    pool = get_pool()
    ts = now()
    today = datetime.now(timezone.utc).date()
    await pool.execute(
        "INSERT INTO send_log (contact_id, subject, date, timestamp) VALUES ($1, $2, $3, $4)",
        str(contact_id), subject, today, ts,
    )
    await pool.execute(
        "INSERT INTO outreach_log (contact_id, subject, timestamp) VALUES ($1, $2, $3)",
        str(contact_id), subject, ts,
    )
