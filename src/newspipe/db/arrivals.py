"""Arrivals persistence — append-only, idempotent on (source_id, external_id)."""

from __future__ import annotations

import psycopg
from psycopg.types.json import Jsonb

from newspipe.fetchers.base import RawItem

_INSERT_ARRIVAL = """
INSERT INTO arrivals (source_id, external_id, url, url_canonical, title, published_at, raw)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (source_id, external_id) DO NOTHING
"""


def insert_arrivals(
    conn: psycopg.Connection,
    source_id: int,
    items: list[RawItem],
) -> int:
    """Insert items as arrivals; returns the number of newly inserted rows.

    Re-fetching the same items is safe: existing (source_id, external_id)
    rows are ignored, so this is idempotent.
    """
    inserted = 0
    for item in items:
        cur = conn.execute(
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
        )
        inserted += cur.rowcount
    return inserted
