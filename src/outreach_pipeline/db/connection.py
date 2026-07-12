"""Database connection and schema management."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("data/pipeline.db")


def get_db(db_path=None):
    """Get a database connection, creating tables if needed."""
    path = db_path or DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _create_tables(conn)
    return conn


def _create_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS contacts (
            id TEXT PRIMARY KEY,
            properties TEXT NOT NULL DEFAULT '{}',
            company_context TEXT DEFAULT '',
            crm_context TEXT DEFAULT '',
            buying_intent TEXT DEFAULT '',
            intent_signals TEXT DEFAULT '[]',
            score INTEGER DEFAULT 0,
            score_reasoning TEXT DEFAULT '',
            product_match TEXT DEFAULT '',
            fetched_at TEXT,
            enriched_at TEXT,
            scored_at TEXT
        );

        CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            engagement_id INTEGER REFERENCES engagements(id),
            contact_id TEXT NOT NULL REFERENCES contacts(id),
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            score_at_send INTEGER DEFAULT 0,
            status TEXT DEFAULT 'draft',
            rag_context TEXT DEFAULT '',
            created_at TEXT,
            sent_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_drafts_engagement ON drafts(engagement_id);
        CREATE INDEX IF NOT EXISTS idx_drafts_contact ON drafts(contact_id);

        CREATE TABLE IF NOT EXISTS replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id TEXT REFERENCES contacts(id),
            subject TEXT DEFAULT '',
            body_preview TEXT DEFAULT '',
            direction TEXT DEFAULT '',
            timestamp TEXT,
            urgency TEXT DEFAULT '',
            sentiment TEXT DEFAULT '',
            response_time_hours REAL DEFAULT NULL,
            detected_at TEXT
        );

        CREATE TABLE IF NOT EXISTS outreach_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id TEXT REFERENCES contacts(id),
            subject TEXT DEFAULT '',
            timestamp TEXT,
            mql_date TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS send_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id TEXT REFERENCES contacts(id),
            subject TEXT DEFAULT '',
            date TEXT,
            timestamp TEXT
        );

        CREATE TABLE IF NOT EXISTS do_not_contact (
            contact_id TEXT PRIMARY KEY,
            reason TEXT DEFAULT '',
            added_at TEXT
        );

        CREATE TABLE IF NOT EXISTS scrape_blocklist (
            domain TEXT PRIMARY KEY,
            reason TEXT DEFAULT '',
            added_at TEXT
        );

        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_date TEXT,
            end_date TEXT,
            contacts_fetched INTEGER DEFAULT 0,
            contacts_enriched INTEGER DEFAULT 0,
            contacts_scored INTEGER DEFAULT 0,
            emails_drafted INTEGER DEFAULT 0,
            emails_sent INTEGER DEFAULT 0,
            hot_leads INTEGER DEFAULT 0,
            started_at TEXT,
            completed_at TEXT,
            summary TEXT DEFAULT ''
        );
        -- Submissions (3-layer model)
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id TEXT NOT NULL REFERENCES contacts(id),
            message TEXT DEFAULT '',
            message_hash TEXT,
            form_name TEXT DEFAULT '',
            form_id TEXT DEFAULT '',
            page_url TEXT DEFAULT '',
            page_title TEXT DEFAULT '',
            mql_date TEXT,
            properties_snapshot TEXT DEFAULT '{}',
            source TEXT DEFAULT 'hubspot',
            intent_type TEXT DEFAULT '',
            intent_signals TEXT DEFAULT '[]',
            product_match TEXT DEFAULT '',
            product_family TEXT DEFAULT '',
            support_flag BOOLEAN DEFAULT 0,
            event_flag BOOLEAN DEFAULT 0,
            classification_reasoning TEXT DEFAULT '',
            classified_at TEXT,
            captured_at TEXT
        );

        -- Engagements (3-layer model)
        CREATE TABLE IF NOT EXISTS engagements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id TEXT NOT NULL REFERENCES contacts(id),
            kind TEXT NOT NULL DEFAULT 'sales',
            status TEXT DEFAULT 'open',
            block_code TEXT DEFAULT '',
            block_reason TEXT DEFAULT '',
            block_terminal BOOLEAN DEFAULT 0,
            score INTEGER DEFAULT 0,
            score_reasoning TEXT DEFAULT '',
            priority TEXT DEFAULT 'normal',
            primary_product TEXT DEFAULT '',
            retarget_of INTEGER REFERENCES engagements(id),
            opened_at TEXT,
            closed_at TEXT,
            closed_reason TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS engagement_submissions (
            engagement_id INTEGER REFERENCES engagements(id),
            submission_id INTEGER REFERENCES submissions(id),
            PRIMARY KEY (engagement_id, submission_id)
        );
    """)
    conn.commit()

    # Create indexes (IF NOT EXISTS handles idempotency)
    for idx in [
        "CREATE INDEX IF NOT EXISTS idx_submissions_contact_date ON submissions(contact_id, mql_date)",
        "CREATE INDEX IF NOT EXISTS idx_submissions_hash ON submissions(contact_id, message_hash, form_id)",
        "CREATE INDEX IF NOT EXISTS idx_submissions_source ON submissions(source)",
        "CREATE INDEX IF NOT EXISTS idx_engagements_contact ON engagements(contact_id, status, kind)",
        "CREATE INDEX IF NOT EXISTS idx_engagement_submissions_reverse ON engagement_submissions(submission_id)",
    ]:
        conn.execute(idx)

    # Partial unique index for retarget_of (SQLite supports this)
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_engagements_retarget "
            "ON engagements(retarget_of) WHERE retarget_of IS NOT NULL"
        )
    except Exception:
        pass  # already exists or not supported

    conn.commit()

    # --- Migrations (safe to re-run) ---
    for migration in [
        "ALTER TABLE pipeline_runs ADD COLUMN summary TEXT DEFAULT ''",
        "ALTER TABLE contacts ADD COLUMN enrichment_confidence TEXT DEFAULT ''",
        "ALTER TABLE engagements ADD COLUMN score_confidence TEXT DEFAULT ''",
    ]:
        try:
            conn.execute(migration)
            conn.commit()
        except Exception:
            pass  # column already exists


def _now():
    return datetime.now(timezone.utc).isoformat()


def _get_config_value(key, default):
    """Read a value from config.yaml, with fallback to default."""
    try:
        from outreach_pipeline.config import load_config
        return load_config().get(key, default)
    except Exception:
        return default


def _row_to_dict(row):
    """Convert a contact Row to a dict with parsed JSON fields.

    Replaces None with '' to avoid null values in LLM tool responses
    (Gemini tool-calling can error on nulls).
    """
    d = dict(row)
    for field in ("properties", "intent_signals"):
        if field in d and isinstance(d[field], str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    # Replace None values with empty strings
    for k, v in d.items():
        if v is None:
            d[k] = ""
        elif isinstance(v, dict):
            d[k] = {dk: "" if dv is None else dv for dk, dv in v.items()}
    return d
