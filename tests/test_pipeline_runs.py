"""Tests for pipeline run telemetry queries (`db/pipeline_runs.py`)."""

from __future__ import annotations

from datetime import UTC, datetime

from newspipe.db.pipeline_runs import (
    finalize_pipeline_run,
    insert_pipeline_run,
    select_last_successful_run_finished_at,
)


def test_select_last_successful_run_finished_at_ignores_failed_runs(db_conn, source_scope):
    source_scope("zz-pr-src")
    failed_id = insert_pipeline_run(db_conn, "zz-pr-failed", datetime.now(UTC))
    finalize_pipeline_run(db_conn, failed_id, status="failed", stats={})
    db_conn.commit()

    ok_id = insert_pipeline_run(db_conn, "zz-pr-ok", datetime.now(UTC))
    finalize_pipeline_run(db_conn, ok_id, status="success", stats={})
    db_conn.commit()

    finished_at = select_last_successful_run_finished_at(db_conn)

    row = db_conn.execute(
        "SELECT finished_at FROM pipeline_runs WHERE run_id = %s", (ok_id,)
    ).fetchone()
    assert finished_at == row["finished_at"]


def test_select_last_successful_run_finished_at_picks_the_latest(db_conn, source_scope):
    source_scope("zz-pr-src2")
    older_id = insert_pipeline_run(db_conn, "zz-pr-older", datetime.now(UTC))
    finalize_pipeline_run(db_conn, older_id, status="success", stats={})
    db_conn.commit()

    newer_id = insert_pipeline_run(db_conn, "zz-pr-newer", datetime.now(UTC))
    finalize_pipeline_run(db_conn, newer_id, status="success", stats={})
    db_conn.commit()

    finished_at = select_last_successful_run_finished_at(db_conn)

    row = db_conn.execute(
        "SELECT finished_at FROM pipeline_runs WHERE run_id = %s", (newer_id,)
    ).fetchone()
    assert finished_at == row["finished_at"]
