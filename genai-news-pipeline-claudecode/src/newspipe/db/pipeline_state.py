"""Small key/value store for pipeline bookkeeping (the `pipeline_state` table).

Used to track things that don't belong on any one domain table — e.g. the
last time the labeling step actually ran, so its cadence can be decoupled
from the fetch schedule (see `graph/build.py::label`).
"""

from __future__ import annotations

import psycopg


def get_state(conn: psycopg.Connection, key: str) -> str | None:
    """Return the stored value for `key`, or None if it has never been set."""
    row = conn.execute("SELECT value FROM pipeline_state WHERE key = %s", (key,)).fetchone()
    return row["value"] if row else None


def set_state(conn: psycopg.Connection, key: str, value: str) -> None:
    """Upsert `key` to `value`, stamping `updated_at`."""
    conn.execute(
        """
        INSERT INTO pipeline_state (key, value, updated_at)
        VALUES (%s, %s, now())
        ON CONFLICT (key) DO UPDATE
        SET value = EXCLUDED.value, updated_at = now()
        """,
        (key, value),
    )
