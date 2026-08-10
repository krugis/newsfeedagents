# newspipe — GenAI/ML News Ingestion Pipeline (Phase 1)

Hourly ingestion of GenAI/ML news: fetch from zero-auth sources → normalize →
deduplicate → label with an LLM → persist to Postgres, orchestrated by LangGraph
with durable checkpointing.

**Status:** Phase 1, gated build. Currently at **Gate 1.3** (LLM labeling).
The remaining sub-phases (graph, scheduler) build on this and land one at a
time at their respective gates.

## Stack

- Python 3.12 (managed with `uv`)
- Postgres 16 (Docker Compose, host port **5433**)
- psycopg 3 + Pydantic / pydantic-settings
- feedparser + httpx (fetching)
- langchain-openai + langchain-core (labeling via structured output, DeepSeek)
- Coming in later gates: LangGraph (orchestration/checkpointing), APScheduler
  (scheduling).

## Quickstart

```bash
cp .env.example .env          # then fill in DEEPSEEK_API_KEY when it exists
docker-compose up -d          # start Postgres 16 on localhost:5433 (standalone docker-compose; the `docker compose` v2 plugin is not installed on this server)
uv sync                       # create the venv and install the project
uv run python -m newspipe.db.migrate   # apply schema migrations (idempotent)
uv run python scripts/seed_sources.py  # seed the source registry (idempotent)
uv run python -m newspipe fetch        # fetch every due source once
uv run pytest                 # run the test suite
uv run pytest -m live         # also run live network integration tests
```

## Layout

```
pyproject.toml          # project metadata, deps, ruff + pytest config
.env.example            # documents every configuration variable
docker-compose.yml      # Postgres 16 (port 5433, data in a named volume)
src/newspipe/
  __main__.py           # CLI dispatch (`python -m newspipe <command>`)
  config.py             # pydantic-settings, all env-driven
  normalize.py          # URL + title canonicalization (title_hash)
  dedup.py              # dedup v1: canonical-URL then 72h title-hash match
  fetch.py              # `fetch` orchestration (per-source isolation)
  seeding.py            # Phase 1 source registry seed data
  labeling/
    schema.py           # HeadlineLabel — the structured-output contract
    labeler.py          # prompt v1 + batch labeling over unlabeled stories
  db/
    engine.py           # psycopg connection management
    migrate.py          # tiny SQL migration runner
    migrations/         # 0001_initial.sql, 0002_sources_method_sitemap.sql
    sources.py          # source registry queries + upsert
    arrivals.py         # idempotent arrivals persistence
    labels.py           # select unlabeled stories + insert label rows
  fetchers/             # one fetcher per method type
    base.py             # RawItem, shared httpx client, bounded retry
    rss.py              # feedparser-based RSS/Atom
    hn_algolia.py       # HN Algolia keyword search + front page
    google_news_rss.py  # Google News RSS (redirect-target extraction)
    sitemap.py          # XML sitemap (Anthropic adaptation)
  models/
    schemas.py          # Pydantic domain models (sources, arrivals, ...)
scripts/
  seed_sources.py       # idempotent source seeding
tests/
  conftest.py           # migrates the DB once per session + source cleanup
  fixtures/             # real recorded responses (no-network unit tests)
  test_schema.py        # smoke test: all tables exist
  test_schemas.py       # domain model validation
  test_fetchers.py      # per-fetcher unit tests (fixtures)
  test_fetchers_live.py # live integration tests (marked `live`)
  test_fetch.py         # seeding + arrivals idempotency + due-source logic
  test_labeling.py      # schema/prompt/persistence + mocked batch + live label
```

## CLI

