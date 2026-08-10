# CLAUDE CODE — BUILD PROMPT: GenAI/ML News Ingestion Pipeline, Phase 1

## Your role and mission

You are building **Phase 1 of an hourly GenAI/ML news ingestion pipeline** on this server. Phase 1 is a complete end-to-end vertical slice: fetch news from a small set of zero-auth sources → normalize → deduplicate → label with an LLM → persist to Postgres, orchestrated by LangGraph with durable checkpointing, triggered hourly by APScheduler.

You will work in **gated sub-phases**. This is mandatory:

> **GATE PROTOCOL (applies after EVERY sub-phase):**
> 1. STOP all implementation work.
> 2. Present a concise summary: what was built, key design decisions made, deviations from this spec (if any, with justification), file tree of what changed, and the exact commands I can run to verify (including expected output).
> 3. Run your own verification (tests, a demo invocation) and show me the actual output.
> 4. List any open questions or decisions you want my input on.
> 5. Then WAIT. Do not begin the next sub-phase until I explicitly reply with **"proceed"** (or give feedback — in which case apply the feedback, re-verify, re-present, and wait again).

Never merge sub-phases, never work ahead "while waiting," never silently expand scope.

## Environment and constraints

- **Working directory:** create a new project folder at `/home/agate/newsfeedagents/genai-news-pipeline-claudecode`. All code lives there.
- **Language/stack:** Python 3.11+, managed with `uv` (fall back to `venv` + `pip` if `uv` unavailable). LangGraph + langchain-core + langchain-openai for orchestration and labeling. Postgres for storage (check if a local Postgres is running; if not, set one up via Docker Compose in the project — ask me at the Gate 1.0 if you're unsure which I prefer). `psycopg` / SQLAlchemy Core for relational access. APScheduler for scheduling. `feedparser` + `httpx` for fetching.
- **Secrets:** all config via `.env` (python-dotenv), never hardcoded. Create `.env.example` documenting every variable. I will fill in `DEEPSEEK_API_KEY` and DB credentials myself — if a key is missing, build everything and mock/skip only the affected call in tests; do not invent keys.
- **LLM for labeling:** `deepseek-chat` (DeepSeek-V3 — the cost-effective model) via `langchain-openai`'s `ChatOpenAI` against DeepSeek's OpenAI-compatible endpoint, using `.with_structured_output()`.
- **Code standards:** type hints everywhere, `ruff` for lint/format, `pytest` for tests, small modules, no dead code. Each node in the graph must be a thin adapter over plain, independently testable functions (framework-replaceable at the edges).
- **Scope discipline — explicitly OUT of Phase 1:** no Reddit, no Hugging Face, no Tavily/Exa/Brave, no arXiv, no embeddings/pgvector dedup, no alerting, no web UI. If you think something out-of-scope is needed, raise it at a gate; do not build it.

## Phase 1 sources (seed data — exactly these, no more)

All zero-auth. Seed them into the source registry in sub-phase 1.1:

| name | method | config |
|---|---|---|
| TechCrunch AI | rss | feed URL for the AI category |
| VentureBeat AI | rss | feed URL for the AI channel |
| The Verge | rss | main feed (filter AI-relevant downstream via labeling) |
| Anthropic Blog | rss | official blog/news feed |
| OpenAI Blog | rss | official blog/news feed |
| Hacker News | hn_algolia | Algolia API `https://hn.algolia.com/api/v1/` — (a) search_by_date query for AI/LLM/GenAI keywords, (b) current front page for cross-referencing |
| Google News query: "generative AI" | google_news_rss | `https://news.google.com/rss/search?q=...` |
| Google News query: "large language model" | google_news_rss | same pattern |

Verify each feed URL actually resolves at build time; if one is wrong/moved, find the correct one and note the change at the gate.

---

## SUB-PHASE 1.0 — Scaffolding, database, schema

**Deliverables:**

1. Project skeleton at `/home/agate/newsfeedagents/genai-news-pipeline-claudecode`:
   ```
   pyproject.toml, .env.example, .gitignore, README.md
   docker-compose.yml            # Postgres 16 (if no local PG — confirm at gate)
   src/newspipe/
     config.py                   # pydantic-settings, all env-driven
     db/  (engine.py, migrations/)
     models/ (schemas.py)        # Pydantic domain models
   tests/
   ```
2. Git repo initialized, first commit. Commit at every gate thereafter with message `gate-1.x: <summary>`.
3. **Database DDL** (plain SQL migration files, applied via a tiny migration runner or `alembic` — your call, justify at gate):
   - `sources` — source registry: `source_id PK, name, method (rss|hn_algolia|google_news_rss), config JSONB, poll_interval_minutes INT DEFAULT 60, last_polled_at TIMESTAMPTZ, enabled BOOL DEFAULT true, created_at`.
   - `arrivals` — every raw item ever fetched, append-only: `arrival_id PK, source_id FK, external_id TEXT, url TEXT, url_canonical TEXT, title TEXT, published_at TIMESTAMPTZ, fetched_at TIMESTAMPTZ, raw JSONB, story_id FK NULL`. Unique constraint on `(source_id, external_id)` to make fetching idempotent.
   - `stories` — canonical deduplicated stories: `story_id PK, canonical_url TEXT UNIQUE, title TEXT, first_seen_at, last_seen_at, arrival_count INT DEFAULT 1, hn_front_page BOOL DEFAULT false`.
   - `labels` — one row per labeling of a story: `label_id PK, story_id FK, is_hot BOOL, importance SMALLINT CHECK 1..10, category TEXT, rationale TEXT, model TEXT, prompt_version TEXT, labeled_at`. (Separate table, not columns on `stories`, so relabeling/evals are possible later.)
   - `pipeline_runs` — `run_id PK, thread_id TEXT, started_at, finished_at, status, stats JSONB`.
   - Sensible indexes: `arrivals(url_canonical)`, `arrivals(story_id)`, `stories(first_seen_at)`, `labels(story_id, labeled_at)`.
4. `config.py` loading DB URL, DeepSeek key, model name, batch concurrency, from env.
5. Smoke test: `pytest` test that connects to the DB and confirms all tables exist.

**→ GATE 1.0.** Show me the schema (actual `\d+` output), the file tree, and your migration-tool decision. Wait.

---

## SUB-PHASE 1.1 — Fetcher layer + source seeding

**Deliverables:**

1. `src/newspipe/fetchers/` with **one fetcher per method type, not per source**:
   - `base.py` — common signature: `fetch(source_row) -> list[RawItem]` where `RawItem` is a Pydantic model `(external_id, url, title, published_at, raw)`. Shared httpx client with timeout, retry (bounded, exponential), and a custom User-Agent.
   - `rss.py` — feedparser-based; `external_id` = entry id or link hash.
   - `hn_algolia.py` — keyword search via `search_by_date` (window: since source's `last_polled_at`, capped at 24h) **plus** front-page fetch; front-page hits marked in `raw` so dedup can set `stories.hn_front_page`.
   - `google_news_rss.py` — RSS subclass handling Google's redirect URLs (extract the real target URL when possible; keep the Google URL as fallback).
2. Seed script `scripts/seed_sources.py` inserting exactly the Phase 1 source table above (idempotent upsert).
3. Persistence of arrivals: `INSERT ... ON CONFLICT (source_id, external_id) DO NOTHING`, so re-fetching is safe. Update `sources.last_polled_at` after a successful fetch.
4. A failing source must never break the run: per-source try/except, error recorded in run stats.
5. Tests: unit tests for each fetcher against **saved fixture payloads** (record one real response per fetcher into `tests/fixtures/`), plus one live integration test per fetcher marked `@pytest.mark.live`.
6. CLI: `python -m newspipe fetch` runs all due fetchers once and prints per-source counts.

**→ GATE 1.1.** Run `python -m newspipe fetch` live, show me per-source arrival counts and 3 sample rows from `arrivals`. Wait.

---

## SUB-PHASE 1.2 — Normalization + dedup v1

**Deliverables:**

1. `normalize.py`: URL canonicalization — lowercase host, strip `utm_*`/`fbclid`/tracking params, strip fragments, resolve `http→https`, trailing-slash policy; title normalization (whitespace, unicode NFKC) and `title_hash` (sha256 of normalized title).
2. `dedup.py` — v1 logic, exact-match only (no embeddings):
   - Match an arrival to an existing story by `url_canonical` first, then by `title_hash` within a 72h window of `stories.first_seen_at`.
   - Match → attach `arrival.story_id`, increment `arrival_count`, update `last_seen_at`, set `hn_front_page` if applicable.
   - No match → create new story.
   - Must be race-safe for concurrent execution (advisory lock or `ON CONFLICT` upsert pattern — justify your choice at the gate).
3. Backfill: dedup runs over all currently unattached arrivals, so items fetched in 1.1 get storified.
4. Tests: same URL from two sources collapses; same title different URL within window collapses; different stories don't; tracking-param variants collapse.
5. CLI: `python -m newspipe dedup`, printing stories created/updated.

**→ GATE 1.2.** Show me a real example from the DB of one story with `arrival_count >= 2` and its arrivals. Wait.

---

## SUB-PHASE 1.3 — LLM labeling

**Deliverables:**

1. `labeling/schema.py`:
   ```python
   class HeadlineLabel(BaseModel):
       is_hot: bool  # major/breaking GenAI-ML event vs routine
       importance: int  # 1-10, Field(ge=1, le=10)
       category: Literal[
           "model_release",
           "research",
           "industry",
           "funding",
           "policy_regulation",
           "tooling_infra",
           "other",
       ]
       is_genai_ml_relevant: bool  # The Verge/GNews bring non-AI items; filter here
       rationale: str  # one sentence
   ```
2. `labeling/labeler.py`: `init_chat_model` → `.with_structured_output(HeadlineLabel)`; prompt as a versioned constant (`PROMPT_VERSION = "p1"`) stored into `labels.prompt_version`. Prompt receives: title, source names it arrived from, arrival_count, hn_front_page — cross-source arrival is an explicit importance signal, and the prompt must say so.
3. Batch execution over unlabeled stories via `.abatch` with `max_concurrency` from config; `.with_retry()` for transient failures; a story that fails labeling stays unlabeled (picked up next run), never blocks the batch.
4. Persist one `labels` row per story per labeling.
5. Tests: schema validation tests; labeler test with the model call mocked; one `@pytest.mark.live` test labeling 3 real stories (skipped cleanly if `DEEPSEEK_API_KEY` unset).
6. CLI: `python -m newspipe label`, printing labeled count + a table of (title, is_hot, importance, category).

**→ GATE 1.3.** Run live labeling on ~10 real stories, show me the labeled table, and flag any labels you disagree with (that's prompt feedback for me). Wait.

---

## SUB-PHASE 1.4 — LangGraph assembly + checkpointing

**Deliverables:**

1. `graph/state.py` — TypedDict state: `run_id, due_source_ids, fetch_results (Annotated[list, operator.add]), new_arrival_ids, affected_story_ids, labeled_story_ids, errors (Annotated[list, operator.add]), stats`.
2. `graph/build.py` — `StateGraph`:
   ```
   select_due_sources → [Send fan-out: fetch_source per source] → dedup → label → finalize
   ```
   - `select_due_sources`: query registry for `enabled AND (last_polled_at IS NULL OR last_polled_at + poll_interval < now())`.
   - `fetch_source` (one Send per source): fetch + persist arrivals; on error, append to `errors`, return empty — never raise.
   - `dedup` and `label` nodes: thin wrappers over 1.2/1.3 functions.
   - `finalize`: write `pipeline_runs` row with stats (per-source counts, new stories, labeled, errors, duration).
3. **`PostgresSaver` checkpointer** wired in; `thread_id` = `run-YYYYMMDD-HH` so re-invoking the same hour resumes rather than restarts.
4. CLI: `python -m newspipe run` (one full graph invocation) and `python -m newspipe run --resume <thread_id>`.
5. **Crash-resume proof:** a test (or scripted demo) that kills the process after fetch but before label, re-invokes with the same thread_id, and shows fetch is not re-executed. This is the acceptance test of the whole sub-phase.
6. Optional-but-preferred: LangSmith tracing enabled purely via env vars if I set them; zero code coupling.

**→ GATE 1.4.** Demo: one clean full run (show `pipeline_runs` stats), then the kill-and-resume demo with logs proving checkpoint recovery. Wait.

---

## SUB-PHASE 1.5 — Scheduler, hardening, runbook

**Deliverables:**

1. `scheduler.py`: APScheduler `BlockingScheduler`, hourly cron trigger at minute 5, `max_instances=1`, `coalesce=True`, `misfire_grace_time=600`. Each tick invokes the graph with the hour-slot thread_id.
2. Process management: a `systemd` unit file (`deploy/newspipe.service`) running the scheduler under my user, `Restart=on-failure`, env file loading — install instructions in README, but **do not enable it without asking me at the gate**.
3. Structured logging (JSON lines) to stdout + rotating file in `logs/`; log per-run summary at INFO, per-source detail at DEBUG.
4. Operational CLI: `python -m newspipe status` — last 5 runs from `pipeline_runs`, unlabeled backlog count, per-source last success, error tail.
5. Failure-mode review: confirm and document in README what happens when (a) a feed 404s, (b) Postgres is briefly down, (c) DeepSeek API is rate-limited, (d) two runs would overlap. Fix anything not already handled gracefully.
6. Full test suite green; `ruff` clean; README complete: setup from scratch, all CLI commands, schema diagram (ASCII fine), 72h soak-test checklist (what I should check after 3 days of unattended running).

**→ GATE 1.5 (final).** Present: full demo of `status` after at least 2 real scheduled/manual runs, the failure-mode table, and your recommendation list for Phase 2 prerequisites (e.g., pgvector install). Then wait for my sign-off before enabling the systemd timer.

---

## Standing rules (all sub-phases)

- Ask me at a gate whenever a decision is architecturally significant (migration tool, Docker vs local PG, lock strategy). For trivial choices, decide and note it.
- If a spec detail here turns out to be wrong or impossible (dead feed URL, API change), adapt minimally and report the deviation at the gate — never silently.
- Keep every gate's summary short enough to read in 2 minutes; put detail in the code and README, not the gate message.
- Do not `git push` anywhere, do not open ports beyond Postgres on localhost, do not install system-wide packages without listing them at the gate.

Begin with **Sub-phase 1.0** now.
