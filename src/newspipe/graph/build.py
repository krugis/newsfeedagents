"""LangGraph assembly for one pipeline run (Sub-phase 1.4).

Flow: `select_due_sources → [Send fan-out: fetch_source per source] → dedup →
label → finalize`.

Every node's writes are checkpointed by a `PostgresSaver` bound to a
`thread_id`, so a crash between nodes resumes from the last checkpoint
instead of re-running earlier work — in particular, a resumed run never
re-executes fetch (the acceptance test of this sub-phase).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from newspipe.config import get_settings
from newspipe.db.arrivals import insert_arrivals_returning
from newspipe.db.engine import connect
from newspipe.db.pipeline_runs import finalize_pipeline_run, insert_pipeline_run
from newspipe.db.sources import select_due_sources as select_due_sources_db
from newspipe.db.sources import select_source_by_id, update_last_polled
from newspipe.dedup import run_dedup
from newspipe.fetchers import get_fetcher
from newspipe.graph.state import PipelineState
from newspipe.labeling.labeler import label_unlabeled
from newspipe.logging_setup import setup_logging

logger = logging.getLogger(__name__)


def select_due_sources(state: PipelineState) -> dict:
    """Open a pipeline_runs row and pick which sources are due this run."""
    now = datetime.now(UTC)
    with connect() as conn:
        due = select_due_sources_db(conn, now)
        run_id = insert_pipeline_run(conn, state.get("thread_id") or "run", now)
    return {"due_source_ids": [s.source_id for s in due], "run_id": run_id}


def fan_out(state: PipelineState) -> list[Send] | str:
    """One Send per due source, or a direct hop to dedup when none are due.

    With zero due sources an empty Send list would produce an empty fetch
    superstep and the graph would end there (the fetch_source->dedup edge only
    fires if fetch_source ran) — so route straight to dedup instead.
    """
    due = state.get("due_source_ids", [])
    if not due:
        return "dedup"
    return [Send("fetch_source", {"source_id": sid}) for sid in due]


def fetch_source(state: PipelineState) -> dict:
    """Fetch one source and persist arrivals; never raises — errors land in state."""
    source_id = state["source_id"]
    now = datetime.now(UTC)
    with connect() as conn:
        source = select_source_by_id(conn, source_id)
        if source is None:
            return {"errors": [f"source {source_id}: not found"]}
        try:
            items = get_fetcher(source.method)(source)
            new_ids = insert_arrivals_returning(conn, source.source_id, items)
            update_last_polled(conn, source.source_id, now)
        except Exception as exc:  # noqa: BLE001 - per-source isolation
            return {"errors": [f"{source.name}: {type(exc).__name__}: {exc}"]}
    return {
        "fetch_results": [
            {"source": source.name, "status": "ok", "fetched": len(items), "new": len(new_ids)}
        ],
        "new_arrival_ids": new_ids,
    }


def dedup(state: PipelineState) -> dict:
    """Thin wrapper over dedup v1 (1.2)."""
    stats = run_dedup()
    return {
        "affected_story_ids": stats.affected_story_ids,
        "dedup_stats": {
            "arrivals_processed": stats.arrivals_processed,
            "stories_created": stats.stories_created,
            "arrivals_attached": stats.arrivals_attached,
            "title_matches": stats.title_matches,
        },
    }


def label(state: PipelineState) -> dict:
    """Thin wrapper over the labeling batch (1.3), bounded by the backfill guard.

    Without a limit a backfilled DB would be drained in a single run; the
    hourly cadence plus label_limit_per_run clears it gradually instead.
    """
    stats = label_unlabeled(limit=get_settings().label_limit_per_run)
    return {"labeled_story_ids": stats.labeled_story_ids}


def finalize(state: PipelineState) -> dict:
    """Close the pipeline_runs row with a stats summary."""
    now = datetime.now(UTC)
    stats = _run_stats(state)
    with connect() as conn:
        started = conn.execute(
            "SELECT started_at FROM pipeline_runs WHERE run_id = %s", (state["run_id"],)
        ).fetchone()
        if started:
            stats["duration_s"] = round((now - started["started_at"]).total_seconds(), 3)
        finalize_pipeline_run(conn, state["run_id"], status="success", stats=stats)
    return {"stats": stats}


def _run_stats(state: PipelineState) -> dict:
    fetched = state.get("fetch_results", [])
    dedup_stats = state.get("dedup_stats", {})
    return {
        "per_source": fetched,
        "sources_ok": sum(1 for r in fetched if r.get("status") == "ok"),
        "sources_failed": len(state.get("errors", [])),
        "new_arrivals": len(state.get("new_arrival_ids", [])),
        "stories_created": dedup_stats.get("stories_created", 0),
        "arrivals_attached": dedup_stats.get("arrivals_attached", 0),
        "stories_touched": len(state.get("affected_story_ids", [])),
        "labeled": len(state.get("labeled_story_ids", [])),
        "errors": state.get("errors", []),
    }


def build_graph(checkpointer, interrupt_before: list[str] | None = None) -> CompiledStateGraph:
    """Assemble and compile the pipeline StateGraph with durable checkpointing.

    `interrupt_before` lets a caller stop the graph at a node boundary (used by
    the crash-resume proof to model a process killed after fetch).
    """
    builder = StateGraph(PipelineState)
    builder.add_node("select_due_sources", select_due_sources)
    builder.add_node("fetch_source", fetch_source)
    builder.add_node("dedup", dedup)
    builder.add_node("label", label)
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "select_due_sources")
    builder.add_conditional_edges(
        "select_due_sources", fan_out, {"fetch_source": "fetch_source", "dedup": "dedup"}
    )
    builder.add_edge("fetch_source", "dedup")
    builder.add_edge("dedup", "label")
    builder.add_edge("label", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer, interrupt_before=interrupt_before)


def initial_state(thread_id: str) -> PipelineState:
    """The starting state for a fresh run under `thread_id`."""
    return {
        "thread_id": thread_id,
        "run_id": None,
        "due_source_ids": [],
        "fetch_results": [],
        "new_arrival_ids": [],
        "affected_story_ids": [],
        "labeled_story_ids": [],
        "errors": [],
        "dedup_stats": {},
        "stats": {},
    }


def run_pipeline(thread_id: str | None = None) -> dict:
    """Invoke the graph once under `thread_id` (default hour-slot).

    Returns the final pipeline state. When the thread already has a checkpoint
    (a crashed run), langgraph requires `input=None` to resume — passing an
    input would restart the run — so the checkpoint existence check decides.
    """
    settings = get_settings()
    thread_id = thread_id or f"run-{datetime.now(UTC):%Y%m%d-%H}"
    with PostgresSaver.from_conn_string(settings.database_url) as saver:
        saver.setup()
        graph = build_graph(saver)
        config = {"configurable": {"thread_id": thread_id}}
        has_checkpoint = saver.get_tuple(config) is not None
        run_input = None if has_checkpoint else initial_state(thread_id)
        return graph.invoke(run_input, config=config)


def log_run(result: dict) -> None:
    """INFO summary + DEBUG per-source detail + WARNING per error, for a run."""
    stats = result.get("stats", {})
    logger.info(
        "run_completed",
        extra={
            "extra_run_id": result.get("run_id"),
            "extra_thread_id": result.get("thread_id"),
            "extra_status": "success",
            "extra_stats": stats,
        },
    )
    for source in stats.get("per_source", []):
        logger.debug(
            "source_result",
            extra={
                "extra_source": source.get("source"),
                "extra_fetched": source.get("fetched"),
                "extra_new": source.get("new"),
            },
        )
    for error in stats.get("errors", []):
        logger.warning("source_error", extra={"extra_error": error})


def main(resume: str | None = None) -> int:
    """CLI: one full graph invocation, checkpointed under a thread_id.

    The default thread_id is `run-YYYYMMDD-HH`, so re-invoking within the same
    hour resumes a crashed run from its last checkpoint instead of restarting.
    `--resume <thread_id>` targets a specific thread explicitly.
    """
    setup_logging(to_stdout=False)
    thread_id = resume or f"run-{datetime.now(UTC):%Y%m%d-%H}"
    print(f"thread: {thread_id}")
    result = run_pipeline(thread_id)
    log_run(result)
    stats = result.get("stats", {})
    print(f"run_id:            {result.get('run_id')}")
    print(f"sources ok/failed: {stats.get('sources_ok')}/{stats.get('sources_failed')}")
    print(f"new arrivals:      {stats.get('new_arrivals')}")
    print(
        f"stories created:   {stats.get('stories_created')} "
        f"(touched {stats.get('stories_touched')})"
    )
    print(f"labeled:           {stats.get('labeled')}")
    print(f"duration:          {stats.get('duration_s')}s")
    for error in stats.get("errors", []):
        print(f"  ERROR: {error}")
    return 1 if stats.get("sources_failed") else 0