| Command | Purpose |
|---|---|
| `python -m newspipe fetch` | Fetch all due sources once; prints per-source counts (fetched/new). Never breaks on one source's failure. |
| `python -m newspipe dedup` | Deduplicate all unattached arrivals into stories (canonical URL, then 72h title-hash). Race-safe via advisory lock. |
| `python -m newspipe label` | Label unlabeled stories with the LLM; prints a table of the new labels. `--limit` caps the batch (default `LABEL_LIMIT_PER_RUN`). |
| `python -m newspipe.db.migrate` | Apply pending schema migrations (idempotent). |
| `python scripts/seed_sources.py` | Upsert the Phase 1 source registry (idempotent). |

## Dedup v1

Exact-match only (no embeddings):

1. **Canonical URL** — lowercase host, `http→https`, strips `utm_*`/`fbclid`/
   tracking params and fragments, trailing-slash policy.
2. **Title-hash within 72h** of `stories.first_seen_at` (sha256 of the
   NFKC-normalized, whitespace-collapsed title).
3. Match → attach the arrival, bump `arrival_count`, update `last_seen_at`, set
   `hn_front_page` if flagged. Miss → create a new story.

Race-safety: the whole run is one transaction guarded by a Postgres advisory
lock (`pg_advisory_xact_lock`), so concurrent dedup runs can't create duplicate
stories. The two-key lookup isn't expressible as a pure upsert, so a lock is
the simple, correct choice.

**Known v1 limitation:** a publisher that reuses an identical title for distinct
posts within 72h will false-positive collapse (observed: OpenAI's recurring
"Team update" posts). Planned for later phases — smarter matching, not a
change to v1.

## Labeling

Each unlabeled story is labeled with `deepseek-chat` (DeepSeek-V3 — the
cost-effective model; `deepseek-reasoner`/R1 is pricier and overkill for
one-sentence labeling) via `ChatOpenAI` against DeepSeek's OpenAI-compatible
endpoint, using structured output
(`HeadlineLabel`), so the model must return exactly:

```python
is_hot: bool                       # major/breaking event vs routine
importance: int  # 1..10
category: Literal[model_release, research, industry, funding,
                  policy_regulation, tooling_infra, other]
is_genai_ml_relevant: bool         # filters non-AI items from broad feeds
rationale: str                     # one sentence
```

The prompt (version `p1`, stored in `labels.prompt_version`) receives the
title, the **source names** it arrived from, `arrival_count`, and
`hn_front_page` — the prompt states that **cross-source arrival is an explicit
importance signal**. Stories are labeled in batches via `.abatch` with
`max_concurrency` from config and `.with_retry()`; a story that fails labeling
stays unlabeled and is retried on the next run, never blocking the batch. One
`labels` row is written per story per labeling (so relabeling / evals can be
added later without touching `stories`).

```bash
uv run python -m newspipe label --limit 10   # label up to 10 oldest unlabeled
```

## Sources

Seeded from `src/newspipe/seeding.py` (exactly the Phase 1 list). Verified live
at build time; two deviations:

- **Anthropic Blog** publishes no public RSS feed — it is fetched from its
  XML sitemap (`sitemap` method), filtering `/news/` URLs.
- **Google News RSS** item links are opaque redirect tokens that no longer
  embed the target URL (nor resolve via redirect) — the Google URL is kept as
  the item URL (spec fallback); cross-source dedup relies on title-hash in 1.2.

## Configuration

Every setting lives in `.env` (see `.env.example` for the full list):

| Variable            | Default                                               | Purpose                    |
|---------------------|-------------------------------------------------------|----------------------------|
| `DATABASE_URL`      | `postgresql://newspipe:newspipe@localhost:5433/newspipe` | Postgres connection URL |
| `DEEPSEEK_API_KEY`  | *(empty)*                                             | LLM labeling (DeepSeek)    |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com`                           | OpenAI-compatible endpoint |
| `MODEL_NAME`        | `deepseek-chat`                                      | Labeling model (cost-effective V3) |
| `BATCH_CONCURRENCY` | `8`                                                   | Max concurrent LLM calls   |
| `LABEL_LIMIT_PER_RUN` | `100`                                               | Cap on unlabeled stories labeled per `label` run (backfill guard) |

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
