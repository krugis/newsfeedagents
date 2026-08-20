-- 0005_stories_last_seen_index: supports the retention query (stories with no
-- new arrival in `retention_days` days).

CREATE INDEX idx_stories_last_seen_at ON stories (last_seen_at);
