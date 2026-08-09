"""LangGraph pipeline tests: end-to-end run + crash-resume acceptance.

The acceptance test (``test_crash_resume_fetch_not_reexecuted``) is the
Sub-phase 1.4 gate: it simulates the process dying after fetch but before
label, re-invokes with the same thread_id, and proves fetch is not
re-executed (the accumulated ``new_arrival_ids`` would double if it were).
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

from newspipe.db.engine import get_engine
from newspipe.graph.build import build_graph
from newspipe.graph.state import INITIAL_STATE
from newspipe.models.schemas import RawItem, Source

TEST_SOURCE_PREFIX = "__graph_test__"
TEST_URL_PREFIX = "https://graph.test/"


class _StubFetcher:
    def __init__(self, items: list[RawItem], error: Exception | None = None) -> None:
        self._items = items
        self._error = error

    def fetch(self, source) -> list[RawItem]:
        if self._error is not None:
            raise self._error
        return self._items


def _new_source(name: str) -> int:
    with get_engine().begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO sources (name, method, config, poll_interval_minutes)"
                " VALUES (:name, 'rss', '{}'::jsonb, 0) RETURNING source_id"
            ),
            {"name": name},
        ).fetchone()
    return int(row[0])


def _test_only_sources(conn, source_ids: list[int] | None = None) -> list[Source]:
    """Stub for graph's select_due_sources: only the test's own sources."""
    stmt = (
        "SELECT source_id, name, method, config, poll_interval_minutes, last_polled_at,"
        " enabled, created_at FROM sources WHERE name LIKE :prefix ORDER BY source_id"
    )
    rows = conn.execute(text(stmt), {"prefix": TEST_SOURCE_PREFIX + "%"}).mappings().fetchall()
    return [Source(**dict(row)) for row in rows]


def _skip_label(limit: int | None = None, chain=None) -> dict:
    return {
        "skipped": True,
        "selected": 0,
        "labeled": 0,
        "failed": 0,
        "results": {},
        "contexts": [],
    }


@pytest.fixture()
def thread_id() -> str:
    return "test-" + uuid.uuid4().hex[:12]


@pytest.fixture(autouse=True)
def _cleanup_graph_data() -> None:
    yield
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "DELETE FROM labels WHERE story_id IN"
                " (SELECT story_id FROM stories WHERE canonical_url LIKE :prefix)"
            ),
            {"prefix": TEST_URL_PREFIX + "%"},
        )
        conn.execute(
            text(
                "DELETE FROM arrivals WHERE source_id IN"
                " (SELECT source_id FROM sources WHERE name LIKE :prefix)"
            ),
            {"prefix": TEST_SOURCE_PREFIX + "%"},
        )
        conn.execute(
            text("DELETE FROM stories WHERE canonical_url LIKE :prefix"),
            {"prefix": TEST_URL_PREFIX + "%"},
        )
        conn.execute(
            text("DELETE FROM sources WHERE name LIKE :prefix"),
            {"prefix": TEST_SOURCE_PREFIX + "%"},
        )
        conn.execute(text("DELETE FROM pipeline_runs WHERE thread_id LIKE 'test-%'"))


def _stub_single_item() -> _StubFetcher:
    return _StubFetcher(
        [RawItem(external_id="g1", url=TEST_URL_PREFIX + "a", title="Graph story A")]
    )


def test_graph_end_to_end(monkeypatch: pytest.MonkeyPatch, thread_id: str) -> None:
    sid = _new_source(TEST_SOURCE_PREFIX + "e2e")
    monkeypatch.setattr("newspipe.fetch.get_fetcher", lambda method: _stub_single_item())
    monkeypatch.setattr("newspipe.graph.build.select_due_sources", _test_only_sources)
    monkeypatch.setattr("newspipe.graph.build.run_label", _skip_label)

    graph = build_graph()
    result = graph.invoke(INITIAL_STATE, config={"configurable": {"thread_id": thread_id}})

    assert result["run_id"]
    assert len(result["fetch_results"]) == 1
    assert result["fetch_results"][0]["inserted"] == 1
    assert result["fetch_results"][0]["source_id"] == sid
    assert len(result["new_arrival_ids"]) == 1
    assert len(result["affected_story_ids"]) == 1
    assert result["labeled_story_ids"] == []
    assert result["stats"]["new_stories"] == 1
    assert result["stats"]["sources"][TEST_SOURCE_PREFIX + "e2e"]["inserted"] == 1
    assert result["stats"]["label"]["skipped"] is True

    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, stats FROM pipeline_runs WHERE thread_id = :tid"
            ),
            {"tid": thread_id},
        ).mappings().fetchone()
    assert row is not None
    assert row["status"] == "completed"
    assert row["stats"]["new_stories"] == 1


def test_failing_source_is_isolated_in_graph(
    monkeypatch: pytest.MonkeyPatch, thread_id: str
) -> None:
    _new_source(TEST_SOURCE_PREFIX + "boom")
    boom = RuntimeError("feed down")
    monkeypatch.setattr(
        "newspipe.fetch.get_fetcher", lambda method: _StubFetcher([], error=boom)
    )
    monkeypatch.setattr("newspipe.graph.build.select_due_sources", _test_only_sources)
    monkeypatch.setattr("newspipe.graph.build.run_label", _skip_label)

    graph = build_graph()
    result = graph.invoke(INITIAL_STATE, config={"configurable": {"thread_id": thread_id}})

    assert len(result["errors"]) == 1
    assert "feed down" in result["errors"][0]
    assert result["fetch_results"][0]["error"] == "feed down"
    assert result["stats"]["error_count"] == 1

    with get_engine().connect() as conn:
        status = conn.execute(
            text("SELECT status FROM pipeline_runs WHERE thread_id = :tid"),
            {"tid": thread_id},
        ).scalar()
    assert status == "completed_with_errors"


def test_crash_resume_fetch_not_reexecuted(
    monkeypatch: pytest.MonkeyPatch, thread_id: str
) -> None:
    """Acceptance test: kill after fetch, resume, fetch must not re-run."""
    sid = _new_source(TEST_SOURCE_PREFIX + "crash")
    monkeypatch.setattr("newspipe.fetch.get_fetcher", lambda method: _stub_single_item())
    monkeypatch.setattr("newspipe.graph.build.select_due_sources", _test_only_sources)
    monkeypatch.setattr("newspipe.graph.build.run_label", _skip_label)

    graph = build_graph()

    # 1. first attempt "dies" in the label node (after fetch, before label)
    os.environ["NEWSPIPE_CRASH_AFTER_FETCH"] = "1"
    try:
        with pytest.raises(RuntimeError, match="simulated crash"):
            graph.invoke(INITIAL_STATE, config={"configurable": {"thread_id": thread_id}})
    finally:
        del os.environ["NEWSPIPE_CRASH_AFTER_FETCH"]

    # fetch happened exactly once, and the run row is left open ('running')
    with get_engine().connect() as conn:
        arrivals = conn.execute(
            text("SELECT count(*) FROM arrivals WHERE source_id = :sid"), {"sid": sid}
        ).scalar()
        status = conn.execute(
            text("SELECT status FROM pipeline_runs WHERE thread_id = :tid"),
            {"tid": thread_id},
        ).scalar()
    assert arrivals == 1
    assert status == "running"

    # 2. resume with the same thread_id -> completes; fetch must NOT re-execute.
    #    Resuming passes None as input: re-passing the initial state would
    #    restart the run from scratch.
    result = graph.invoke(None, config={"configurable": {"thread_id": thread_id}})

    assert len(result["fetch_results"]) == 1  # not 2: fetch node was replayed from checkpoint
    assert len(result["new_arrival_ids"]) == 1  # not 2: would double if fetch re-ran
    assert result["stats"]["label"]["skipped"] is True

    with get_engine().connect() as conn:
        arrivals = conn.execute(
            text("SELECT count(*) FROM arrivals WHERE source_id = :sid"), {"sid": sid}
        ).scalar()
        status = conn.execute(
            text("SELECT status FROM pipeline_runs WHERE thread_id = :tid"),
            {"tid": thread_id},
        ).scalar()
    assert arrivals == 1
    assert status == "completed"
