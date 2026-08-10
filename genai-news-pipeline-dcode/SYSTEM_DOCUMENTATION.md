# newspipe — System Documentation

How the Phase 1 GenAI/ML news ingestion pipeline works, how long it took to
build, and rough token usage. Companion to `README.md` (setup, commands,
failure modes, soak checklist).

---

## 1. How the system works

### 1.1 Purpose
Phase 1 is a complete end-to-end vertical slice: fetch news from zero-auth
sources → normalize → deduplicate → label with an LLM → persist to Postgres,
orchestrated by **LangGraph** with durable checkpointing, scheduled via
**APScheduler**.

### 1.2 Data flow (one run)

```
sources registry (Postgres)
   │  select due (enabled AND last_polled_at + interval <= now())
   ▼
select_due_sources  ──Send fan-out──▶ fetch_source ×N (one per due source)
   │                                     │  fetcher by method type
   │                                     │  insert arrivals (ON CONFLICT DO NOTHING)
   │                                     │  update last_polled_at
   │                                     ▼
   └──────────────▶ dedup ──▶ label ──▶ finalize ──▶ pipeline_runs row
                     │          │
                     │          └ labels (is_hot, importance 1-10, category,
                     │                     is_genai_ml_relevant, rationale,
                     │                     model, prompt_version)
                     └ stories (canonical_url, title_hash, arrival_count,
                                first/last_seen_at, hn_front_page)
```

Graph: `select_due_sources → [Send fan-out: fetch_source per source] → dedup → label → finalize`

### 1.3 Stages in detail

1. **Fetch** (`src/newspipe/fetchers/`): one fetcher per *method*, not per
   source. `base.py` shares an httpx client with bounded exponential retry
   (3 attempts, 1s→8s backoff, transport + 5xx) and a custom User-Agent.
   - `rss.py` — feedparser; `external_id` = entry id or sha256 of link.
   - `hn_algolia.py` — `search_by_date` per keyword (window = last_polled_at,
     capped 24h) + front-page fetch; front-page hits flagged in `raw` so
     dedup can set `stories.hn_front_page`.
   - `google_news_rss.py` — RSS subclass; tries to extract the real target
     URL from Google's opaque tokens (modern tokens no longer decode; the
     Google URL is kept as fallback).
   Arrivals are inserted with `INSERT … ON CONFLICT (source_id, external_id)
   DO NOTHING`, so re-fetching is idempotent. A failing source never breaks
   the run (per-source try/except, error recorded in stats).

2. **Normalize** (`normalize.py`): URL canonicalization (lowercase host,
   http→https, drop default ports, strip `utm_*`/`fbclid`/`gclid`/`mc_cid`/
   `mc_eid` + Google News redirect params, strip fragments, trailing-slash
   policy, sort remaining query params) and title normalization (NFKC +
   whitespace collapse) → `title_hash` (sha256).

3. **Dedup v1** (`dedup.py`): exact-match only (no embeddings). Each
   unattached arrival matches an existing story by `url_canonical` first,
   then by `title_hash` within 72h of `stories.first_seen_at`. Match →
   attach `story_id`, `arrival_count+1`, update `last_seen_at`, set
   `hn_front_page`. No match → new story. The whole pass holds a Postgres
   advisory xact lock (fixed key) so concurrent dedup runs serialize.

4. **Label** (`labeling/`): `init_chat_model(deepseek-chat,
   provider=deepseek).with_structured_output(HeadlineLabel).with_retry()`.
   Prompt version `p1` stored in `labels.prompt_version`; the prompt receives
   title, source names, arrival_count, hn_front_page and treats cross-source
   arrival as an importance signal. Batched via `.abatch` with
   `max_concurrency`, per-item fallback; a failed story stays unlabeled for
   the next run. Labeling is capped at `LABEL_LIMIT_PER_RUN` (default 100)
   per run.

5. **Orchestration + checkpointing** (`graph/`): `PostgresSaver`
   checkpointer with `thread_id = run-YYYYMMDD-HH`; re-invoking the same
   slot resumes from the checkpoint (`run --resume <thread_id>`, passes
   `None` as input) instead of restarting — a crash between nodes does not
   re-execute completed nodes. `finalize` writes `pipeline_runs`
   (status, per-source stats, new/updated stories, labeled, errors,
   duration).

6. **Scheduling** (`scheduler.py`): APScheduler `BlockingScheduler`,
   cron from `SCHEDULE_CRON` (default `5 0,12 * * *` = every 12h at 00:05
   and 12:05 UTC), `max_instances=1`, `coalesce=True`,
   `misfire_grace_time=600` so runs never overlap and missed runs coalesce.
   Runs as a systemd service (`deploy/newspipe.service`, Restart=on-failure,
   EnvironmentFile=.env).

### 1.4 Schema (Postgres 16)

```
sources (source_id PK, name UNIQUE, method, config JSONB,
         poll_interval_minutes, last_polled_at, enabled, created_at)
arrivals (arrival_id PK, source_id FK, external_id, url, url_canonical,
          title, published_at, fetched_at, raw JSONB, story_id FK,
          UNIQUE(source_id, external_id))
stories (story_id PK, canonical_url UNIQUE, title, title_hash, first_seen_at,
         last_seen_at, arrival_count, hn_front_page)
labels (label_id PK, story_id FK, is_hot, importance 1-10 CHECK, category,
        is_genai_ml_relevant, rationale, model, prompt_version, labeled_at)
pipeline_runs (run_id PK, thread_id UNIQUE, started_at, finished_at,
               status, stats JSONB)
schema_migrations (migration runner bookkeeping)
checkpoints / checkpoint_writes / checkpoint_blobs (LangGraph PostgresSaver)
```

