"""Queries against the sources registry."""

from __future__ import annotations

from sqlalchemy import bindparam, text

from newspipe.models.schemas import Source

_SOURCE_COLUMNS = """
    source_id, name, method, config, poll_interval_minutes,
    last_polled_at, enabled, created_at
"""

_DUE_WHERE = (
    "WHERE enabled AND (last_polled_at IS NULL"
    " OR last_polled_at + make_interval(mins => poll_interval_minutes) <= now())"
)


def select_due_sources(conn, source_ids: list[int] | None = None) -> list[Source]:
    """Enabled sources due for polling (optionally restricted to ids)."""
    stmt = text("SELECT " + _SOURCE_COLUMNS + " FROM sources " + _DUE_WHERE + " ORDER BY source_id")
    params: dict[str, object] = {}
    if source_ids:
        stmt = text(
            "SELECT " + _SOURCE_COLUMNS + " FROM sources " + _DUE_WHERE
            + " AND source_id IN :source_ids ORDER BY source_id"
        ).bindparams(bindparam("source_ids", expanding=True))
        params["source_ids"] = tuple(source_ids)
    rows = conn.execute(stmt, params).mappings().fetchall()
    return [Source(**dict(row)) for row in rows]
