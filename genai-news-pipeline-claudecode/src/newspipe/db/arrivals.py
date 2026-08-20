"""Arrivals persistence — append-only, idempotent on (source_id, external_id)."""

from __future__ import annotations

import psycopg
from psycopg.types.json import Jsonb

from newspipe.fetchers.base import RawItem

_INSERT_ARRIVAL = """
INSERT INTO arrivals (source_id, external_id, url, url_canonical, title, published_at, raw)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (source_id, external_id) DO NOTHING
RETURNING arrival_id
"""


def insert_arrivals_returning(
    conn: psycopg.Connection,
    source_id: int,
    items: list[RawItem],
) -> list[int]:
    """Insert items as arrivals; returns the newly inserted arrival_ids.

    Re-fetching the same items is safe: existing (source_id, external_id)
    rows are ignored and yield no id, so this is idempotent.
    """
    inserted: list[int] = []
    for item in items:
        row = conn.execute(
            _INSERT_ARRIVAL,
            (
                source_id,
                item.external_id,
                item.url,
                None,  # url_canonical is populated by normalization (Gate 1.2)
                item.title,
                item.published_at,
                Jsonb(item.raw),
            ),
        ).fetchone()
        if row:
            inserted.append(row["arrival_id"])
    return inserted


def insert_arrivals(
    conn: psycopg.Connection,
    source_id: int,
    items: list[RawItem],
) -> int:
    """Insert items as arrivals; returns the number of newly inserted rows."""
    return len(insert_arrivals_returning(conn, source_id, items))


def select_arrivals_page(conn: psycopg.Connection, limit: int, offset: int) -> list[dict]:
    """One page of arrivals, newest first, joined to their source name."""
    return conn.execute(
        """
        SELECT a.fetched_at, s.name AS source, a.title
          FROM arrivals a
          JOIN sources s ON s.source_id = a.source_id
         ORDER BY a.fetched_at DESC, a.arrival_id DESC
         LIMIT %s OFFSET %s
        """,
        (limit, offset),
    ).fetchall()


def count_arrivals(conn: psycopg.Connection) -> int:
    """Total number of arrivals (for pagination)."""
    return conn.execute("SELECT count(*) AS n FROM arrivals").fetchone()["n"]
