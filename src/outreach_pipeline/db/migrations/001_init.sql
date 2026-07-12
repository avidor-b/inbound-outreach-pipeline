-- Postgres schema for the outreach pipeline.
-- Equivalent to the SQLite schema but using Postgres types.

CREATE TABLE IF NOT EXISTS contacts (
    id TEXT PRIMARY KEY,
    properties JSONB NOT NULL DEFAULT '{}',
    company_context TEXT NOT NULL DEFAULT '',
    crm_context TEXT NOT NULL DEFAULT '',
    buying_intent TEXT NOT NULL DEFAULT '',
    intent_signals JSONB NOT NULL DEFAULT '[]',
    score INTEGER NOT NULL DEFAULT 0,
    score_reasoning TEXT NOT NULL DEFAULT '',
    product_match TEXT NOT NULL DEFAULT '',
    enrichment_confidence TEXT NOT NULL DEFAULT '',
    fetched_at TIMESTAMPTZ,
    enriched_at TIMESTAMPTZ,
    scored_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS submissions (
    id SERIAL PRIMARY KEY,
    contact_id TEXT NOT NULL REFERENCES contacts(id),
    message TEXT NOT NULL DEFAULT '',
    message_hash TEXT,
    form_name TEXT NOT NULL DEFAULT '',
    form_id TEXT NOT NULL DEFAULT '',
    page_url TEXT NOT NULL DEFAULT '',
    page_title TEXT NOT NULL DEFAULT '',
    mql_date DATE,
    properties_snapshot JSONB NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'hubspot',
    intent_type TEXT NOT NULL DEFAULT '',
    intent_signals JSONB NOT NULL DEFAULT '[]',
    product_match TEXT NOT NULL DEFAULT '',
    product_family TEXT NOT NULL DEFAULT '',
    support_flag BOOLEAN NOT NULL DEFAULT FALSE,
    event_flag BOOLEAN NOT NULL DEFAULT FALSE,
    classification_reasoning TEXT NOT NULL DEFAULT '',
    classified_at TIMESTAMPTZ,
    captured_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_submissions_contact_date ON submissions(contact_id, mql_date);
CREATE INDEX IF NOT EXISTS idx_submissions_hash ON submissions(contact_id, message_hash, form_id);
CREATE INDEX IF NOT EXISTS idx_submissions_source ON submissions(source);

CREATE TABLE IF NOT EXISTS engagements (
    id SERIAL PRIMARY KEY,
    contact_id TEXT NOT NULL REFERENCES contacts(id),
    kind TEXT NOT NULL DEFAULT 'sales',
    status TEXT NOT NULL DEFAULT 'open',
    block_code TEXT NOT NULL DEFAULT '',
    block_reason TEXT NOT NULL DEFAULT '',
    block_terminal BOOLEAN NOT NULL DEFAULT FALSE,
    score INTEGER NOT NULL DEFAULT 0,
    score_reasoning TEXT NOT NULL DEFAULT '',
    score_confidence TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'normal',
    primary_product TEXT NOT NULL DEFAULT '',
    retarget_of INTEGER REFERENCES engagements(id),
    opened_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    closed_reason TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_engagements_contact ON engagements(contact_id, status, kind);
CREATE UNIQUE INDEX IF NOT EXISTS idx_engagements_retarget
    ON engagements(retarget_of) WHERE retarget_of IS NOT NULL;

CREATE TABLE IF NOT EXISTS engagement_submissions (
    engagement_id INTEGER REFERENCES engagements(id),
    submission_id INTEGER REFERENCES submissions(id),
    PRIMARY KEY (engagement_id, submission_id)
);

CREATE INDEX IF NOT EXISTS idx_engagement_submissions_reverse ON engagement_submissions(submission_id);

CREATE TABLE IF NOT EXISTS drafts (
    id SERIAL PRIMARY KEY,
    engagement_id INTEGER REFERENCES engagements(id),
    contact_id TEXT NOT NULL REFERENCES contacts(id),
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    score_at_send INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'draft',
    rag_context TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_drafts_engagement ON drafts(engagement_id);
CREATE INDEX IF NOT EXISTS idx_drafts_contact ON drafts(contact_id);

CREATE TABLE IF NOT EXISTS replies (
    id SERIAL PRIMARY KEY,
    contact_id TEXT REFERENCES contacts(id),
    subject TEXT NOT NULL DEFAULT '',
    body_preview TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL DEFAULT '',
    timestamp TIMESTAMPTZ,
    urgency TEXT NOT NULL DEFAULT '',
    sentiment TEXT NOT NULL DEFAULT '',
    response_time_hours REAL,
    detected_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS outreach_log (
    id SERIAL PRIMARY KEY,
    contact_id TEXT REFERENCES contacts(id),
    subject TEXT NOT NULL DEFAULT '',
    timestamp TIMESTAMPTZ,
    mql_date DATE
);

CREATE TABLE IF NOT EXISTS send_log (
    id SERIAL PRIMARY KEY,
    contact_id TEXT REFERENCES contacts(id),
    subject TEXT NOT NULL DEFAULT '',
    date DATE,
    timestamp TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS do_not_contact (
    contact_id TEXT PRIMARY KEY,
    reason TEXT NOT NULL DEFAULT '',
    added_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS scrape_blocklist (
    domain TEXT PRIMARY KEY,
    reason TEXT NOT NULL DEFAULT '',
    added_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id SERIAL PRIMARY KEY,
    start_date DATE,
    end_date DATE,
    contacts_fetched INTEGER NOT NULL DEFAULT 0,
    contacts_enriched INTEGER NOT NULL DEFAULT 0,
    contacts_scored INTEGER NOT NULL DEFAULT 0,
    emails_drafted INTEGER NOT NULL DEFAULT 0,
    emails_sent INTEGER NOT NULL DEFAULT 0,
    hot_leads INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    summary TEXT NOT NULL DEFAULT ''
);

-- Thread management (server/UI)
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'viewer',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    user_id TEXT REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    thread_id TEXT REFERENCES threads(id),
    role TEXT NOT NULL DEFAULT 'user',
    content TEXT NOT NULL DEFAULT '',
    tool_name TEXT,
    tool_args TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS artifacts (
    id SERIAL PRIMARY KEY,
    thread_id TEXT REFERENCES threads(id),
    type TEXT NOT NULL DEFAULT 'email_draft',
    contact_id TEXT,
    data JSONB NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'created',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS approvals (
    id SERIAL PRIMARY KEY,
    artifact_id INTEGER REFERENCES artifacts(id),
    user_id TEXT REFERENCES users(id),
    action TEXT NOT NULL DEFAULT '',
    feedback TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
