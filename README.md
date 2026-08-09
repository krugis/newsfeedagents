# newspipe — hourly GenAI/ML news ingestion pipeline (Phase 1)

Phase 1 is a complete end-to-end vertical slice: fetch news from zero-auth
sources → normalize → deduplicate → label with an LLM → persist to Postgres,
orchestrated by LangGraph with durable checkpointing, triggered hourly by
APScheduler.

## Stack

- Python 3.11+ (managed with `uv`), ruff, pytest
- LangGraph + langchain-core + langchain-deepseek (labeling)
- Postgres 16 via Docker Compose; SQLAlchemy Core + psycopg
- APScheduler, feedparser, httpx

## Setup from scratch

```bash
cd /home/agate/genai-news-pipeline
uv sync --all-extras          # create .venv and install deps
cp .env.example .env          # edit .env: DATABASE_URL, ANTHROPIC_API_KEY
docker-compose up -d          # start Postgres 16 (localhost only)
uv run python -m newspipe migrate   # apply SQL migrations
uv run pytest                 # smoke test: tables exist
```

## CLI

```bash
uv run python -m newspipe migrate   # apply pending migrations
uv run python -m newspipe fetch     # poll every due source once (prints per-source counts)
uv run python -m newspipe dedup     # storify all unattached arrivals (normalize + dedup v1)
uv run python -m newspipe label     # LLM-label unlabeled stories (--limit N, prints table)
uv run python scripts/seed_sources.py  # (idempotent) insert the Phase 1 source registry
```

Tests: `uv run pytest` (offline, DB required). Live network tests are marked
`@pytest.mark.live` and run with `uv run pytest --live`.

## Layout

```
src/newspipe/
  config.py        # pydantic-settings, all env-driven
  fetch.py         # fetch runner (per-source isolation, run stats)
  normalize.py     # URL canonicalization + title normalization/hash
  dedup.py         # dedup v1: canonical-URL then title-hash (72h window), advisory-locked
  db/engine.py     # SQLAlchemy engine factory
  db/migrate.py    # tiny SQL-file migration runner
  db/migrations/   # plain-SQL migrations (0001_initial.sql, 0002_stories_title_hash.sql, ...)
  db/sources.py    # due-source selection
  db/arrivals.py   # idempotent arrival persistence
  fetchers/        # one fetcher per method type (base/rss/hn_algolia/google_news_rss)
  labeling/        # LLM labeling (schema.py HeadlineLabel, labeler.py chain + batch)
  models/schemas.py# Pydantic domain models (RawItem, Source)
scripts/seed_sources.py  # Phase 1 source registry (idempotent upsert)
tests/             # pytest suite; tests/fixtures/ holds recorded responses
```

## Dedup v1 (exact match)

`python -m newspipe dedup` storifies every unattached arrival. Matching is
exact-match only (no embeddings): first by `url_canonical`, then by
`title_hash` within 72h of `stories.first_seen_at`. A match increments
`arrival_count`, updates `last_seen_at`, and sets `hn_front_page` when the
arrival was on the HN front page. The whole pass holds a Postgres advisory
xact lock (fixed key) so concurrent dedup runs serialize. Note: identical
titles in different articles (e.g. OpenAI's repeated "Team update" posts)
collapse into one story by design in v1; LLM labeling can differentiate
content, and embedding-based dedup is a Phase 2 item.

## LLM labeling

`python -m newspipe label` selects stories without a labels row and labels them
in batches (`max_concurrency` from `BATCH_CONCURRENCY`). The chain is
`init_chat_model(deepseek-chat, provider=deepseek).with_structured_output(HeadlineLabel).with_retry()`
with prompt version `p1` stored in `labels.prompt_version`. The prompt receives
title, source names, arrival_count, and hn_front_page, and treats cross-source
arrival as an explicit importance signal. A story that fails labeling stays
unlabeled for the next run. Without `ANTHROPIC_API_KEY` the command skips
cleanly. One `labels` row is persisted per labeling (relabeling is possible
later because labels are a separate table).

## Sources

The Phase 1 registry is seeded by `scripts/seed_sources.py`. **Anthropic Blog
is seeded disabled**: Anthropic publishes no official RSS feed (verified at
build time), so there is no feed URL to poll. Enable it once a feed is chosen
(e.g. a community mirror) or a scraper is built (Phase 2+).

## Database

Tables: `sources`, `arrivals`, `stories`, `labels`, `pipeline_runs` plus the
runner's `schema_migrations` bookkeeping table. See
`src/newspipe/db/migrations/0001_initial.sql` for DDL.

## Roadmap (gates)

- 1.0 scaffolding, DB, schema (this gate)
- 1.1 fetcher layer + source seeding
- 1.2 normalization + dedup v1
- 1.3 LLM labeling
- 1.4 LangGraph assembly + checkpointing
- 1.5 scheduler, hardening, runbook
