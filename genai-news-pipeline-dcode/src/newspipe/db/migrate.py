"""Tiny SQL-file migration runner.

Applies ``migrations/*.sql`` in filename order, recording applied versions in
the ``schema_migrations`` table. Each migration runs inside one transaction
via the psycopg3 connection's implicit transaction block.

Decision: a tiny runner instead of Alembic — the schema is small, the
migrations are plain SQL (per spec), and we avoid an extra framework for five
tables. If the schema grows complex later, swapping to Alembic is a drop-in
replacement because the SQL files are the source of truth.
"""

from __future__ import annotations

from pathlib import Path

import psycopg

from newspipe.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def migrate(database_url: str | None = None) -> list[str]:
    """Apply pending migrations, returning the versions applied."""
    url = database_url or get_settings().database_url
    applied_versions: list[str] = []

    with psycopg.connect(url) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = path.stem
            if version in applied:
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            applied_versions.append(version)

    return applied_versions
