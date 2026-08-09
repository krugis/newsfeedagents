"""Status CLI query tests against the real DB (self-contained rows)."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from newspipe.db.engine import get_engine
from newspipe.status import get_status


@pytest.fixture()
def thread_id() -> str:
    return "status-test-" + uuid.uuid4().hex[:8]


def test_get_status_reports_runs_backlog_and_sources(thread_id: str) -> None:
    engine = get_engine()
    stats = json.dumps(
        {"new_stories": 3, "labeled": 2, "error_count": 1, "errors": ["source 99: feed down"]}
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO pipeline_runs (thread_id, status, stats)"
                " VALUES (:tid, 'completed_with_errors', CAST(:stats AS jsonb))"
            ),
            {"tid": thread_id, "stats": stats},
        )
    try:
        status = get_status()
        assert status["runs"][0]["thread_id"] == thread_id
        assert status["runs"][0]["status"] == "completed_with_errors"
        assert status["runs"][0]["new_stories"] == 3
        assert status["runs"][0]["labeled"] == 2
        assert status["runs"][0]["error_count"] == 1
        assert status["runs"][0]["errors"] == ["source 99: feed down"]
        assert status["unlabeled_backlog"] >= 0
        assert any(s["name"] == "TechCrunch AI" for s in status["sources"])
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM pipeline_runs WHERE thread_id = :tid"), {"tid": thread_id}
            )


def test_get_status_with_no_runs() -> None:
    # runs are ordered DESC by run_id; this only checks the shape when empty is possible
    status = get_status()
    assert isinstance(status["runs"], list)
    assert isinstance(status["unlabeled_backlog"], int)
