"""Smoke test: connects to Postgres and confirms the full schema exists."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

EXPECTED_TABLES = {"sources", "arrivals", "stories", "labels", "pipeline_runs", "schema_migrations"}


@pytest.fixture(scope="module")
def tables(engine: Engine) -> set[str]:
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            ).fetchall()
    except OperationalError as exc:  # pragma: no cover - depends on local Postgres
        pytest.skip(f"Postgres not reachable: {exc}")
    return {row[0] for row in rows}


def test_all_core_tables_exist(tables: set[str]) -> None:
    assert tables >= EXPECTED_TABLES


def test_arrivals_unique_constraint_exists(engine: Engine) -> None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conname = 'uq_arrivals_source_external'"
            )
        ).fetchone()
    assert row is not None


def test_required_indexes_exist(engine: Engine) -> None:
    expected = {
        "idx_arrivals_url_canonical",
        "idx_arrivals_story_id",
        "idx_stories_first_seen_at",
        "idx_labels_story_labeled_at",
    }
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
        ).fetchall()
    present = {row[0] for row in rows}
    assert expected <= present
