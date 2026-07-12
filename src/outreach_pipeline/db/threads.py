"""Thread state management — async Postgres (asyncpg).

threads, messages, artifacts, approvals, users.

Artifact lifecycle state machine:
    created -> pending_approval -> approved -> sent
                                -> rejected
"""
import uuid

from outreach_pipeline.db.pool import get_pool, now, row_to_dict, rows_to_dicts


# Valid status transitions for artifacts
_VALID_TRANSITIONS = {
    "created": {"pending_approval"},
    "pending_approval": {"approved", "rejected"},
    "approved": {"sent"},
    "rejected": {"pending_approval"},  # allow re-draft -> re-submit
    "sent": set(),
}


# --- Users ---

async def create_user(user_id, email, name, role="viewer", password_hash=""):
    """Create a user. Returns True on success, False if already exists."""
    pool = get_pool()
    try:
        await pool.execute(
            "INSERT INTO users (id, email, name, role, password_hash, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            user_id, email, name, role, password_hash, now(),
        )
        return True
    except Exception:
        return False


async def get_user(user_id):
    """Get a user by ID."""
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM users WHERE id=$1", user_id)
    return row_to_dict(row)


# --- Threads ---

async def create_thread(title, user_id):
    """Create a thread. Returns thread ID."""
    pool = get_pool()
    thread_id = str(uuid.uuid4())[:8]
    ts = now()
    await pool.execute(
        "INSERT INTO threads (id, title, user_id, status, created_at, updated_at) VALUES ($1, $2, $3, 'active', $4, $5)",
        thread_id, title, user_id, ts, ts,
    )
    return thread_id


async def get_thread(thread_id):
    """Get a thread by ID."""
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM threads WHERE id=$1", thread_id)
    return row_to_dict(row)


async def list_threads(user_id=None, status="active"):
    """List threads, optionally filtered by user and status."""
    pool = get_pool()
    if user_id:
        rows = await pool.fetch(
            "SELECT * FROM threads WHERE user_id=$1 AND status=$2 ORDER BY updated_at DESC",
            user_id, status,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM threads WHERE status=$1 ORDER BY updated_at DESC",
            status,
        )
    return rows_to_dicts(rows)


async def archive_thread(thread_id):
    """Archive a thread."""
    pool = get_pool()
    await pool.execute("UPDATE threads SET status='archived', updated_at=$1 WHERE id=$2", now(), thread_id)


# --- Messages ---

async def add_message(thread_id, role, content="", tool_name=None, tool_args=None):
    """Add a message to a thread. Returns message ID."""
    pool = get_pool()
    ts = now()
    row = await pool.fetchrow(
        "INSERT INTO messages (thread_id, role, content, tool_name, tool_args, created_at) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
        thread_id, role, content, tool_name, tool_args, ts,
    )
    # Update thread's updated_at
    await pool.execute("UPDATE threads SET updated_at=$1 WHERE id=$2", ts, thread_id)
    return row["id"]


async def list_messages(thread_id):
    """List messages in a thread, ordered by creation time."""
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT * FROM messages WHERE thread_id=$1 ORDER BY id ASC",
        thread_id,
    )
    return rows_to_dicts(rows)


# --- Artifacts ---

async def create_artifact(thread_id, artifact_type, contact_id, data):
    """Create an artifact with status 'created'. Returns artifact ID."""
    import json
    pool = get_pool()
    data_json = json.dumps(data) if isinstance(data, dict) else data
    row = await pool.fetchrow(
        "INSERT INTO artifacts (thread_id, type, contact_id, data, status, created_at) VALUES ($1, $2, $3, $4::jsonb, 'created', $5) RETURNING id",
        thread_id, artifact_type, contact_id, data_json, now(),
    )
    return row["id"]


async def get_artifact(artifact_id):
    """Get an artifact by ID."""
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM artifacts WHERE id=$1", artifact_id)
    return row_to_dict(row)


async def update_artifact_status(artifact_id, new_status):
    """Update artifact status with transition validation.

    Valid transitions:
        created -> pending_approval
        pending_approval -> approved | rejected
        approved -> sent
        rejected -> pending_approval (re-draft)
    """
    pool = get_pool()
    row = await pool.fetchrow("SELECT status FROM artifacts WHERE id=$1", artifact_id)
    if not row:
        raise ValueError(f"Artifact {artifact_id} not found")

    current = row["status"]
    allowed = _VALID_TRANSITIONS.get(current, set())
    if new_status not in allowed:
        raise ValueError(
            f"Invalid transition: {current} → {new_status}. "
            f"Allowed from '{current}': {allowed or 'none'}"
        )

    await pool.execute("UPDATE artifacts SET status=$1 WHERE id=$2", new_status, artifact_id)


async def list_artifacts(thread_id, status=None):
    """List artifacts for a thread, optionally filtered by status."""
    pool = get_pool()
    if status:
        rows = await pool.fetch(
            "SELECT * FROM artifacts WHERE thread_id=$1 AND status=$2 ORDER BY id ASC",
            thread_id, status,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM artifacts WHERE thread_id=$1 ORDER BY id ASC",
            thread_id,
        )
    return rows_to_dicts(rows)


# --- Approvals ---

async def record_approval(artifact_id, user_id, action, feedback=None):
    """Record an approval or rejection. Updates artifact status accordingly.

    action: 'approve' or 'reject'
    """
    pool = get_pool()
    # Record the decision in the audit log
    await pool.execute(
        "INSERT INTO approvals (artifact_id, user_id, action, feedback, created_at) VALUES ($1, $2, $3, $4, $5)",
        artifact_id, user_id, action, feedback or "", now(),
    )

    # Update artifact status
    new_status = "approved" if action == "approve" else "rejected"
    await update_artifact_status(artifact_id, new_status)


async def get_approvals(artifact_id):
    """Get approval history for an artifact."""
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT * FROM approvals WHERE artifact_id=$1 ORDER BY id ASC",
        artifact_id,
    )
    return rows_to_dicts(rows)
