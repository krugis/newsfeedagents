"""Tests for the LangGraph pipeline (Sub-phase 1.4): nodes, stats, and the
crash-resume acceptance test.

The checkpointer lives in the same Postgres the pipeline uses (isolated
per-project), so these run against the real dev DB. Thread ids are unique per
test invocation so a re-run of the suite starts fresh instead of resuming a
completed checkpoint.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

import pytest
from langgraph.checkpoint.postgres import PostgresSaver

from newspipe.config import get_settings
from newspipe.graph import build
from newspipe.labeling.labeler import LabelStats


@pytest.fixture
def checkpointer():
    """A live PostgresSaver bound to the pipeline DB, closed on teardown.

    The `with` block stays open for the whole test so a crash-resume test can
    invoke twice against the same pool.
    """
    with PostgresSaver.from_conn_string(get_settings().database_url) as saver:
        saver.setup()
        yield saver


def _thread(prefix: str) -> str:
    return f"zz-test-{prefix}-{uuid.uuid4().hex[:10]}"


def _noop_fetcher(calls: dict, external_id: str, title: str):
    """A fetcher that records calls and returns a single fixed item (no network)."""

    def fake(source):  # noqa: ARG001
        calls["n"] += 1
        from newspipe.fetchers.base import RawItem

        return [
            RawItem(external_id=external_id, url=f"https://example.com/{external_id}", title=title)
        ]

    return fake


@contextmanager
def _due_only(db_conn, source_scope, names: list[str]):
    """Make only the given test sources due for the graph run.

    source_scope inserts are uncommitted until committed, and the dev DB's 8
    seeded real sources are usually due — so we commit the test sources and
    mark every non-zz source as freshly polled (restoring afterwards), leaving
    exactly `names` due.
    """
    for name in names:
        source_scope(name)
    db_conn.commit()
    real = db_conn.execute(
        "SELECT source_id, last_polled_at FROM sources WHERE name NOT LIKE 'zz-%'"
    ).fetchall()
    db_conn.execute("UPDATE sources SET last_polled_at = now() WHERE name NOT LIKE 'zz-%'")
    db_conn.commit()
    try:
        yield
    finally:
        for row in real:
            db_conn.execute(
                "UPDATE sources SET last_polled_at = %s WHERE source_id = %s",
                (row["last_polled_at"], row["source_id"]),
            )
        db_conn.commit()


def _invoke(graph, thread_id: str):
    return graph.invoke(
        build.initial_state(thread_id), config={"configurable": {"thread_id": thread_id}}
    )


def test_clean_run_writes_pipeline_run(db_conn, source_scope, checkpointer, monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(
        "newspipe.graph.build.get_fetcher",
        lambda method: _noop_fetcher(calls, "zz-gc-1", "Clean Run Story"),
    )
    monkeypatch.setattr(
        "newspipe.graph.build.label_unlabeled",
        lambda limit=None: LabelStats(labeled_story_ids=[111]),
    )

    with _due_only(db_conn, source_scope, ["zz-graph-clean"]):
        graph = build.build_graph(checkpointer)
        thread_id = _thread("clean")
        result = _invoke(graph, thread_id)

    assert calls["n"] == 1  # fetched exactly once
    assert result["run_id"] is not None
    stats = result["stats"]
    assert stats["sources_ok"] == 1
    assert stats["sources_failed"] == 0
    assert stats["new_arrivals"] == 1
    assert stats["stories_created"] == 1
    assert stats["stories_touched"] == 1
    assert stats["labeled"] == 1
    assert stats["duration_s"] >= 0

    row = db_conn.execute(
        "SELECT status FROM pipeline_runs WHERE run_id = %s", (result["run_id"],)
    ).fetchone()
    assert row["status"] == "success"
    # one run row only — select_due_sources ran once
    rows = db_conn.execute(
        "SELECT count(*) AS n FROM pipeline_runs WHERE thread_id = %s", (thread_id,)
    ).fetchone()
    assert rows["n"] == 1


def test_fanout_fetches_each_due_source(db_conn, source_scope, checkpointer, monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(
        "newspipe.graph.build.get_fetcher",
        lambda method: _noop_fetcher(calls, "zz-gf-1", "Fanout Story"),
    )
    monkeypatch.setattr("newspipe.graph.build.label_unlabeled", lambda limit=None: LabelStats())

    with _due_only(db_conn, source_scope, ["zz-graph-fanout-a", "zz-graph-fanout-b"]):
        graph = build.build_graph(checkpointer)
        result = _invoke(graph, _thread("fanout"))

    assert calls["n"] == 2
    assert result["stats"]["sources_ok"] == 2
    assert result["stats"]["new_arrivals"] == 2  # one distinct item per source


def test_source_error_lands_in_state_not_crash(db_conn, source_scope, checkpointer, monkeypatch):
    calls = {"n": 0}

    def failing_fetcher(source):
        calls["n"] += 1
        raise RuntimeError("fetch exploded")

    monkeypatch.setattr("newspipe.graph.build.get_fetcher", lambda method: failing_fetcher)
    monkeypatch.setattr("newspipe.graph.build.label_unlabeled", lambda limit=None: LabelStats())

    with _due_only(db_conn, source_scope, ["zz-graph-err"]):
        graph = build.build_graph(checkpointer)
        result = _invoke(graph, _thread("err"))

    assert calls["n"] == 1
    assert result["stats"]["sources_ok"] == 0
    assert result["stats"]["sources_failed"] == 1
    assert result["stats"]["errors"]  # the error message landed in state


def test_no_due_sources_still_finalizes(db_conn, source_scope, checkpointer, monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(
        "newspipe.graph.build.get_fetcher",
        lambda method: _noop_fetcher(calls, "zz-gn-1", "Not Due"),
    )
    monkeypatch.setattr("newspipe.graph.build.label_unlabeled", lambda limit=None: LabelStats())

    with _due_only(db_conn, source_scope, []):
        graph = build.build_graph(checkpointer)
        result = _invoke(graph, _thread("notdue"))

    assert calls["n"] == 0
    assert result["run_id"] is not None
    assert result["stats"]["sources_ok"] == 0
    assert result["stats"]["new_arrivals"] == 0


def test_crash_resume_skips_fetch(db_conn, source_scope, checkpointer, monkeypatch):
    """The acceptance test: crash after fetch, resume, and fetch is NOT re-run.

    A process killed after fetch leaves a checkpoint at the boundary before
    `label`. We model that exactly with LangGraph's interrupt_before=["label"]
    (the checkpoint it leaves is what a killed process leaves), then resume
    the full graph on the same thread and assert fetch did not re-execute.
    """
    calls = {"n": 0}
    monkeypatch.setattr(
        "newspipe.graph.build.get_fetcher",
        lambda method: _noop_fetcher(calls, "zz-gr-1", "Crash Resume Story"),
    )
    labeled = {"ids": [999]}
    monkeypatch.setattr(
        "newspipe.graph.build.label_unlabeled",
        lambda limit=None: LabelStats(labeled_story_ids=labeled["ids"]),
    )

    with _due_only(db_conn, source_scope, ["zz-graph-crash"]):
        builder_graph = build.build_graph

        # first "run": stop the graph right before label (crash boundary)
        graph = build.build_graph(checkpointer, interrupt_before=["label"])
        thread_id = _thread("crash")
        config = {"configurable": {"thread_id": thread_id}}
        partial = graph.invoke(build.initial_state(thread_id), config=config)
        assert calls["n"] == 1  # fetch ran before the stop
        assert "label" not in (partial.get("labeled_story_ids") or [])  # label never ran

        # resume the full graph on the SAME thread — fetch must not re-execute
        result = builder_graph(checkpointer).invoke(None, config=config)
        assert calls["n"] == 1  # still 1 — fetch was restored from the checkpoint
        assert result["labeled_story_ids"] == labeled["ids"]  # label ran on resume
        assert result["run_id"] is not None
        assert result["stats"]["new_arrivals"] == 1
        assert result["stats"]["sources_ok"] == 1

    # select_due_sources also did not re-run: exactly one pipeline_runs row
    rows = db_conn.execute(
        "SELECT count(*) AS n FROM pipeline_runs WHERE thread_id = %s", (thread_id,)
    ).fetchone()
    assert rows["n"] == 1
