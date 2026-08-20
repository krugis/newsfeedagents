-- 0004_widen_importance_check: importance scale is now configurable
-- (settings.importance_min/importance_max), so widen the DB-level CHECK to a
-- generous ceiling that fits any configured range; the exact configured
-- bounds are enforced in the application layer at insert time.

ALTER TABLE labels DROP CONSTRAINT labels_importance_check;
ALTER TABLE labels ADD CONSTRAINT labels_importance_check
    CHECK (importance BETWEEN 1 AND 100);
