"""Guardrail checks — async Postgres (asyncpg).

opt-out, duplicate, send limit, events/support checks, scrape blocklist.
"""
import json
import re
from datetime import datetime, timezone

from outreach_pipeline.db.pool import get_pool, now, get_config_value
from outreach_pipeline.organization import known_product_mentioned


async def check_opt_out(contact_id):
    """Check if contact is on do-not-contact list."""
    pool = get_pool()
    row = await pool.fetchrow("SELECT reason FROM do_not_contact WHERE contact_id=$1", str(contact_id))
    if row:
        return True, f"Contact on do-not-contact list: {row['reason']}"
    return False, ""


async def add_opt_out(contact_id, reason=""):
    """Add contact to do-not-contact list."""
    pool = get_pool()
    try:
        await pool.execute(
            "INSERT INTO do_not_contact VALUES ($1, $2, $3)",
            str(contact_id), reason, now(),
        )
        return True
    except Exception:
        # UniqueViolationError — already exists
        return False


async def check_duplicate(contact_id, cooldown_days=None, mql_date=None):
    """Check if contact was emailed recently (unless fresh message)."""
    pool = get_pool()
    if cooldown_days is None:
        cooldown_days = get_config_value("duplicate_cooldown_days", 30)

    # Parse mql_date to datetime for comparison with TIMESTAMPTZ values
    mql_dt = None
    if mql_date:
        if isinstance(mql_date, str):
            mql_dt = datetime.fromisoformat(mql_date.replace("Z", "+00:00"))
        else:
            mql_dt = mql_date

    # Check for existing draft
    draft = await pool.fetchrow("SELECT 1 FROM drafts WHERE contact_id=$1", str(contact_id))
    if draft:
        if mql_dt:
            row = await pool.fetchrow(
                "SELECT timestamp FROM outreach_log WHERE contact_id=$1 ORDER BY timestamp DESC LIMIT 1",
                str(contact_id),
            )
            if row and mql_dt > row["timestamp"]:
                return False, ""
        return True, "Draft already exists"

    # Check outreach log
    row = await pool.fetchrow(
        """SELECT timestamp FROM outreach_log WHERE contact_id=$1
           AND timestamp > (NOW() - ($2 || ' days')::interval)
           ORDER BY timestamp DESC LIMIT 1""",
        str(contact_id), str(cooldown_days),
    )
    if row:
        if mql_dt and mql_dt > row["timestamp"]:
            return False, ""
        ts = row['timestamp']
        date_str = ts.strftime("%Y-%m-%d") if hasattr(ts, 'strftime') else str(ts)[:10]
        return True, f"Emailed on {date_str} (within {cooldown_days}-day cooldown)"
    return False, ""


async def check_send_limit(daily_limit=None):
    """Check daily send count."""
    pool = get_pool()
    if daily_limit is None:
        daily_limit = get_config_value("daily_send_limit", 20)
    today = datetime.now(timezone.utc).date()
    row = await pool.fetchrow("SELECT COUNT(*) as cnt FROM send_log WHERE date=$1", today)
    count = row["cnt"]
    if count >= daily_limit:
        return True, f"Daily limit reached ({count}/{daily_limit})", count
    return False, "", count


