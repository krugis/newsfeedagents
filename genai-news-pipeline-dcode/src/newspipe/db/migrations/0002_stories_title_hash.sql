-- 0002_stories_title_hash.sql — support title-hash dedup matching.
ALTER TABLE stories ADD COLUMN title_hash TEXT;
CREATE INDEX idx_stories_title_hash ON stories (title_hash);
