-- JWT revocation table. Tokens carry a `jti` claim; logout (and
-- future admin-level "kick user") insert the jti here. _decode_token
-- checks membership on every request. Stale rows are purged on
-- insert by dropping anything past its `expires_at` — keeps the
-- table bounded (one row per active session, dropped by expiry).

CREATE TABLE IF NOT EXISTS revoked_tokens (
    jti TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    revoked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_revoked_tokens_expires_at ON revoked_tokens(expires_at);
