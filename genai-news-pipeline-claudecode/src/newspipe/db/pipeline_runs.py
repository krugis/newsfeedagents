"""Pipeline run telemetry (`pipeline_runs` table)."""

from __future__ import annotations

from datetime import datetime

from psycopg.types.json import Jsonb


def insert_pipeline_run(conn, thread_id: str, started_at: datetime) -> int:
    """Open a run row with status `running`; returns its `run_id`.

    `finalize_pipeline_run` closes the row. The row is created once per graph
    run — the `select_due_sources` node that calls this is checkpointed, so a
    crash-resumed run does not create a duplicate.
    """
    row = conn.execute(
        """
        INSERT INTO pipeline_runs (thread_id, started_at, status)
        VALUES (%s, %s, 'running')
        RETURNING run_id
        """,
        (thread_id, started_at),
    ).fetchone()
    return row["run_id"]


def finalize_pipeline_run(conn, run_id: int, *, status: str, stats: dict) -> None:
    """Close a run row with a status and a stats summary."""
    conn.execute(
        """
        UPDATE pipeline_runs
           SET finished_at = now(), status = %s, stats = %s
         WHERE run_id = %s
        """,
        (status, Jsonb(stats), run_id),
    )


def select_last_successful_run_finished_at(conn) -> datetime | None:
    """When the most recent successful (fetch→dedup→label) pipeline run finished.

    Used as the "data last updated" freshness indicator on the front page,
    /topic, and the bot's replies — a completed run is a stronger freshness
    signal than "newest story timestamp" (which could reflect a single lucky
    arrival even if the pipeline itself has been failing).
    """
    row = conn.execute(
        "SELECT max(finished_at) AS finished_at FROM pipeline_runs WHERE status = 'success'"
    ).fetchone()
    return row["finished_at"] if row else None
