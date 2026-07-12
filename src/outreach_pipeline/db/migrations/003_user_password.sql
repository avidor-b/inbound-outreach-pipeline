-- Password-based auth. Stores bcrypt hashes; empty string means the
-- user was created before passwords were required (e.g. via Google
-- OAuth) — they can set a password later or keep using OAuth.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS password_hash TEXT NOT NULL DEFAULT '';
