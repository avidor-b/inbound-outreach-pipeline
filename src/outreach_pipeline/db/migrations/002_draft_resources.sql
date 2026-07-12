-- Draft resource tracking — structured metadata about sendable assets
-- the drafter retrieved (and optionally referenced) in each email.
--
-- promised_resources       : top catalog matches surfaced to the reviewer
--                            ([{id, title, url, asset_type, summary, match_score}])
-- referenced_resource_ids  : subset of promised_resources ids actually
--                            cited in the email body (empty if the draft
--                            doesn't offer a resource)
-- unverified_promises      : free-text phrases in the body that look like
--                            resource offers but don't match the catalog
--                            (populated by the post-draft validator,
--                            surfaced as a yellow warning in the UI)

ALTER TABLE drafts
    ADD COLUMN IF NOT EXISTS promised_resources JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE drafts
    ADD COLUMN IF NOT EXISTS referenced_resource_ids JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE drafts
    ADD COLUMN IF NOT EXISTS unverified_promises JSONB NOT NULL DEFAULT '[]'::jsonb;
