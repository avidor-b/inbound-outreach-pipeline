"""SQLite database for the outreach pipeline.

Single file: data/pipeline.db
Replaces all JSON files with queryable, per-contact operations.
"""
import json
import re
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
            contact_id TEXT PRIMARY KEY REFERENCES contacts(id),
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            score_at_send INTEGER DEFAULT 0,
            status TEXT DEFAULT 'draft',
            revisions INTEGER DEFAULT 0,
            rag_context TEXT DEFAULT '',
            created_at TEXT,
            sent_at TEXT
        );

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
            completed_at TEXT
        );
    """)
    conn.commit()


# --- Contacts ---

def upsert_contact(conn, contact_id, properties, fetched_at=None):
    """Insert or update a contact from HubSpot data."""
    ts = fetched_at or _now()
    props_json = json.dumps(properties) if isinstance(properties, dict) else properties
    conn.execute(
        """INSERT INTO contacts (id, properties, fetched_at)
           VALUES (?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET properties=excluded.properties, fetched_at=excluded.fetched_at""",
        (str(contact_id), props_json, ts),
    )
    conn.commit()


def enrich_contact(conn, contact_id, company_context="", crm_context=""):
    """Add enrichment data to a contact."""
    conn.execute(
        """UPDATE contacts SET company_context=?, crm_context=?, enriched_at=? WHERE id=?""",
        (company_context, crm_context, _now(), str(contact_id)),
    )
    conn.commit()


def score_contact(conn, contact_id, buying_intent, intent_signals, score, score_reasoning, product_match=""):
    """Add scoring data to a contact."""
    signals_json = json.dumps(intent_signals) if isinstance(intent_signals, list) else intent_signals
    conn.execute(
        """UPDATE contacts SET buying_intent=?, intent_signals=?, score=?, score_reasoning=?, product_match=?, scored_at=?
           WHERE id=?""",
        (buying_intent, signals_json, int(score), score_reasoning, product_match, _now(), str(contact_id)),
    )
    conn.commit()


def get_contact(conn, contact_id):
    """Get a single contact with all data."""
    row = conn.execute("SELECT * FROM contacts WHERE id=?", (str(contact_id),)).fetchone()
    return _row_to_dict(row) if row else None


def get_contacts(conn, min_score=0, limit=100, offset=0, scored_only=False, today_only=False, unenriched=False, unscored=False):
    """Get contacts, optionally filtered."""
    conditions = ["score >= ?"]
    params = [min_score]
    if scored_only:
        conditions.append("scored_at IS NOT NULL")
    if today_only:
        conditions.append("date(fetched_at) = date('now')")
    if unenriched:
        conditions.append("enriched_at IS NULL")
    if unscored:
        conditions.append("(scored_at IS NULL OR score = 0)")
    where = "WHERE " + " AND ".join(conditions)
    rows = conn.execute(
        f"SELECT * FROM contacts {where} ORDER BY score DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_contacts(conn):
    """Get contact counts by stage."""
    row = conn.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN enriched_at IS NOT NULL THEN 1 ELSE 0 END) as enriched,
               SUM(CASE WHEN scored_at IS NOT NULL THEN 1 ELSE 0 END) as scored
        FROM contacts
    """).fetchone()
    return dict(row)


# --- Drafts ---

def save_draft(conn, contact_id, subject, body, score=0, rag_context=""):
    """Save or update an email draft."""
    conn.execute(
        """INSERT INTO drafts (contact_id, subject, body, score_at_send, rag_context, status, created_at)
           VALUES (?, ?, ?, ?, ?, 'draft', ?)
           ON CONFLICT(contact_id) DO UPDATE SET subject=excluded.subject, body=excluded.body,
           score_at_send=excluded.score_at_send, rag_context=excluded.rag_context,
           revisions=drafts.revisions+1, created_at=excluded.created_at""",
        (str(contact_id), subject, body, int(score), rag_context, _now()),
    )
    conn.commit()


def mark_sent(conn, contact_id):
    """Mark a draft as sent."""
    conn.execute("UPDATE drafts SET status='sent', sent_at=? WHERE contact_id=?", (_now(), str(contact_id)))
    conn.commit()


