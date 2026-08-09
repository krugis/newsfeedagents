-- 0001_initial.sql — Phase 1 core schema.

CREATE TABLE sources (
    source_id              BIGSERIAL PRIMARY KEY,
    name                   TEXT NOT NULL UNIQUE,
    method                 TEXT NOT NULL CHECK (method IN ('rss', 'hn_algolia', 'google_news_rss')),
    config                 JSONB NOT NULL DEFAULT '{}'::jsonb,
    poll_interval_minutes  INT NOT NULL DEFAULT 60,
    last_polled_at         TIMESTAMPTZ,
    enabled                BOOLEAN NOT NULL DEFAULT true,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE stories (
    story_id        BIGSERIAL PRIMARY KEY,
    canonical_url   TEXT NOT NULL UNIQUE,
    title           TEXT NOT NULL,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    arrival_count   INT NOT NULL DEFAULT 1,
    hn_front_page   BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE arrivals (
    arrival_id      BIGSERIAL PRIMARY KEY,
    source_id       BIGINT NOT NULL REFERENCES sources (source_id),
    external_id     TEXT NOT NULL,
    url             TEXT NOT NULL,
    url_canonical   TEXT,
    title           TEXT NOT NULL,
    published_at    TIMESTAMPTZ,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw             JSONB NOT NULL DEFAULT '{}'::jsonb,
    story_id        BIGINT REFERENCES stories (story_id),
    CONSTRAINT uq_arrivals_source_external UNIQUE (source_id, external_id)
);

CREATE TABLE labels (
    label_id        BIGSERIAL PRIMARY KEY,
    story_id        BIGINT NOT NULL REFERENCES stories (story_id),
    is_hot          BOOLEAN,
    importance      SMALLINT CHECK (importance BETWEEN 1 AND 10),
    category        TEXT,
    rationale       TEXT,
    model           TEXT,
    prompt_version  TEXT,
    labeled_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE pipeline_runs (
    run_id          BIGSERIAL PRIMARY KEY,
    thread_id       TEXT NOT NULL UNIQUE,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    status          TEXT,
    stats           JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX idx_arrivals_url_canonical ON arrivals (url_canonical);
CREATE INDEX idx_arrivals_story_id ON arrivals (story_id);
CREATE INDEX idx_stories_first_seen_at ON stories (first_seen_at);
CREATE INDEX idx_labels_story_labeled_at ON labels (story_id, labeled_at);
