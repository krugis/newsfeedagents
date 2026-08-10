# Build Comparison — `genai-news-pipeline-dcode` vs `genai-news-pipeline-claudecode`

Two independent implementations of the **same** Phase 1 spec (an hourly
GenAI/ML news pipeline: fetch → normalize → dedup → LLM label → Postgres,
orchestrated by LangGraph with durable checkpointing, scheduled by APScheduler),
built back-to-back on 2026-08-09 (dcode) and 2026-08-10 (claudecode).

This document compares the two builds on **build cost/effort**, **architecture**,
and **code quality**. Sources: each project's `SYSTEM_DOCUMENTATION.md`, the
Claude Code session telemetry, and a direct code scan of both trees.

---

## 1. Executive summary

Both builds fully satisfy the spec across every gated sub-phase (1.0 → 1.5),
and both are deployable. They differ mainly in trade-offs:

- **dcode** is the leaner build: roughly **3× fewer LLM requests**, **~5× less
  output**, and a measured cost of **$0.22**. It uses SQLAlchemy, tenacity, and
  the native `langchain-deepseek` provider, and ships a configurable scheduler
  (`SCHEDULE_CRON`). Its weaknesses are a silently-swallowed 404 path, a stale
  `ANTHROPIC_API_KEY` string left after the provider swap, and ruff *format*
  not being enforced.
- **claudecode** is the more thorough build: it actually covers **all 8 sources**
  (including Anthropic Blog via a new `sitemap` fetcher — dcode disabled it),
  reports feed 404s as source errors instead of swallowing them, enforces both
  ruff lint *and* format, and carries more live tests (9 vs 4). Its costs are
  heavier tokens and a longer wall-clock session (including a ~4 h idle gap
  awaiting the provider-swap decision).

**Bottom line:** pick claudecode if full source coverage and strict hygiene
matter most; pick dcode if token economy and a configurable schedule matter
most. The two are complementary — merging the best of each (section 7) is
better than choosing one.

| Scorecard | dcode | claudecode |
|---|---|---|
| Spec coverage (gates 1.0–1.5) | ✅ all | ✅ all |
| Source coverage | 7 sources (Anthropic disabled) | **8 sources (incl. Anthropic)** |
| Correct 404 handling | ❌ swallowed silently | ✅ reported as source error |
| Lint + format enforced | lint only | **lint + format** |
| Live tests | 4 | **9** |
| Crash-resume acceptance test | ✅ | ✅ |
| Token economy | **much better** | heavier |
| Scheduler configurability | **configurable (`SCHEDULE_CRON`)** | hourly, fixed |
| Billed cost | **$0.22 (measured)** | not measured (console) |

---

## 2. Build metrics

The most comparable numbers from each build's session telemetry. dcode's
figures are quoted from its `SYSTEM_DOCUMENTATION.md`; claudecode's are summed
from the actual Claude Code session transcript (`1471f683….jsonl`).

| Metric | dcode | claudecode |
|---|---|---|
| Build date | 2026-08-09 | 2026-08-10 |
| **Build duration** (wall-clock, incl. mandated gate pauses) | ~4.5 h (16:25 → 21:02 UTC) | ~6.6 h commit span (07:09 → 13:46 UTC); session span 7 h 55 m (07:01 → 14:57) |
| **Active effort** | 4 h 23 m 53 s (measured) | not separately instrumented; includes a ~4 h idle gap awaiting the Anthropic→DeepSeek decision |
| Build commits (gate history) | 10 | 9 (+1 docs) |
| **LLM requests** (build-session API calls) | **322** | **823** |
| **Input tokens processed** (context + cache reads) | 40.2 M | 116.9 M (0.73 M fresh + 116.1 M cached reads) |
| **Output tokens generated** (responses) | 235.1 K | 1.21 M |
| Token efficiency (output per request) | ~730 | ~1,473 |
| **Build cost** | **$0.22 (reported)** | not measured — verify in usage console; ≈$0.6–0.8 *if* billed at dcode's effective per-token rate (estimate only) |
| Build model | `deepseek-v4-flash` | `deepseek-v4-flash` |

Notes on the numbers:

- **Requests & tokens**: claudecode made ~2.5× the requests and generated ~5×
  the output tokens. The higher output reflects more iteration, more verbose
  agent responses, and the extra work of the sitemap fetcher + live-test
  coverage. Roughly **99% of claudecode's input is cached context re-reads** —
  normal for a long single-context build and billed at the discounted cache
  rate.
- **Cost**: dcode's $0.22 is the one measured figure. claudecode's cost is not
  in any transcript field (billing lives in the usage console); the ~$0.6–0.8
  figure is a back-of-envelope scale of dcode's rate to claudecode's token
  volumes and should be treated as an estimate, not a bill.

---

## 3. Build process comparison

