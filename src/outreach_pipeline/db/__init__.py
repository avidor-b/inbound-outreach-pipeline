"""Primary async Postgres data-layer exports.

The runtime database is asyncpg/Postgres. Legacy SQLite helpers remain in
``outreach_pipeline.db.connection`` for old CLI scripts, but are not re-exported
as the primary DB API.

- contacts: upsert_contact, enrich_contact, score_contact, get_contact, get_contacts, count_contacts
- drafts: save_draft, mark_sent, get_drafts, log_send
- guardrails: check_opt_out, add_opt_out, check_duplicate, check_send_limit, log_send (also in drafts),
              check_events_contact, check_support_request, check_scrape_blocklist, add_scrape_block
- replies: save_reply, get_hot_leads, start_run, update_run
"""

def get_db(*args, **kwargs):  # noqa: D401
    """Deprecated compatibility wrapper for legacy SQLite-only CLI paths."""
    import warnings
    from outreach_pipeline.db.connection import get_db as _legacy_get_db

    warnings.warn(
        "outreach_pipeline.db.get_db is deprecated; use async Postgres modules "
        "for runtime code or outreach_pipeline.db.connection.get_db for legacy "
        "SQLite-only CLI paths.",
        DeprecationWarning,
        stacklevel=2,
    )
    return _legacy_get_db(*args, **kwargs)

# Contacts
from outreach_pipeline.db.contacts import (  # noqa: F401
    PII_FIELDS,
    _strip_pii,
    upsert_contact,
    enrich_contact,
    score_contact,
    get_contact,
    get_contacts,
    count_contacts,
)

# Drafts
from outreach_pipeline.db.drafts import (  # noqa: F401
    save_draft,
    mark_sent,
    get_drafts,
    log_send,
)

# Guardrails
from outreach_pipeline.db.guardrails import (  # noqa: F401
    check_opt_out,
    add_opt_out,
    check_duplicate,
    check_send_limit,
    check_events_contact,
    check_support_request,
    check_scrape_blocklist,
    add_scrape_block,
)

# Replies & pipeline runs
from outreach_pipeline.db.replies import (  # noqa: F401
    save_reply,
    get_hot_leads,
    start_run,
    update_run,
)
