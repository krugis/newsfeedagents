-- 0003_pipeline_state: a small key/value table for pipeline bookkeeping that
-- doesn't belong on any one domain table — starting with the last time the
-- labeling step actually ran, so its cadence can be decoupled from fetch.

CREATE TABLE pipeline_state (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