Migrations are plain SQL files in `src/newspipe/db/migrations/`, applied by a
tiny runner in filename order within one transaction each.

### 1.5 Configuration (`.env`, gitignored)
`DATABASE_URL`, `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL=deepseek-chat`,
`BATCH_CONCURRENCY=5`, `LABEL_LIMIT_PER_RUN=100`, `SCHEDULE_CRON`. See
`.env.example`.

### 1.6 CLI
`migrate` · `fetch` · `dedup` · `label [--limit N]` · `run [--thread X |
--resume X]` · `schedule` · `status`

### 1.7 Testing
`uv run pytest` (DB required; live network tests skipped) / `uv run pytest
--live`. 67 tests: schema, fetchers (fixtures + live), fetch runner, dedup,
normalization, labeling (mocked chain + live), graph crash-resume
acceptance, scheduler config, logging, status.

### 1.8 Tech stack

| Layer | Choice |
|---|---|
| Language / runtime | Python 3.11+ (managed with `uv`; `.python-version` pins 3.12) |
| Orchestration | LangGraph (StateGraph + Send fan-out) with `PostgresSaver` checkpoints |
| LLM labeling | langchain-core + `init_chat_model(deepseek-chat, provider=deepseek)` via langchain-deepseek, `.with_structured_output(HeadlineLabel)` |
| Database | Postgres 16 (Docker Compose, localhost-only), SQLAlchemy Core + psycopg[binary], langgraph-checkpoint-postgres |
| Scheduling | APScheduler `BlockingScheduler` (cron from `SCHEDULE_CRON`), systemd unit |
| Ingestion | feedparser (RSS), httpx (HN Algolia API, Google News RSS), custom fetcher base with bounded retry |
| Config / typing | pydantic-settings, python-dotenv, Pydantic v2 models |
| Tooling | ruff (lint), pytest (+ `--live` marker), hatchling build backend |

### 1.9 Codebase metrics (lines of code)

Actual counts as of the last commit (excludes `.venv/`, `.git/`, caches, logs):

| Area | Files | Lines |
|---|---|---|
| Application (`src/newspipe/`) | 27 | 1,619 |
| Tests (`tests/`) | 15 | 1,453 |
| **Python total** | 42 | **3,072** |
| SQL migrations (`src/newspipe/db/migrations/`) | 3 | 67 |
| Seed script (`scripts/seed_sources.py`) | 1 | 97 |
| Ops/config (pyproject.toml, docker-compose.yml, systemd unit, .gitignore, .python-version) | 5 | 109 |
| Docs (README.md, SYSTEM_DOCUMENTATION.md, buildprompt.md) | 3 | 573 |

By application module (`src/newspipe/`): graph 264, fetchers 282, labeling
206, db 152, models 34, top-level modules (cli, config, normalize, dedup,
scheduler, status, logging, fetch) 681. ~10 commits total.

---

## 2. Development time

Wall-clock span from the first scaffolding to the final commit:
**~4.5 hours** (2026-08-09 16:25 → 21:02 UTC), gated at each sub-phase per
the build protocol.

Git commit timeline (build-order evidence):

| Commit | Time (UTC) | What |
|---|---|---|
| `73a5597` | 16:35 | gate-1.0 project skeleton, DB schema, migration runner |
| `888bda8` | 17:18 | gate-1.1 fetcher layer + source seeding |
| `009ff32` | 18:34 | gate-1.2 normalization + dedup v1 |
| `d7edfc9` | 19:58 | gate-1.3 LLM labeling |
| `845cf22` | 20:10 | gate-1.3 provider swap Anthropic → DeepSeek |
| `a538849` | 20:32 | gate-1.4 LangGraph + checkpointing |
| `8842ac9` | 20:39 | gate-1.4 label cap (100/run) |
| `824695b` | 20:49 | gate-1.5 scheduler, hardening, runbook |
| `9d099a2` | 21:02 | SCHEDULE_CRON configurable (every 12h default) |

Note: the 4.5h includes the required gate pauses while awaiting user
approval between sub-phases; active implementation was continuous during
each sub-phase. Code volume ≈ 3,072 lines of Python (1,619 src + 1,453
tests), 10 commits.

---

## 3. Token usage (actual session telemetry)

Usage stats for the agent session that built the project:

| Provider | Model | Requests | Input Tokens | Output Tokens | Cost |
|---|---|---|---|---|---|
| deepseek | deepseek-v4-flash | 322 | 40.2M | 235.1K | $0.22 |

Agent active time: **4h 23m 53s**.

(The earlier estimate below is kept for reference only; the table above is
the exact platform figure.)

- **Estimated total tokens processed across the session (input + output +
  tool results): ~400K–550K** (model context window: 1M).
- Roughly half of that is tool results (fixture payloads, DB `\d+` output,
  live-run outputs, tracebacks during debugging).
- Code written/reviewed ≈ 3,000 lines across the repo, which is the bulk of
  the output tokens.
