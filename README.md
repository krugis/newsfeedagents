# newspipe — GenAI/ML News Ingestion Pipeline (Phase 1)

Hourly ingestion of GenAI/ML news: fetch from zero-auth sources → normalize →
deduplicate → label with an LLM → persist to Postgres, orchestrated by LangGraph
with durable checkpointing.

**Status:** Phase 1, gated build. All sub-phases built (fetch → normalize →
dedup → label → orchestrate → schedule). **Gate 1.5 is the final gate** —
awaiting sign-off before enabling the systemd timer.

## Stack

- Python 3.12 (managed with `uv`)
- Postgres 16 (Docker Compose, host port **5433**)
- psycopg 3 + Pydantic / pydantic-settings
- feedparser + httpx (fetching)
- langchain-openai + langchain-core (labeling via structured output, any OpenAI-compatible endpoint — defaults to DeepSeek)
- LangGraph + langgraph-checkpoint-postgres (orchestration/checkpointing)
- APScheduler (hourly scheduling)
- Flask + Jinja2 (the admin/news web UI)

## Quickstart

```bash
cp .env.example .env          # then fill in LLM_API_KEY when it exists
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
    schema.py           # HeadlineLabel — dynamic structured-output contract (configurable categories/importance)
    labeler.py          # prompt v1 + batch labeling over unlabeled stories
  graph/
    state.py            # PipelineState TypedDict (reduced list channels)
    build.py            # StateGraph nodes, fan-out, checkpointer, `run` CLI
  scheduler.py          # APScheduler pipeline job (configurable cron) + optional daily retention job, systemd entrypoint
  status.py             # `status` CLI: last runs, backlog, source health
  retention.py          # opt-in retention purge (RETENTION_DAYS)
  logging_setup.py      # JSON-lines logging (stdout + rotating file in logs/)
  web/                   # admin/news web UI (Flask)
    app.py               # create_app() factory, /admin + /news routes
    auth.py               # session login/logout, login_required
    settings_editor.py    # editable-config form <-> Settings validation <-> .env
    actions.py             # fetch/dedup/label triggers, in-process lock
    templates/, static/    # Jinja2 templates + plain CSS (no CDN/JS framework)
  db/
    engine.py           # psycopg connection management
    migrate.py          # tiny SQL migration runner
    migrations/         # 0001_initial.sql .. 0005_stories_last_seen_index.sql
    sources.py          # source registry queries + upsert
    arrivals.py         # idempotent arrivals persistence (+ returning ids)
    labels.py           # select unlabeled stories + insert label rows
    pipeline_runs.py    # run telemetry: open + finalize a run row
    pipeline_state.py   # small KV store (e.g. last label run, for LABEL_INTERVAL_MINUTES)
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
  test_graph.py         # graph nodes/stats + crash-resume acceptance test
  test_status.py        # status queries (runs, backlog, sources, errors)
  test_scheduler.py     # hour-slot thread id, job config, JSON logging
deploy/
  newspipe.service      # systemd unit running the scheduler
logs/                   # rotating JSON log files (created at runtime)
```

## CLI

