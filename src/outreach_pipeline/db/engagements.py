"""Engagement CRUD — async Postgres (asyncpg)."""

from outreach_pipeline.db.pool import get_pool, now, row_to_dict, rows_to_dicts

VALID_TRANSITIONS = {
    "open":               {"scoring", "blocked", "closed"},
    "scoring":            {"drafting", "blocked", "closed"},
    "drafting":           {"pending_approval", "sdr_handoff", "blocked", "closed"},
    "pending_approval":   {"approved", "drafting", "sdr_handoff", "closed"},
    "approved":           {"sent", "drafting", "closed"},
    "sent":               {"closed"},
    "blocked":            {"open", "closed"},
    "sdr_handoff":        {"closed", "drafting"},
    "closed":             set(),
}


async def create_engagement(
    contact_id, kind, status="open",
    block_code="", block_reason="", block_terminal=False,
    primary_product="", retarget_of=None,
):
    """Create an engagement. Returns engagement ID."""
    pool = get_pool()
    row = await pool.fetchrow(
        """INSERT INTO engagements
           (contact_id, kind, status, block_code, block_reason, block_terminal,
            primary_product, retarget_of, opened_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
           RETURNING id""",
        str(contact_id), kind, status, block_code, block_reason,
        block_terminal, primary_product, retarget_of, now(),
    )
    return row["id"]


async def get_engagement(engagement_id):
    """Get an engagement by ID."""
    try:
        eid = int(engagement_id)
    except (ValueError, TypeError):
        return {}
    if eid > 2_147_483_647 or eid < 0:
        return {}  # likely a contact ID (long number) passed instead of engagement ID
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM engagements WHERE id=$1", eid)
    return row_to_dict(row)


async def update_status(engagement_id, new_status, block_code="", block_reason=""):
    """Update engagement status with transition validation.

    Terminal blocked engagements (support/events) cannot reopen.
    Policy blocked engagements can reopen when condition clears.
    """
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT status, block_terminal FROM engagements WHERE id=$1", engagement_id,
    )
    if not row:
        raise ValueError(f"Engagement {engagement_id} not found")

    current = row["status"]
    terminal = row["block_terminal"]
    allowed = VALID_TRANSITIONS.get(current, set())

    if new_status not in allowed:
        raise ValueError(
            f"Invalid transition: {current} → {new_status}. "
            f"Allowed: {allowed or 'none (terminal)'}"
        )

    # Terminal blocked cannot reopen
    if current == "blocked" and new_status == "open" and terminal:
        raise ValueError(
            f"Cannot reopen terminal-blocked engagement {engagement_id}. "
            "Reclassify the engagement kind to unblock."
        )

    params = []
    idx = 1

    set_clauses = [f"status=${idx}"]
    params.append(new_status)
    idx += 1

    if new_status == "blocked":
        set_clauses.append(f"block_code=${idx}")
        params.append(block_code)
        idx += 1
        set_clauses.append(f"block_reason=${idx}")
        params.append(block_reason)
        idx += 1
    elif new_status == "open":
        set_clauses.append("block_code=''")
        set_clauses.append("block_reason=''")
    elif new_status == "closed":
        set_clauses.append(f"closed_at=${idx}")
        params.append(now())
        idx += 1

    params.append(engagement_id)
    sets = ", ".join(set_clauses)
    await pool.execute(f"UPDATE engagements SET {sets} WHERE id=${idx}", *params)


async def score_engagement(engagement_id, score, score_reasoning="", primary_product=""):
    """Set score on an engagement."""
    pool = get_pool()
    await pool.execute(
        "UPDATE engagements SET score=$1, score_reasoning=$2, primary_product=$3 WHERE id=$4",
        score, score_reasoning, primary_product, engagement_id,
    )


async def add_submission(engagement_id, submission_id):
    """Link a submission to an engagement."""
    pool = get_pool()
    await pool.execute(
        "INSERT INTO engagement_submissions (engagement_id, submission_id) VALUES ($1, $2)",
        engagement_id, submission_id,
    )


async def get_submissions(engagement_id):
    """Get all submissions linked to an engagement."""
    pool = get_pool()
    rows = await pool.fetch(
        """SELECT s.* FROM submissions s
           JOIN engagement_submissions es ON s.id = es.submission_id
           WHERE es.engagement_id = $1
           ORDER BY s.mql_date ASC, s.id ASC""",
        engagement_id,
    )
    return rows_to_dicts(rows)


async def list_engagements(contact_id=None, kind=None, status=None, limit=100):
    """List engagements with optional filters."""
    pool = get_pool()
    conditions = []
    params = []
    idx = 1
    if contact_id:
        conditions.append(f"contact_id = ${idx}")
        params.append(str(contact_id))
        idx += 1
    if kind:
        conditions.append(f"kind = ${idx}")
        params.append(kind)
        idx += 1
    if status:
        conditions.append(f"status = ${idx}")
        params.append(status)
        idx += 1
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    params.append(limit)
    rows = await pool.fetch(
        f"SELECT * FROM engagements {where} ORDER BY opened_at DESC LIMIT ${idx}",
        *params,
    )
    return rows_to_dicts(rows)


async def close_superseded_retargets(contact_id):
    """Close open retarget engagements when a new submission arrives.

    A retarget is only valid while the contact is silent. New submission
    means the retarget is obsolete.
    """
    pool = get_pool()
    await pool.execute(
        """UPDATE engagements SET status='closed',
           closed_at=$1, closed_reason='superseded_by_new_submission'
           WHERE contact_id=$2 AND kind='retarget' AND status != 'closed'""",
        now(), str(contact_id),
    )
