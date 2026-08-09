"""Persistence helpers for arrivals."""

from __future__ import annotations

import json

from sqlalchemy import text

from newspipe.models.schemas import RawItem

_INSERT = text(
    """
    INSERT INTO arrivals
        (source_id, external_id, url, url_canonical, title, published_at, raw)
    VALUES
        (:source_id, :external_id, :url, :url_canonical, :title, :published_at, CAST(:raw AS jsonb))
    ON CONFLICT (source_id, external_id) DO NOTHING
    """
)


def insert_arrivals(conn, source_id: int, items: list[RawItem]) -> int:
    """Insert arrivals idempotently; returns the number actually inserted."""
    inserted = 0
    for item in items:
        result = conn.execute(
            _INSERT,
            {
                "source_id": source_id,
                "external_id": item.external_id,
                "url": item.url,
                "url_canonical": None,
                "title": item.title,
                "published_at": item.published_at,
                "raw": json.dumps(item.raw, default=str),
            },
        )
        inserted += result.rowcount or 0
    return inserted


def update_source_last_polled(conn, source_id: int) -> None:
    conn.execute(
        text("UPDATE sources SET last_polled_at = now() WHERE source_id = :source_id"),
        {"source_id": source_id},
    )
