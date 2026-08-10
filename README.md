# newspipe — GenAI/ML News Ingestion Pipeline (Phase 1)

Hourly ingestion of GenAI/ML news: fetch from zero-auth sources → normalize →
deduplicate → label with an LLM → persist to Postgres, orchestrated by LangGraph
with durable checkpointing.

**Status:** Phase 1, gated build. Currently at **Gate 1.0** (scaffold + schema).
The remaining sub-phases (fetchers, dedup, labeling, graph, scheduler) build on
this skeleton and land one at a time at their respective gates.

## Stack

- Python 3.12 (managed with `uv`)
- Postgres 16 (Docker Compose, host port **5433**)
- psycopg 3 + Pydantic / pydantic-settings
- Coming in later gates: LangGraph + langchain-core + langchain-anthropic
  (labeling/orchestration), feedparser + httpx (fetching), APScheduler
  (scheduling), pytest + ruff.

## Quickstart

```bash
cp .env.example .env          # then fill in ANTHROPIC_API_KEY when it exists
docker-compose up -d          # start Postgres 16 on localhost:5433 (standalone docker-compose; the `docker compose` v2 plugin is not installed on this server)
uv sync                       # create the venv and install the project
uv run python -m newspipe.db.migrate   # apply schema migrations (idempotent)
uv run pytest                 # run the test suite
```

## Layout

```
pyproject.toml          # project metadata, deps, ruff + pytest config
.env.example            # documents every configuration variable
docker-compose.yml      # Postgres 16 (port 5433, data in a named volume)
src/newspipe/
  config.py             # pydantic-settings, all env-driven
  db/
    engine.py           # psycopg connection management
    migrate.py          # tiny SQL migration runner
    migrations/         # 0001_initial.sql and any later .sql migrations
  models/
    schemas.py          # Pydantic domain models (sources, arrivals, ...)
tests/
  conftest.py           # migrates the DB once per session
  test_schema.py        # smoke test: all tables exist
  test_schemas.py       # domain model validation
```

## Configuration

Every setting lives in `.env` (see `.env.example` for the full list):

| Variable            | Default                                               | Purpose                    |
|---------------------|-------------------------------------------------------|----------------------------|
| `DATABASE_URL`      | `postgresql://newspipe:newspipe@localhost:5433/newspipe` | Postgres connection URL |
| `ANTHROPIC_API_KEY` | *(empty)*                                             | LLM labeling (Gate 1.3)    |
| `MODEL_NAME`        | `claude-sonnet-4-6`                                   | Labeling model             |
| `BATCH_CONCURRENCY` | `8`                                                   | Max concurrent LLM calls   |

## Database schema

```
sources (registry) 1─N arrivals (raw, append-only) N─1 stories (deduped) 1─N labels
                                            └───── pipeline_runs (run telemetry)
```

- `sources` — the source registry (name, method, config, poll interval).
- `arrivals` — every raw item ever fetched, append-only; unique
  `(source_id, external_id)` makes fetching idempotent.
- `stories` — canonical deduplicated stories (`canonical_url` unique, plus
  `title_hash` for title-based matching, `arrival_count`, `hn_front_page`).
- `labels` — one row per labeling of a story (relabeling / evals later).
- `pipeline_runs` — one row per pipeline execution.

Inspect with:

```bash
docker exec -it newspipe-postgres-claudecode psql -U newspipe -d newspipe -c '\d+ sources'
```

## Migration-tool decision

A **tiny migration runner** (`db/migrate.py`) was chosen over Alembic:

- Plain SQL files applied in lexicographic order, each in its own transaction,
  tracked in a `schema_migrations` table → fully idempotent.
- Zero extra dependency and no boilerplate — appropriate for five tables that
  only ever move forward in Phase 1. If the schema grows into branching /
  downgrades later, Alembic can adopt the existing migrations.
