"""Submission CRUD — async Postgres (asyncpg)."""
import hashlib
import json

from outreach_pipeline.db.pool import get_pool, now, parse_date, row_to_dict, rows_to_dicts


def _compute_hash(message: str) -> str | None:
    """Compute normalized message hash. Returns None if message is empty."""
    if not message or not message.strip():
        return None
    normalized = " ".join(message.lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest()


async def create_submission(
    contact_id, message="", form_name="", form_id="",
    page_url="", page_title="", mql_date=None,
    properties_snapshot=None, source="hubspot",
    message_translation="",
):
    """Create a submission. Returns submission ID."""
    pool = get_pool()
    msg_hash = _compute_hash(message)
    props = json.dumps(properties_snapshot) if isinstance(properties_snapshot, dict) else (properties_snapshot or "{}")
    row = await pool.fetchrow(
        """INSERT INTO submissions
           (contact_id, message, message_translation, message_hash,
            form_name, form_id, page_url,
            page_title, mql_date, properties_snapshot, source, captured_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
           RETURNING id""",
        str(contact_id), message, message_translation, msg_hash,
        form_name, form_id, page_url,
        page_title, parse_date(mql_date), props, source, now(),
    )
    return row["id"]


async def get_submission(submission_id):
    """Get a submission by ID."""
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM submissions WHERE id=$1", submission_id)
    return row_to_dict(row)


async def list_submissions(contact_id=None, include_migration=False):
    """List submissions, optionally filtered by contact. Excludes source=migration by default."""
    pool = get_pool()
    conditions = []
    params = []
    idx = 1
    if contact_id:
        conditions.append(f"contact_id = ${idx}")
        params.append(str(contact_id))
        idx += 1
    if not include_migration:
        conditions.append("source != 'migration'")
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    rows = await pool.fetch(
        f"SELECT * FROM submissions {where} ORDER BY mql_date ASC, id ASC",
        *params,
    )
    return rows_to_dicts(rows)


async def is_duplicate(contact_id, message="", form_id="", mql_date=None):
    """Check if a submission is a duplicate.

    Dedup rules:
    - Message present: contact_id + form_id + message_hash — date is
      ignored so a HubSpot re-fetch that assigns a fresh timestamp to
      the same form submission doesn't slip through as a "new" row.
    - No message but form_id present: contact_id + form_id + date —
      messageless events (content downloads, page visits) can
      legitimately repeat on different days, so the date keeps them
      distinct.
    - No message AND no form_id: never a dup — not enough data to
      dedup against.
    """
    pool = get_pool()
    mql = parse_date(mql_date)
    if message and message.strip():
        msg_hash = _compute_hash(message)
        row = await pool.fetchrow(
            """SELECT 1 FROM submissions
               WHERE contact_id=$1 AND form_id=$2 AND message_hash=$3""",
            str(contact_id), form_id, msg_hash,
        )
    elif form_id:
        # No message but known form — the submission is identified by
        # (contact, form, date). A prior ingest may have captured the
        # message already; don't clobber it with an empty duplicate.
        row = await pool.fetchrow(
            """SELECT 1 FROM submissions
               WHERE contact_id=$1 AND form_id=$2 AND mql_date = $3""",
            str(contact_id), form_id, mql,
        )
    else:
        # No message AND no form_id — not enough info to dedup
        return False
    return row is not None


async def classify_submission(
    submission_id, intent_type="", product_family="",
    product_match="", intent_signals=None, support_flag=False,
    event_flag=False, classification_reasoning="",
):
    """Update classification fields on a submission."""
    pool = get_pool()
    signals_json = json.dumps(intent_signals) if intent_signals else "[]"
    await pool.execute(
        """UPDATE submissions SET
           intent_type=$1, product_family=$2, product_match=$3,
           intent_signals=$4, support_flag=$5, event_flag=$6,
           classification_reasoning=$7, classified_at=$8
           WHERE id=$9""",
        intent_type, product_family, product_match, signals_json,
        support_flag, event_flag, classification_reasoning,
        now(), submission_id,
    )
