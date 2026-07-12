-- Store an English translation of the submission message at ingest
-- time for non-English originals. Translations are LLM-generated —
-- the original stays in `message`, the translation in
-- `message_translation`, so the UI can always show both.

ALTER TABLE submissions ADD COLUMN IF NOT EXISTS message_translation TEXT;
