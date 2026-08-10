"""LangGraph assembly for the news pipeline.

Flow::

    select_due_sources -> [Send fan-out: fetch_source per source] -> dedup -> label -> finalize

Checkpointing: ``PostgresSaver`` with ``thread_id = run-YYYYMMDD-HH`` so
re-invoking the same hour slot resumes from the checkpoint instead of
restarting. Node functions are thin adapters over the plain, independently
testable functions from 1.1-1.3.

Crash simulation hook (used by the gate 1.4 acceptance test and demo):
setting ``NEWSPIPE_CRASH_AFTER_FETCH=1`` makes the label node raise, which
mimics the process dying after fetch but before label.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from functools import lru_cache

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from psycopg_pool import ConnectionPool
from sqlalchemy import text

from newspipe.config import get_settings
from newspipe.db.engine import get_engine
from newspipe.db.sources import select_due_sources, select_source_by_id
from newspipe.dedup import run_dedup
from newspipe.fetch import fetch_one_source
from newspipe.graph.state import PipelineState
from newspipe.labeling.labeler import run_label

THREAD_PREFIX = "run-"

_CREATE_OR_TOUCH_RUN = text(
    """
    INSERT INTO pipeline_runs (thread_id, status)
    VALUES (:thread_id, 'running')
    ON CONFLICT (thread_id) DO UPDATE SET status = 'running'
    RETURNING run_id
    """
)
_FINISH_RUN = text(
    """
    UPDATE pipeline_runs
    SET finished_at = now(), status = :status, stats = CAST(:stats AS jsonb)
    WHERE run_id = :run_id
    """
)
_RUN_STARTED_AT = text("SELECT started_at FROM pipeline_runs WHERE run_id = :run_id")


def hour_thread_id(now: datetime | None = None) -> str:
    """Hour-slot thread id, e.g. ``run-20260809-17``."""
    return THREAD_PREFIX + (now or datetime.now(UTC)).strftime("%Y%m%d-%H")


def select_due_sources_node(state: PipelineState, config: RunnableConfig) -> dict:
    """Select due sources and create/touch the pipeline_runs row."""
    thread_id = config["configurable"]["thread_id"]
    engine = get_engine()
    with engine.begin() as conn:
        sources = select_due_sources(conn)
        run_id = int(conn.execute(_CREATE_OR_TOUCH_RUN, {"thread_id": thread_id}).scalar_one())
    return {
        "run_id": str(run_id),
        "due_source_ids": [s.source_id for s in sources],
        "stats": {**state.get("stats", {}), "thread_id": thread_id},
    }


def route_fetch(state: PipelineState) -> list[Send] | str:
    """Fan out one Send per due source; skip fetch entirely if none are due."""
    due = state.get("due_source_ids") or []
    if not due:
        return "dedup"
    return [Send("fetch_source", {"source_id": sid}) for sid in due]


def fetch_source_node(payload: dict) -> dict:
    """Fetch + persist one source; never raises, errors go into state."""
    source_id = int(payload["source_id"])
    engine = get_engine()
    try:
        with engine.connect() as conn:
            source = select_source_by_id(conn, source_id)
        res = fetch_one_source(source)
        return {
            "fetch_results": [
                {
                    "source_id": source.source_id,
                    "name": source.name,
                    "fetched": res["fetched"],
                    "inserted": res["inserted"],
                }
            ],
            "new_arrival_ids": res["arrival_ids"],
        }
    except Exception as exc:  # noqa: BLE001 - a failing source never breaks the run
        return {
            "fetch_results": [
                {
                    "source_id": source_id,
                    "name": f"source {source_id}",
                    "fetched": 0,
                    "inserted": 0,
                    "error": str(exc),
                }
            ],
            "errors": [f"source {source_id}: {exc}"],
        }


def dedup_node(state: PipelineState) -> dict:
    """Thin wrapper over 1.2's dedup."""
    res = run_dedup()
    return {
        "affected_story_ids": res["affected_story_ids"],
        "stats": {
            **state.get("stats", {}),
            "dedup": {
                "arrivals_processed": res["arrivals_processed"],
                "stories_created": res["stories_created"],
                "stories_updated": res["stories_updated"],
                "errors": res["errors"],
            },
        },
    }


