# newspipe — System Documentation (claudecode build)

Phase 1 of an hourly GenAI/ML news ingestion pipeline: fetch → normalize →
dedup → LLM label → persist to Postgres, orchestrated by LangGraph with durable
checkpointing and triggered hourly by APScheduler.

This is the companion to `README.md` — architecture, how the system works, and
the time / token cost of building it.

## 1. How the system works

### 1.1 Purpose

Every hour the pipeline polls 8 zero-auth sources for GenAI/ML news, dedupes
them into canonical stories, and labels each story with a cheap LLM
(`deepseek-chat`) so a digest can later surface what matters (hot, importance,
category, relevance). All state lives in Postgres; the run itself is one
LangGraph state machine whose writes are checkpointed so a crash resumes
instead of restarting.

### 1.2 Data flow (one run)

```
select_due_sources → [Send fan-out: fetch_source per due source] → dedup → label → finalize
```

1. `select_due_sources` — opens a `pipeline_runs` row (`running`) and picks
   enabled sources whose poll interval has elapsed. With none due it hops
   straight to `dedup`.
2. `fetch_source` — one parallel invocation per due source: fetch → persist
   arrivals (idempotent on `(source_id, external_id)`). A failing source
   appends to `state.errors` and never breaks the batch.
3. `dedup` — attaches arrivals to stories (canonical URL, then 72h title-hash),
   race-safe via a Postgres advisory lock.
4. `label` — sends unlabeled stories to the LLM in a batch, bounded by
   `LABEL_LIMIT_PER_RUN` (so a backfilled DB drains gradually). Failures stay
   unlabeled and retry next run.
5. `finalize` — closes the run row with `status='success'` and stats
   (per-source counts, new arrivals/stories, labeled, errors, duration).

Every node's writes are checkpointed by a `PostgresSaver` under the
`thread_id` `run-YYYYMMDD-HH`, so re-invoking the same hour resumes from the
last checkpoint — a crash after fetch never re-fetches.

### 1.3 Stages in detail

**Fetching** (`src/newspipe/fetchers/`) — one fetcher per method, all zero-auth:

| Method | Source(s) | Notes |
|---|---|---|
| `rss` | TechCrunch AI, OpenAI Blog, The Verge, VentureBeat AI | `feedparser` |
| `hn_algolia` | Hacker News | keyword `search_by_date` windowed since last poll (capped 24h) + front page, flagged `raw["hn_front_page"]` |
| `google_news_rss` | Google News (×2 queries) | redirect-target extraction best-effort; opaque tokens → Google URL fallback (spec deviation, reported at gate) |
| `sitemap` | Anthropic Blog | no public RSS → XML sitemap filtered to `/news/` (spec deviation, reported at gate) |

Shared `httpx` client, bounded exponential retry on `429/5xx` only, custom
User-Agent.

**Normalization** (`normalize.py`) — canonical URL (https, lowercase host,
strip `utm_*`/`fbclid`/`gclid`/fragments, trailing-slash policy), NFKC
title + whitespace collapse, sha256 `title_hash`.

