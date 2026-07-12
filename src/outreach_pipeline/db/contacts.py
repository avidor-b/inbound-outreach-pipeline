"""Contact CRUD operations — async Postgres (asyncpg)."""
import json
import re

from outreach_pipeline.db.pool import get_pool, now, parse_date, row_to_dict, rows_to_dicts

# PII fields stripped before storing. Fetched fresh from HubSpot on demand.
PII_FIELDS = {"firstname", "lastname", "email", "phone", "mobilephone", "jobtitle"}

# Freemail domains — not useful for company lookup
_FREEMAIL = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "aol.com", "icloud.com", "mail.com", "protonmail.com",
    "ymail.com", "live.com", "msn.com",
    # Reserved documentation domains must never trigger live enrichment.
    "example.com", "example.net", "example.org",
}

# PII patterns for message redaction
_PII_EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
_PII_PHONE_RE = re.compile(r'(?:\+\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{2,4}[-.\s]?\d{2,4}')
_PII_NAME_CONTEXT_RE = re.compile(
    r'((?:[Mm]y\s+name\s+is|[Ii]\s+am|[Tt]his\s+is)\s+)'
    r'([A-Z][a-z]+\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)'
    r'(?=\s*[,.\s]|$)',
)
_PII_SIGNOFF_RE = re.compile(
    r'(?:(?:[Tt]hank(?:s| you)[.!,]?\s*)'
    r'|(?:[Bb]est|[Rr]egards|[Cc]heers)[,.]?\s*)'
    r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)'
    r'\s*$',
)
# Credential patterns — passwords, tokens, API keys shared in messages
# Matches: "password is X", "password: X", "password=X", "pwd: X", "api key: X"
_PII_CREDENTIAL_RE = re.compile(
    r'\b(password|passwd|pwd|passcode|token|api[\s_-]?key|secret|credentials?|auth[\s_-]?code)'
    r'(?:\s+is|\s*[=:])\s*'
    r'(\S+)',
    re.IGNORECASE,
)


def _strip_pii(properties):
    """Remove PII fields from a properties dict."""
    if isinstance(properties, str):
        try:
            properties = json.loads(properties)
        except (json.JSONDecodeError, TypeError):
            return properties

    email = properties.get("email", "")
    stripped = {k: v for k, v in properties.items() if k not in PII_FIELDS}

    if email and "@" in email:
        domain = email.split("@", 1)[1].lower()
        stripped["has_email"] = True
        if domain not in _FREEMAIL:
            stripped["email_domain"] = domain

    if "message" in stripped and stripped["message"]:
        stripped["message"] = _redact_message_pii(stripped["message"])

    return stripped


def _redact_message_pii(message: str) -> str:
    """Redact PII patterns from message text.

    Order matters: credentials first (before phone/email patterns
    can mangle the value), then emails, phones, names.
    """
    message = _PII_CREDENTIAL_RE.sub(r"\1 [REDACTED]", message)
    message = _PII_EMAIL_RE.sub("[email redacted]", message)
    message = _PII_PHONE_RE.sub("[phone redacted]", message)
    message = _PII_NAME_CONTEXT_RE.sub(r"\1[name redacted]", message)
    message = _PII_SIGNOFF_RE.sub(lambda m: m.group(0).replace(m.group(1), "[name redacted]"), message)
    return message


async def upsert_contact(contact_id, properties, fetched_at=None):
    """Insert or update a contact. PII fields stripped. Resets enrichment on re-fetch."""
    pool = get_pool()
    ts = fetched_at or now()
    # asyncpg requires datetime objects for TIMESTAMPTZ columns
    if isinstance(ts, str):
        from datetime import datetime as _dt, timezone as _tz
        ts = _dt.fromisoformat(ts.replace("Z", "+00:00"))
    if isinstance(properties, dict):
        properties = _strip_pii(properties)
    props_json = json.dumps(properties) if isinstance(properties, dict) else properties
    await pool.execute(
        """INSERT INTO contacts (id, properties, fetched_at)
           VALUES ($1, $2::jsonb, $3)
           ON CONFLICT(id) DO UPDATE SET
               properties=EXCLUDED.properties,
               fetched_at=EXCLUDED.fetched_at,
               company_context='',
               crm_context='',
               buying_intent='',
               intent_signals='[]'::jsonb,
               score=0,
               score_reasoning='',
               product_match='',
               enriched_at=NULL,
               scored_at=NULL""",
        str(contact_id), props_json, ts,
    )


async def enrich_contact(contact_id, company_context="", crm_context="", enrichment_confidence=""):
    """Add enrichment data to a contact."""
    pool = get_pool()
    await pool.execute(
        "UPDATE contacts SET company_context=$1, crm_context=$2, enrichment_confidence=$3, enriched_at=$4 WHERE id=$5",
        company_context, crm_context, enrichment_confidence, now(), str(contact_id),
    )