| Command | Purpose |
|---|---|
| `python -m newspipe fetch` | Fetch all due sources once; prints per-source counts (fetched/new). Never breaks on one source's failure. |
| `python -m newspipe dedup` | Deduplicate all unattached arrivals into stories (canonical URL, then 72h title-hash). Race-safe via advisory lock. |
| `python -m newspipe label` | Label unlabeled stories with the LLM; prints a table of the new labels. `--limit` caps the batch (default `LABEL_LIMIT_PER_RUN`). |
| `python -m newspipe run` | One full pipeline run (fetch→dedup→label→finalize), checkpointed under `run-YYYYMMDD-HH`. `--resume <thread_id>` resumes a crashed run. |
| `python -m newspipe status` | Operational view: last 5 runs, unlabeled backlog, per-source last poll, recent errors. |
| `python -m newspipe scheduler` | Run the scheduler in the foreground (systemd normally runs it). |
| `python -m newspipe retention` | Purge news past `RETENTION_DAYS` (no-op unless it's set). `--dry-run` reports counts without deleting. |
| `python -m newspipe web` | Run the admin/news web UI on `WEB_HOST:WEB_PORT` (default `127.0.0.1:8010`). |
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

Each unlabeled story is labeled via `ChatOpenAI` against `LLM_BASE_URL` (any
OpenAI-compatible endpoint — defaults to DeepSeek's `deepseek-chat`, V3, the
cost-effective model; `deepseek-reasoner`/R1 is pricier and overkill for
one-sentence labeling), using structured output. The schema is built
dynamically from config (`labeling/schema.py::build_headline_label_model`),
so the model must return exactly:

```python
is_hot: bool                       # major/breaking event vs routine
importance: int  # IMPORTANCE_MIN..IMPORTANCE_MAX (default 1..10)
category: Literal[...]             # LABEL_CATEGORIES (default: model_release, research,
                                    # industry, funding, policy_regulation, tooling_infra, other)
is_genai_ml_relevant: bool         # filters non-AI items from broad feeds
rationale: str                     # one sentence
```

The prompt (version `p1`, stored in `labels.prompt_version`) receives the
title, the **source names** it arrived from, `arrival_count`, and
`hn_front_page` — the prompt states that **cross-source arrival is an explicit
importance signal**, and its importance line is interpolated from
`IMPORTANCE_MIN`/`IMPORTANCE_MAX`. Stories are labeled in batches via
`.abatch` with `max_concurrency` from config and `.with_retry()`; a story that
fails labeling stays unlabeled and is retried on the next run, never blocking
the batch. One `labels` row is written per story per labeling (so relabeling /
evals can be added later without touching `stories`).

By default labeling runs on every pipeline tick, bounded by
`LABEL_LIMIT_PER_RUN`; set `LABEL_INTERVAL_MINUTES` to throttle it to a
cadence independent of the fetch/run schedule (tracked in `pipeline_state`).

```bash
uv run python -m newspipe label --limit 10   # label up to 10 oldest unlabeled
```

## Pipeline run (LangGraph)

One run is a `StateGraph` compiled with a `PostgresSaver` checkpointer:

```
select_due_sources → [Send fan-out: fetch_source per source] → dedup → label → finalize
```

- `select_due_sources` opens a `pipeline_runs` row (`running`) and picks the
  due sources; with none due it hops straight to `dedup`.
- `fetch_source` runs once per due source (parallel superstep); a failing
  source appends to `state.errors` and never breaks the run.
- `dedup` / `label` are thin wrappers over the 1.2/1.3 functions (labeling is
  bounded by `LABEL_LIMIT_PER_RUN` so a backfilled DB drains gradually).
- `finalize` closes the run row with `status='success'` and a stats summary
  (per-source counts, new arrivals/stories, labeled, errors, duration).

**Durable checkpointing:** every node's writes are checkpointed under a
`thread_id` (`run-YYYYMMDD-HH` by default), so re-invoking the same thread
resumes from the last checkpoint instead of restarting. The checkpointer lives
in the same Postgres (its own `checkpoints`/`checkpoint_writes`/
`checkpoint_blobs` tables, created idempotently by `saver.setup()`).

```bash
uv run python -m newspipe run                      # this hour's thread
uv run python -m newspipe run --resume run-20260810-13   # resume a crash
```

A crash after fetch but before label is the acceptance test: the resumed run
prints `resuming existing thread (fetch will not be re-executed)` and the
fetch superstep is restored from the checkpoint, never re-run (verified in
`tests/test_graph.py::test_crash_resume_skips_fetch` and in the live demo).

## Scheduling (APScheduler + systemd)

`python -m newspipe scheduler` runs a `BlockingScheduler`: hourly at minute 5
by default (`SCHEDULER_CRON_MINUTE`/`SCHEDULER_CRON_HOUR`, any APScheduler
cron expression), `max_instances=1` (no overlapping runs), `coalesce=True`,
`misfire_grace_time=600`. Each tick invokes the graph under the hour-slot
thread id `run-YYYYMMDD-HH`, so a crash that left a checkpoint resumes instead
of restarting. If `RETENTION_DAYS` is set, a second daily job (03:00 UTC)
purges expired news — see [Retention](#retention).

Run it under systemd:

```bash
sudo cp deploy/newspipe.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start newspipe      # start now
sudo systemctl enable newspipe     # start on boot — only after Gate 1.5 sign-off
journalctl -u newspipe -f          # structured JSON lines
```

The unit runs the venv's `python -m newspipe.scheduler`, loads `.env` via
`EnvironmentFile`, and `Restart=on-failure`. The project is currently
root-owned; if you deploy under another account, change `User=` and
`chown -R` the project.

## Retention

Opt-in, hard delete, off by default (`RETENTION_DAYS` unset = keep news
forever). When set, a story is expired once it's had no new arrival in
`RETENTION_DAYS` days (`stories.last_seen_at`); `retention.py::purge_expired`
deletes its `labels`, then its `arrivals`, then the `stories` row itself (no
`ON DELETE CASCADE` in the schema, so this is done in FK-safe order), plus any
arrival that was never deduped into a story (`story_id IS NULL`), by its own
fetch time.

```bash
uv run python -m newspipe retention --dry-run   # preview counts, deletes nothing
uv run python -m newspipe retention             # actually purge
```

With `RETENTION_DAYS` set, the scheduler also runs this daily at 03:00 UTC —
see [Scheduling](#scheduling-apscheduler--systemd).

## Web UI

`python -m newspipe web` serves a small two-page site behind one admin login
(session cookie, single account from `ADMIN_USERNAME`/`ADMIN_PASSWORD` — no
user table). Binds to `127.0.0.1:8010` by default.

- **`/admin`** — every editable setting from [Configuration](#configuration)
  except `DATABASE_URL` (changing the DB connection via a form served by a
  connection to that same DB would be a footgun) rendered as a form; saving
  re-validates through the same `Settings` model used everywhere else (so an
  invalid combination like `IMPORTANCE_MIN >= IMPORTANCE_MAX` is rejected,
  nothing written) and rewrites `.env` in place, taking effect on the web
  process's next request. **A separately-running `scheduler` systemd process
  won't see the change until it's restarted.** The same page has three
  buttons — Start Digest (Collect News), Prepare News, Start Labeling —
  running `fetch`/`dedup`/`label` synchronously and showing the result;
  an in-process lock rejects a second trigger while one is already running.
- **`/news`** — a plain, unstyled table of `arrivals` (date, source, title
  truncated to 100 chars), newest first, paginated at 10 or 20 rows per page.

Security is scoped to "trusted single operator on their own server", matching
the rest of the project: `ADMIN_PASSWORD` is plaintext in `.env` (same as
`LLM_API_KEY`), there's no CSRF protection on the forms, and it's Flask's
built-in dev server, not a production WSGI setup. Don't change `WEB_HOST` to
expose it beyond localhost without addressing those first.

## Logging

Structured JSON-lines logging (`logging_setup.py`) to stdout **and** a rotating
file `logs/pipeline.log` (10 MB × 5 backups). Each line carries `ts` (UTC ISO),
`level`, `logger`, `message`, and structured `extra_*` fields. The scheduler
emits `run_completed` at INFO with the full stats payload, one `source_result`
at DEBUG per source, and `source_error` at WARNING per failure. The interactive
CLIs log JSON to the file only and keep stdout human-readable.

```bash
tail -f logs/pipeline.log | jq -c 'select(.level=="WARNING" or .level=="ERROR")'
```

## Failure modes

| Scenario | What happens | Why it's safe |
|---|---|---|
| A feed 404s | `get_with_retry` does not retry 4xx → `fetch_source` catches → error appended to `state.errors` → the run continues | Per-source isolation: one dead feed never breaks the batch; the run still finalizes with `sources_failed>0` |
| Postgres briefly down | A node's `connect()` raises → the graph `invoke` raises → the tick logs `tick_failed` → the next hour retries (systemd restarts the scheduler on failure) | Dedup is one transaction under an advisory lock; checkpoints only write when PG is reachable, so a partial run simply resumes later |
| DeepSeek rate-limited | `ChatOpenAI` SDK backoff + `.with_retry()` retry the call; a story that still fails stays unlabeled (`return_exceptions=True`) | Labeling never blocks the batch; failed stories are retried on the next run |
| Two runs would overlap | `max_instances=1` serializes scheduler ticks; a manual re-run in the same hour resumes the same thread checkpoint (fetch not re-executed); dedup holds an advisory lock | No duplicate fetches, arrivals, or stories |

## 72-hour soak checklist

After you enable the scheduler, check these once a day for ~3 days:

1. `python -m newspipe status` — expect 8 sources `on`, error tail empty, one
   new `run-YYYYMMDD-HH` row per hour (24/day), and `unlabeled backlog`
   trending down toward the rate `LABEL_LIMIT_PER_RUN` allows.
2. `logs/pipeline.log` — one `run_completed` INFO line per hour with sane
   stats; per-source DEBUG lines; no `tick_failed` / `source_error` growth.
3. Rotating log works: file stays ≤ 10 MB, backups appear in `logs/`.
4. Spot-check labeled stories (see Labeling): categories sane, hot items not
   routine, `is_genai_ml_relevant=False` filtering broad-feed noise.
5. Crash-resume drill: `sudo systemctl restart newspipe` mid-run, then check
   the next log line says `resuming existing thread` (fetch not re-executed).
6. Dedup sanity: `status` `stories` count should not balloon — duplicate
   cross-source stories collapse onto one `stories` row.

## Schema

```
sources (registry) 1─N arrivals (raw, append-only) N─1 stories (deduped) 1─N labels
                            └──────── pipeline_runs (run telemetry)
LangGraph checkpoint tables (checkpoints / checkpoint_writes / checkpoint_blobs)
in the same Postgres, created idempotently by PostgresSaver.setup().
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
| `LLM_API_KEY`       | *(empty)*                                             | LLM labeling (any OpenAI-compatible endpoint) |
| `LLM_BASE_URL`      | `https://api.deepseek.com`                           | OpenAI-compatible endpoint |
| `MODEL_NAME`        | `deepseek-chat`                                      | Labeling model (cost-effective V3) |
| `BATCH_CONCURRENCY` | `8`                                                   | Max concurrent LLM calls   |
| `LABEL_LIMIT_PER_RUN` | `100`                                               | Cap on unlabeled stories labeled per `label` run (backfill guard) |
| `LABEL_INTERVAL_MINUTES` | `0`                                              | Labeling cadence, independent of fetch; `0` = every run |
| `LABEL_CATEGORIES`  | `model_release,research,industry,funding,policy_regulation,tooling_infra,other` | Comma-separated category enum for labeling |
| `IMPORTANCE_MIN` / `IMPORTANCE_MAX` | `1` / `10`                          | Importance scale bounds (inclusive) |
| `SCHEDULER_CRON_MINUTE` / `SCHEDULER_CRON_HOUR` | `5` / `*`                | Pipeline run cadence (APScheduler cron fields) |
| `RETENTION_DAYS`    | *(unset — keep forever)*                             | Days to keep news before hard-deleting it |
| `WEB_HOST` / `WEB_PORT` | `127.0.0.1` / `8010`                              | Web UI bind address (see [Web UI](#web-ui)) |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / *(unset — login refused)*      | Web UI login; login is disabled until `ADMIN_PASSWORD` is set |
| `WEB_SESSION_SECRET` | *(auto-generated per process start)*                | Signs the session cookie; set explicitly for sessions to survive a restart |

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
- Zero extra dependency and no boilerplate — appropriate for a handful of
  tables that only ever move forward in Phase 1. If the schema grows into branching /
  downgrades later, Alembic can adopt the existing migrations.