def label_node(state: PipelineState) -> dict:
    """Thin wrapper over 1.3's labeling; crash hook for the resume demo."""
    if os.environ.get("NEWSPIPE_CRASH_AFTER_FETCH") == "1":
        raise RuntimeError("simulated crash after fetch (NEWSPIPE_CRASH_AFTER_FETCH=1)")
    settings = get_settings()
    res = run_label(limit=settings.label_limit_per_run)
    return {
        "labeled_story_ids": list(res.get("results", {}).keys()),
        "stats": {
            **state.get("stats", {}),
            "label": {
                "skipped": res.get("skipped", False),
                "selected": res.get("selected", 0),
                "labeled": res.get("labeled", 0),
                "failed": res.get("failed", 0),
                "limit": settings.label_limit_per_run,
            },
        },
    }


def finalize_node(state: PipelineState, config: RunnableConfig) -> dict:
    """Write the pipeline_runs row: status, stats, duration."""
    engine = get_engine()
    run_id = int(state["run_id"])
    with engine.connect() as conn:
        started_at = conn.execute(_RUN_STARTED_AT, {"run_id": run_id}).scalar_one()
    duration = (datetime.now(UTC) - started_at).total_seconds()

    errors = state.get("errors") or []
    sources_stats: dict[str, dict] = {}
    for entry in state.get("fetch_results") or []:
        name = entry.get("name", f"source {entry.get('source_id')}")
        sources_stats[name] = {
            "fetched": entry.get("fetched", 0),
            "inserted": entry.get("inserted", 0),
        }
        if entry.get("error"):
            sources_stats[name]["error"] = entry["error"]

    dedup = state.get("stats", {}).get("dedup", {})
    label = state.get("stats", {}).get("label", {})
    stats = {
        **state.get("stats", {}),
        "sources": sources_stats,
        "new_stories": dedup.get("stories_created", 0),
        "stories_updated": dedup.get("stories_updated", 0),
        "labeled": label.get("labeled", 0),
        "label_skipped": label.get("skipped", False),
        "errors": errors,
        "error_count": len(errors),
        "duration_seconds": round(duration, 2),
    }
    status = "completed_with_errors" if errors else "completed"
    stats["status"] = status
    with engine.begin() as conn:
        conn.execute(
            _FINISH_RUN,
            {"run_id": run_id, "status": status, "stats": json.dumps(stats)},
        )
    return {"stats": stats}


@lru_cache
def _checkpoint_pool(dsn: str) -> ConnectionPool:
    """Process-lifetime pool backing the PostgresSaver checkpointer.

    Connections are autocommit: the checkpointer's ``setup()`` runs
    ``CREATE INDEX CONCURRENTLY``, which cannot run inside a transaction.
    """
    pool = ConnectionPool(dsn, open=False, kwargs={"autocommit": True})
    pool.open()
    return pool


def build_graph():
    """Compile the pipeline StateGraph with the PostgresSaver checkpointer."""
    settings = get_settings()
    saver = PostgresSaver(_checkpoint_pool(settings.database_url))
    saver.setup()

    builder = StateGraph(PipelineState)
    builder.add_node("select_due_sources", select_due_sources_node)
    builder.add_node("fetch_source", fetch_source_node)
    builder.add_node("dedup", dedup_node)
    builder.add_node("label", label_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "select_due_sources")
    builder.add_conditional_edges("select_due_sources", route_fetch, {"dedup": "dedup"})
    builder.add_edge("fetch_source", "dedup")
    builder.add_edge("dedup", "label")
    builder.add_edge("label", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=saver)
