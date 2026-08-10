"""Tiny migration runner: applies .sql files in lexicographic order.

Applied versions are recorded in the `schema_migrations` table so the runner
is idempotent. Each migration runs inside a single transaction (the psycopg
connection context manager rolls back on error), so a failed migration never
leaves partial state.
"""

from __future__ import annotations

from pathlib import Path

from newspipe.db.engine import connect

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def run_migrations() -> list[str]:
    """Apply any not-yet-applied migration files and return the versions applied."""
    applied: list[str] = []
    with connect() as conn:
        conn.execute(_MIGRATIONS_TABLE)
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = path.name
            row = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version = %s", (version,)
            ).fetchone()
            if row:
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            applied.append(version)
    return applied


if __name__ == "__main__":
    versions = run_migrations()
    print(f"Applied migrations: {', '.join(versions) if versions else 'none (up to date)'}")
