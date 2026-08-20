"""Smoke test: connects to Postgres and confirms the schema exists."""

from __future__ import annotations

EXPECTED_TABLES = {
    "sources",
    "arrivals",
    "stories",
    "labels",
    "pipeline_runs",
    "pipeline_state",
    "schema_migrations",
}


def test_all_tables_exist(db_conn) -> None:
    rows = db_conn.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        """
    ).fetchall()
    tables = {row["table_name"] for row in rows}
    missing = EXPECTED_TABLES - tables
    assert not missing, f"missing tables: {sorted(missing)}"


def test_expected_columns(db_conn) -> None:
    rows = db_conn.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
        """
    ).fetchall()
    cols: dict[str, set[str]] = {}
    for row in rows:
        cols.setdefault(row["table_name"], set()).add(row["column_name"])

    assert {"source_id", "name", "method", "config", "last_polled_at"} <= cols["sources"]
    assert {"canonical_url", "title_hash", "arrival_count", "hn_front_page"} <= cols["stories"]
    assert {"arrival_id", "external_id", "url_canonical", "raw", "story_id"} <= cols["arrivals"]
    assert {"is_hot", "importance", "category", "prompt_version", "model"} <= cols["labels"]
    assert {"thread_id", "status", "stats"} <= cols["pipeline_runs"]


def test_migration_runner_is_idempotent() -> None:
    from newspipe.db.migrate import run_migrations

    assert run_migrations() == []
