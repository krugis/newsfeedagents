-- 0007_topic_search_fts: proper relevance for /topic search, replacing the
-- plain `title ILIKE '%query%'` substring match:
--   - a generated tsvector column + GIN index for full-text search, ranked
--     by ts_rank (term-frequency-weighted relevance, not just presence).
--   - pg_trgm + a trigram GIN index for typo-tolerant fuzzy fallback when
--     the full-text match finds nothing (e.g. "andropic" -> "Anthropic").

CREATE EXTENSION IF NOT EXISTS pg_trgm;

ALTER TABLE stories ADD COLUMN title_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('english', title)) STORED;

CREATE INDEX idx_stories_title_tsv ON stories USING GIN (title_tsv);
CREATE INDEX idx_stories_title_trgm ON stories USING GIN (title gin_trgm_ops);
