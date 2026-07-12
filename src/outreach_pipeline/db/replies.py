"""Reply tracking and pipeline run operations — async Postgres (asyncpg)."""

from outreach_pipeline.db.pool import get_pool, now, parse_date, row_to_dict, rows_to_dicts


async def save_reply(contact_id, subject, body_preview, direction, timestamp, urgency="", sentiment=""):
    """Save a reply. Auto-calculates response_time_hours from send to reply."""
    pool = get_pool()
    response_hours = None
    if direction == "INCOMING" and timestamp:
        sent_row = await pool.fetchrow(
            "SELECT sent_at FROM drafts WHERE contact_id=$1 AND sent_at IS NOT NULL", str(contact_id),
        )
        if sent_row and sent_row["sent_at"]:
            try:
                from datetime import datetime as _dt, timezone as _tz
                sent_dt = sent_row["sent_at"]  # already datetime from asyncpg
                if isinstance(sent_dt, str):
                    sent_dt = _dt.fromisoformat(sent_dt.replace("Z", "+00:00"))
                reply_dt = _dt.fromisoformat(timestamp.replace("Z", "+00:00")) if isinstance(timestamp, str) else timestamp
                response_hours = round((reply_dt - sent_dt).total_seconds() / 3600, 1)
            except (ValueError, TypeError):
                pass

    # Parse timestamp string to datetime for TIMESTAMPTZ column
    ts_value = timestamp
    if isinstance(timestamp, str) and timestamp:
        try:
            from datetime import datetime as _dt
            ts_value = _dt.fromisoformat(timestamp.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            ts_value = None

    await pool.execute(
        """INSERT INTO replies (contact_id, subject, body_preview, direction, timestamp, urgency, sentiment, response_time_hours, detected_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
        str(contact_id), subject, body_preview, direction, ts_value, urgency, sentiment, response_hours, now(),
    )


async def get_hot_leads():
    """Get contacts with high-urgency replies."""
    pool = get_pool()
    rows = await pool.fetch(
        """SELECT r.contact_id, r.subject, r.body_preview, r.urgency, r.sentiment,
                  r.timestamp, r.response_time_hours,
                  c.score, c.company_context
           FROM replies r JOIN contacts c ON r.contact_id = c.id
           WHERE r.urgency = 'high' ORDER BY r.timestamp DESC""",
    )
    return rows_to_dicts(rows)


# --- Pipeline Runs ---

async def start_run(start_date, end_date):
    """Record a pipeline run start."""
    pool = get_pool()
    row = await pool.fetchrow(
        "INSERT INTO pipeline_runs (start_date, end_date, started_at) VALUES ($1, $2, $3) RETURNING id",
        parse_date(start_date), parse_date(end_date), now(),
    )
    return row["id"]


async def update_run(run_id, **kwargs):
    """Update pipeline run stats."""
    pool = get_pool()
    if not kwargs:
        return
    params = []
    sets = []
    idx = 1
    for k, v in kwargs.items():
        sets.append(f"{k}=${idx}")
        params.append(v)
        idx += 1
    params.append(run_id)
    await pool.execute(
        f"UPDATE pipeline_runs SET {', '.join(sets)} WHERE id=${idx}",
        *params,
    )
