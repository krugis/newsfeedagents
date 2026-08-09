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
    RETURNING arrival_id
    """
)


def insert_arrivals(conn, source_id: int, items: list[RawItem]) -> list[int]:
    """Insert arrivals idempotently; returns the inserted arrival_ids."""
    inserted_ids: list[int] = []
    for item in items:
        arrival_id = conn.execute(
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
        ).scalar_one_or_none()
        if arrival_id is not None:
            inserted_ids.append(int(arrival_id))
    return inserted_ids


def update_source_last_polled(conn, source_id: int) -> None:
    conn.execute(
        text("UPDATE sources SET last_polled_at = now() WHERE source_id = :source_id"),
        {"source_id": source_id},
    )