| Aspect | dcode | claudecode |
|---|---|---|
| Gate protocol | Followed — stop + summary + wait for "proceed" at each sub-phase | Followed — same protocol, one extra user-directed gate (provider swap) |
| Provider swap | Anthropic → DeepSeek mid-build (`langchain-deepseek`) | Anthropic → DeepSeek mid-build (`langchain-openai` against the OpenAI-compatible endpoint) |
| Notable mid-build decisions | Made scheduler cadence configurable (12 h default) — reported as a deviation | Added `sitemap` fetcher to cover Anthropic Blog; forced `function_calling` structured output for DeepSeek |
| Documentation discipline | `SYSTEM_DOCUMENTATION.md` with session telemetry + cost | `SYSTEM_DOCUMENTATION.md` with transcript-derived telemetry (no measured cost) |

Both made the same real-world discovery: DeepSeek is the cost-effective
labeling provider, and Anthropic's blog has no public RSS.

---

## 4. Architecture & implementation comparison

| Stage | dcode | claudecode |
|---|---|---|
| **DB access** | SQLAlchemy Core | raw psycopg 3 (`dict_row`) |
| **Fetchers** | `rss`, `hn_algolia`, `google_news_rss` (3 methods) | `rss`, `hn_algolia`, `google_news_rss`, **`sitemap`** (4 methods) |
| **Anthropic Blog** | Disabled (`enabled: False`), note to defer to Phase 2 | **Fetched live** via XML sitemap filtered to `/news/` |
| **Retry strategy** | tenacity (3 attempts, exp 1→8 s, transport + 5xx) | httpx built-in retry (3 attempts, exp backoff, `429/500/502/503/504`) + langchain `.with_retry()` |
| **404 handling** | ❌ not raised — feed yields 0 items, still marks polled | ✅ raises → recorded as a source error, `last_polled_at` not bumped |
| **LLM provider** | native `langchain-deepseek`, `deepseek-chat` | `langchain-openai` `ChatOpenAI(base_url=api.deepseek.com)`, `deepseek-chat` |
| **Structured output** | `.with_structured_output()` | `.with_structured_output(…, method="function_calling")` (forced — DeepSeek rejects `json_schema`) |
| **Label batch** | capped 100/run, `abatch(max_concurrency=5)` + per-item `ainvoke` fallback | capped 100/run, `abatch(max_concurrency=8, return_exceptions=True)` |
| **Dedup v1** | canonical URL → 72 h title-hash, one xact under `pg_advisory_xact_lock` | same strategy, same lock |
| **Graph / checkpointing** | LangGraph + `PostgresSaver`, hour-slot `thread_id`, crash-resume via env-kill simulation | LangGraph + `PostgresSaver`, hour-slot `thread_id`, crash-resume modeled with `interrupt_before` |
| **Empty-due-set path** | not highlighted | explicitly hops to `dedup` (an empty Send list would end the graph) |
| **Scheduler** | `SCHEDULE_CRON` configurable (default every 12 h at :05), `max_instances=1`, `coalesce`, misfire 600 | fixed hourly at minute 5 (per spec), same hardening flags |
| **Migrations** | 3 (incl. `labels.is_genai_ml_relevant`) | 2 (incl. `sources.method` gains `sitemap`) |
| **Logging** | JSON-lines to stdout + rotating file | JSON-lines to stdout + rotating file |

Both follow the same overall shape (thin graph nodes over plain, testable
functions), which reflects the shared spec more than any design overlap — the
two trees were built independently.

---

## 5. Code quality analysis

Methodology: direct read of both source trees, `ruff` runs on each, test
inventory, and the recorded session telemetry. Nothing was modified.

### 5.1 Static measures

| Measure | dcode | claudecode |
|---|---|---|
| App code (`src/newspipe/`) | 27 files / 1,619 lines | 31 files / 2,039 lines |
| Tests | 14 modules / 1,419 lines | 12 modules / 1,380 lines |
| Test suite size | 71 collected (67 offline + 4 live) | 71 collected (62 offline + 9 live) |
| Lint (`ruff check`) | ✅ clean | ✅ clean |
| Format (`ruff format --check`) | ❌ 11 files would reformat | ✅ clean |
| Test DB isolation | real Postgres integration tests with cleanup | real Postgres integration tests with cleanup (incl. checkpoint rows) |

### 5.2 Findings — dcode

**Strengths**
- Clean layering: graph nodes are thin adapters over testable plain functions.
- Per-source failure isolation with stats; dedup serialized by an advisory
  transaction lock; idempotent arrivals (`ON CONFLICT DO NOTHING`).
- Labeling has a per-item `ainvoke` fallback when a chunk fails.
- Exceptional README (11.6 KB): setup, runbook, failure modes, 72 h soak list.