async def score_contact(contact_id, buying_intent, intent_signals, score, score_reasoning, product_match=""):
    """Add scoring data to a contact."""
    pool = get_pool()
    signals_json = json.dumps(intent_signals) if isinstance(intent_signals, list) else intent_signals
    await pool.execute(
        """UPDATE contacts SET buying_intent=$1, intent_signals=$2::jsonb, score=$3,
           score_reasoning=$4, product_match=$5, scored_at=$6 WHERE id=$7""",
        buying_intent, signals_json, int(score), score_reasoning, product_match, now(), str(contact_id),
    )


async def get_contact(contact_id):
    """Get a single contact."""
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM contacts WHERE id=$1", str(contact_id))
    return row_to_dict(row)


async def search_contacts(company: str = "", limit: int = 10) -> list[dict]:
    """Search contacts by company name (case-insensitive partial match)."""
    pool = get_pool()
    if company:
        rows = await pool.fetch(
            "SELECT * FROM contacts WHERE properties->>'company' ILIKE $1 ORDER BY fetched_at DESC NULLS LAST LIMIT $2",
            f"%{company}%", limit,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM contacts ORDER BY fetched_at DESC NULLS LAST LIMIT $1",
            limit,
        )
    return rows_to_dicts(rows)


async def get_contacts(min_score=0, limit=100, offset=0, scored_only=False,
                       today_only=False, unenriched=False, unscored=False,
                       date=None, after=None, before=None,
                       fetched_date=None, fetched_after=None, fetched_before=None,
                       no_reply=False, has_draft=None):
    """Get contacts, optionally filtered. Date filters use MQL date in report timezone."""
    pool = get_pool()
    # Cast MQL timestamp to the report timezone before extracting date.
    # HubSpot stores UTC but users think in their local TZ (e.g. America/New_York).
    # Without this, a contact at 2026-03-31T03:00Z (11PM ET on March 30) shows as March 31.
    from outreach_pipeline.db.pool import get_config_value
    _tz = get_config_value("report_timezone", "America/New_York")
    _MQL = f"(properties->>'hs_v2_date_entered_marketingqualifiedlead')::timestamptz AT TIME ZONE '{_tz}'"
    conditions = ["score >= $1"]
    params: list = [min_score]
    idx = 2

    if scored_only:
        conditions.append("scored_at IS NOT NULL")
    if date:
        conditions.append(f"({_MQL})::date = ${idx}")
        params.append(parse_date(date))
        idx += 1
    if after:
        conditions.append(f"({_MQL})::date >= ${idx}")
        params.append(parse_date(after))
        idx += 1
    if before:
        conditions.append(f"({_MQL})::date <= ${idx}")
        params.append(parse_date(before))
        idx += 1
    if fetched_date:
        conditions.append(f"fetched_at::date = ${idx}")
        params.append(parse_date(fetched_date))
        idx += 1
    if fetched_after:
        conditions.append(f"fetched_at::date >= ${idx}")
        params.append(parse_date(fetched_after))
        idx += 1
    if fetched_before:
        conditions.append(f"fetched_at::date <= ${idx}")
        params.append(parse_date(fetched_before))
        idx += 1
    if today_only:
        conditions.append("fetched_at::date = CURRENT_DATE")
    if unenriched:
        conditions.append("enriched_at IS NULL")
    if unscored:
        conditions.append("(scored_at IS NULL OR score = 0)")
    if no_reply:
        conditions.append(
            "id IN (SELECT contact_id FROM drafts WHERE status='sent') "
            "AND id NOT IN (SELECT contact_id FROM replies WHERE direction='INCOMING')"
        )
    if has_draft is True:
        conditions.append("id IN (SELECT contact_id FROM drafts)")
    elif has_draft is False:
        conditions.append("id NOT IN (SELECT contact_id FROM drafts)")

    where = "WHERE " + " AND ".join(conditions)
    rows = await pool.fetch(
        f"SELECT * FROM contacts {where} ORDER BY score DESC LIMIT ${idx} OFFSET ${idx+1}",
        *params, limit, offset,
    )
    return rows_to_dicts(rows)


async def count_contacts():
    """Get contact counts by stage."""
    pool = get_pool()
    row = await pool.fetchrow("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN enriched_at IS NOT NULL THEN 1 ELSE 0 END) as enriched,
               SUM(CASE WHEN scored_at IS NOT NULL THEN 1 ELSE 0 END) as scored
        FROM contacts
    """)
    return row_to_dict(row)