async def check_events_contact(contact_id):
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
    pool = get_pool()
    row = await pool.fetchrow("SELECT properties FROM contacts WHERE id=$1", str(contact_id))
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

    # Message text: explicit intent to meet/attend at a named event or webinar.
    _S = r'(?:mwc|mobile\s+world\s+congress|gitex|ibc|ces|gsma|fyuz|ims)'
    _msg_pat = (
        r'(?:'
        r'meet(?:ing)?\s+(?:you\s+|us\s+|while\s+(?:we\s+are\s+)?)?at\s+' + _S + r'|'
        r'at\s+(?:the\s+)?' + _S + r'\s+(?:booth|event|stand|meeting|show)|'
        + _S + r'\s+(?:booth|meeting|tickets?|stand)|'
        r'tickets?\s+for\s+' + _S + r'|'
        r'visit\s+(?:your|the)\s+booth|'
        r'attend(?:ed|ing)?\s+(?:the\s+|a\s+|an\s+)?(?:' + _S + r'|event|conference|show|webinar|seminar|workshop|summit)'
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
    if NAMED_EVENTS.search(conversion) and EVENT_URL_PATH.search(first_url):
        return True, (
            f"Contact registered via events form ({conversion[:120]!r}) and "
            f"started on events page ({first_url[:120]!r}). "
            "Route to the events team, do not send SDR outreach."
        )

    return False, ""


async def check_support_request(contact_id):
    """Check if a contact is submitting a support request for existing equipment.

    Existing customers reporting faults, requesting technical support, or asking
    about SW/firmware updates on equipment they already own should be handled by
    the support team — not SDR outreach.

    Detection (any of the following is sufficient to block):
    - Strong standalone signal: explicit "technical support" request, "inop", processor reset
    - Ownership signal + troubleshooting signal (equipment not working, rebooting, etc.)
    - Ownership signal + SW/firmware update request on existing units
    """
    pool = get_pool()
    row = await pool.fetchrow("SELECT properties FROM contacts WHERE id=$1", str(contact_id))
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
    _OWNERSHIP = re.compile(
        r'\b(?:'
        r'i\s+have\s+(?:some|a\s+few|\d+)\s'
        r'|i\s+have\s+\d+\s+years?\s+(?:of\s+)?using\b'
        r'|i(?:\'ve|\s+have)\s+been\s+(?:using|running|operating)\b'
        r'|we\s+(?:have|own|use)\s+(?:some|a\s+few|\d+)\s'
        r'|(?:i|we)\s+(?:purchased|bought|deployed|installed)\b'
        r'|currently\s+(?:using|running|deployed|installed)\b'
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
        # Help with setup/connection of existing equipment
        r'|(?:seeking|need|require)\s+(?:assistance|help|support)\s+(?:with\s+)?(?:connecting|configuring|setting up|installing)\b'
        r'|(?:help|assist)\s+(?:me\s+)?(?:with\s+)?(?:connecting|configuring|setting up)\b'
        r')',
        re.IGNORECASE,
    )

    # SW/firmware update on existing equipment
    _SW_UPDATE = re.compile(
        r'\b(?:'
        r'update\s+(?:my|our|all\s+(?:my|our))\s+\w+\s+(?:sw|software|firmware|versions?)\b'
        r'|update\s+(?:sw|firmware|software)\s+(?:versions?|build)\b'
        r'|(?:sw|firmware|software)\s+(?:upgrade|update)\b'
        r'|recommended\s+to\s+update\b'
        r')',
        re.IGNORECASE,
    )

    # Strong standalone signals
    _STRONG_SUPPORT = re.compile(
        r'\b(?:'
        r'technical\s+support\b'
        r'|contact\s+(?:your\s+|our\s+)?support\b'
        r'|(?:inop|non-?functional)\b'
        r'|(?:warranty\s+(?:claim|replacement|repair)|rma)\b'
        r'|processor\s+reset\b'
        # Remote support tools = existing customer needing help
        r'|(?:teamviewer|anydesk|remote\s+(?:support|access|session))\b'
        r'|\u062a\u0635\u0641\u064a\u0631\b'
        # Purchase order / order status inquiries
        r'|(?:status\s+of\s+)?(?:PO|purchase\s+order|P\.O\.)\s*#?\s*\d'
        r'|order\s+status\b'
        r'|(?:where|when)\s+is\s+(?:my|our|the)\s+(?:order|shipment|delivery)\b'
        r'|tracking\s+(?:number|info|information)\s+for\s+(?:my|our|the)\s+order\b'
        r')',
        re.IGNORECASE,
    )

    # A configured model name is also evidence that the contact already has
    # equipment, without baking any vendor's catalog into this guardrail.
    has_ownership = bool(_OWNERSHIP.search(message)) or known_product_mentioned(message)
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


async def check_vendor_pitch(contact_id):
    """Check if a contact is a vendor/supplier trying to sell TO us.

    These are inbound partnership/supplier pitches from companies describing
    their own products and capabilities — not inquiring about our configured products.
    """
    pool = get_pool()
    row = await pool.fetchrow("SELECT properties FROM contacts WHERE id=$1", str(contact_id))
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

    from outreach_pipeline.policies.submission import (
        _VENDOR_PROVIDE, _VENDOR_PARTNERSHIP, _VENDOR_SELF_PROMO, _PRODUCT_PATTERNS,
    )

    has_vendor_provide = bool(_VENDOR_PROVIDE.search(message))
    has_partnership = bool(_VENDOR_PARTNERSHIP.search(message))
    has_self_promo = bool(_VENDOR_SELF_PROMO.search(message))
    has_company_product = any(p.search(message) for p in _PRODUCT_PATTERNS.values())

    if not has_company_product and (
        has_vendor_provide or (has_partnership and has_self_promo)
    ):
        snippet = message[:200].replace("\n", " ")
        return True, (
            f"Contact's message is a vendor/supplier pitch ({snippet!r}). "
            "They are selling TO us, not buying FROM us. "
            "Do not send SDR outreach — forward to procurement if relevant."
        )

    return False, ""


async def check_scrape_blocklist(domain):
    """Check if domain is blocked."""
    pool = get_pool()
    domain = domain.lower().strip()
    row = await pool.fetchrow(
        "SELECT reason FROM scrape_blocklist WHERE $1 LIKE '%' || domain",
        domain,
    )
    if row:
        return True, f"Domain blocked: {row['reason']}"
    return False, ""


async def add_scrape_block(domain, reason=""):
    """Add domain to scrape blocklist."""
    pool = get_pool()
    try:
        await pool.execute(
            "INSERT INTO scrape_blocklist VALUES ($1, $2, $3)",
            domain.lower().strip(), reason, now(),
        )
        return True
    except Exception:
        # UniqueViolationError — already exists
        return False