def get_drafts(conn, status=None, limit=100):
    """Get drafts, optionally filtered by status."""
    if status:
        rows = conn.execute("SELECT * FROM drafts WHERE status=? ORDER BY score_at_send DESC LIMIT ?", (status, limit)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM drafts ORDER BY score_at_send DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# --- Guardrails ---

def check_opt_out(conn, contact_id):
    """Check if contact is on do-not-contact list."""
    row = conn.execute("SELECT reason FROM do_not_contact WHERE contact_id=?", (str(contact_id),)).fetchone()
    if row:
        return True, f"Contact on do-not-contact list: {row['reason']}"
    return False, ""


def add_opt_out(conn, contact_id, reason=""):
    """Add contact to do-not-contact list."""
    try:
        conn.execute("INSERT INTO do_not_contact VALUES (?, ?, ?)", (str(contact_id), reason, _now()))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def check_duplicate(conn, contact_id, cooldown_days=None, mql_date=None):
    """Check if contact was emailed recently (unless fresh message)."""
    if cooldown_days is None:
        cooldown_days = _get_config_value("duplicate_cooldown_days", 30)
    # Check for existing draft
    draft = conn.execute("SELECT 1 FROM drafts WHERE contact_id=?", (str(contact_id),)).fetchone()
    if draft:
        if mql_date:
            row = conn.execute(
                "SELECT timestamp FROM outreach_log WHERE contact_id=? ORDER BY timestamp DESC LIMIT 1",
                (str(contact_id),),
            ).fetchone()
            if row and mql_date > row["timestamp"]:
                return False, ""
        return True, "Draft already exists"

    # Check outreach log
    row = conn.execute(
        """SELECT timestamp FROM outreach_log WHERE contact_id=?
           AND timestamp > datetime('now', ?) ORDER BY timestamp DESC LIMIT 1""",
        (str(contact_id), f"-{cooldown_days} days"),
    ).fetchone()
    if row:
        if mql_date and mql_date > row["timestamp"]:
            return False, ""
        return True, f"Emailed on {row['timestamp'][:10]} (within {cooldown_days}-day cooldown)"
    return False, ""


def check_send_limit(conn, daily_limit=None):
    """Check daily send count."""
    if daily_limit is None:
        daily_limit = _get_config_value("daily_send_limit", 20)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = conn.execute("SELECT COUNT(*) as cnt FROM send_log WHERE date=?", (today,)).fetchone()
    count = row["cnt"]
    if count >= daily_limit:
        return True, f"Daily limit reached ({count}/{daily_limit})", count
    return False, "", count


def log_send(conn, contact_id, subject):
    """Record a send."""
    ts = _now()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn.execute("INSERT INTO send_log (contact_id, subject, date, timestamp) VALUES (?, ?, ?, ?)",
                 (str(contact_id), subject, today, ts))
    conn.execute("INSERT INTO outreach_log (contact_id, subject, timestamp) VALUES (?, ?, ?)",
                 (str(contact_id), subject, ts))
    conn.commit()


def check_events_contact(conn, contact_id):
    """Check if a contact is seeking a meeting at a trade show or conference.

    Contacts who came in via the events page, or whose message explicitly asks
    to meet at a named event, are handled by the events team — not SDR outreach.

    Uses only definitive signals to avoid false positives from mass marketing
    campaigns (e.g. an MWC email blast sets last_url and recent_conversion_event_name
    for the whole database, including contacts with genuine product inquiries).

    Signals (any one is sufficient to block):
    - hs_analytics_first_url contains '/events' — they started on the events page
    - message text explicitly mentions attending/meeting at a named trade show
    - recent_conversion_event_name contains a specific named event (MWC, GITEX…)
      AND hs_analytics_first_url is also events-related (belt-and-suspenders for
      contacts who genuinely filled in an event registration form)

    Signals deliberately excluded:
    - hs_analytics_last_url — unreliable; set by clicking any marketing email
    - hs_analytics_source_data_1/2 — too weak
    - Generic 'event' keyword in campaign fields without a named show
    """
    row = conn.execute("SELECT properties FROM contacts WHERE id=?", (str(contact_id),)).fetchone()
    if not row:
        return False, ""

    props = row["properties"]
    if isinstance(props, str):
        try:
            props = json.loads(props)
        except (json.JSONDecodeError, TypeError):
            return False, ""

    EVENT_URL_PATH = re.compile(r'/events\b', re.IGNORECASE)

    # Specific named trade shows — generic 'event' word excluded to avoid matching
    # form/campaign names that contain 'event' but aren't trade show registrations.
    NAMED_EVENTS = re.compile(
        r'\b(?:mwc|mobile\s+world\s+congress|gsm|gsma|ces|ibc|gitex|fyuz|pta|ptc|'
        r'capacity\s+(?:middle\s+east|europe|africa|connect)|'
        r'broadband\s+world\s+forum|bwf|european\s+communications|'
        r'africa\s+tech|afrotech|telecoms\s+world)\b',
        re.IGNORECASE,
    )

    # Message text: explicit intent to meet/attend at a named event.
    # Matches "meet at MWC", "meet while we are at MWC", "meeting at MWC",
    # "visit your booth", "MWC tickets", "attend MWC", etc.
    _S = r'(?:mwc|mobile\s+world\s+congress|gitex|ibc|ces|gsma|fyuz)'
    _msg_pat = (
        r'(?:'
        # "meet/meeting at MWC", "meet you at MWC", "meet while [we are] at MWC"
        r'meet(?:ing)?\s+(?:you\s+|us\s+|while\s+(?:we\s+are\s+)?)?at\s+' + _S + r'|'
        # "at [the] MWC booth/event/stand"
        r'at\s+(?:the\s+)?' + _S + r'\s+(?:booth|event|stand|meeting|show)|'
        # "MWC booth/meeting/tickets/stand"
        + _S + r'\s+(?:booth|meeting|tickets?|stand)|'
        # "tickets for MWC"
        r'tickets?\s+for\s+' + _S + r'|'
        # "visit your/the booth"
        r'visit\s+(?:your|the)\s+booth|'
        # "attend [the] MWC/event/conference/show"
        r'attend\s+(?:the\s+)?(?:' + _S + r'|event|conference|show)'
        r')'
    )
    MESSAGE_EVENTS = re.compile(_msg_pat, re.IGNORECASE)

    first_url = props.get("hs_analytics_first_url") or ""
    message = props.get("message") or ""
    conversion = props.get("recent_conversion_event_name") or ""

    # Signal 1: started their journey on the events page
    if EVENT_URL_PATH.search(first_url):
        return True, (
            f"Contact originated from events page (hs_analytics_first_url: \"{first_url[:120]}\"). "
            "This contact is seeking a meeting at a trade show or conference — "
            "route to the events team, do not send SDR outreach."
        )

    # Signal 2: message explicitly mentions attending/meeting at a named event
    if MESSAGE_EVENTS.search(message):
        snippet = message[:200].replace("\n", " ")
        return True, (
            f"Contact's message mentions meeting at a trade show ({snippet!r}). "
            "Route to the events team, do not send SDR outreach."
        )

    # Signal 3: named event in their conversion form AND first_url is also events-related
    # (avoids flagging contacts imported into an event campaign who have product inquiries)
    if NAMED_EVENTS.search(conversion) and EVENT_URL_PATH.search(first_url):
        return True, (
            f"Contact registered via events form ({conversion[:120]!r}) and "
            f"started on events page ({first_url[:120]!r}). "
            "Route to the events team, do not send SDR outreach."
        )

    return False, ""


def check_support_request(conn, contact_id):
    """Check if a contact is submitting a support request for existing equipment.

    Existing customers reporting faults, requesting technical support, or asking
    about SW/firmware updates on equipment they already own should be handled by
    the support team — not SDR outreach.

    Detection (any of the following is sufficient to block):
    - Strong standalone signal: explicit "technical support" request, "inop", processor reset
    - Ownership signal + troubleshooting signal (equipment not working, rebooting, etc.)
    - Ownership signal + SW/firmware update request on existing units
    """
    row = conn.execute("SELECT properties FROM contacts WHERE id=?", (str(contact_id),)).fetchone()
    if not row:
        return False, ""

    props = row["properties"]
    if isinstance(props, str):
        try:
            props = json.loads(props)
        except (json.JSONDecodeError, TypeError):
            return False, ""

    message = props.get("message") or ""
    if not message:
        return False, ""

    # Ownership: already possess equipment.
    # Requires a quantifier ("some", a number) or an explicit verb to avoid false
    # positives on "I have a question / requirement / project".
    _OWNERSHIP = re.compile(
        r'\b(?:'
        r'i\s+have\s+(?:some|a\s+few|\d+)\s'                         # "I have some outdoor radio", "I have 3"
        r'|i\s+have\s+\d+\s+years?\s+(?:of\s+)?using\b'              # "I have 5 years using"
        r'|i(?:\'ve|\s+have)\s+been\s+(?:using|running|operating)\b'  # "I've been using"
        r'|we\s+(?:have|own|use)\s+(?:some|a\s+few|\d+)\s'           # "we have 5 units"
        r'|(?:i|we)\s+(?:purchased|bought|deployed|installed)\b'      # "I purchased", "we deployed"
        r'|currently\s+(?:using|running|deployed|installed)\b'        # "currently using"
        r')',
        re.IGNORECASE,
    )

    # Troubleshooting: equipment fault, can't make it work, random reboots, etc.
    _TROUBLESHOOT = re.compile(
        r'\b(?:'
        r'can\s*not\s+get\s+(?:\w+\s+){0,3}(?:to\s+)?(?:work|connect|link|sync)\b'
        r"|can't\s+get\s+(?:\w+\s+){0,3}(?:to\s+)?(?:work|connect|link|sync)\b"
        r'|(?:tried|trying)\s+different\b'
        r'|reboot(?:s|ing|ed)?\s+randomly\b'
        r'|randomly\s+reboot(?:s|ing|ed)?\b'
        r'|not\s+(?:working|connecting|syncing|linking|functioning)\b'
        r'|(?:equipment|unit|device|radio|link)\s+(?:is\s+)?(?:down|faulty|broken|offline)\b'
        r')',
        re.IGNORECASE,
    )

    # SW/firmware update on existing equipment (maintenance, not new purchase)
    _SW_UPDATE = re.compile(
        r'\b(?:'
        r'update\s+(?:my|our|all\s+(?:my|our))\s+\w+\s+(?:sw|software|firmware|versions?)\b'
        r'|update\s+(?:sw|firmware|software)\s+(?:versions?|build)\b'
        r'|(?:sw|firmware|software)\s+(?:upgrade|update)\b'
        r'|recommended\s+to\s+update\b'
        r')',
        re.IGNORECASE,
    )

    # Strong standalone signals — explicit support request, no ownership context needed
    _STRONG_SUPPORT = re.compile(
        r'\b(?:'
        r'technical\s+support\b'
        r'|contact\s+(?:your\s+|our\s+)?support\b'
        r'|(?:inop|non-?functional)\b'
        r'|(?:warranty\s+(?:claim|replacement|repair)|rma)\b'
        r'|processor\s+reset\b'
        r'|\u062a\u0635\u0641\u064a\u0631\b'  # تصفير — Arabic "reset/zero-out"
        r')',
        re.IGNORECASE,
    )

    has_ownership = bool(_OWNERSHIP.search(message))
    has_troubleshoot = bool(_TROUBLESHOOT.search(message))
    has_sw_update = bool(_SW_UPDATE.search(message))
    standalone = bool(_STRONG_SUPPORT.search(message))

    if standalone or (has_ownership and (has_troubleshoot or has_sw_update)):
        snippet = message[:200].replace("\n", " ")
        return True, (
            f"Contact's message indicates a support request for existing equipment ({snippet!r}). "
            "Route to the technical support team. Do not draft SDR outreach."
        )

    return False, ""


def check_scrape_blocklist(conn, domain):
    """Check if domain is blocked."""
    domain = domain.lower().strip()
    row = conn.execute("SELECT reason FROM scrape_blocklist WHERE ? LIKE '%' || domain", (domain,)).fetchone()
    if row:
        return True, f"Domain blocked: {row['reason']}"
    return False, ""


def add_scrape_block(conn, domain, reason=""):
    """Add domain to scrape blocklist."""
    try:
        conn.execute("INSERT INTO scrape_blocklist VALUES (?, ?, ?)", (domain.lower().strip(), reason, _now()))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


# --- Replies ---

def save_reply(conn, contact_id, subject, body_preview, direction, timestamp, urgency="", sentiment=""):
    """Save a reply. Auto-calculates response_time_hours from send to reply."""
    response_hours = None
    if direction == "INCOMING" and timestamp:
        sent_row = conn.execute(
            "SELECT sent_at FROM drafts WHERE contact_id=? AND sent_at IS NOT NULL", (str(contact_id),)
        ).fetchone()
        if sent_row and sent_row["sent_at"]:
            try:
                from datetime import datetime as _dt
                sent_dt = _dt.fromisoformat(sent_row["sent_at"].replace("Z", "+00:00"))
                reply_dt = _dt.fromisoformat(timestamp.replace("Z", "+00:00"))
                response_hours = round((reply_dt - sent_dt).total_seconds() / 3600, 1)
            except (ValueError, TypeError):
                pass

    conn.execute(
        """INSERT INTO replies (contact_id, subject, body_preview, direction, timestamp, urgency, sentiment, response_time_hours, detected_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (str(contact_id), subject, body_preview, direction, timestamp, urgency, sentiment, response_hours, _now()),
    )
    conn.commit()


def get_hot_leads(conn):
    """Get contacts with high-urgency replies."""
    rows = conn.execute(
        """SELECT r.contact_id, r.subject, r.body_preview, r.urgency, r.sentiment,
                  r.timestamp, r.response_time_hours,
                  c.score, c.company_context
           FROM replies r JOIN contacts c ON r.contact_id = c.id
           WHERE r.urgency = 'high' ORDER BY r.timestamp DESC""",
    ).fetchall()
    return [dict(r) for r in rows]


# --- Pipeline Runs ---

def start_run(conn, start_date, end_date):
    """Record a pipeline run start."""
    conn.execute("INSERT INTO pipeline_runs (start_date, end_date, started_at) VALUES (?, ?, ?)",
                 (start_date, end_date, _now()))
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def update_run(conn, run_id, **kwargs):
    """Update pipeline run stats."""
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [run_id]
    conn.execute(f"UPDATE pipeline_runs SET {sets} WHERE id=?", vals)
    conn.commit()


# --- Helpers ---

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
    """Convert a contact Row to a dict with parsed JSON fields."""
    d = dict(row)
    for field in ("properties", "intent_signals"):
        if field in d and isinstance(d[field], str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d
