"""Tests for the operational status queries (Sub-phase 1.5)."""

from __future__ import annotations

from datetime import UTC, datetime

from newspipe.db.pipeline_runs import finalize_pipeline_run, insert_pipeline_run
from newspipe.status import _recent_errors, _recent_runs, _source_status, _unlabeled_backlog


def test_recent_runs_and_errors(db_conn, source_scope):
    source_scope("zz-status-src")
    run_id = insert_pipeline_run(db_conn, "zz-status-thread", datetime.now(UTC))
    finalize_pipeline_run(
        db_conn,
        run_id,
        status="success",
        stats={"sources_ok": 1, "sources_failed": 1, "errors": ["some feed exploded"]},
    )
    db_conn.commit()

    runs = _recent_runs(db_conn, limit=5)
    assert runs[0]["run_id"] == run_id
    assert runs[0]["status"] == "success"
    assert runs[0]["thread_id"] == "zz-status-thread"

    errors = _recent_errors(db_conn, limit=5)
    assert any(e["error"] == "some feed exploded" for e in errors)


def test_source_status_and_backlog(db_conn, source_scope):
    source_scope("zz-status-srcb")
    sources = _source_status(db_conn)
    assert any(s["name"] == "zz-status-srcb" for s in sources)
    assert isinstance(_unlabeled_backlog(db_conn), int)