**Issues**
1. **404s are silently swallowed** (`fetchers/base.py:70-72`) — only `>=500`
   raises, so a dead feed returns 0 items, no error is recorded, and the source
   is still marked polled (`fetch.py:22`). Contradicts its own README §404 claim.
2. **Stale provider string** — `__main__.py:54` still prints
   "`ANTHROPIC_API_KEY` not set" after the DeepSeek swap (it is
   `DEEPSEEK_API_KEY`).
3. **No `UNIQUE(story_id)` on `labels`** + non-atomic select/persist in
   `run_label` (`labeler.py:165-170`) → two concurrent manual `label` runs can
   double-insert a label.
4. **Format not enforced** — `ruff format --check` fails on 11 files; only lint
   is configured.
5. Unbounded fetch fan-out (one `Send` per due source, no cap).
6. Minor: dead `utc_now()` (`fetchers/base.py:40`); `canonicalize_url`
   preserves URL userinfo; `saver.setup()` re-runs checkpoint DDL each tick.

### 5.3 Findings — claudecode

**Strengths**
- Correct 404 semantics: non-retryable 4xx raises → `fetch_source` catches →
  error lands in `state.errors` → source is *not* marked polled.
- Both `ruff check` and `ruff format --check` are clean and enforced.
- Crash-resume acceptance test is robust on LangGraph 1.1 (uses
  `interrupt_before` — a raising node does not leave a resumable checkpoint in
  this version), plus an explicit empty-due-set path.
- More live coverage: 9 live tests (one per fetcher + real DeepSeek labeling of
  3 stories), all skipping cleanly without `DEEPSEEK_API_KEY`.
- Cost-effective labeling: `deepseek-chat`, 10 stories ≈ a few cents.

**Issues**
1. **Same `labels.story_id` uniqueness gap** as dcode — no `UNIQUE(story_id)`;
   two concurrent label runs could double-insert. (The scheduler is protected by
   `max_instances=1`; the manual CLI is not.)
2. **Token-heavy build**: 823 requests / 1.21 M output vs dcode's 322 / 235 K —
   less economical, partly from extra iteration and verbose responses.
3. Scheduler cadence is **hard-coded hourly** — less configurable than dcode's
   `SCHEDULE_CRON`.
4. `BATCH_CONCURRENCY=8` (vs dcode's 5) — more aggressive parallel DeepSeek
   usage during backfill.
5. Uses raw psycopg instead of SQLAlchemy — simpler, but more hand-written SQL
   and no ORM-style tooling if the schema grows.
6. Minor: `saver.setup()` also re-runs checkpoint DDL on every `run_pipeline`.

### 5.4 Shared observations

- Both are free of secrets in git (`.env` gitignored, verified untracked).
- Both centralize config in pydantic-settings and are fully env-driven.
- Both correctly serialize dedup with an advisory lock; both have the same
  theoretical concurrent-label double-insert gap.
- Neither caps fetch fan-out to sources (fine at 7–8 sources today).

---

## 6. Deliverable parity (per spec sub-phase)

| Sub-phase | dcode | claudecode |
|---|---|---|
| 1.0 skeleton + schema + migrations | ✅ | ✅ |
| 1.1 fetchers + seeding + fetch CLI | ✅ (7 sources) | ✅ (8 sources, +sitemap) |
| 1.2 normalization + dedup v1 | ✅ | ✅ |
| 1.3 LLM labeling + live tests | ✅ | ✅ |
| 1.4 LangGraph + PostgresSaver + crash-resume proof | ✅ | ✅ |
| 1.5 scheduler + systemd + JSON logging + `status` + failure modes + runbook | ✅ (configurable cron) | ✅ (hourly, per spec) |

Both built every deliverable; all deviations from the literal spec were
reported at the relevant gate.

---

## 7. Verdict & recommendations

**If you keep one as-is:** claudecode for correctness of failure handling and
full source coverage; dcode for token economy and a configurable schedule.

**Best of both (recommended merge):**
1. **From claudecode → dcode:** the correct 404 handling (raise on 4xx, don't
   mark polled), the `sitemap` fetcher for Anthropic Blog, enforced
   `ruff format`, and the extra live tests.
2. **From dcode → claudecode:** the configurable `SCHEDULE_CRON`, and consider
   dcode's per-item label fallback for extra labeling resilience.
3. **Both:** add `UNIQUE(story_id)` to `labels` (or an advisory lock around
   label selection) so concurrent manual runs can't double-insert; add a fetch
   fan-out cap if the source registry grows.

**Phase 2 suggestions** (identical for both): pgvector/embeddings for smarter
dedup (fixes the 72 h title-hash false-positive on repeated titles), a labeled
eval set for the prompt, and a read-only digest view over `labels`.

---

*Compiled 2026-08-10. dcode figures from its `SYSTEM_DOCUMENTATION.md`;
claudecode token figures summed from the Claude Code session transcript.
Code-quality findings from direct static review of both trees.*