**Dedup** (`dedup.py`) — one transaction under `pg_advisory_xact_lock`:
match each unattached arrival by canonical URL, else by title-hash within a
72h window of the story's `first_seen_at`; attach or create. Known v1
limitation: a publisher reusing an identical title (OpenAI's "Team update")
false-positives within 72h — documented, deferred to a smarter matcher.

**Labeling** (`labeling/`) — `ChatOpenAI(model=deepseek-chat,
base_url=https://api.deepseek.com).with_structured_output(HeadlineLabel,
method="function_calling").with_retry()`. DeepSeek's OpenAI-compatible API
rejects langchain-openai's default `json_schema` response format, so
function-calling (tool schema) is forced. Prompt v1 (`PROMPT_VERSION="p1"`)
states that **cross-source arrival is an explicit importance signal** (it
receives title, source names, arrival_count, hn_front_page). Batch via
`.abatch(config={"max_concurrency": BATCH_CONCURRENCY}, return_exceptions=True)`;
one `labels` row per story per labeling.

**Orchestration** (`graph/`) — `StateGraph` compiled with a `PostgresSaver`
(same Postgres; its own checkpoint tables). `fan_out` returns one `Send` per
due source, or hops to `dedup` when none are due (an empty Send list would end
the graph). The `run` CLI detects an existing checkpoint (`saver.get_tuple`)
and resumes with `input=None` — passing input would restart the run.

**Scheduling** (`scheduler.py`) — APScheduler `BlockingScheduler`, hourly at
minute 5, `max_instances=1` (no overlap), `coalesce=True`,
`misfire_grace_time=600`. Each tick invokes the graph under the hour-slot
thread id. Runs via `python -m newspipe scheduler` or the systemd unit
(`deploy/newspipe.service`).

### 1.4 Schema (Postgres 16)

```
sources (registry) 1─N arrivals (raw, append-only) N─1 stories (deduped) 1─N labels
                            └──────── pipeline_runs (run telemetry)
LangGraph checkpoint tables (checkpoints / checkpoint_writes / checkpoint_blobs)
in the same Postgres, created idempotently by PostgresSaver.setup().
```

- `sources` — registry (name, method, config, poll interval, enabled).
- `arrivals` — every raw item ever fetched; unique `(source_id, external_id)`.
- `stories` — canonical deduped stories (`canonical_url` unique, `title_hash`,
  `arrival_count`, `hn_front_page`).
- `labels` — one row per labeling of a story (relabeling/evals later).
- `pipeline_runs` — one row per run (thread_id, status, stats).

Managed by a tiny SQL migration runner (`db/migrate.py`, idempotent,
`schema_migrations` table) — 2 migrations, 69 lines.

### 1.5 Configuration (`.env`, gitignored)

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql://newspipe:newspipe@localhost:5433/newspipe` | Postgres (port 5433 — isolated from sibling services) |
| `DEEPSEEK_API_KEY` | *(required for labeling)* | DeepSeek (OpenAI-compatible) |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | Endpoint |
| `MODEL_NAME` | `deepseek-chat` | Cost-effective V3 |
| `BATCH_CONCURRENCY` | `8` | Max concurrent LLM calls |
| `LABEL_LIMIT_PER_RUN` | `100` | Unlabeled stories labeled per run (backfill guard) |

### 1.6 CLI

| Command | Purpose |
|---|---|
| `python -m newspipe fetch` | Fetch all due sources once (per-source counts) |
| `python -m newspipe dedup` | Deduplicate unattached arrivals into stories |
| `python -m newspipe label [--limit N]` | Label unlabeled stories, print (title, is_hot, importance, category) |
| `python -m newspipe run [--resume THREAD]` | One full graph run, checkpointed under `run-YYYYMMDD-HH` |
| `python -m newspipe status` | Last 5 runs, unlabeled backlog, source health, error tail |
| `python -m newspipe scheduler` | Hourly scheduler (foreground or systemd) |
| `python -m newspipe.db.migrate` | Apply schema migrations (idempotent) |
| `python scripts/seed_sources.py` | Upsert the source registry (idempotent) |

### 1.7 Testing

62 tests (9 marked `live` and deselected by default), `ruff` clean. Unit tests
use recorded fixture payloads (no network); DB-backed tests run against the
real Postgres and clean up after themselves (including test checkpoint rows).
Live tests hit real feeds and the real DeepSeek API, skipping cleanly without
`DEEPSEEK_API_KEY`. The crash-resume acceptance test models a process killed
after fetch with `interrupt_before=["label"]` and asserts fetch is not
re-executed on resume.

### 1.8 Tech stack

Python 3.12 (uv) · Postgres 16 (Docker, host port 5433) · psycopg 3 ·
pydantic v2 / pydantic-settings · feedparser + httpx · langchain-openai +
langchain-core · LangGraph 1.1 + langgraph-checkpoint-postgres · APScheduler 3 ·
pytest + ruff.

### 1.9 Codebase metrics (lines of code)

Actual counts from `git ls-files` at the final commit (excludes `.venv/`,
`.git/`, caches, `logs/`, `.env`):

| Area | Files | Lines |
|---|---|---|
| Application (`src/newspipe/`) | 31 | 2,039 |
| Tests (`tests/`) | 12 | 1,380 |
| **Python total** | 44 | **3,434** |
| SQL migrations (`db/migrations/`) | 2 | 69 |
| Seed script (`scripts/`) | 1 | 15 |
| Deploy (`deploy/newspipe.service`) | 1 | 27 |
| Docs (README.md, SYSTEM_DOCUMENTATION.md) | 2 | 498 |
| **All tracked** | **60** | **8,256** |

Application modules by size: `graph/` (LangGraph orchestration), `fetchers/`
(4 fetchers + base), `labeling/` (schema + labeler), `db/` (engine, migrate,
sources, arrivals, labels, pipeline_runs), plus top-level `config`, `fetch`,
`dedup`, `normalize`, `scheduler`, `status`, `logging_setup`, `__main__`.

## 2. Development time

**Wall-clock span: ~6.6 hours** (2026-08-10 07:09 → 13:46 UTC), gated at each
sub-phase per the build protocol. This includes the mandatory gate pauses
awaiting explicit user approval, plus a ~4h idle gap between Gate 1.2 and 1.3
(08:35 → 12:29) during which the session was paused and the user decided to
switch the labeling provider from Anthropic to DeepSeek. Active implementation
was continuous within each sub-phase.

Git commit timeline (build-order evidence):

| Commit | Time (UTC) | What |
|---|---|---|
| `f8ec267` | 07:09 | gate-1.0 project skeleton, DB schema, migration runner |
| `ea1cdec` | 07:18 | gate-1.0 README: standalone docker-compose note |
| `10b764d` | 08:30 | gate-1.1 fetcher layer + source seeding + fetch CLI |
| `aa67bdd` | 08:35 | gate-1.2 normalization + dedup v1 |
| `eef8b8f` | 12:29 | gate-1.3 LLM labeling (langchain structured output) |
| `c5ee8e4` | 12:39 | gate-1.3 provider swap Anthropic → DeepSeek |
| `a69c908` | 13:35 | gate-1.4 LangGraph assembly + PostgresSaver checkpointing |
| `01f0fd6` | 13:46 | gate-1.5 scheduler, JSON logging, status CLI, runbook |

9 commits. Code volume ≈ 3,434 lines of Python (2,039 src + 1,380 tests + 15
seed script), 60 tracked files.

## 3. Token usage (actual session telemetry)

Usage recorded in the Claude Code session transcript for this build (single
session, file `1471f683…jsonl`; the model for the build agent is
`deepseek-v4-flash`):

| Requests | Uncached input | Cached input (context re-reads) | Output | Total processed |
|---|---|---|---|---|
| 823 | 731,107 | 116,129,408 | 1,212,633 | 118,073,148 |

Notes on the numbers:

- **823 model requests** ≈ one per build step (agent turns + tool calls).
- **~98% of input is cached context re-reads** — a long, gated, single-context
  build re-reads its ~140K-token context every step from the prompt cache. That
  is normal for this workflow and bills at the discounted cache-read rate.
- **1.21M output tokens** is the actual generation (code, summaries, prompts).
- The **exact billable cost** is available in the platform's usage console;
  it depends on the cache-read / cache-write / standard pricing tiers.
- This covers **building** the solution only. The pipeline's *runtime* usage is
  separate and small: live demos made a few hundred `deepseek-chat` labeling
  calls (Gate 1.3: 10 stories; Gate 1.4: 100 + 100) at ~$0.03–$0.05 total.

The sibling `genai-news-pipeline-dcode` build documents its own session
telemetry in its `SYSTEM_DOCUMENTATION.md`; this project is the independent,
parallel build of the same spec in this repository.
