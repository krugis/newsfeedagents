"""Fetch runner: ``python -m newspipe fetch``."""

from __future__ import annotations

from newspipe.db.arrivals import insert_arrivals, update_source_last_polled
from newspipe.db.engine import get_engine
from newspipe.db.sources import select_due_sources
from newspipe.fetchers import get_fetcher


def run_fetch(source_ids: list[int] | None = None) -> dict:
    """Poll every due source once; a single failure never aborts the rest.

    Returns per-source counts plus any errors, keyed by source name.
    """
    stats: dict = {"sources": {}, "errors": {}, "total_fetched": 0, "total_inserted": 0}
    engine = get_engine()
    with engine.begin() as conn:
        for source in select_due_sources(conn, source_ids=source_ids):
            try:
                items = get_fetcher(source.method).fetch(source)
                inserted = insert_arrivals(conn, source.source_id, items)
                update_source_last_polled(conn, source.source_id)
            except Exception as exc:  # noqa: BLE001 - per-source isolation is required
                stats["errors"][source.name] = str(exc)
                stats["sources"][source.name] = {"fetched": 0, "inserted": 0, "error": str(exc)}
                continue
            stats["sources"][source.name] = {"fetched": len(items), "inserted": inserted}
            stats["total_fetched"] += len(items)
            stats["total_inserted"] += inserted
    return stats
