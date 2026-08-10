"""Source registry queries."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from newspipe.models.schemas import Source

_SELECT_COLUMNS = """
SELECT source_id, name, method, config, poll_interval_minutes,
       last_polled_at, enabled, created_at
FROM sources
"""


def select_due_sources(conn: psycopg.Connection, now: datetime) -> list[Source]:
    """Return enabled sources whose poll interval has elapsed (or never polled)."""
    rows = conn.execute(
        _SELECT_COLUMNS
        + """
        WHERE enabled
          AND (last_polled_at IS NULL
               OR last_polled_at + make_interval(mins => poll_interval_minutes) < %s)
        ORDER BY source_id
        """,
        (now,),
    ).fetchall()
    return [Source(**row) for row in rows]


def select_all_sources(conn: psycopg.Connection) -> list[Source]:
    """Return every source in the registry."""
    rows = conn.execute(_SELECT_COLUMNS + " ORDER BY source_id").fetchall()
    return [Source(**row) for row in rows]


def select_source_by_id(conn: psycopg.Connection, source_id: int) -> Source | None:
    """Return one source by id, or None if it no longer exists."""
    row = conn.execute(_SELECT_COLUMNS + " WHERE source_id = %s", (source_id,)).fetchone()
    return Source(**row) if row else None


def update_last_polled(conn: psycopg.Connection, source_id: int, when: datetime) -> None:
    """Record a successful poll timestamp on a source."""
    conn.execute(
        "UPDATE sources SET last_polled_at = %s WHERE source_id = %s",
        (when, source_id),
    )


def upsert_source(
    conn: psycopg.Connection,
    name: str,
    method: str,
    config: dict[str, Any],
) -> None:
    """Insert a source, or update its method/config if the name already exists."""
    conn.execute(
        """
        INSERT INTO sources (name, method, config)
        VALUES (%s, %s, %s)
        ON CONFLICT (name) DO UPDATE
        SET method = EXCLUDED.method, config = EXCLUDED.config
        """,
        (name, method, Jsonb(config)),
    )
